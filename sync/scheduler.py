"""
Calendar Writer - Handles all write operations to Microsoft Graph
"""
import logging
import requests
from typing import Dict, Optional

import config

logger = logging.getLogger(__name__)


class CalendarWriter:
    """Handles writing calendar data to Microsoft Graph"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
    
    def create_event(self, calendar_id: str, event_data: Dict) -> bool:
        """Create a new event in the specified calendar"""
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
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Created event: {event_data.get('subject')}")
                return True
            else:
                logger.error(f"❌ Failed to create event: {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Error creating event: {e}")
            return False
    
    def update_event(self, calendar_id: str, event_id: str, event_data: Dict) -> bool:
        """Update an existing event"""
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
            
            if response.status_code == 200:
                logger.info(f"✅ Updated event: {event_data.get('subject')}")
                return True
            else:
                logger.error(f"❌ Failed to update event: {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Error updating event: {e}")
            return False
    
    def delete_event(self, calendar_id: str, event_id: str, suppress_notifications: bool = True) -> bool:
        """Delete an event from the calendar"""
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
            
            if response.status_code in [200, 204]:
                logger.info(f"✅ Deleted event ID: {event_id[:8]}...")
                return True
            else:
                logger.error(f"❌ Failed to delete event: {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Error deleting event: {e}")
            return False
    
    def _prepare_event_data(self, source_event: Dict) -> Dict:
        """Prepare event data for creation/update"""
        # Extract location for body content
        source_location = source_event.get('location', {})
        location_text = ""
        
        if source_location:
            if isinstance(source_location, dict):
                location_text = source_location.get('displayName', '')
            else:
                location_text = str(source_location)
        
        # Create body content with location
        body_content = ""
        if location_text:
            body_content = f"<p><strong>Location:</strong> {location_text}</p>"
        
        # Build event data
        event_data = {
            'subject': source_event.get('subject'),
            'start': source_event.get('start'),
            'end': source_event.get('end'),
            'categories': source_event.get('categories'),
            'body': {'contentType': 'html', 'content': body_content},
            'location': {},  # Clear location for privacy
            'isAllDay': source_event.get('isAllDay', False),
            'showAs': 'busy',  # Always busy for public calendar
            'isReminderOn': False  # No reminders on public calendar
        }
        
        # Add recurrence for series masters
        if source_event.get('type') == 'seriesMaster' and source_event.get('recurrence'):
            event_data['recurrence'] = source_event.get('recurrence')
        
        return event_data
    
    def batch_delete_events(self, calendar_id: str, event_ids: list) -> Dict:
        """Delete multiple events and return results"""
        results = {
            'successful': 0,
            'failed': 0,
            'details': []
        }
        
        for event_id in event_ids:
            success = self.delete_event(calendar_id, event_id)
            
            if success:
                results['successful'] += 1
            else:
                results['failed'] += 1
            
            results['details'].append({
                'event_id': event_id[:8] + '...',
                'success': success
            })
        
        return results
