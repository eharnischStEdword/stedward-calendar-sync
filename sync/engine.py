"""
Sync Engine - Core calendar synchronization logic
"""
import logging
from datetime import datetime
from typing import Dict, List, Tuple
from threading import Lock

import config
from cal_ops.reader import CalendarReader
from cal_ops.writer import CalendarWriter

logger = logging.getLogger(__name__)


class SyncEngine:
    """Core engine for calendar synchronization"""
    
    def __init__(self, auth_manager):
        self.auth = auth_manager
        self.reader = CalendarReader(auth_manager)
        self.writer = CalendarWriter(auth_manager)
        
        # Sync state
        self.sync_lock = Lock()
        self.last_sync_time = None
        self.last_sync_result = {"success": False, "message": "Not synced yet"}
        self.sync_in_progress = False
        
        # Rate limiting
        self.sync_request_times = []
    
    def sync_calendars(self) -> Dict:
        """Main sync function"""
        # Check if already syncing
        with self.sync_lock:
            if self.sync_in_progress:
                return {"error": "Sync already in progress"}
            self.sync_in_progress = True
        
        # Check rate limit
        if not self._check_rate_limit():
            return {"error": "Rate limit exceeded. Please wait before syncing again."}
        
        # Record sync request
        self.sync_request_times.append(datetime.now())
        
        logger.info("🚀 Starting calendar sync")
        
        try:
            # Get calendar IDs
            source_id = self.reader.find_calendar_id(config.SOURCE_CALENDAR)
            target_id = self.reader.find_calendar_id(config.TARGET_CALENDAR)
            
            if not source_id or not target_id:
                return {"error": "Required calendars not found"}
            
            # Safety check
            if source_id == target_id:
                return {"error": "SAFETY ABORT: Source and target calendars are identical"}
            
            logger.info(f"Source calendar ID: {source_id}")
            logger.info(f"Target calendar ID: {target_id}")
            
            # Get events
            source_events = self.reader.get_public_events(source_id)
            target_events = self.reader.get_calendar_events(target_id)
            
            if source_events is None or target_events is None:
                return {"error": "Failed to retrieve calendar events"}
            
            # Build target map
            target_map = self._build_event_map(target_events)
            logger.info(f"Built target map with {len(target_map)} unique events")
            
            # Determine operations needed
            to_add, to_update, to_delete = self._determine_sync_operations(
                source_events, target_events, target_map
            )
            
            logger.info(f"📋 SYNC PLAN: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
            
            # Check dry run mode
            if config.DRY_RUN_MODE:
                logger.info("🧪 DRY RUN MODE - No changes will be made")
                return {
                    'success': True,
                    'message': f'DRY RUN: Would add {len(to_add)}, update {len(to_update)}, delete {len(to_delete)}',
                    'dry_run': True,
                    'added': len(to_add),
                    'updated': len(to_update),
                    'deleted': len(to_delete)
                }
            
            # Execute sync operations
            results = self._execute_sync_operations(target_id, to_add, to_update, to_delete)
            
            # Update sync state
            with self.sync_lock:
                self.last_sync_time = datetime.now()
                self.last_sync_result = results
            
            logger.info(f"🎉 Sync completed: {results}")
            return results
            
        except Exception as e:
            import traceback
            error_result = {
                'success': False,
                'message': f'Sync failed: {str(e)}',
                'error': str(e),
                'traceback': traceback.format_exc()
            }
            
            with self.sync_lock:
                self.last_sync_result = error_result
            
            logger.error(f"💥 Sync error: {error_result}")
            return error_result
            
        finally:
            with self.sync_lock:
                self.sync_in_progress = False
    
    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits"""
        current_time = datetime.now()
        from datetime import timedelta
        
        # Remove old requests
        self.sync_request_times = [
            t for t in self.sync_request_times 
            if current_time - t < timedelta(hours=1)
        ]
        
        return len(self.sync_request_times) < config.MAX_SYNC_REQUESTS_PER_HOUR
    
    def _normalize_subject(self, subject: str) -> str:
        """Normalize event subject for matching"""
        if not subject:
            return ""
        return ' '.join(subject.strip().lower().split())
    
    def _normalize_datetime(self, dt_str: str) -> str:
        """Normalize datetime string for matching"""
        if not dt_str:
            return ""
        try:
            if 'T' in dt_str:
                date_part, time_part = dt_str.split('T', 1)
                time_part = time_part.split('+')[0].split('Z')[0].split('-')[0]
                return f"{date_part}T{time_part}"
            return dt_str
        except:
            return dt_str
    
  # Update sync/engine.py - Replace the _create_event_signature method with this improved version:

def _create_event_signature(self, event: Dict) -> str:
    """Create unique signature for an event"""
    subject = self._normalize_subject(event.get('subject', ''))
    event_type = event.get('type', 'unknown')
    
    # For recurring events, we need to be more careful
    if event_type == 'seriesMaster':
        # For recurring series masters, use subject + recurrence pattern
        # This ensures the same recurring event is recognized even if IDs differ
        recurrence = event.get('recurrence', {})
        
        # Create a stable signature from recurrence pattern
        pattern_type = recurrence.get('pattern', {}).get('type', 'unknown')
        interval = recurrence.get('pattern', {}).get('interval', 1)
        
        # Include key recurrence details in signature
        recurrence_sig = f"{pattern_type}:{interval}"
        
        # For weekly events, include days of week
        if pattern_type == 'weekly':
            days = recurrence.get('pattern', {}).get('daysOfWeek', [])
            days_str = ','.join(sorted(days))
            recurrence_sig += f":{days_str}"
        
        return f"recurring:{subject}:{recurrence_sig}"
    
    elif event_type == 'occurrence':
        # Skip individual occurrences - we handle the series master
        # This prevents duplicate handling
        return f"skip:occurrence:{subject}"
    
    # For single events, use the existing logic
    start = self._normalize_datetime(event.get('start', {}).get('dateTime', ''))
    try:
        if 'T' in start:
            date_part = start.split('T')[0]
            time_part = start.split('T')[1][:5]
            return f"single:{subject}:{date_part}:{time_part}"
    except:
        pass
    
    return f"single:{subject}:{start}"

# Also update the sync logic to skip occurrences in _determine_sync_operations:
def _determine_sync_operations(
    self, 
    source_events: List[Dict], 
    target_events: List[Dict],
    target_map: Dict[str, Dict]
) -> Tuple[List[Dict], List[Tuple[Dict, Dict]], List[Dict]]:
    """Determine what operations are needed"""
    to_add = []
    to_update = []
    
    # Make a copy of target_map for tracking deletions
    remaining_targets = target_map.copy()
    
    for source_event in source_events:
        signature = self._create_event_signature(source_event)
        
        # Skip individual occurrences of recurring events
        if signature.startswith("skip:occurrence:"):
            continue
        
        if signature in remaining_targets:
            # Event exists - check if update needed
            target_event = remaining_targets[signature]
            
            if self._needs_update(source_event, target_event):
                to_update.append((source_event, target_event))
            
            # Remove from remaining targets
            del remaining_targets[signature]
        else:
            # Event doesn't exist - add it
            to_add.append(source_event)
    
    # Filter out any "skip:occurrence" signatures from deletions
    to_delete = [
        event for sig, event in remaining_targets.items() 
        if not sig.startswith("skip:occurrence:")
    ]

    # In _create_event_signature, add:
logger.debug(f"Creating signature for event: {event.get('subject')} (type: {event_type})")
logger.debug(f"Generated signature: {signature}")
    
    return to_add, to_update, to_delete
    
    def _build_event_map(self, events: List[Dict]) -> Dict[str, Dict]:
        """Build a map of events by signature"""
        event_map = {}
        
        for event in events:
            signature = self._create_event_signature(event)
            
            if signature in event_map:
                # Keep newer event if duplicate
                existing_created = event_map[signature].get('createdDateTime', '')
                new_created = event.get('createdDateTime', '')
                if new_created > existing_created:
                    event_map[signature] = event
            else:
                event_map[signature] = event
        
        return event_map
    
    def _determine_sync_operations(
        self, 
        source_events: List[Dict], 
        target_events: List[Dict],
        target_map: Dict[str, Dict]
    ) -> Tuple[List[Dict], List[Tuple[Dict, Dict]], List[Dict]]:
        """Determine what operations are needed"""
        to_add = []
        to_update = []
        
        # Make a copy of target_map for tracking deletions
        remaining_targets = target_map.copy()
        
        for source_event in source_events:
            signature = self._create_event_signature(source_event)
            
            if signature in remaining_targets:
                # Event exists - check if update needed
                target_event = remaining_targets[signature]
                
                if self._needs_update(source_event, target_event):
                    to_update.append((source_event, target_event))
                
                # Remove from remaining targets
                del remaining_targets[signature]
            else:
                # Event doesn't exist - add it
                to_add.append(source_event)
        
        # Remaining events in target should be deleted
        to_delete = list(remaining_targets.values())
        
        return to_add, to_update, to_delete
    
def _needs_update(self, source_event: Dict, target_event: Dict) -> bool:
    """Check if an event needs updating"""
    # First check if modification times are available
    source_modified = source_event.get('lastModifiedDateTime')
    target_modified = target_event.get('lastModifiedDateTime')
    
    # If both have modification times and source isn't newer, skip update
    if source_modified and target_modified:
        if source_modified <= target_modified:
            return False
    
    # Continue with existing checks...
        # Compare key fields
        if source_event.get('subject') != target_event.get('subject'):
            return True
        
        if source_event.get('start') != target_event.get('start'):
            return True
        
        if source_event.get('end') != target_event.get('end'):
            return True
        
        if source_event.get('isAllDay') != target_event.get('isAllDay'):
            return True
        
        if source_event.get('recurrence') != target_event.get('recurrence'):
            return True
        
        return False
    
    def _execute_sync_operations(
        self, 
        target_calendar_id: str,
        to_add: List[Dict],
        to_update: List[Tuple[Dict, Dict]],
        to_delete: List[Dict]
    ) -> Dict:
        """Execute the sync operations"""
        successful = 0
        failed = 0
        total = len(to_add) + len(to_update) + len(to_delete)
        
        # Add new events
        for event in to_add:
            if self.writer.create_event(target_calendar_id, event):
                successful += 1
            else:
                failed += 1
        
        # Update existing events
        for source_event, target_event in to_update:
            event_id = target_event.get('id')
            if self.writer.update_event(target_calendar_id, event_id, source_event):
                successful += 1
            else:
                failed += 1
        
        # Delete removed events
        for event in to_delete:
            event_id = event.get('id')
            if self.writer.delete_event(target_calendar_id, event_id):
                successful += 1
            else:
                failed += 1
        
        return {
            'success': True,
            'message': f'Sync complete: {successful}/{total} operations successful',
            'successful_operations': successful,
            'failed_operations': failed,
            'added': len(to_add),
            'updated': len(to_update),
            'deleted': len(to_delete),
            'safeguards_active': config.MASTER_CALENDAR_PROTECTION
        }
    
    def get_status(self) -> Dict:
        """Get current sync status"""
        with self.sync_lock:
            return {
                'last_sync_time': self.last_sync_time,
                'last_sync_result': self.last_sync_result,
                'sync_in_progress': self.sync_in_progress,
                'rate_limit_remaining': config.MAX_SYNC_REQUESTS_PER_HOUR - len(self.sync_request_times)
            }
