# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Consolidated Calendar Operations - Handles all read/write operations to Microsoft Graph
"""
import logging
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
import pytz
import uuid

import config
from utils import RetryUtils, DateTimeUtils

logger = logging.getLogger(__name__)


def is_all_day_event(event):
    """
    Detect if an event should be treated as all-day based on its timing characteristics.
    
    This function checks multiple conditions to identify all-day events:
    - Events explicitly marked as all-day in source data
    - Events with date-only formatting (no time components)
    - Events that span exactly 24 hours starting at midnight (but only if explicitly marked)
    """
    try:
        # Check if source explicitly marks this as all-day
        if event.get('isAllDay', False):
            logger.debug(f"Event '{event.get('subject', 'Unknown')}' marked as all-day by source")
            return True
            
        # Parse start and end times
        start_time = event.get('start', {})
        end_time = event.get('end', {})
        
        # If using date-only format, it's definitely all-day
        if 'date' in start_time and 'date' in end_time:
            logger.debug(f"Event '{event.get('subject', 'Unknown')}' uses date-only format - treating as all-day")
            return True
            
        # Handle datetime format - only treat as all-day if explicitly marked
        if 'dateTime' in start_time and 'dateTime' in end_time:
            start_dt = datetime.fromisoformat(start_time['dateTime'].replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_time['dateTime'].replace('Z', '+00:00'))
            
            # Only treat as all-day if it spans exactly 24 hours AND starts at midnight
            # AND the source explicitly marked it as all-day
            duration = end_dt - start_dt
            is_24_hours = duration.total_seconds() == 86400  # 24 * 60 * 60
            starts_at_midnight = start_dt.hour == 0 and start_dt.minute == 0 and start_dt.second == 0
            
            if is_24_hours and starts_at_midnight and event.get('isAllDay', False):
                logger.debug(f"Event '{event.get('subject', 'Unknown')}' is 24-hour event starting at midnight and marked as all-day")
                return True
            else:
                logger.debug(f"Event '{event.get('subject', 'Unknown')}' is timed event: {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}")
                return False
            
        return False
        
    except (ValueError, KeyError, AttributeError) as e:
        # Log the error but don't fail the sync
        logger.warning(f"Could not determine if event is all-day: {e}")
        return False


def format_all_day_event(source_event):
    """
    Convert an all-day event to proper Microsoft Graph format.
    
    Microsoft Graph requires dateTime format even for all-day events,
    but interprets them as date-only when isAllDay is true.
    """
    try:
        start_time = source_event.get('start', {})
        end_time = source_event.get('end', {})
        
        # Extract date components and format as midnight times
        if 'dateTime' in start_time:
            start_dt = datetime.fromisoformat(start_time['dateTime'].replace('Z', '+00:00'))
            start_date_str = start_dt.date().isoformat() + "T00:00:00.0000000"
        elif 'date' in start_time:
            start_date_str = start_time['date'] + "T00:00:00.0000000"
        else:
            raise ValueError("No valid start date found")
            
        if 'dateTime' in end_time:
            end_dt = datetime.fromisoformat(end_time['dateTime'].replace('Z', '+00:00'))
            end_date_str = end_dt.date().isoformat() + "T00:00:00.0000000"
        elif 'date' in end_time:
            end_date_str = end_time['date'] + "T00:00:00.0000000"
        else:
            raise ValueError("No valid end date found")
        
        # Return properly formatted all-day event structure
        return {
            'isAllDay': True,
            'start': {
                'dateTime': start_date_str,
                'timeZone': 'UTC'
            },
            'end': {
                'dateTime': end_date_str,
                'timeZone': 'UTC'
            }
        }
        
    except Exception as e:
        logger.error(f"Error formatting all-day event: {e}")
        return None


class CalendarReader:
    """Handles reading calendar data from Microsoft Graph"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
        # Add caching for calendar IDs
        self._calendar_cache = {}
        self._cache_expiry = {}
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def get_calendars(self) -> Optional[List[Dict]]:
        """Get all calendars for the shared mailbox with retry logic"""
        headers = self.auth.get_headers()
        if not headers:
            logger.error("No valid authentication headers")
            return None
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars"
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if self.auth.refresh_access_token():
                    headers = self.auth.get_headers()
                    response = requests.get(url, headers=headers, timeout=30)
                else:
                    logger.error("Authentication failed")
                    return None
            
            if response.status_code == 200:
                calendars = response.json().get('value', [])
                logger.info(f"Successfully retrieved {len(calendars)} calendars")
                return calendars
            else:
                logger.error(f"Failed to get calendars: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return None
        
        except requests.exceptions.Timeout:
            logger.error("Request timeout while getting calendars")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("Connection error while getting calendars")
            raise
        except Exception as e:
            logger.error(f"Error getting calendars: {e}")
            raise
    
    def find_calendar_id(self, calendar_name: str) -> Optional[str]:
        """Find a calendar ID by name with caching"""
        # Check cache first
        if calendar_name in self._calendar_cache:
            expiry = self._cache_expiry.get(calendar_name)
            if expiry and DateTimeUtils.get_central_time() < expiry:
                logger.info(f"Using cached calendar ID for '{calendar_name}'")
                return self._calendar_cache[calendar_name]
        
        # If not in cache or expired, fetch
        calendars = self.get_calendars()
        if not calendars:
            return None
        
        for calendar in calendars:
            if calendar.get('name') == calendar_name:
                calendar_id = calendar.get('id')
                logger.info(f"Found calendar '{calendar_name}' with ID: {calendar_id}")
                
                # Cache the result for 1 hour
                self._calendar_cache[calendar_name] = calendar_id
                self._cache_expiry[calendar_name] = DateTimeUtils.get_central_time() + timedelta(hours=1)
                
                return calendar_id
        
        logger.warning(f"Calendar '{calendar_name}' not found")
        return None
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def get_calendar_events(self, calendar_id: str, select_fields: List[str] = None) -> Optional[List[Dict]]:
        """Get all events from a specific calendar with retry logic"""
        headers = self.auth.get_headers()
        if not headers:
            return None
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events"
        
        # Default fields to retrieve
        if not select_fields:
            select_fields = [
                'id', 'subject', 'start', 'end', 'categories', 
                'location', 'isAllDay', 'showAs', 'type', 
                'recurrence', 'seriesMasterId', 'body', 'createdDateTime',
                'lastModifiedDateTime'
            ]
        
        all_events = []
        page_counter = 0
        max_pages = 50  # Safety guard to avoid infinite pagination loops
        
        # Handle pagination
        while url and page_counter < max_pages:
            params = {
                '$top': 100,  # Reduced from 500 for better performance
                '$select': ','.join(select_fields)
            }
            
            try:
                response = requests.get(url, headers=headers, params=params if not all_events else None, timeout=30)
                
                if response.status_code == 401:
                    # Try refreshing token
                    if self.auth.refresh_access_token():
                        headers = self.auth.get_headers()
                        response = requests.get(url, headers=headers, params=params if not all_events else None, timeout=30)
                    else:
                        logger.error("Authentication failed during event retrieval")
                        return None
                
                if response.status_code == 200:
                    data = response.json()
                    events = data.get('value', [])
                    all_events.extend(events)
                    page_counter += 1
                    
                    # Check for next page
                    url = data.get('@odata.nextLink')
                    if url:
                        logger.info(f"Fetching next page of events (current total: {len(all_events)})")
                else:
                    logger.error(f"Failed to get events: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return None
            
            except requests.exceptions.Timeout:
                logger.error("Request timeout while getting events")
                raise
            except requests.exceptions.ConnectionError:
                logger.error("Connection error while getting events")
                raise
            except Exception as e:
                logger.error(f"Error getting events: {e}")
                raise
        
        logger.info(f"Retrieved total of {len(all_events)} events from calendar {calendar_id}")
        return all_events
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def get_calendar_instances(self, calendar_id: str, start_date: str, end_date: str) -> Optional[List[Dict]]:
        """
        Get calendar event instances including exceptions
        Uses calendarView endpoint to get expanded occurrences
        
        Args:
            calendar_id: ID of the calendar
            start_date: ISO 8601 formatted start date
            end_date: ISO 8601 formatted end date
            
        Returns:
            List of event instances including exceptions
        """
        headers = self.auth.get_headers()
        if not headers:
            return None
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/calendarView"
        
        params = {
            'startDateTime': start_date,
            'endDateTime': end_date,
            '$select': 'id,subject,start,end,categories,location,isAllDay,showAs,type,seriesMasterId,isCancelled,originalStart,body,lastModifiedDateTime',
            '$top': 250
        }
        
        all_instances = []
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if self.auth.refresh_access_token():
                    headers = self.auth.get_headers()
                    response = requests.get(url, headers=headers, params=params, timeout=30)
                else:
                    logger.error("Authentication failed during instance retrieval")
                    return None
            
            if response.status_code == 200:
                data = response.json()
                instances = data.get('value', [])
                all_instances.extend(instances)
                
                # Handle pagination if needed
                next_link = data.get('@odata.nextLink')
                page_counter = 0
                max_pages = 50  # Safety guard for pagination
                while next_link and page_counter < max_pages:
                    next_response = requests.get(next_link, headers=headers, timeout=30)
                    if next_response.status_code == 200:
                        next_data = next_response.json()
                        all_instances.extend(next_data.get('value', []))
                        next_link = next_data.get('@odata.nextLink')
                        page_counter += 1
                    else:
                        logger.warning(f"Failed to get next page of instances: {next_response.status_code}")
                        break

                
                logger.info(f"Retrieved {len(all_instances)} instances from calendar {calendar_id}")
                
                # Log cancelled instances for debugging
                cancelled_count = sum(1 for i in all_instances if i.get('isCancelled', False))
                if cancelled_count > 0:
                    logger.info(f"Found {cancelled_count} cancelled instances")
                
                return all_instances
            else:
                logger.error(f"Failed to get calendar instances: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("Request timeout while getting instances")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("Connection error while getting instances")
            raise
        except Exception as e:
            logger.error(f"Error getting instances: {e}")
            raise
    
    def get_public_events(self, calendar_id: str, include_instances: bool = False) -> Optional[List[Dict]]:
        """Get only public events from a calendar"""
        # ALWAYS use regular events to avoid duplicates, ignore include_instances parameter
        all_events = self.get_calendar_events(calendar_id)
            
        if not all_events:
            return None
        
        public_events = []
        stats = {
            'total_events': len(all_events),
            'non_public': 0,
            'not_busy': 0,
            'tentative': 0,
            'recurring_instances': 0,
            'past_events': 0,
            'future_events': 0,
            'cancelled': 0
        }
        
        # Create timezone-aware cutoff dates
        central_tz = pytz.timezone('America/Chicago')
        now_central = DateTimeUtils.get_central_time()
        
        # Convert to UTC for comparison
        cutoff_date = (now_central - timedelta(days=config.SYNC_CUTOFF_DAYS)).astimezone(pytz.UTC)
        future_cutoff = (now_central + timedelta(days=365)).astimezone(pytz.UTC)
        
        for event in all_events:
            # Skip cancelled events entirely
            if event.get('isCancelled', False):
                stats['cancelled'] += 1
                continue
            
            # Check if public
            categories = event.get('categories', [])
            if 'Public' not in categories:
                stats['non_public'] += 1
                logger.debug(f"Skipping non-public event: {event.get('subject')} (categories: {categories})")
                continue

            # CRITICAL: Also check if event is marked as Busy
            show_as = event.get('showAs', 'busy')
            if show_as != 'busy':
                stats['not_busy'] += 1
                logger.info(f"📝 Skipping non-busy public event: {event.get('subject')} (showAs: {show_as})")
                continue
            
            # ALWAYS skip recurring instances to avoid duplicates
            event_type = event.get('type', 'singleInstance')
            if event_type == 'occurrence':
                stats['recurring_instances'] += 1
                continue
            
            # Check event date
            try:
                # Parse event date
                event_date = DateTimeUtils.parse_graph_datetime(event.get('start', {}))
                if not event_date:
                    continue

                # Ensure both datetimes are timezone-aware for comparison
                if event_date.tzinfo is None:
                    # If naive, assume it's UTC
                    event_date = pytz.UTC.localize(event_date)

                # Convert to UTC for comparison
                event_date_utc = event_date.astimezone(pytz.UTC)

                # Skip old events (unless it's a recurring event that should always be synced)
                if event_date_utc < cutoff_date:
                    # Special override for recurring events - always include them regardless of age
                    if event.get('type') == 'seriesMaster':
                        logger.info(f"🔄 Including old recurring event: {event.get('subject')} (started: {event_date_utc})")
                    else:
                        stats['past_events'] += 1
                        # Debug logging for specific events
                        if 'Ladies Auxiliary' in event.get('subject', ''):
                            logger.info(f"🔍 Ladies Auxiliary event filtered - too old: {event.get('subject')} (date: {event_date_utc}, cutoff: {cutoff_date})")
                        continue

                # Skip events too far in the future (but allow recurring events to extend further)
                if event_date_utc > future_cutoff:
                    # Special override for recurring events - allow them to extend further into the future
                    if event.get('type') == 'seriesMaster':
                        logger.info(f"🔄 Including recurring event despite future date: {event.get('subject')} (extends to: {event_date_utc})")
                    else:
                        stats['future_events'] += 1
                        # Debug logging for specific events
                        if 'Ladies Auxiliary' in event.get('subject', ''):
                            logger.info(f"🔍 Ladies Auxiliary event filtered - too far in future: {event.get('subject')} (date: {event_date_utc}, cutoff: {future_cutoff})")
                        continue
            except Exception as e:
                logger.warning(f"Could not parse date for event {event.get('subject')}: {e}")
                # If we can't parse the date, include the event to be safe
                pass
            
            public_events.append(event)
        
        logger.info(f"Found {len(public_events)} public events to sync")
        logger.info(f"Event statistics: {stats}")
        
        return public_events
    
    def clear_calendar_cache(self):
        """Clear the calendar ID cache"""
        self._calendar_cache.clear()
        self._cache_expiry.clear()
        logger.info("Calendar cache cleared")


class CalendarWriter:
    """Handles writing calendar data to Microsoft Graph"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def create_event(self, calendar_id: str, event_data: Dict) -> bool:
        """Create a new event in the specified calendar with retry logic"""
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to write to source calendar!")
            return False
        
        headers = self.auth.get_headers()
        if not headers:
            return False
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events"
        
        # Prepare event data
        create_data = self._prepare_event_data(event_data)
        
        try:
            response = requests.post(url, headers=headers, json=create_data, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if self.auth.refresh_access_token():
                    headers = self.auth.get_headers()
                    response = requests.post(url, headers=headers, json=create_data, timeout=30)
                else:
                    logger.error("Authentication failed during event creation")
                    return False
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Created event: {event_data.get('subject')} (All-day: {create_data.get('isAllDay', False)})")
                return True
            else:
                logger.error(f"❌ Failed to create event: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
        
        except requests.exceptions.Timeout:
            logger.error("Request timeout while creating event")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("Connection error while creating event")
            raise
        except Exception as e:
            logger.error(f"❌ Error creating event: {e}")
            raise
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def update_event(self, calendar_id: str, event_id: str, event_data: Dict) -> bool:
        """Update an existing event with retry logic"""
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to update in source calendar!")
            return False
        
        headers = self.auth.get_headers()
        if not headers:
            return False
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}"
        
        # Prepare update data
        update_data = self._prepare_event_data(event_data)
        
        try:
            response = requests.patch(url, headers=headers, json=update_data, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if self.auth.refresh_access_token():
                    headers = self.auth.get_headers()
                    response = requests.patch(url, headers=headers, json=update_data, timeout=30)
                else:
                    logger.error("Authentication failed during event update")
                    return False
            
            if response.status_code == 200:
                logger.info(f"✅ Updated event: {event_data.get('subject')} (All-day: {update_data.get('isAllDay', False)})")
                return True
            else:
                logger.error(f"❌ Failed to update event: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
        
        except requests.exceptions.Timeout:
            logger.error("Request timeout while updating event")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("Connection error while updating event")
            raise
        except Exception as e:
            logger.error(f"❌ Error updating event: {e}")
            raise
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def delete_event(self, calendar_id: str, event_id: str, suppress_notifications: bool = True) -> bool:
        """Delete an event from the calendar with retry logic"""
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to delete from source calendar!")
            return False
        
        headers = self.auth.get_headers()
        if not headers:
            return False
        
        # Add header to suppress notifications if requested
        if suppress_notifications:
            headers['Prefer'] = 'outlook.timezone="UTC", outlook.send-notifications="false"'
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}"
        
        try:
            response = requests.delete(url, headers=headers, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if self.auth.refresh_access_token():
                    headers = self.auth.get_headers()
                    if suppress_notifications:
                        headers['Prefer'] = 'outlook.timezone="UTC", outlook.send-notifications="false"'
                    response = requests.delete(url, headers=headers, timeout=30)
                else:
                    logger.error("Authentication failed during event deletion")
                    return False
            
            if response.status_code in [200, 204]:
                logger.info(f"✅ Deleted event ID: {event_id[:8]}...")
                return True
            elif response.status_code == 404:
                logger.warning(f"Event not found for deletion: {event_id[:8]}...")
                return True  # Consider already deleted as success
            else:
                logger.error(f"❌ Failed to delete event: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
        
        except requests.exceptions.Timeout:
            logger.error("Request timeout while deleting event")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("Connection error while deleting event")
            raise
        except Exception as e:
            logger.error(f"❌ Error deleting event: {e}")
            raise
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def delete_occurrence(self, calendar_id: str, event_id: str, occurrence_date: str) -> bool:
        """
        Delete a specific occurrence of a recurring event
        
        Args:
            calendar_id: ID of the calendar
            event_id: ID of the recurring event series
            occurrence_date: ISO 8601 date of the occurrence to delete
            
        Returns:
            True if deletion successful, False otherwise
        """
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to delete occurrence from source calendar!")
            return False
        
        headers = self.auth.get_headers()
        if not headers:
            logger.error("❌ No auth headers available for occurrence deletion")
            return False
        
        try:
            # Parse the occurrence date to create a time window
            # Parse the occurrence date (should be in UTC)
            occurrence_dt = datetime.fromisoformat(occurrence_date.replace('Z', '+00:00'))
            
            # Create a 1-hour window around the occurrence
            start_window = (occurrence_dt - timedelta(hours=1)).isoformat()
            end_window = (occurrence_dt + timedelta(hours=1)).isoformat()
            
            # First, get the specific occurrence using the time window
            url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}/instances"
            params = {
                'startDateTime': start_window,
                'endDateTime': end_window
            }
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                logger.error(f"❌ Failed to get occurrence: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
            
            instances = response.json().get('value', [])
            
            # Find the occurrence that matches our target date
            target_occurrence = None
            for instance in instances:
                instance_start = instance.get('start', {}).get('dateTime', '')
                if instance_start.startswith(occurrence_date.split('T')[0]):  # Match date part
                    target_occurrence = instance
                    break
            
            if not target_occurrence:
                logger.error(f"❌ Occurrence not found for date {occurrence_date}")
                return False
            
            # Delete the occurrence using its ID
            occurrence_id = target_occurrence['id']
            delete_url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{occurrence_id}"
            
            delete_response = requests.delete(delete_url, headers=headers)
            
            if delete_response.status_code == 204:
                logger.info(f"✅ Successfully deleted occurrence: {target_occurrence.get('subject', 'Unknown')} on {occurrence_date}")
                return True
            else:
                logger.error(f"❌ Failed to delete occurrence: {delete_response.status_code}")
                logger.error(f"Response: {delete_response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Exception during occurrence deletion: {str(e)}")
            return False
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1)
    def update_occurrence(self, calendar_id: str, event_id: str, occurrence_date: str, update_data: Dict) -> bool:
        """
        Update a specific occurrence of a recurring event
        
        Args:
            calendar_id: ID of the calendar
            event_id: ID of the recurring event series
            occurrence_date: ISO 8601 date of the occurrence to update
            update_data: Event data to update with
            
        Returns:
            True if update successful, False otherwise
        """
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to update occurrence in source calendar!")
            return False
        
        headers = self.auth.get_headers()
        if not headers:
            return False
        
        try:
            # First, get the specific occurrence using the time window approach
            # Parse the occurrence date to create a time window
            # Parse the occurrence date (should be in UTC)
            occurrence_dt = datetime.fromisoformat(occurrence_date.replace('Z', '+00:00'))
            
            # Create a 1-hour window around the occurrence
            start_window = (occurrence_dt - timedelta(hours=1)).isoformat()
            end_window = (occurrence_dt + timedelta(hours=1)).isoformat()
            
            # Get the specific occurrence using the time window
            instances_url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}/instances"
            params = {
                'startDateTime': start_window,
                'endDateTime': end_window
            }
            
            response = requests.get(instances_url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"❌ Failed to get occurrence: {response.status_code}")
                return False
            
            instances = response.json().get('value', [])
            
            # Find the occurrence that matches our target date
            target_occurrence = None
            for instance in instances:
                instance_start = instance.get('start', {}).get('dateTime', '')
                if instance_start.startswith(occurrence_date.split('T')[0]):  # Match date part
                    target_occurrence = instance
                    break
            
            if not target_occurrence:
                logger.error(f"❌ Occurrence not found for date {occurrence_date}")
                return False
            
            # Now update the occurrence using its ID
            occurrence_id = target_occurrence['id']
            update_url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{occurrence_id}"
            
            # Prepare update data with all-day event handling
            prepared_data = self._prepare_event_data(update_data)
            
            response = requests.patch(update_url, headers=headers, json=prepared_data, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if self.auth.refresh_access_token():
                    headers = self.auth.get_headers()
                    response = requests.patch(update_url, headers=headers, json=prepared_data, timeout=30)
                else:
                    logger.error("Authentication failed during occurrence update")
                    return False
            
            if response.status_code == 200:
                logger.info(f"✅ Updated occurrence on {occurrence_date} for event: {update_data.get('subject')}")
                return True
            else:
                logger.error(f"❌ Failed to update occurrence: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
        
        except requests.exceptions.Timeout:
            logger.error("Request timeout while updating occurrence")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("Connection error while updating occurrence")
            raise
        except Exception as e:
            logger.error(f"❌ Error updating occurrence: {e}")
            raise
    
    def _prepare_event_data(self, source_event: Dict) -> Dict:
        """Prepare event data for creation/update"""
        # Build base event data
        event_data = {
            'subject': source_event.get('subject'),
            'categories': source_event.get('categories', []),
            'body': {'contentType': 'html', 'content': ''},  # Clear body for privacy
            'location': source_event.get('location', {}),  # KEEP location data
            'showAs': 'busy',  # Always busy for public calendar
            'isReminderOn': False,  # No reminders on public calendar
            'sensitivity': 'normal'  # Ensure not marked as private
        }
        
        # Calculate event duration for diagnostic purposes
        subject = source_event.get('subject', 'Unknown')
        start_data = source_event.get('start', {})
        end_data = source_event.get('end', {})
        source_is_all_day = source_event.get('isAllDay', False)
        
        # DIAGNOSTIC: Log long events for review
        start_str = start_data.get('dateTime', '')
        end_str = end_data.get('dateTime', '')
        
        if start_str and end_str and not source_is_all_day:
            try:
                start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                duration_hours = (end_dt - start_dt).total_seconds() / 3600
                
                # Log events longer than 8 hours for review
                if duration_hours > 8:
                    # Convert to Central Time for display
                    central_start = start_dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-5)))  # CST
                    central_end = end_dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-5)))  # CST
                    logger.info(f"📏 Long event detected: '{subject}'")
                    logger.info(f"   Duration: {duration_hours:.1f} hours")
                    logger.info(f"   Central Time: {central_start.strftime('%I:%M %p')} to {central_end.strftime('%I:%M %p')}")
                    logger.info(f"   This may be intentional (e.g., all-day adoration) or a data entry error")
            except Exception as e:
                logger.debug(f"Could not calculate duration for {subject}: {e}")
        
        # Simple logic: Use source's isAllDay flag directly
        event_data['isAllDay'] = source_is_all_day
        
        if source_is_all_day:
            # All-day event handling
            start_dt = source_event.get('start', {})
            end_dt = source_event.get('end', {})
            
            # Extract just the date part
            if 'dateTime' in start_dt:
                start_date = start_dt['dateTime'].split('T')[0]
            elif 'date' in start_dt:
                start_date = start_dt['date']
            else:
                logger.error(f"No valid start date for all-day event: {subject}")
                start_date = None
                
            if 'dateTime' in end_dt:
                end_date = end_dt['dateTime'].split('T')[0]
            elif 'date' in end_dt:
                end_date = end_dt['date']
            else:
                logger.error(f"No valid end date for all-day event: {subject}")
                end_date = None
            
            if start_date and end_date:
                event_data['start'] = {
                    'date': start_date,
                    'timeZone': 'Central Standard Time'
                }
                event_data['end'] = {
                    'date': end_date,
                    'timeZone': 'Central Standard Time'
                }
            else:
                event_data['start'] = source_event.get('start', {})
                event_data['end'] = source_event.get('end', {})
        else:
            # Timed event handling - preserve original data
            event_data['start'] = start_data
            event_data['end'] = end_data
        
        # Add recurrence for series masters
        if source_event.get('type') == 'seriesMaster' and source_event.get('recurrence'):
            event_data['recurrence'] = source_event.get('recurrence')
        
        return event_data
    
    def _get_next_day(self, date_string):
        """Get the next day for all-day event end date"""
        from datetime import datetime, timedelta
        
        date_obj = datetime.strptime(date_string, '%Y-%m-%d')
        next_day = date_obj + timedelta(days=1)
        return next_day.strftime('%Y-%m-%d')
    
    def batch_create_events(self, calendar_id: str, events: List[Dict], batch_size: int = 20) -> Dict:
        """Create multiple events in batches for better performance"""
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to batch write to source calendar!")
            return {'successful': 0, 'failed': len(events), 'errors': ['Protected calendar']}
        
        results = {
            'successful': 0,
            'failed': 0,
            'errors': []
        }
        
        headers = self.auth.get_headers()
        if not headers:
            results['failed'] = len(events)
            results['errors'].append('No authentication')
            return results
        
        # Microsoft Graph batch endpoint
        batch_url = "https://graph.microsoft.com/v1.0/$batch"
        
        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            
            # Create batch request
            batch_requests = []
            for idx, event in enumerate(batch):
                batch_requests.append({
                    'id': str(idx + 1),
                    'method': 'POST',
                    'url': f'/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events',
                    'body': self._prepare_event_data(event),
                    'headers': {
                        'Content-Type': 'application/json'
                    }
                })
            
            batch_payload = {
                'requests': batch_requests
            }
            
            try:
                response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=60)
                
                if response.status_code == 200:
                    batch_results = response.json().get('responses', [])
                    
                    for result in batch_results:
                        if result.get('status') in [200, 201]:
                            results['successful'] += 1
                            # Log all-day status if available in response
                            response_body = result.get('body', {})
                            if response_body and isinstance(response_body, dict):
                                is_all_day = response_body.get('isAllDay', False)
                                subject = response_body.get('subject', 'Unknown')
                                logger.debug(f"✅ Batch created: {subject} (All-day: {is_all_day})")
                        else:
                            results['failed'] += 1
                            error_msg = f"Event {result.get('id')}: Status {result.get('status')}"
                            results['errors'].append(error_msg)
                            logger.error(error_msg)
                else:
                    # Batch request failed
                    results['failed'] += len(batch)
                    error_msg = f"Batch request failed: {response.status_code}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
                    
            except Exception as e:
                results['failed'] += len(batch)
                error_msg = f"Batch exception: {str(e)}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        
        logger.info(f"Batch create completed: {results['successful']} successful, {results['failed']} failed")
        return results
    
    def batch_delete_events(self, calendar_id: str, event_ids: List[str], batch_size: int = 20) -> Dict:
        """Delete multiple events in batches"""
        if config.MASTER_CALENDAR_PROTECTION and calendar_id == config.SOURCE_CALENDAR:
            logger.error("PROTECTION: Attempted to batch delete from source calendar!")
            return {'successful': 0, 'failed': len(event_ids), 'errors': ['Protected calendar']}
        
        results = {
            'successful': 0,
            'failed': 0,
            'errors': []
        }
        
        headers = self.auth.get_headers()
        if not headers:
            results['failed'] = len(event_ids)
            results['errors'].append('No authentication')
            return results
        
        # Add preference header for batch
        headers['Prefer'] = 'outlook.timezone="UTC", outlook.send-notifications="false"'
        
        # Microsoft Graph batch endpoint
        batch_url = "https://graph.microsoft.com/v1.0/$batch"
        
        for i in range(0, len(event_ids), batch_size):
            batch = event_ids[i:i + batch_size]
            
            # Create batch request
            batch_requests = []
            for idx, event_id in enumerate(batch):
                batch_requests.append({
                    'id': str(idx + 1),
                    'method': 'DELETE',
                    'url': f'/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}',
                    'headers': {
                        'Prefer': 'outlook.timezone="UTC", outlook.send-notifications="false"'
                    }
                })
            
            batch_payload = {
                'requests': batch_requests
            }
            
            try:
                response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=60)
                
                if response.status_code == 200:
                    batch_results = response.json().get('responses', [])
                    
                    for result in batch_results:
                        if result.get('status') in [200, 204, 404]:  # 404 is OK (already deleted)
                            results['successful'] += 1
                        else:
                            results['failed'] += 1
                            error_msg = f"Event {result.get('id')}: Status {result.get('status')}"
                            results['errors'].append(error_msg)
                            logger.error(error_msg)
                else:
                    # Batch request failed
                    results['failed'] += len(batch)
                    error_msg = f"Batch request failed: {response.status_code}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
                    
            except Exception as e:
                results['failed'] += len(batch)
                error_msg = f"Batch exception: {str(e)}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        
        logger.info(f"Batch delete completed: {results['successful']} successful, {results['failed']} failed")
        return results 