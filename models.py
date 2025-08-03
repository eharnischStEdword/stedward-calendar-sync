# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Data models and calendar sync functionality for St. Edward Calendar Sync
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import requests
import pytz

import config
from utils import DateTimeUtils, ValidationUtils, MetricsUtils, RetryUtils, structured_logger, cache_manager
from auth import auth_manager

logger = logging.getLogger(__name__)

# =============================================================================
# CALENDAR PROVIDER ABSTRACTION
# =============================================================================

class CalendarProvider:
    """Abstract base class for calendar providers"""
    
    def __init__(self, credentials: Dict):
        self.credentials = credentials
        self.client = self._build_client()
    
    def get_events(self, calendar_id: str, start_date: str, end_date: str) -> List[Dict]:
        """Template method - same structure for all providers"""
        raw_events = self._fetch_events(calendar_id, start_date, end_date)
        return [self._normalize_event(event) for event in raw_events]
    
    def create_event(self, calendar_id: str, event_data: Dict) -> bool:
        """Create event in calendar"""
        return self._create_event(calendar_id, event_data)
    
    def update_event(self, calendar_id: str, event_id: str, event_data: Dict) -> bool:
        """Update event in calendar"""
        return self._update_event(calendar_id, event_id, event_data)
    
    def delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Delete event from calendar"""
        return self._delete_event(calendar_id, event_id)
    
    def _build_client(self):
        """Build API client - to be implemented by subclasses"""
        raise NotImplementedError
    
    def _fetch_events(self, calendar_id: str, start_date: str, end_date: str) -> List[Dict]:
        """Fetch events from calendar - to be implemented by subclasses"""
        raise NotImplementedError
    
    def _normalize_event(self, event: Dict) -> Dict:
        """Normalize event data - to be implemented by subclasses"""
        raise NotImplementedError
    
    def _create_event(self, calendar_id: str, event_data: Dict) -> bool:
        """Create event - to be implemented by subclasses"""
        raise NotImplementedError
    
    def _update_event(self, calendar_id: str, event_id: str, event_data: Dict) -> bool:
        """Update event - to be implemented by subclasses"""
        raise NotImplementedError
    
    def _delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Delete event - to be implemented by subclasses"""
        raise NotImplementedError

