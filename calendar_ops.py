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

def get_utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


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
    def get_calendar_events(self, calendar_id: str, select_fields: List[str] = None, start: datetime = None, end: datetime = None) -> Optional[List[Dict]]:
        """Get all calendar events with proper pagination"""
        try:
            headers = self.auth.get_headers()
            if not headers:
                return None
            
            # Use provided date range or calculate default range
            if start and end:
                # Use provided date range
                start_date = start
                end_date = end
            else:
                # Calculate date range - MUST stay within 730 day limit
                central_tz = pytz.timezone('America/Chicago')
                now_central = DateTimeUtils.get_central_time()
                
                # Microsoft Graph limit is 1825 days total, but we'll use 730 days (2 years)
                # Let's do 1 year back, 1 year forward = 365 + 365 = 730 days
                start_date = now_central - timedelta(days=365)  # 1 year back
                end_date = now_central + timedelta(days=365)    # 1 year forward
            
            # Convert to UTC for API call
            start_utc = start_date.astimezone(pytz.UTC)
            end_utc = end_date.astimezone(pytz.UTC)
            
            start_time = start_utc.isoformat()
            end_time = end_utc.isoformat()
            
            # Calculate actual day span for logging
            day_span = (end_utc - start_utc).days
            logger.info(f"📅 Date range: {day_span} days from {start_time[:10]} to {end_time[:10]}")
            
            if day_span > 730:
                logger.error(f"⚠️ Date range {day_span} days exceeds our limit of 730 days!")
                # Adjust to stay within limit
                end_date = start_date + timedelta(days=730)
                end_utc = end_date.astimezone(pytz.UTC)
                end_time = end_utc.isoformat()
                logger.info(f"📅 Adjusted end date to stay within limit: {end_time[:10]}")
            
            # Use calendarView to expand recurring events
            endpoint = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/calendarView"
            
            params = {
                "startDateTime": start_time,
                "endDateTime": end_time,
                "$select": "id,subject,body,start,end,categories,showAs,type,seriesMasterId,isCancelled,recurrence,sensitivity,isAllDay,responseStatus,organizer",
                "$top": 100  # Fetch in chunks of 100
            }
            
            all_events = []
            request_count = 0
            
            logger.info(f"[Sync] Querying from {start_time} to {end_time}")
            logger.info(f"📅 Fetching calendar events (within 730-day limit)")
            
            while endpoint:
                request_count += 1
                logger.info(f"  Fetching page {request_count}...")
                
                # Make request
                if request_count == 1:
                    response = requests.get(endpoint, headers=headers, params=params, timeout=30)
                else:
                    # For subsequent pages, use the @odata.nextLink URL directly
                    response = requests.get(endpoint, headers=headers, timeout=30)
                
                if response.status_code == 401:
                    # Try refreshing token
                    if self.auth.refresh_access_token():
                        headers = self.auth.get_headers()
                        response = requests.get(endpoint, headers=headers, params=params if request_count == 1 else None, timeout=30)
                    else:
                        logger.error("Authentication failed during event retrieval")
                        return None
                
                if not response.ok:
                    logger.error(f"Failed to fetch events: {response.status_code} - {response.text}")
                    break
                    
                data = response.json()
                events = data.get('value', [])
                all_events.extend(events)
                
                logger.info(f"  Retrieved {len(events)} events (total so far: {len(all_events)})")
                
                # Check for next page
                endpoint = data.get('@odata.nextLink')
                
                # Safety limit to prevent infinite loops
                if request_count > 50:
                    logger.warning("Hit pagination safety limit of 50 pages")
                    break
            
            logger.info(f"✅ Total events fetched: {len(all_events)}")
            
            # Log sample of events for debugging
            if all_events:
                sample_dates = {}
                for event in all_events:
                    date = event.get('start', {}).get('dateTime', '')[:10]
                    if date:
                        sample_dates[date] = sample_dates.get(date, 0) + 1
                
                # Show October specifically
                current_year = datetime.fromisoformat(get_utc_now_iso()).year
                october_count = sum(count for date, count in sample_dates.items() if date.startswith(f'{current_year}-10'))
                logger.info(f"📊 October {current_year} events fetched: {october_count}")
            
            return all_events
            
        except Exception as e:
            logger.error(f"Error fetching calendar events: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
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
        
        logger.info(f"[Sync] Querying instances from {start_date} to {end_date}")
        
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
    
    def get_public_events(self, calendar_id: str, include_instances: bool = False, start: datetime = None, end: datetime = None) -> Optional[List[Dict]]:
        """Get only public events from a calendar"""
        # Get all events including expanded recurring occurrences
        all_events = self.get_calendar_events(calendar_id, start=start, end=end)
        
        if not all_events:
            return None
        
        # IMPORTANT: Filter out duplicate seriesMasters when we have occurrences
        # Group events by seriesMasterId to identify duplicates
        series_masters = {}
        occurrences = []
        single_events = []
        
        for event in all_events:
            event_type = event.get('type', 'singleInstance')
            series_master_id = event.get('seriesMasterId')
            
            if event_type == 'seriesMaster':
                # Track series masters
                series_masters[event['id']] = event
            elif event_type == 'occurrence':
                # Collect occurrences
                occurrences.append(event)
            else:
                # Single instance events
                single_events.append(event)
        
        # If we have occurrences for a series, don't include the seriesMaster
        series_with_occurrences = set()
        for occ in occurrences:
            if occ.get('seriesMasterId'):
                series_with_occurrences.add(occ['seriesMasterId'])
        
        # Build final event list
        filtered_events = []
        
        # Add single events
        filtered_events.extend(single_events)
        
        # CRITICAL FIX: Handle orphaned occurrences
        # Group occurrences by their seriesMasterId
        occurrences_by_master = {}
        for occ in occurrences:
            master_id = occ.get('seriesMasterId')
            if master_id:
                if master_id not in occurrences_by_master:
                    occurrences_by_master[master_id] = []
                occurrences_by_master[master_id].append(occ)
        
        # Add series masters that exist
        for master_id, master_event in series_masters.items():
            filtered_events.append(master_event)
        
        # Add orphaned occurrences (those without a series master in our window)
        for occ in occurrences:
            master_id = occ.get('seriesMasterId')
            # If we don't have the series master, this is an orphaned occurrence
            if master_id and master_id not in series_masters:
                # Check if it meets sync criteria before adding
                categories = occ.get('categories', [])
                show_as = occ.get('showAs', 'busy')
                if 'Public' in categories and show_as == 'busy':
                    filtered_events.append(occ)
                    logger.info(f"✅ Including orphaned occurrence: {occ.get('subject')} on {occ.get('start', {}).get('dateTime', '')[:10]}")
        
        # Diagnostic logging removed - system is working correctly
        
        # Now apply the Public/Busy filtering
        all_events = filtered_events  # Use filtered list for rest of processing
        
        # Diagnostic logging
        logger.info(f"📊 DIAGNOSTIC: Retrieved {len(all_events)} total events from calendar")
        
        # Debug logging for recurring events
        logger.info(f"📊 Recurring Event Analysis:")
        logger.info(f"  Total events fetched: {len(all_events)}")
        logger.info(f"  Series masters: {len(series_masters)}")
        logger.info(f"  Occurrences: {len(occurrences)}")
        logger.info(f"  Series with occurrences in window: {len(series_with_occurrences)}")
        
        # Log orphaned occurrences
        orphaned_count = 0
        for occ in occurrences:
            if occ.get('seriesMasterId') not in series_masters:
                orphaned_count += 1
        logger.info(f"  Orphaned occurrences: {orphaned_count}")
        
        # Log event type breakdown
        event_types = {}
        for event in all_events:
            event_type = event.get('type', 'unknown')
            event_types[event_type] = event_types.get(event_type, 0) + 1
        logger.info(f"📊 Event types breakdown: {event_types}")

        # Log first 5 events for inspection
        for i, event in enumerate(all_events[:5]):
            logger.info(f"Sample event {i+1}:")
            logger.info(f"  Subject: {event.get('subject')}")
            logger.info(f"  Type: {event.get('type')}")
            logger.info(f"  Categories: {event.get('categories', [])}")
            logger.info(f"  ShowAs: {event.get('showAs')}")
            logger.info(f"  Start: {event.get('start')}")
        
        public_events = []
        stats = {
            'total_events': len(all_events),
            'non_public': 0,
            'not_busy': 0,
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
                logger.info(f"❌ REJECTED (cancelled): {event.get('subject')}")
                continue
            
            # Check if public
            categories = event.get('categories', [])
            if 'Public' not in categories:
                stats['non_public'] += 1
                logger.info(f"❌ REJECTED (not public): {event.get('subject')} - Categories: {categories}")
                continue

            # CRITICAL: Also check if event is marked as Busy
            show_as = event.get('showAs', 'busy')
            if show_as != 'busy':
                stats['not_busy'] += 1
                logger.info(f"❌ REJECTED (not busy): {event.get('subject')} - ShowAs: {show_as}")
                continue
            
            # Check event date
            try:
                event_type = event.get('type', 'singleInstance')
                
                # Handle different event types appropriately
                if event_type == 'seriesMaster':
                    # Always include recurring series masters
                    logger.debug(f"Including recurring series: {event.get('subject')}")
                elif event_type == 'occurrence':
                    # Check if this occurrence has a series master in our data
                    series_master_id = event.get('seriesMasterId')
                    if series_master_id and series_master_id in series_masters:
                        # Skip this occurrence - we'll handle it via the series master
                        logger.debug(f"Skipping occurrence (has series master): {event.get('subject')}")
                        continue
                    else:
                        # This is an orphaned occurrence - include it
                        logger.info(f"✅ Including orphaned occurrence: {event.get('subject')} on {event.get('start', {}).get('dateTime', '')[:10]}")
                        stats['orphaned_occurrences'] = stats.get('orphaned_occurrences', 0) + 1
                else:
                    # For single events, check the date
                    event_date = DateTimeUtils.parse_graph_datetime(event.get('start', {}))
                    if not event_date:
                        continue

                    # Ensure both datetimes are timezone-aware for comparison
                    if event_date.tzinfo is None:
                        # If naive, assume it's UTC
                        event_date = pytz.UTC.localize(event_date)

                    # Convert to UTC for comparison
                    event_date_utc = event_date.astimezone(pytz.UTC)

                    # Skip old single events
                    if event_date_utc < cutoff_date:
                        stats['past_events'] += 1
                        continue

                    # Skip events too far in the future (for single events only)
                    if event_date_utc > future_cutoff:
                        stats['future_events'] += 1
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
    
    def _prepare_event_for_api(self, event):
        """Prepare event data specifically for Graph API requirements"""
        # Get clean timestamps without microseconds
        if 'start' in event and 'dateTime' in event['start']:
            start_dt = event['start']['dateTime'].replace('Z', '').split('.')[0]
        else:
            start_dt = event.get('start', {}).get('dateTime', '')
            
        if 'end' in event and 'dateTime' in event['end']:
            end_dt = event['end']['dateTime'].replace('Z', '').split('.')[0]
        else:
            end_dt = event.get('end', {}).get('dateTime', '')
        
        # Build the properly formatted event
        api_event = {
            'subject': event.get('subject', ''),
            'start': {
                'dateTime': start_dt,
                'timeZone': 'UTC'  # Since times are already in UTC with Z suffix
            },
            'end': {
                'dateTime': end_dt,
                'timeZone': 'UTC'
            },
            'showAs': event.get('showAs', 'busy'),
            'categories': event.get('categories', [])
        }
        
        # Add body if present
        if 'body' in event:
            api_event['body'] = {
                'contentType': 'HTML',
                'content': event['body'].get('content', '')
            }
        
        # Add location if present
        if 'location' in event and event['location']:
            api_event['location'] = {'displayName': event['location'].get('displayName', '')}
        
        # Add isAllDay if true
        if event.get('isAllDay'):
            api_event['isAllDay'] = True
        
        # Create unique identifier from source event
        source_id = event.get('id', '')
        sync_marker = f"<!-- SYNC_ID:{source_id} -->"
        
        # Add to body content
        if 'body' not in api_event:
            api_event['body'] = {'contentType': 'HTML', 'content': ''}
            
        api_event['body']['content'] = sync_marker + api_event['body'].get('content', '')
            
        return api_event
    
    def clear_synced_events_only(self, calendar_id):
        """Delete only events that were created by sync (have SYNC_MARKER in body)"""
        events = self.get_calendar_events(calendar_id)
        deleted = 0
        
        for event in events:
            # Only delete if it has our sync marker
            body_content = event.get('body', {}).get('content', '')
            if 'SYNC_ID:' in body_content or 'Auto-synced from' in body_content:
                try:
                    self.delete_event(calendar_id, event['id'])
                    deleted += 1
                except:
                    pass
        
        return deleted
    
    def _prepare_event_data(self, source_event: Dict) -> Dict:
        """Prepare event data for creation/update - DEPRECATED, use _prepare_event_for_api"""
        return self._prepare_event_for_api(source_event)
    
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
                    'body': self._prepare_event_for_api(event),
                    'headers': {
                        'Content-Type': 'application/json'
                    }
                })
            
            batch_payload = {
                'requests': batch_requests
            }
            
            # DEBUG: Log the first event being sent
            if batch_requests:
                import json
                # logger.info(f"🔍 BATCH REQUEST DEBUG - First event:")
                # logger.info(f"   Subject: {batch_requests[0]['body'].get('subject', 'No Subject')}")
                # logger.info(f"   Start: {batch_requests[0]['body'].get('start', {})}")
                # logger.info(f"   End: {batch_requests[0]['body'].get('end', {})}")
                # logger.info(f"   Body Content: {batch_requests[0]['body'].get('body', {}).get('content', '')[:100]}...")
                # logger.info(f"   Categories: {batch_requests[0]['body'].get('categories', [])}")
                pass
            
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
                            # Get the original event data to log what failed
                            event_idx = int(result.get('id')) - 1
                            failed_event = batch[event_idx] if event_idx < len(batch) else None
                            
                            # Build detailed error message
                            error_msg = f"Event {result.get('id')}: Status {result.get('status')}"
                            if failed_event:
                                error_msg += f" - Subject: '{failed_event.get('subject', 'Unknown')}'"
                                # Log additional event details for debugging
                                start_time = failed_event.get('start', {})
                                if isinstance(start_time, dict):
                                    error_msg += f" - Start: {start_time.get('dateTime', start_time.get('date', 'Unknown'))}"
                                error_msg += f" - All-day: {failed_event.get('isAllDay', False)}"
                            
                            # Extract detailed error information from response
                            if result.get('body'):
                                error_body = result.get('body', {})
                                if isinstance(error_body, dict):
                                    error_detail = error_body.get('error', {})
                                    if isinstance(error_detail, dict):
                                        error_msg += f" - API Error: {error_detail.get('message', 'Unknown error')}"
                                        error_code = error_detail.get('code', '')
                                        if error_code:
                                            error_msg += f" (Code: {error_code})"
                                        # Log validation errors if present
                                        if 'innerError' in error_detail:
                                            inner_error = error_detail['innerError']
                                            if isinstance(inner_error, dict):
                                                error_msg += f" - Details: {inner_error.get('message', '')}"
                            
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