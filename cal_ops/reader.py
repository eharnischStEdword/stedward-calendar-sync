"""
Calendar Reader - Handles all read operations from Microsoft Graph
"""
import logging
import requests
from typing import List, Dict, Optional

import config

logger = logging.getLogger(__name__)


class CalendarReader:
    """Handles reading calendar data from Microsoft Graph"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
    
    def get_calendars(self) -> Optional[List[Dict]]:
        """Get all calendars for the shared mailbox"""
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
                return response.json().get('value', [])
            else:
                logger.error(f"Failed to get calendars: {response.status_code}")
                return None
        
        except Exception as e:
            logger.error(f"Error getting calendars: {e}")
            return None
    
    def find_calendar_id(self, calendar_name: str) -> Optional[str]:
        """Find a calendar ID by name"""
        calendars = self.get_calendars()
        if not calendars:
            return None
        
        for calendar in calendars:
            if calendar.get('name') == calendar_name:
                calendar_id = calendar.get('id')
                logger.info(f"Found calendar '{calendar_name}' with ID: {calendar_id}")
                return calendar_id
        
        logger.warning(f"Calendar '{calendar_name}' not found")
        return None
    
    def get_calendar_events(self, calendar_id: str, select_fields: List[str] = None) -> Optional[List[Dict]]:
        """Get all events from a specific calendar"""
        headers = self.auth.get_headers()
        if not headers:
            return None
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events"
        
        # Default fields to retrieve
        if not select_fields:
            select_fields = [
                'id', 'subject', 'start', 'end', 'categories', 
                'location', 'isAllDay', 'showAs', 'type', 
                'recurrence', 'seriesMasterId', 'body', 'createdDateTime'
            ]
        
        params = {
            '$top': 500,
            '$select': ','.join(select_fields)
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                events = response.json().get('value', [])
                logger.info(f"Retrieved {len(events)} events from calendar {calendar_id}")
                return events
            else:
                logger.error(f"Failed to get events: {response.status_code}")
                return None
        
        except Exception as e:
            logger.error(f"Error getting events: {e}")
            return None
    
    def get_public_events(self, calendar_id: str) -> Optional[List[Dict]]:
        """Get only public events from a calendar"""
        all_events = self.get_calendar_events(calendar_id)
        if not all_events:
            return None
        
        public_events = []
        stats = {
            'non_public': 0,
            'tentative': 0,
            'recurring_instances': 0,
            'past_events': 0
        }
        
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=config.SYNC_CUTOFF_DAYS)
        
        for event in all_events:
            # Check if public
            if 'Public' not in event.get('categories', []):
                stats['non_public'] += 1
                continue
            
            # Skip tentative events
            if event.get('showAs', 'busy') != 'busy':
                stats['tentative'] += 1
                continue
            
            # Skip recurring instances
            if event.get('type') == 'occurrence':
                stats['recurring_instances'] += 1
                continue
            
            # Skip old events
            try:
                event_start = event.get('start', {}).get('dateTime', '')
                if event_start:
                    event_date = datetime.fromisoformat(event_start.replace('Z', ''))
                    if event_date < cutoff_date:
                        stats['past_events'] += 1
                        continue
            except:
                pass
            
            public_events.append(event)
        
        logger.info(f"Found {len(public_events)} public events to sync")
        logger.info(f"Skipped: {stats}")
        
        return public_events
