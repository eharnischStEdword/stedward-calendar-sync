# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Sync Engine - Complete Working Version with Category Updates and Central Time + Occurrence Support
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from threading import Lock
import hashlib
import json
import pytz

import config
from cal_ops.reader import CalendarReader
from cal_ops.writer import CalendarWriter
from utils.logger import StructuredLogger
from utils.metrics import MetricsCollector
from utils.circuit_breaker import CircuitBreaker
from sync.history import SyncHistory
from sync.validator import SyncValidator
from utils.timezone import get_central_time, format_central_time
from sync.change_tracker import ChangeTracker

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
        
        # Sync state for resumable operations
        self.sync_state = {
            'in_progress': False,
            'phase': None,  # 'add', 'update', 'delete'
            'progress': 0,
            'total': 0,
            'last_checkpoint': None
        }
        
        # Rate limiting
        self.sync_request_times = []
        
        # Enhanced features
        self.structured_logger = StructuredLogger(__name__)
        self.metrics = MetricsCollector()
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=300)
        self.history = SyncHistory()
        self.validator = SyncValidator()
        
        # Change tracking for efficient syncing
        self.change_tracker = ChangeTracker()
    
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
        """Actual sync implementation with timeout protection"""
        start_time = get_central_time()
        
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
        self.sync_request_times.append(get_central_time())
        
        logger.info("🚀 Starting calendar sync")
        self.structured_logger.log_sync_event('sync_started', {
            'timestamp': get_central_time().isoformat()
        })
        
        # Use efficient change-based sync if cache is valid
        if self.change_tracker.is_cache_valid():
            logger.info("🔄 Using efficient change-based sync")
            return self._do_change_based_sync()
        else:
            logger.info("🔄 Cache invalid or missing - using full sync")
            return self._do_full_sync()
        
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
            
            # Get events - ALWAYS use regular events, not instances to avoid duplicates
            source_events = self.reader.get_public_events(source_id, include_instances=False)
            target_events = self.reader.get_calendar_events(target_id)
            
            if source_events is None or target_events is None:
                return {"error": "Failed to retrieve calendar events"}
            
            logger.info(f"📊 Retrieved {len(source_events)} source events and {len(target_events)} target events")
            
            # Debug event signatures for troubleshooting
            self._debug_event_signatures(source_events[:5], "SOURCE")
            self._debug_event_signatures(target_events[:5], "TARGET")
            
            # Build target map
            target_map = self._build_event_map(target_events)
            logger.info(f"Built target map with {len(target_map)} unique events")
            
            # Determine operations needed - don't check instances to avoid duplicates
            to_add, to_update, to_delete = self._determine_sync_operations(
                source_events, target_events, target_map, check_instances=False
            )
            
            # Skip instance sync to prevent duplicates
            instance_ops = {'added': 0, 'updated': 0, 'deleted': 0}
            
            logger.info(f"📋 SYNC PLAN: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
            
            # Debug what we're planning to do
            if to_add:
                logger.info("📝 Events to ADD:")
                for event in to_add[:3]:  # Log first 3
                    sig = self._create_event_signature(event)
                    logger.info(f"  - {event.get('subject')} | Signature: {sig}")
            
            if to_update:
                logger.info("🔄 Events to UPDATE:")
                for source_event, target_event in to_update[:3]:  # Log first 3
                    sig = self._create_event_signature(source_event)
                    logger.info(f"  - {source_event.get('subject')} | Signature: {sig}")
            
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
                    'added': len(to_add) + instance_ops['added'],
                    'updated': len(to_update) + instance_ops['updated'],
                    'deleted': len(to_delete) + instance_ops['deleted']
                }
            else:
                # Initialize sync state for progress tracking
                total_ops = len(to_add) + len(to_update) + len(to_delete)
                self.sync_state.update({
                    'in_progress': True,
                    'phase': None,
                    'progress': 0,
                    'total': total_ops,
                    'last_checkpoint': get_central_time()
                })
                
                # Execute sync operations
                result = self._execute_sync_operations_batch(target_id, to_add, to_update, to_delete)

                # -----------------------------------------------------------------
                # Handle cancelled instances of recurring events (delete-only)
                # -----------------------------------------------------------------
                if config.SYNC_OCCURRENCE_EXCEPTIONS:
                    cancelled_deleted = self._handle_cancelled_occurrences(source_id, target_id)
                    if cancelled_deleted:
                        # Update result counters
                        result['deleted'] += cancelled_deleted
                        if 'operation_details' in result:
                            result['operation_details']['delete_success'] += cancelled_deleted

                
                # Add instance operation counts
                result['added'] += instance_ops['added']
                result['updated'] += instance_ops['updated']
                result['deleted'] += instance_ops['deleted']
                
                # Validate sync result if successful
                if result.get('success') and not config.DRY_RUN_MODE:
                    logger.info("Validating sync results...")
                    fresh_source = self.reader.get_public_events(source_id, include_instances=False)
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
            duration = (get_central_time() - start_time).total_seconds()
            result['duration'] = duration
            
            # Check for timeout - if sync is taking too long, log warning
            if duration > 120:  # 2 minutes
                logger.warning(f"⚠️ Sync took {duration:.1f}s - approaching timeout limit")
            if duration > 180:  # 3 minutes
                logger.error(f"🚨 Sync took {duration:.1f}s - may cause worker timeout")
            
            # Update sync state
            with self.sync_lock:
                self.last_sync_time = get_central_time()
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
            duration = (get_central_time() - start_time).total_seconds()
            
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
                # Clear sync state
                self.sync_state.update({
                    'in_progress': False,
                    'phase': None,
                    'progress': 0,
                    'total': 0,
                    'last_checkpoint': None
                })
    
    def _do_change_based_sync(self) -> Dict:
        """Efficient sync using change detection"""
        start_time = get_central_time()
        
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
            
            # Get current source events
            source_events = self.reader.get_public_events(source_id, include_instances=False)
            if source_events is None:
                return {"error": "Failed to retrieve source calendar events"}
            
            logger.info(f"📊 Retrieved {len(source_events)} source events")
            
            # Detect changes using change tracker
            changes = self.change_tracker.detect_changes(source_events)
            
            # Only process events that have changed
            events_to_sync = changes['added'] + changes['updated']
            events_to_delete = changes['deleted']
            
            if not events_to_sync and not events_to_delete:
                logger.info("✅ No changes detected - sync complete")
                return {
                    'success': True,
                    'message': 'No changes detected',
                    'added': 0,
                    'updated': 0,
                    'deleted': 0,
                    'duration': (get_central_time() - start_time).total_seconds(),
                    'change_based': True
                }
            
            logger.info(f"🔄 Processing {len(events_to_sync)} changed events and {len(events_to_delete)} deletions")
            
            # Get target events for mapping (only for updates)
            target_events = self.reader.get_calendar_events(target_id)
            if target_events is None:
                return {"error": "Failed to retrieve target calendar events"}
            
            target_map = self._build_event_map(target_events)
            
            # Prepare operations
            to_add = changes['added']
            to_update = []
            to_delete = []
            
            # Map updates to target events
            for updated_event in changes['updated']:
                signature = self._create_event_signature(updated_event)
                if signature in target_map:
                    to_update.append((updated_event, target_map[signature]))
                else:
                    # If not found in target, treat as new
                    to_add.append(updated_event)
            
            # Map deletions to target events
            for deleted_event in changes['deleted']:
                signature = self._create_event_signature(deleted_event)
                if signature in target_map:
                    to_delete.append(target_map[signature])
            
            # Execute operations
            result = self._execute_sync_operations_batch(target_id, to_add, to_update, to_delete)
            
            # Update cache with current events
            self.change_tracker.update_cache(source_events)
            
            # Add change-based info to result
            result['change_based'] = True
            result['cache_stats'] = self.change_tracker.get_cache_stats()
            
            return result
            
        except Exception as e:
            import traceback
            duration = (get_central_time() - start_time).total_seconds()
            
            error_result = {
                'success': False,
                'message': f'Change-based sync failed: {str(e)}',
                'error': str(e),
                'traceback': traceback.format_exc(),
                'duration': duration,
                'change_based': True
            }
            
            logger.error(f"💥 Change-based sync error after {duration:.2f} seconds: {error_result}")
            return error_result
    
    def _do_full_sync(self) -> Dict:
        """Full sync (original method) - used when cache is invalid"""
        start_time = get_central_time()
        
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
            
            # Get events - ALWAYS use regular events, not instances to avoid duplicates
            source_events = self.reader.get_public_events(source_id, include_instances=False)
            target_events = self.reader.get_calendar_events(target_id)
            
            if source_events is None or target_events is None:
                return {"error": "Failed to retrieve calendar events"}
            
            logger.info(f"📊 Retrieved {len(source_events)} source events and {len(target_events)} target events")
            
            # Debug event signatures for troubleshooting
            self._debug_event_signatures(source_events[:5], "SOURCE")
            self._debug_event_signatures(target_events[:5], "TARGET")
            
            # Build target map
            target_map = self._build_event_map(target_events)
            logger.info(f"Built target map with {len(target_map)} unique events")
            
            # Determine operations needed - don't check instances to avoid duplicates
            to_add, to_update, to_delete = self._determine_sync_operations(
                source_events, target_events, target_map, check_instances=False
            )
            
            # Skip instance sync to prevent duplicates
            instance_ops = {'added': 0, 'updated': 0, 'deleted': 0}
            
            logger.info(f"📋 SYNC PLAN: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
            
            # Debug what we're planning to do
            if to_add:
                logger.info("📝 Events to ADD:")
                for event in to_add[:3]:  # Log first 3
                    sig = self._create_event_signature(event)
                    logger.info(f"  - {event.get('subject')} | Signature: {sig}")
            
            if to_update:
                logger.info("🔄 Events to UPDATE:")
                for source_event, target_event in to_update[:3]:  # Log first 3
                    sig = self._create_event_signature(source_event)
                    logger.info(f"  - {source_event.get('subject')} | Signature: {sig}")
            
            if to_delete:
                logger.info("🗑️ Events to DELETE:")
                for event in to_delete[:3]:  # Log first 3
                    sig = self._create_event_signature(event)
                    logger.info(f"  - {event.get('subject')} | Signature: {sig}")
            
            # Check dry run mode
            if config.DRY_RUN_MODE:
                logger.info("🔍 DRY RUN MODE - No actual changes will be made")
                return {
                    'success': True,
                    'message': 'Dry run completed - no changes made',
                    'dry_run': True,
                    'added': len(to_add) + instance_ops['added'],
                    'updated': len(to_update) + instance_ops['updated'],
                    'deleted': len(to_delete) + instance_ops['deleted']
                }
            else:
                # Execute sync operations
                result = self._execute_sync_operations_batch(target_id, to_add, to_update, to_delete)

                # -----------------------------------------------------------------
                # Handle cancelled instances of recurring events (delete-only)
                # -----------------------------------------------------------------
                if config.SYNC_OCCURRENCE_EXCEPTIONS:
                    cancelled_deleted = self._handle_cancelled_occurrences(source_id, target_id)
                    if cancelled_deleted:
                        # Update result counters
                        result['deleted'] += cancelled_deleted
                        if 'operation_details' in result:
                            result['operation_details']['delete_success'] += cancelled_deleted

                
                # Add instance operation counts
                result['added'] += instance_ops['added']
                result['updated'] += instance_ops['updated']
                result['deleted'] += instance_ops['deleted']
                
                # Validate sync result if successful
                if result.get('success') and not config.DRY_RUN_MODE:
                    logger.info("Validating sync results...")
                    fresh_source = self.reader.get_public_events(source_id, include_instances=False)
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
            duration = (get_central_time() - start_time).total_seconds()
            result['duration'] = duration
            
            # Check for timeout - if sync is taking too long, log warning
            if duration > 120:  # 2 minutes
                logger.warning(f"⚠️ Sync took {duration:.1f}s - approaching timeout limit")
            if duration > 180:  # 3 minutes
                logger.error(f"🚨 Sync took {duration:.1f}s - may cause worker timeout")
            
            # Update sync state
            with self.sync_lock:
                self.last_sync_time = get_central_time()
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
            duration = (get_central_time() - start_time).total_seconds()
            
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
            if event.get('isCancelled'):
                logger.info(f"      CANCELLED: True")
    
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
        current_time = get_central_time()
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
        """Normalize datetime string for matching"""
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
        """Create unique signature for an event"""
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
            # Skip occurrences entirely when not syncing them
            return f"skip:occurrence:{subject}"
        
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
        """Build a map of events by signature"""
        event_map = {}
        
        for event in events:
            signature = self._create_event_signature(event)
            
            # Skip occurrences entirely
            if signature.startswith("skip:occurrence:"):
                continue
            
            if signature in event_map:
                # Keep the newer event based on creation time
                existing = event_map[signature]
                existing_created = existing.get('createdDateTime', '')
                new_created = event.get('createdDateTime', '')
                if new_created > existing_created:
                    logger.warning(f"Signature collision! Keeping newer event for '{signature}'")
                    event_map[signature] = event
                else:
                    logger.warning(f"Signature collision! Keeping existing event for '{signature}'")
            else:
                event_map[signature] = event
        
        return event_map
    
    def _determine_sync_operations(
        self, 
        source_events: List[Dict], 
        target_events: List[Dict],
        target_map: Dict[str, Dict],
        check_instances: bool = False
    ) -> Tuple[List[Dict], List[Tuple[Dict, Dict]], List[Dict]]:
        """Determine what operations are needed"""
        to_add = []
        to_update = []
        
        # Make a copy of target_map for tracking deletions
        remaining_targets = target_map.copy()
        
        logger.info(f"🔍 Analyzing {len(source_events)} source events against {len(target_map)} target events")
        
        for source_event in source_events:
            signature = self._create_event_signature(source_event)
            subject = source_event.get('subject', 'No subject')
            
            # Skip occurrences entirely
            if signature.startswith("skip:occurrence:"):
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
        
        # Remaining events in target should be deleted (not in source anymore)
        to_delete = list(remaining_targets.values())
        
        logger.info(f"📊 Operation summary:")
        logger.info(f"  - {len(to_add)} events to ADD")
        logger.info(f"  - {len(to_update)} events to UPDATE") 
        logger.info(f"  - {len(to_delete)} events to DELETE")
        logger.info(f"  - {len(remaining_targets)} unmatched target events")
        
        return to_add, to_update, to_delete
    
    def _sync_recurring_exceptions(self, source_id: str, target_id: str) -> Dict:
        """
        Sync exceptions for recurring events - DISABLED to prevent duplicates
        Returns dict with counts of instance operations
        """
        # Always return zeros since we're disabling this feature
        return {'added': 0, 'updated': 0, 'deleted': 0}

    # ------------------------------------------------------------------
    # NEW: Cancelled Occurrence Handling
    # ------------------------------------------------------------------
    def _handle_cancelled_occurrences(self, source_id: str, target_id: str) -> int:
        """Delete occurrences that are cancelled on the master calendar.

        Returns:
            Number of occurrences successfully deleted on the target.
        """
        try:
            window_start = (get_central_time() - timedelta(days=1)).astimezone(pytz.UTC)
            window_end = (get_central_time() + timedelta(days=config.OCCURRENCE_SYNC_DAYS)).astimezone(pytz.UTC)

            # ISO format strings expected by Graph
            start_str = window_start.isoformat()
            end_str = window_end.isoformat()

            # Fetch expanded instances (occurrences) within the window
            source_instances = self.reader.get_calendar_instances(source_id, start_str, end_str) or []
            target_instances = self.reader.get_calendar_instances(target_id, start_str, end_str) or []

            logger.info(f"🔍 Checking for cancelled occurrences:")
            logger.info(f"  Source instances: {len(source_instances)}")
            logger.info(f"  Target instances: {len(target_instances)}")

            # Build quick-lookup sets keyed by subject + start for cancelled vs active occurrences
            def _make_key(inst):
                subj = (inst.get('subject') or '').strip().lower()
                dt = inst.get('originalStart') or inst.get('start', {}).get('dateTime', '')
                # Normalise datetime string (strip timezone markers to compare raw moment)
                if dt.endswith('Z'):
                    dt = dt[:-1]
                return f"{subj}|{dt}"

            # Find cancelled instances in source
            cancelled_keys = {
                _make_key(i) for i in source_instances if i.get('isCancelled', False)
            }

            # Find instances that exist in target but not in source (missing instances)
            source_keys = {_make_key(i) for i in source_instances if not i.get('isCancelled', False)}
            target_keys = {_make_key(i) for i in target_instances if not i.get('isCancelled', False)}
            
            # Instances that should be deleted (in target but not in source)
            missing_in_source = target_keys - source_keys

            logger.info(f"  Cancelled instances found: {len(cancelled_keys)}")
            logger.info(f"  Missing in source: {len(missing_in_source)}")

            # No cancelled occurrences? Fast exit
            if not cancelled_keys and not missing_in_source:
                logger.info("  No cancelled or missing instances found")
                return 0

            deleted_count = 0

            # Handle explicitly cancelled instances
            for inst in target_instances:
                if inst.get('isCancelled', False):
                    # Already cancelled; skip
                    continue

                key = _make_key(inst)
                if key in cancelled_keys:
                    series_id = inst.get('seriesMasterId')
                    occ_date = inst.get('originalStart') or inst.get('start', {}).get('dateTime', '')
                    if series_id and occ_date:
                        logger.info(f"  🗑️ Deleting cancelled instance: {inst.get('subject')} on {occ_date}")
                        if self.writer.delete_occurrence(target_id, series_id, occ_date):
                            deleted_count += 1

            # Handle missing instances (deleted from source but still in target)
            for inst in target_instances:
                if inst.get('isCancelled', False):
                    continue

                key = _make_key(inst)
                if key in missing_in_source:
                    series_id = inst.get('seriesMasterId')
                    occ_date = inst.get('originalStart') or inst.get('start', {}).get('dateTime', '')
                    if series_id and occ_date:
                        logger.info(f"  🗑️ Deleting missing instance: {inst.get('subject')} on {occ_date}")
                        if self.writer.delete_occurrence(target_id, series_id, occ_date):
                            deleted_count += 1

            if deleted_count:
                logger.info(f"🗑️ Deleted {deleted_count} cancelled/missing occurrences to keep calendars in sync")

            return deleted_count
        except Exception as e:
            logger.error(f"Error while handling cancelled occurrences: {e}")
            return 0
    
    def _needs_update(self, source_event: Dict, target_event: Dict) -> bool:
        """Check if an event needs updating - OPTIMIZED FOR PERFORMANCE"""
        subject = source_event.get('subject', 'Unknown')
        
        # Quick check: compare modification times first (fastest check)
        source_modified = source_event.get('lastModifiedDateTime')
        target_modified = target_event.get('lastModifiedDateTime')
        
        if source_modified == target_modified:
            # If modification times match, no changes needed
            return False
        
        # If we get here, there are changes - do detailed comparison
        logger.info(f"🔍 Checking if '{subject}' needs update (modified times differ)")
        
        # Compare key fields
        if source_event.get('subject') != target_event.get('subject'):
            logger.info(f"  📝 Subject changed: '{source_event.get('subject')}' != '{target_event.get('subject')}'")
            return True
        
        if source_event.get('start') != target_event.get('start'):
            logger.info(f"  🕐 Start time changed for '{subject}'")
            return True
        
        if source_event.get('end') != target_event.get('end'):
            logger.info(f"  🕐 End time changed for '{subject}'")
            return True
        
        if source_event.get('isAllDay') != target_event.get('isAllDay'):
            logger.info(f"  📅 All-day flag changed for '{subject}'")
            return True
        
        # Compare categories
        source_categories = set(source_event.get('categories', []))
        target_categories = set(target_event.get('categories', []))
        
        if source_categories != target_categories:
            logger.info(f"  ✅ CATEGORIES CHANGED for '{subject}': {source_categories} != {target_categories}")
            return True
        
        # Compare location
        source_location = source_event.get('location', {})
        target_location = target_event.get('location', {})
        
        # Normalize location comparison (could be string or dict)
        source_loc_str = source_location.get('displayName', '') if isinstance(source_location, dict) else str(source_location)
        target_loc_str = target_location.get('displayName', '') if isinstance(target_location, dict) else str(target_location)
        
        if source_loc_str != target_loc_str:
            logger.info(f"  ✅ LOCATION CHANGED for '{subject}': '{source_loc_str}' != '{target_loc_str}'")
            return True
        
        # Compare body content
        source_body = source_event.get('body', {}).get('content', '') if isinstance(source_event.get('body'), dict) else ''
        target_body = target_event.get('body', {}).get('content', '') if isinstance(target_event.get('body'), dict) else ''
        
        if source_body != target_body:
            logger.info(f"  ✅ BODY CONTENT CHANGED for '{subject}'")
            return True
        
        # For recurring events, check if recurrence pattern changed
        if source_event.get('type') == 'seriesMaster':
            source_recurrence = source_event.get('recurrence', {})
            target_recurrence = target_event.get('recurrence', {})
            if source_recurrence != target_recurrence:
                logger.info(f"  ✅ RECURRENCE PATTERN CHANGED for '{subject}'")
                return True
        
        # No changes detected (modification times might be different due to timezone or other metadata)
        logger.info(f"  ➡️  No changes detected for '{subject}' (modification times differ but content is same)")
        return False
    
    def _execute_sync_operations_batch(
        self, 
        target_calendar_id: str,
        to_add: List[Dict],
        to_update: List[Tuple[Dict, Dict]],
        to_delete: List[Dict]
    ) -> Dict:
        """Execute sync operations in small chunks with checkpoints"""
        successful = 0
        failed = 0
        
        operation_details = {
            'add_success': 0,
            'add_failed': 0,
            'update_success': 0,
            'update_failed': 0,
            'delete_success': 0,
            'delete_failed': 0
        }
        
        # Process in very small batches to avoid timeouts
        BATCH_SIZE = 5
        
        # Phase 1: Additions
        if to_add:
            self.sync_state['phase'] = 'add'
            for i in range(0, len(to_add), BATCH_SIZE):
                batch = to_add[i:i + BATCH_SIZE]
                logger.info(f"Adding batch {i//BATCH_SIZE + 1}/{(len(to_add) + BATCH_SIZE - 1)//BATCH_SIZE}")
                
                batch_result = self.writer.batch_create_events(target_calendar_id, batch)
                successful += batch_result['successful']
                failed += batch_result['failed']
                operation_details['add_success'] += batch_result['successful']
                operation_details['add_failed'] += batch_result['failed']
                
                # Update progress
                self.sync_state['progress'] = i + len(batch)
                self.sync_state['last_checkpoint'] = get_central_time()
                
                # Yield control to prevent timeout
                time.sleep(0.1)
        
        # Phase 2: Updates (most time-consuming)
        if to_update:
            self.sync_state['phase'] = 'update'
            for i in range(0, len(to_update), BATCH_SIZE):
                batch = to_update[i:i + BATCH_SIZE]
                logger.info(f"Updating batch {i//BATCH_SIZE + 1}/{(len(to_update) + BATCH_SIZE - 1)//BATCH_SIZE}")
                
                for source_event, target_event in batch:
                    event_id = target_event.get('id')
                    if self.writer.update_event(target_calendar_id, event_id, source_event):
                        successful += 1
                        operation_details['update_success'] += 1
                    else:
                        failed += 1
                        operation_details['update_failed'] += 1
                
                # Update progress
                self.sync_state['progress'] = i + len(batch)
                self.sync_state['last_checkpoint'] = get_central_time()
                
                # Longer pause between update batches
                time.sleep(0.5)
        
        # Phase 3: Deletions
        if to_delete:
            self.sync_state['phase'] = 'delete'
            for i in range(0, len(to_delete), BATCH_SIZE):
                batch = to_delete[i:i + BATCH_SIZE]
                event_ids = [event.get('id') for event in batch]
                
                batch_result = self.writer.batch_delete_events(target_calendar_id, event_ids)
                successful += batch_result['successful']
                failed += batch_result['failed']
                operation_details['delete_success'] += batch_result['successful']
                operation_details['delete_failed'] += batch_result['failed']
                
                # Update progress
                self.sync_state['progress'] = i + len(batch)
                self.sync_state['last_checkpoint'] = get_central_time()
                
                time.sleep(0.1)
        
        # Clear state
        self.sync_state['phase'] = None
        self.sync_state['progress'] = 0
        
        total = len(to_add) + len(to_update) + len(to_delete)
        
        return {
            'success': True,
            'message': f'Sync complete: {successful}/{total} operations successful',
            'successful_operations': successful,
            'failed_operations': failed,
            'added': operation_details['add_success'],
            'updated': operation_details['update_success'],
            'deleted': operation_details['delete_success'],
            'operation_details': operation_details,
            'safeguards_active': config.MASTER_CALENDAR_PROTECTION
        }
    
    def get_status(self) -> Dict:
        """Get current sync status"""
        with self.sync_lock:
            status = {
                'last_sync_time': self.last_sync_time.isoformat() if self.last_sync_time else None,
                'last_sync_time_display': format_central_time(self.last_sync_time) if self.last_sync_time else 'Never',
                'last_sync_result': self.last_sync_result,
                'sync_in_progress': self.sync_in_progress,
                'sync_progress': {
                    'phase': self.sync_state.get('phase'),
                    'progress': self.sync_state.get('progress', 0),
                    'total': self.sync_state.get('total', 0),
                    'percent': round((self.sync_state.get('progress', 0) / max(self.sync_state.get('total', 1), 1)) * 100, 1)
                } if self.sync_in_progress else None,
                'rate_limit_remaining': config.MAX_SYNC_REQUESTS_PER_HOUR - len(self.sync_request_times),
                'circuit_breaker_state': self.circuit_breaker.state,
                'total_syncs': len(self.history.history),
                'authenticated': self.auth.is_authenticated() if self.auth else False,
                'scheduler_running': True,  # Placeholder - scheduler status
                'timezone': 'America/Chicago',
                'current_time': get_central_time().isoformat(),
                'occurrence_sync_enabled': config.SYNC_OCCURRENCE_EXCEPTIONS,
                'change_tracker': self.change_tracker.get_cache_stats()
            }
            return status
