"""
Calendar Writer - Handles all write operations to Microsoft Graph with enhancements
"""
import logging
import requests
from typing import Dict, List, Optional
import uuid

import config
from utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class CalendarWriter:
    """Handles writing calendar data to Microsoft Graph"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
    
    @retry_with_backoff(max_retries=3, base_delay=1)
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
                logger.info(f"✅ Created event: {event_data.get('subject')}")
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
    
    @retry_with_backoff(max_retries=3, base_delay=1)
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
                logger.info(f"✅ Updated event: {event_data.get('subject')}")
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
    
    @retry_with_backoff(max_retries=3, base_delay=1)
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
        
        # Create body content with ONLY location - NO original content
        body_content = ""
        if location_text:
            body_content = f"<p><strong>Location:</strong> {location_text}</p>"
        
        # DO NOT append original content - public calendar should only show location
        
        # Build event data
        event_data = {
            'subject': source_event.get('subject'),
            'start': source_event.get('start'),
            'end': source_event.get('end'),
            'categories': source_event.get('categories', []),
            'body': {'contentType': 'html', 'content': body_content},
            'location': {},  # Clear location for privacy
            'isAllDay': source_event.get('isAllDay', False),
            'showAs': 'busy',  # Always busy for public calendar
            'isReminderOn': False,  # No reminders on public calendar
            'sensitivity': 'normal'  # Ensure not marked as private
        }
        
        # Add recurrence for series masters
        if source_event.get('type') == 'seriesMaster' and source_event.get('recurrence'):
            event_data['recurrence'] = source_event.get('recurrence')
        
        return event_data
    
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
