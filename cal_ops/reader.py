# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Calendar Reader - Handles all read operations from Microsoft Graph with enhancements
"""
import logging
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta

import config
from utils.retry import retry_with_backoff
from utils.timezone import get_central_time

logger = logging.getLogger(__name__)


class CalendarReader:
    """Handles reading calendar data from Microsoft Graph"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
        # Add caching for calendar IDs
        self._calendar_cache = {}
        self._cache_expiry = {}
    
    @retry_with_backoff(max_retries=3, base_delay=1)
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
            if expiry and get_central_time() < expiry:
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
                self._cache_expiry[calendar_name] = get_central_time() + timedelta(hours=1)
                
                return calendar_id
        
        logger.warning(f"Calendar '{calendar_name}' not found")
        return None
    
    @retry_with_backoff(max_retries=3, base_delay=1)
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
        
        # Handle pagination
        while url:
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
    
    @retry_with_backoff(max_retries=3, base_delay=1)
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
                while next_link:
                    next_response = requests.get(next_link, headers=headers, timeout=30)
                    if next_response.status_code == 200:
                        next_data = next_response.json()
                        all_instances.extend(next_data.get('value', []))
                        next_link = next_data.get('@odata.nextLink')
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
        if include_instances and config.SYNC_OCCURRENCE_EXCEPTIONS:
            # Use calendar instances for date range
            import pytz
            start_date = get_central_time()
            end_date = start_date + timedelta(days=config.OCCURRENCE_SYNC_DAYS)
            
            # Convert to UTC for API
            start_str = start_date.astimezone(pytz.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
            end_str = end_date.astimezone(pytz.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            logger.info(f"Getting calendar instances from {start_str} to {end_str}")
            all_events = self.get_calendar_instances(calendar_id, start_str, end_str)
        else:
            all_events = self.get_calendar_events(calendar_id)
            
        if not all_events:
            return None
        
        public_events = []
        stats = {
            'total_events': len(all_events),
            'non_public': 0,
            'tentative': 0,
            'recurring_instances': 0,
            'past_events': 0,
            'future_events': 0,
            'cancelled': 0
        }
        
        from datetime import datetime, timedelta
        cutoff_date = get_central_time() - timedelta(days=config.SYNC_CUTOFF_DAYS)
        future_cutoff = get_central_time() + timedelta(days=365)  # Don't sync events more than a year out
        
        for event in all_events:
            # Check if cancelled (for instances)
            if event.get('isCancelled', False):
                stats['cancelled'] += 1
                # Include cancelled events so we can delete them from target
                if include_instances:
                    public_events.append(event)
                continue
            
            # Check if public
            categories = event.get('categories', [])
            if 'Public' not in categories:
                stats['non_public'] += 1
                continue
            
            # Skip tentative events
            show_as = event.get('showAs', 'busy')
            if show_as != 'busy':
                stats['tentative'] += 1
                logger.debug(f"Skipping tentative event: {event.get('subject')} (showAs: {show_as})")
                continue
            
            # For non-instance mode, skip recurring instances
            if not include_instances:
                event_type = event.get('type', 'singleInstance')
                if event_type == 'occurrence':
                    stats['recurring_instances'] += 1
                    continue
            
            # Check event date
            try:
                # Handle both object and string formats for start field
                start_field = event.get('start', {})
                if isinstance(start_field, dict):
                    event_start = start_field.get('dateTime', '')
                elif isinstance(start_field, str):
                    event_start = start_field
                else:
                    event_start = ''
                    
                if event_start:
                    event_date = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                    
                    # Skip old events
                    if event_date < cutoff_date:
                        stats['past_events'] += 1
                        continue
                    
                    # Skip events too far in the future
                    if event_date > future_cutoff:
                        stats['future_events'] += 1
                        continue
            except Exception as e:
                logger.warning(f"Could not parse date for event {event.get('subject')}: {e}")
            
            public_events.append(event)
        
        logger.info(f"Found {len(public_events)} public events to sync")
        logger.info(f"Event statistics: {stats}")
        
        return public_events
    
    def clear_calendar_cache(self):
        """Clear the calendar ID cache"""
        self._calendar_cache.clear()
        self._cache_expiry.clear()
        logger.info("Calendar cache cleared")
