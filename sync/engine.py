"""
Sync Engine - FIXED VERSION to prevent duplicates
"""
import logging
from datetime import datetime
from typing import Dict, List, Tuple
from threading import Lock
import hashlib
import json

import config
from cal_ops.reader import CalendarReader
from cal_ops.writer import CalendarWriter
from utils.logger import StructuredLogger
from utils.metrics import MetricsCollector
from utils.circuit_breaker import CircuitBreaker
from sync.history import SyncHistory
from sync.validator import SyncValidator

logger = logging.getLogger(__name__)


class SyncEngine:
    """Core engine for calendar synchronization - FIXED for duplicates"""
    
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
        
        # Enhanced features
        self.structured_logger = StructuredLogger(__name__)
        self.metrics = MetricsCollector()
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=300)
        self.history = SyncHistory()
        self.validator = SyncValidator()
    
    def sync_calendars(self) -> Dict:
        """Main sync function with circuit breaker protection"""
        try:
            # Use circuit breaker to protect against cascading failures
            return self.circuit_breaker.call(self._do_sync)
        except Exception as e:
            if "Circuit breaker is open" in str(e):
                error_msg = "Service temporarily unavailable due to recent failures. Please try again later."
                logger.error(error_msg)
                return {"error": error_msg, "circuit_breaker_open": True}
            raise
    
    def _do_sync(self) -> Dict:
        """Actual sync implementation"""
        start_time = datetime.now()
        
        # Check if already syncing
        with self.sync_lock:
            if self.sync_in_progress:
                return {"error": "Sync already in progress"}
            self.sync_in_progress = True
        
        # Check rate limit
        if not self._check_rate_limit():
            with self.sync_lock:
                self.sync_in_progress = False
            return {"error": "Rate limit exceeded. Please wait before syncing again."}
        
        # Record sync request
        self.sync_request_times.append(datetime.now())
        
        logger.info("🚀 Starting calendar sync")
        self.structured_logger.log_sync_event('sync_started', {
            'timestamp': datetime.utcnow().isoformat()
        })
        
        try:
            # Pre-flight checks
            if not self._pre_flight_check():
                return {"error": "Pre-flight checks failed. Check logs for details."}
            
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
            
            logger.info(f"📊 Retrieved {len(source_events)} source events and {len(target_events)} target events")
            
            # ENHANCED DEBUGGING: Log event details for signature analysis
            self._debug_event_signatures(source_events[:5], "SOURCE")
            self._debug_event_signatures(target_events[:5], "TARGET")
            
            # Build target map with enhanced debugging
            target_map = self._build_event_map(target_events)
            logger.info(f"Built target map with {len(target_map)} unique events")
            
            # Determine operations needed
            to_add, to_update, to_delete = self._determine_sync_operations(
                source_events, target_events, target_map
            )
            
            logger.info(f"📋 SYNC PLAN: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
            
            # ENHANCED DEBUGGING: Log what we're planning to do
            if to_add:
                logger.info("📝 Events to ADD:")
                for event in to_add[:3]:  # Log first 3
                    sig = self._create_event_signature(event)
                    logger.info(f"  - {event.get('subject')} | Signature: {sig}")
            
            if to_delete:
                logger.info("🗑️ Events to DELETE:")
                for event in to_delete[:3]:  # Log first 3
                    sig = self._create_event_signature(event)
                    logger.info(f"  - {event.get('subject')} | Signature: {sig}")
            
            # Check dry run mode
            if config.DRY_RUN_MODE:
                logger.info("🧪 DRY RUN MODE - No changes will be made")
                result = {
                    'success': True,
                    'message': f'DRY RUN: Would add {len(to_add)}, update {len(to_update)}, delete {len(to_delete)}',
                    'dry_run': True,
                    'added': len(to_add),
                    'updated': len(to_update),
                    'deleted': len(to_delete)
                }
            else:
                # Execute sync operations
                result = self._execute_sync_operations(target_id, to_add, to_update, to_delete)
                
                # Validate sync result if successful
                if result.get('success') and not config.DRY_RUN_MODE:
                    logger.info("Validating sync results...")
                    fresh_source = self.reader.get_public_events(source_id)
                    fresh_target = self.reader.get_calendar_events(target_id)
                    
                    if fresh_source and fresh_target:
                        is_valid, validations = self.validator.validate_sync_result(
                            fresh_source, fresh_target
                        )
                        
                        # Filter out ignored warnings
                        ignored_warnings = getattr(config, 'IGNORE_VALIDATION_WARNINGS', ['no_duplicates', 'event_integrity'])
                        failed_checks = [(check, passed) for check, passed in validations if not passed]
                        non_ignored_failures = [(check, passed) for check, passed in failed_checks 
                                               if check not in ignored_warnings]
                        
                        # Consider valid if only ignored warnings failed
                        is_valid = len(non_ignored_failures) == 0
                        
                        result['validation'] = {
                            'is_valid': is_valid,
                            'checks': validations,
                            'ignored_warnings': ignored_warnings
                        }
                        
                        if not is_valid:
                            logger.warning(f"Sync validation failed: {non_ignored_failures}")
                            self.structured_logger.log_sync_event('sync_validation_failed', {
                                'validations': validations,
                                'non_ignored_failures': non_ignored_failures
                            })
                        elif failed_checks:
                            # Log ignored warnings at INFO level
                            ignored_checks = [(check, passed) for check, passed in failed_checks 
                                            if check in ignored_warnings]
                            logger.info(f"Sync validation passed (ignored warnings: {[check for check, _ in ignored_checks]})")
            
            # Calculate duration
            duration = (datetime.now() - start_time).total_seconds()
            result['duration'] = duration
            
            # Update sync state
            with self.sync_lock:
                self.last_sync_time = datetime.now()
                self.last_sync_result = result
            
            # Record metrics and history
            if not config.DRY_RUN_MODE:
                self.metrics.record_sync_duration(duration)
                self.metrics.record_sync_result(
                    result.get('added', 0),
                    result.get('updated', 0),
                    result.get('deleted', 0),
                    result.get('failed_operations', 0)
                )
                self.history.add_entry(result)
            
            # Log structured event
            self.structured_logger.log_sync_event('sync_completed', {
                'duration_seconds': duration,
                'added': result.get('added', 0),
                'updated': result.get('updated', 0),
                'deleted': result.get('deleted', 0),
                'success': result.get('success', False),
                'dry_run': config.DRY_RUN_MODE
            })
            
            logger.info(f"🎉 Sync completed in {duration:.2f} seconds: {result}")
            return result
            
        except Exception as e:
            import traceback
            duration = (datetime.now() - start_time).total_seconds()
            
            error_result = {
                'success': False,
                'message': f'Sync failed: {str(e)}',
                'error': str(e),
                'traceback': traceback.format_exc(),
                'duration': duration
            }
            
            with self.sync_lock:
                self.last_sync_result = error_result
            
            # Log structured error
            self.structured_logger.log_sync_event('sync_failed', {
                'error': str(e),
                'duration_seconds': duration
            })
            
            # Record failure metrics
            self.metrics.record_sync_duration(duration)
            self.metrics.record_sync_result(0, 0, 0, 1)
            
            logger.error(f"💥 Sync error after {duration:.2f} seconds: {error_result}")
            return error_result
            
        finally:
            with self.sync_lock:
                self.sync_in_progress = False
    
    def _debug_event_signatures(self, events: List[Dict], label: str):
        """Debug helper to log event signatures"""
        logger.info(f"🔍 {label} EVENT SIGNATURES:")
        for event in events:
            signature = self._create_event_signature(event)
            start_time = event.get('start', {}).get('dateTime', 'No start time')
            logger.info(f"  📅 '{event.get('subject', 'No subject')}' -> {signature}")
            logger.info(f"      Start: {start_time}")
            logger.info(f"      Type: {event.get('type', 'singleInstance')}")
            logger.info(f"      ID: {event.get('id', 'No ID')}")
    
    def _pre_flight_check(self) -> bool:
        """Verify system is ready for sync"""
        checks = []
        
        # Check authentication
        auth_valid = self.auth.is_authenticated()
        checks.append(('auth_valid', auth_valid))
        if not auth_valid:
            logger.error("Pre-flight check failed: Authentication invalid")
        
        # Check calendar access
        try:
            calendars = self.reader.get_calendars()
            calendars_accessible = calendars is not None
            checks.append(('calendars_accessible', calendars_accessible))
            if not calendars_accessible:
                logger.error("Pre-flight check failed: Cannot access calendars")
        except Exception as e:
            logger.error(f"Pre-flight check failed: Calendar access error: {e}")
            checks.append(('calendars_accessible', False))
        
        # Check rate limit
        rate_limit_ok = self._check_rate_limit()
        checks.append(('rate_limit_ok', rate_limit_ok))
        if not rate_limit_ok:
            logger.error("Pre-flight check failed: Rate limit exceeded")
        
        # Log check results
        failed_checks = [name for name, result in checks if not result]
        if failed_checks:
            logger.error(f"Pre-flight checks failed: {failed_checks}")
            self.structured_logger.log_sync_event('preflight_checks_failed', {
                'failed_checks': failed_checks
            })
            return False
        
        logger.info("All pre-flight checks passed")
        return True
    
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
        # More aggressive normalization to handle Microsoft Graph variations
        normalized = ' '.join(subject.strip().lower().split())
        # Remove common punctuation that might vary
        normalized = normalized.replace('.', '').replace(',', '').replace(':', '').replace(';', '')
        return normalized
    
    def _normalize_datetime(self, dt_str: str) -> str:
        """Normalize datetime string for matching - IMPROVED"""
        if not dt_str:
            return ""
        try:
            # Remove timezone info and normalize to just date and time
            clean_dt = dt_str.replace('Z', '').replace('+00:00', '')
            if '+' in clean_dt:
                clean_dt = clean_dt.split('+')[0]
            if '-' in clean_dt and clean_dt.count('-') > 2:  # More than just date separators
                clean_dt = clean_dt.rsplit('-', 1)[0]
            
            # Ensure consistent format
            if 'T' in clean_dt:
                date_part, time_part = clean_dt.split('T', 1)
                # Normalize time to HH:MM format for consistency
                time_part = time_part[:5]  # Take only HH:MM
                return f"{date_part}T{time_part}"
            return clean_dt
        except Exception as e:
            logger.warning(f"Failed to normalize datetime '{dt_str}': {e}")
            return dt_str
    
    def _create_event_signature(self, event: Dict) -> str:
        """Create unique signature for an event - COMPLETELY REWRITTEN"""
        subject = self._normalize_subject(event.get('subject', ''))
        event_type = event.get('type', 'singleInstance')
        
        # Get normalized start time
        start_raw = event.get('start', {}).get('dateTime', '')
        start_normalized = self._normalize_datetime(start_raw)
        
        # For recurring events
        if event_type == 'seriesMaster':
            recurrence = event.get('recurrence', {})
            pattern = recurrence.get('pattern', {})
            
            # Create a stable hash of the recurrence pattern
            pattern_data = {
                'type': pattern.get('type', 'unknown'),
                'interval': pattern.get('interval', 1),
                'daysOfWeek': sorted(pattern.get('daysOfWeek', [])),
                'dayOfMonth': pattern.get('dayOfMonth'),
                'index': pattern.get('index')
            }
            
            # Create hash of pattern for consistency
            pattern_str = json.dumps(pattern_data, sort_keys=True)
            pattern_hash = hashlib.md5(pattern_str.encode()).hexdigest()[:8]
            
            signature = f"recurring:{subject}:{pattern_hash}:{start_normalized}"
            logger.debug(f"Recurring signature: {signature}")
            return signature
        
        elif event_type == 'occurrence':
            # Skip individual occurrences - we handle the series master
            return f"skip:occurrence:{subject}:{start_normalized}"
        
        # For single events - include time to distinguish events on same day
        if 'T' in start_normalized:
            date_part = start_normalized.split('T')[0]
            time_part = start_normalized.split('T')[1] if 'T' in start_normalized else '00:00'
            signature = f"single:{subject}:{date_part}:{time_part}"
        else:
            signature = f"single:{subject}:{start_normalized}"
        
        logger.debug(f"Single event signature: {signature}")
        return signature
    
    def _build_event_map(self, events: List[Dict]) -> Dict[str, Dict]:
        """Build a map of events by signature with collision detection"""
        event_map = {}
        collisions = []
        
        for event in events:
            signature = self._create_event_signature(event)
            
            # Skip occurrences
            if signature.startswith("skip:occurrence:"):
                continue
            
            if signature in event_map:
                # Collision detected!
                existing = event_map[signature]
                collisions.append({
                    'signature': signature,
                    'existing': {
                        'subject': existing.get('subject'),
                        'id': existing.get('id'),
                        'start': existing.get('start', {}).get('dateTime')
                    },
                    'new': {
                        'subject': event.get('subject'),
                        'id': event.get('id'),
                        'start': event.get('start', {}).get('dateTime')
                    }
                })
                
                # Keep the newer event based on creation time
                existing_created = existing.get('createdDateTime', '')
                new_created = event.get('createdDateTime', '')
                if new_created > existing_created:
                    logger.warning(f"Signature collision! Keeping newer event for '{signature}'")
                    event_map[signature] = event
                else:
                    logger.warning(f"Signature collision! Keeping existing event for '{signature}'")
            else:
                event_map[signature] = event
        
        if collisions:
            logger.error(f"🚨 FOUND {len(collisions)} SIGNATURE COLLISIONS!")
            for collision in collisions:
                logger.error(f"  Collision: {collision}")
            logger.error("🚨 This might be causing duplicates! Check signature logic!")
        
        return event_map
    
    def _determine_sync_operations(
        self, 
        source_events: List[Dict], 
        target_events: List[Dict],
        target_map: Dict[str, Dict]
    ) -> Tuple[List[Dict], List[Tuple[Dict, Dict]], List[Dict]]:
        """Determine what operations are needed - ENHANCED LOGGING"""
        to_add = []
        to_update = []
        
        # Make a copy of target_map for tracking deletions
        remaining_targets = target_map.copy()
        
        logger.info(f"🔍 Analyzing {len(source_events)} source events against {len(target_map)} target events")
        
        for source_event in source_events:
            signature = self._create_event_signature(source_event)
            subject = source_event.get('subject', 'No subject')
            
            # Skip individual occurrences of recurring events
            if signature.startswith("skip:occurrence:"):
                logger.debug(f"Skipping occurrence: {subject}")
                continue
            
            if signature in remaining_targets:
                # Event exists - check if update needed
                target_event = remaining_targets[signature]
                
                if self._needs_update(source_event, target_event):
                    logger.debug(f"📝 UPDATE needed: {subject}")
                    to_update.append((source_event, target_event))
                else:
                    logger.debug(f"✅ No change needed: {subject}")
                
                # Remove from remaining targets
                del remaining_targets[signature]
            else:
                # Event doesn't exist - add it
                logger.debug(f"➕ ADD needed: {subject} (signature: {signature})")
                to_add.append(source_event)
        
        # Remaining events in target should be deleted
        to_delete = list(remaining_targets.values())
        
        logger.info(f"📊 Operation summary:")
        logger.info(f"  - {len(to_add)} events to ADD")
        logger.info(f"  - {len(to_update)} events to UPDATE") 
        logger.info(f"  - {len(to_delete)} events to DELETE")
        logger.info(f"  - {len(remaining_targets)} unmatched target events")
        
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
        
        # Compare key fields
        if source_event.get('subject') != target_event.get('subject'):
            return True
        
        if source_event.get('start') != target_event.get('start'):
            return True
        
        if source_event.get('end') != target_event.get('end'):
            return True
        
        if source_event.get('isAllDay') != target_event.get('isAllDay'):
            return True
        
        # For recurring events, check if recurrence pattern changed
        if source_event.get('type') == 'seriesMaster':
            source_recurrence = source_event.get('recurrence', {})
            target_recurrence = target_event.get('recurrence', {})
            if source_recurrence != target_recurrence:
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
        
        # Track operation details
        operation_details = {
            'add_success': 0,
            'add_failed': 0,
            'update_success': 0,
            'update_failed': 0,
            'delete_success': 0,
            'delete_failed': 0
        }
        
        # Add new events
        logger.info(f"🎯 Adding {len(to_add)} new events...")
        for event in to_add:
            if self.writer.create_event(target_calendar_id, event):
                successful += 1
                operation_details['add_success'] += 1
            else:
                failed += 1
                operation_details['add_failed'] += 1
        
        # Update existing events
        logger.info(f"🔄 Updating {len(to_update)} existing events...")
        for source_event, target_event in to_update:
            event_id = target_event.get('id')
            if self.writer.update_event(target_calendar_id, event_id, source_event):
                successful += 1
                operation_details['update_success'] += 1
            else:
                failed += 1
                operation_details['update_failed'] += 1
        
        # Delete removed events
        logger.info(f"🗑️ Deleting {len(to_delete)} obsolete events...")
        for event in to_delete:
            event_id = event.get('id')
            if self.writer.delete_event(target_calendar_id, event_id):
                successful += 1
                operation_details['delete_success'] += 1
            else:
                failed += 1
                operation_details['delete_failed'] += 1
        
        # Log operation details
        self.structured_logger.log_sync_event('sync_operations_completed', operation_details)
        
        return {
            'success': True,
            'message': f'Sync complete: {successful}/{total} operations successful',
            'successful_operations': successful,
            'failed_operations': failed,
            'added': len(to_add),
            'updated': len(to_update),
            'deleted': len(to_delete),
            'operation_details': operation_details,
            'safeguards_active': config.MASTER_CALENDAR_PROTECTION
        }
    
    def get_status(self) -> Dict:
        """Get current sync status"""
        with self.sync_lock:
            return {
                'last_sync_time': self.last_sync_time,
                'last_sync_result': self.last_sync_result,
                'sync_in_progress': self.sync_in_progress,
                'rate_limit_remaining': config.MAX_SYNC_REQUESTS_PER_HOUR - len(self.sync_request_times),
                'circuit_breaker_state': self.circuit_breaker.state,
                'total_syncs': len(self.history.history),
                'metrics_summary': self.metrics.get_metrics_summary()
            }