class MicrosoftGraphProvider(CalendarProvider):
    """Microsoft Graph API calendar provider"""
    
    def __init__(self):
        super().__init__({})  # Credentials handled by auth_manager
        self.auth_manager = auth_manager
    
    def _build_client(self):
        """Build Microsoft Graph client"""
        # Client is built through auth_manager
        return None
    
    def _fetch_events(self, calendar_id: str, start_date: str, end_date: str) -> List[Dict]:
        """Fetch events using Microsoft Graph API"""
        headers = self.auth_manager.get_headers()
        if not headers:
            return []
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events"
        params = {
            '$select': 'id,subject,start,end,categories,location,isAllDay,showAs,type,seriesMasterId,isCancelled,originalStart,body,lastModifiedDateTime,recurrence',
            '$filter': f"start/dateTime ge '{start_date}' and end/dateTime le '{end_date}'",
            '$orderby': 'start/dateTime',
            '$top': 250
        }
        
        all_events = []
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 401:
                if self.auth_manager.refresh_if_needed():
                    headers = self.auth_manager.get_headers()
                    response = requests.get(url, headers=headers, params=params, timeout=30)
                else:
                    logger.error("Authentication failed during event fetch")
                    return []
            
            if response.status_code == 200:
                data = response.json()
                events = data.get('value', [])
                all_events.extend(events)
                
                # Handle pagination
                next_link = data.get('@odata.nextLink')
                while next_link:
                    response = requests.get(next_link, headers=headers, timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        all_events.extend(data.get('value', []))
                        next_link = data.get('@odata.nextLink')
                    else:
                        break
                
                logger.info(f"Retrieved {len(all_events)} events from calendar {calendar_id}")
                return all_events
            else:
                logger.error(f"Failed to get events: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []
    
    def _normalize_event(self, event: Dict) -> Dict:
        """Normalize Microsoft Graph event data"""
        return {
            'id': event.get('id'),
            'subject': event.get('subject', 'No subject'),
            'start': event.get('start'),
            'end': event.get('end'),
            'categories': event.get('categories', []),
            'location': event.get('location', {}),
            'isAllDay': event.get('isAllDay', False),
            'showAs': event.get('showAs', 'busy'),
            'type': event.get('type'),
            'seriesMasterId': event.get('seriesMasterId'),
            'isCancelled': event.get('isCancelled', False),
            'originalStart': event.get('originalStart'),
            'body': event.get('body', {}),
            'lastModifiedDateTime': event.get('lastModifiedDateTime'),
            'recurrence': event.get('recurrence')
        }
    
    def _create_event(self, calendar_id: str, event_data: Dict) -> bool:
        """Create event using Microsoft Graph API"""
        headers = self.auth_manager.get_headers()
        if not headers:
            return False
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events"
        
        try:
            response = requests.post(url, headers=headers, json=event_data, timeout=30)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Created event: {event_data.get('subject')}")
                return True
            else:
                logger.error(f"❌ Failed to create event: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Exception creating event: {e}")
            return False
    
    def _update_event(self, calendar_id: str, event_id: str, event_data: Dict) -> bool:
        """Update event using Microsoft Graph API"""
        headers = self.auth_manager.get_headers()
        if not headers:
            return False
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}"
        
        try:
            response = requests.patch(url, headers=headers, json=event_data, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"✅ Updated event: {event_data.get('subject')}")
                return True
            else:
                logger.error(f"❌ Failed to update event: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Exception updating event: {e}")
            return False
    
    def _delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Delete event using Microsoft Graph API"""
        headers = self.auth_manager.get_headers()
        if not headers:
            return False
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{calendar_id}/events/{event_id}"
        
        try:
            response = requests.delete(url, headers=headers, timeout=30)
            
            if response.status_code == 204:
                logger.info(f"✅ Deleted event: {event_id}")
                return True
            else:
                logger.error(f"❌ Failed to delete event: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Exception deleting event: {e}")
            return False

# =============================================================================
# CALENDAR SYNC ENGINE
# =============================================================================

class CalendarSync:
    """Main calendar synchronization engine"""
    
    def __init__(self):
        self.provider = MicrosoftGraphProvider()
        self.auth_manager = auth_manager
        
        # State tracking
        self.last_sync_time = None
        self.last_sync_result = None
        self.sync_in_progress = False
        
        # Cache for change detection
        self.event_cache = {}
        self.cache_file = '/data/event_cache.json'
        self._load_cache()
    
    def sync_calendars(self) -> Dict[str, Any]:
        """Main sync function"""
        if self.sync_in_progress:
            return {'success': False, 'message': 'Sync already in progress'}
        
        start_time = DateTimeUtils.get_central_time()
        self.sync_in_progress = True
        
        try:
            logger.info("🔄 Starting calendar sync...")
            
            # Get calendar IDs
            source_id = self._find_calendar_id(config.SOURCE_CALENDAR)
            target_id = self._find_calendar_id(config.TARGET_CALENDAR)
            
            if not source_id or not target_id:
                return {'success': False, 'message': 'Required calendars not found'}
            
            # Safety check
            if source_id == target_id:
                return {'success': False, 'message': 'SAFETY ABORT: Source and target calendars are identical'}
            
            # Get events from both calendars
            source_events = self._get_public_events(source_id)
            target_events = self._get_calendar_events(target_id)
            
            if source_events is None or target_events is None:
                return {'success': False, 'message': 'Failed to retrieve calendar events'}
            
            # Determine sync operations
            operations = self._determine_sync_operations(source_events, target_events)
            
            # Execute operations
            result = self._execute_sync_operations(target_id, operations)
            
            # Update cache
            self._update_cache(source_events)
            
            # Calculate duration
            duration = (DateTimeUtils.get_central_time() - start_time).total_seconds()
            
            # Update state
            self.last_sync_time = DateTimeUtils.get_central_time()
            self.last_sync_result = result
            
            logger.info(f"🎉 Sync completed in {duration:.2f} seconds")
            
            return {
                'success': True,
                'message': f'Sync complete: {result["successful"]}/{result["total"]} operations successful',
                'duration': duration,
                **result
            }
            
        except Exception as e:
            logger.error(f"❌ Sync error: {e}")
            return {'success': False, 'message': f'Sync failed: {str(e)}'}
        finally:
            self.sync_in_progress = False
    
    def _find_calendar_id(self, calendar_name: str) -> Optional[str]:
        """Find calendar ID by name"""
        headers = self.auth_manager.get_headers()
        if not headers:
            return None
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars"
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                calendars = response.json().get('value', [])
                for calendar in calendars:
                    if calendar.get('name') == calendar_name:
                        return calendar.get('id')
            
            logger.error(f"Calendar '{calendar_name}' not found")
            return None
            
        except Exception as e:
            logger.error(f"Error finding calendar: {e}")
            return None
    
    def _get_public_events(self, calendar_id: str) -> Optional[List[Dict]]:
        """Get public events from calendar"""
        events = self._get_calendar_events(calendar_id)
        if not events:
            return None
        
        # Filter for public events only
        public_events = []
        for event in events:
            # Skip private events
            if event.get('showAs') == 'private':
                continue
            
            # Skip tentative events
            if event.get('showAs') == 'tentative':
                continue
            
            # Skip occurrences (handled separately)
            if event.get('type') == 'occurrence':
                continue
            
            public_events.append(event)
        
        logger.info(f"Found {len(public_events)} public events to sync")
        return public_events
    
    def _get_calendar_events(self, calendar_id: str) -> Optional[List[Dict]]:
        """Get all events from calendar"""
        start_date = (DateTimeUtils.get_central_time() - timedelta(days=config.SYNC_CUTOFF_DAYS)).isoformat()
        end_date = (DateTimeUtils.get_central_time() + timedelta(days=365)).isoformat()
        
        return self.provider.get_events(calendar_id, start_date, end_date)
    
    def _determine_sync_operations(self, source_events: List[Dict], target_events: List[Dict]) -> Dict[str, Any]:
        """Determine what sync operations are needed"""
        # Create lookup maps
        source_map = {event['id']: event for event in source_events}
        target_map = {event['id']: event for event in target_events}
        
        to_add = []
        to_update = []
        to_delete = []
        
        # Find events to add or update
        for source_event in source_events:
            event_id = source_event['id']
            if event_id in target_map:
                # Event exists - check if update needed
                target_event = target_map[event_id]
                if self._needs_update(source_event, target_event):
                    to_update.append((source_event, target_event))
            else:
                # Event doesn't exist - add it
                to_add.append(source_event)
        
        # Find events to delete
        for target_event in target_events:
            event_id = target_event['id']
            if event_id not in source_map:
                to_delete.append(target_event)
        
        return {
            'to_add': to_add,
            'to_update': to_update,
            'to_delete': to_delete,
            'total': len(to_add) + len(to_update) + len(to_delete)
        }
    
    def _needs_update(self, source_event: Dict, target_event: Dict) -> bool:
        """Check if event needs updating"""
        # Compare key fields
        fields_to_check = ['subject', 'start', 'end', 'isAllDay', 'body']
        
        for field in fields_to_check:
            if source_event.get(field) != target_event.get(field):
                return True
        
        return False
    
    def _execute_sync_operations(self, target_id: str, operations: Dict[str, Any]) -> Dict[str, Any]:
        """Execute sync operations"""
        successful = 0
        failed = 0
        
        # Add events
        for event in operations['to_add']:
            if self.provider.create_event(target_id, event):
                successful += 1
            else:
                failed += 1
        
        # Update events
        for source_event, target_event in operations['to_update']:
            if self.provider.update_event(target_id, target_event['id'], source_event):
                successful += 1
            else:
                failed += 1
        
        # Delete events
        for event in operations['to_delete']:
            if self.provider.delete_event(target_id, event['id']):
                successful += 1
            else:
                failed += 1
        
        return {
            'successful': successful,
            'failed': failed,
            'total': operations['total']
        }
    
    def _load_cache(self):
        """Load event cache from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    self.event_cache = json.load(f)
                logger.info(f"✅ Loaded event cache with {len(self.event_cache)} events")
        except Exception as e:
            logger.error(f"Error loading cache: {e}")
            self.event_cache = {}
    
    def _update_cache(self, events: List[Dict]):
        """Update event cache"""
        try:
            self.event_cache = {event['id']: event for event in events}
            
            with open(self.cache_file, 'w') as f:
                json.dump(self.event_cache, f)
            
            logger.info(f"✅ Updated event cache with {len(self.event_cache)} events")
        except Exception as e:
            logger.error(f"Error updating cache: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get sync engine status"""
        return {
            'last_sync_time': self.last_sync_time.isoformat() if self.last_sync_time else None,
            'last_sync_time_display': DateTimeUtils.format_central_time(self.last_sync_time),
            'last_sync_result': self.last_sync_result,
            'sync_in_progress': self.sync_in_progress,
            'cache_size': len(self.event_cache)
        }

# =============================================================================
# INITIALIZATION
# =============================================================================

# Create global sync engine instance
sync_engine = CalendarSync() 