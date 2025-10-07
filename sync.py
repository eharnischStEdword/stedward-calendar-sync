# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Consolidated Sync Logic - Complete calendar synchronization engine
"""
import logging
import time
import threading
import schedule
import json
import os
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Set, Optional
from threading import Lock
from collections import defaultdict
import statistics
import pytz

def get_utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

import config
from calendar_ops import CalendarReader, CalendarWriter
from utils import DateTimeUtils, CircuitBreaker, CircuitBreakerOpenError, RetryUtils, structured_logger
from signature_utils import generate_event_signature, normalize_subject, normalize_datetime, normalize_location

logger = logging.getLogger(__name__)


class ChangeTracker:
    """Tracks changes to calendar events for efficient syncing"""
    
    def __init__(self, cache_file: str = '/data/event_cache.json'):
        self.cache_file = cache_file
        self.event_cache = {}  # signature -> event_data
        self.last_sync_time = None
        # DISABLED FOR DEBUGGING - Force empty cache
        logger.warning("⚠️ CACHE DISABLED FOR DEBUGGING - Starting with empty cache")
        # self._load_cache()
    
    def _load_cache(self):
        """Load cached event data from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    self.event_cache = data.get('events', {})
                    self.last_sync_time = data.get('last_sync_time')
                    logger.info(f"✅ Loaded {len(self.event_cache)} cached events")
            else:
                logger.info("No event cache found - will build on first sync")
        except Exception as e:
            logger.warning(f"Failed to load event cache: {e}")
            self.event_cache = {}
    
    def _save_cache(self):
        """Save event cache to disk"""
        try:
            data = {
                'events': self.event_cache,
                'last_sync_time': DateTimeUtils.get_central_time().isoformat(),
                'cache_version': '1.0'
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"✅ Saved {len(self.event_cache)} events to cache")
        except Exception as e:
            logger.error(f"Failed to save event cache: {e}")
    
    def _create_event_signature(self, event: Dict) -> str:
        """Create a unique signature for an event - Uses shared signature utilities"""
        return generate_event_signature(event)
    
    def detect_changes(self, current_events: List[Dict]) -> Dict:
        """
        Detect changes between cached events and current events
        
        Returns:
            Dict with 'added', 'updated', 'deleted', 'unchanged' lists
        """
        current_signatures = set()
        changes = {
            'added': [],
            'updated': [],
            'deleted': [],
            'unchanged': []
        }
        
        # Process current events
        for event in current_events:
            signature = self._create_event_signature(event)
            current_signatures.add(signature)
            
            if signature in self.event_cache:
                # Event exists - check if changed
                cached_event = self.event_cache[signature]
                if self._event_changed(event, cached_event):
                    changes['updated'].append(event)
                    logger.debug(f"📝 Event changed: {event.get('subject')}")
                else:
                    changes['unchanged'].append(event)
                    logger.debug(f"✅ Event unchanged: {event.get('subject')}")
            else:
                # New event
                changes['added'].append(event)
                logger.debug(f"➕ New event: {event.get('subject')}")
        
        # Find deleted events (in cache but not in current)
        for signature, cached_event in self.event_cache.items():
            if signature not in current_signatures:
                changes['deleted'].append(cached_event)
                logger.debug(f"🗑️ Deleted event: {cached_event.get('subject')}")
        
        logger.info(f"🔍 Change detection summary:")
        logger.info(f"  - {len(changes['added'])} new events")
        logger.info(f"  - {len(changes['updated'])} modified events")
        logger.info(f"  - {len(changes['deleted'])} deleted events")
        logger.info(f"  - {len(changes['unchanged'])} unchanged events")
        
        return changes
    
    def _event_changed(self, event1: Dict, event2: Dict) -> bool:
        """Compare two events to see if they're different"""
        # Compare key fields that matter for sync
        fields_to_compare = [
            'subject', 'body', 'start', 'end', 'location', 
            'categories', 'showAs', 'isCancelled'
        ]
        
        for field in fields_to_compare:
            val1 = event1.get(field)
            val2 = event2.get(field)
            
            if val1 != val2:
                logger.debug(f"Field '{field}' changed: {val1} != {val2}")
                return True
        
        return False
    
    def update_cache(self, events: List[Dict]):
        """Update the cache with current events"""
        new_cache = {}
        
        for event in events:
            signature = self._create_event_signature(event)
            new_cache[signature] = event
        
        self.event_cache = new_cache
        self.last_sync_time = DateTimeUtils.get_central_time()
        self._save_cache()
        
        logger.info(f"✅ Updated cache with {len(new_cache)} events")
    
    def get_cache_stats(self) -> Dict:
        """Get statistics about the cache"""
        # Handle last_sync_time safely - it might be a string or datetime
        last_sync_time_iso = None
        if self.last_sync_time:
            if isinstance(self.last_sync_time, str):
                last_sync_time_iso = self.last_sync_time
            else:
                try:
                    last_sync_time_iso = self.last_sync_time.isoformat()
                except AttributeError:
                    last_sync_time_iso = str(self.last_sync_time)
        
        return {
            'cached_events': len(self.event_cache),
            'last_sync_time': last_sync_time_iso,
            'cache_file': self.cache_file,
            'cache_exists': os.path.exists(self.cache_file)
        }
    
    def clear_cache(self):
        """Clear the event cache"""
        self.event_cache = {}
        self.last_sync_time = None
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
                logger.info("✅ Cleared event cache")
        except Exception as e:
            logger.error(f"Failed to clear cache file: {e}")
    
    def is_cache_valid(self) -> bool:
        """Check if the cache is valid and recent"""
        if not self.last_sync_time:
            return False
        
        try:
            last_sync = datetime.fromisoformat(self.last_sync_time.replace('Z', '+00:00'))
            now = DateTimeUtils.get_central_time()
            age = now - last_sync
            
            # Cache is valid if less than 24 hours old
            return age < timedelta(hours=24)
        except:
            return False
    
    def _normalize_subject(self, subject: str) -> str:
        """Normalize event subject for matching - Uses shared utilities"""
        return normalize_subject(subject)
    
    def _normalize_datetime(self, dt_str: str) -> str:
        """Normalize datetime string for matching - Uses shared utilities"""
        return normalize_datetime(dt_str)


class SyncHistory:
    """Manages historical sync data and statistics"""
    
    def __init__(self, max_entries: int = 100):
        self.history: List[Dict] = []
        self.max_entries = max_entries
    
    def add_entry(self, sync_result: Dict):
        """Add a sync result to history"""
        entry = {
            'timestamp': DateTimeUtils.get_central_time(),
            'result': sync_result,
            'duration': sync_result.get('duration', 0),
            'success': sync_result.get('success', False),
            'operations': {
                'added': sync_result.get('added', 0),
                'updated': sync_result.get('updated', 0),
                'deleted': sync_result.get('deleted', 0),
                'failed': sync_result.get('failed_operations', 0)
            },
            'dry_run': sync_result.get('dry_run', False),
            'error': sync_result.get('error', None),
            'validation': sync_result.get('validation', None)
        }
        
        self.history.append(entry)
        
        # Trim history if it exceeds max entries
        if len(self.history) > self.max_entries:
            self.history.pop(0)
    
    def get_statistics(self, hours: int = 24) -> Dict:
        """Calculate statistics for the given time period"""
        cutoff_time = DateTimeUtils.get_central_time() - timedelta(hours=hours)
        recent_entries = [
            entry for entry in self.history 
            if entry['timestamp'] > cutoff_time
        ]
        
        if not recent_entries:
            return {
                'period_hours': hours,
                'total_syncs': 0,
                'successful_syncs': 0,
                'failed_syncs': 0,
                'success_rate': 0,
                'average_duration': 0,
                'total_operations': {
                    'added': 0,
                    'updated': 0,
                    'deleted': 0,
                    'failed': 0
                },
                'last_sync': None,
                'last_successful_sync': None,
                'validation_failures': 0
            }
        
        # Calculate basic counts
        successful_syncs = [e for e in recent_entries if e['success'] and not e['dry_run']]
        failed_syncs = [e for e in recent_entries if not e['success']]
        
        # Calculate durations (excluding dry runs)
        durations = [e['duration'] for e in successful_syncs if e['duration'] > 0]
        avg_duration = statistics.mean(durations) if durations else 0
        
        # Calculate total operations
        total_operations = defaultdict(int)
        for entry in successful_syncs:
            for op_type, count in entry['operations'].items():
                total_operations[op_type] += count
        
        # Find last syncs
        last_sync = recent_entries[-1] if recent_entries else None
        last_successful = next((e for e in reversed(recent_entries) if e['success']), None)
        
        # Count validation failures
        validation_failures = sum(
            1 for e in recent_entries 
            if e.get('validation') and not e['validation'].get('is_valid', True)
        )
        
        # Calculate percentiles for durations
        duration_percentiles = {}
        if durations:
            duration_percentiles = {
                'p50': statistics.median(durations),
                'p90': self._percentile(durations, 90),
                'p95': self._percentile(durations, 95),
                'min': min(durations),
                'max': max(durations)
            }
        
        return {
            'period_hours': hours,
            'total_syncs': len(recent_entries),
            'successful_syncs': len(successful_syncs),
            'failed_syncs': len(failed_syncs),
            'success_rate': len(successful_syncs) / len(recent_entries) * 100 if recent_entries else 0,
            'average_duration': avg_duration,
            'duration_percentiles': duration_percentiles,
            'total_operations': dict(total_operations),
            'last_sync': last_sync['timestamp'].isoformat() if last_sync else None,
            'last_successful_sync': last_successful['timestamp'].isoformat() if last_successful else None,
            'validation_failures': validation_failures,
            'dry_run_count': sum(1 for e in recent_entries if e.get('dry_run', False))
        }
    
    def get_recent_failures(self, limit: int = 10) -> List[Dict]:
        """Get recent failed syncs"""
        failures = [
            {
                'timestamp': entry['timestamp'].isoformat(),
                'error': entry.get('error', 'Unknown error'),
                'duration': entry.get('duration', 0)
            }
            for entry in reversed(self.history)
            if not entry['success']
        ]
        return failures[:limit]
    
    def get_hourly_breakdown(self, hours: int = 24) -> Dict[int, Dict]:
        """Get sync statistics broken down by hour"""
        cutoff_time = DateTimeUtils.get_central_time() - timedelta(hours=hours)
        hourly_stats = defaultdict(lambda: {
            'total': 0,
            'successful': 0,
            'failed': 0,
            'operations': defaultdict(int)
        })
        
        for entry in self.history:
            if entry['timestamp'] > cutoff_time:
                hour = entry['timestamp'].hour
                hourly_stats[hour]['total'] += 1
                
                if entry['success']:
                    hourly_stats[hour]['successful'] += 1
                    for op_type, count in entry['operations'].items():
                        hourly_stats[hour]['operations'][op_type] += count
                else:
                    hourly_stats[hour]['failed'] += 1
        
        # Convert to regular dict with all hours
        result = {}
        for hour in range(24):
            if hour in hourly_stats:
                stats = hourly_stats[hour]
                stats['operations'] = dict(stats['operations'])
                result[hour] = stats
            else:
                result[hour] = {
                    'total': 0,
                    'successful': 0,
                    'failed': 0,
                    'operations': {'added': 0, 'updated': 0, 'deleted': 0, 'failed': 0}
                }
        
        return result
    
    def get_operation_trends(self, days: int = 7) -> List[Dict]:
        """Get daily operation trends"""
        trends = []
        
        for i in range(days):
            date = DateTimeUtils.get_central_time().date() - timedelta(days=i)
            day_entries = [
                e for e in self.history
                if e['timestamp'].date() == date and e['success']
            ]
            
            daily_ops = defaultdict(int)
            for entry in day_entries:
                for op_type, count in entry['operations'].items():
                    daily_ops[op_type] += count
            
            trends.append({
                'date': date.isoformat(),
                'sync_count': len(day_entries),
                'operations': dict(daily_ops)
            })
        
        return list(reversed(trends))
    
    def clear_history(self):
        """Clear all history"""
        self.history.clear()
    
    def _percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile of a sorted list"""
        if not data:
            return 0
        
        sorted_data = sorted(data)
        index = (percentile / 100) * (len(sorted_data) - 1)
        
        if index.is_integer():
            return sorted_data[int(index)]
        else:
            lower = sorted_data[int(index)]
            upper = sorted_data[int(index) + 1]
            fraction = index - int(index)
            return lower + (upper - lower) * fraction


class SyncValidator:
    """Validates sync results to ensure data integrity and correctness"""
    
    def __init__(self):
        self.validation_rules = [
            self._validate_event_counts,
            self._validate_no_private_events,
            self._validate_event_categories,
            self._validate_event_dates,
            self._validate_no_duplicates,
            self._validate_recurring_events,
            self._validate_event_integrity,
            self._validate_all_day_events
        ]
    
    def validate_sync_result(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[bool, List[Tuple[str, bool]]]:
        """
        Validate that sync was successful
        
        Args:
            source_events: Events from source calendar (already filtered for public)
            target_events: Events from target calendar
            
        Returns:
            Tuple of (overall_valid, list of (check_name, passed) tuples)
        """
        validations = []
        
        for rule in self.validation_rules:
            check_name, passed, details = rule(source_events, target_events)
            validations.append((check_name, passed))
            
            if not passed:
                logger.warning(f"Validation failed: {check_name} - {details}")
        
        overall_valid = all(passed for _, passed in validations)
        
        return overall_valid, validations
    
    def _validate_event_counts(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate that event counts match within acceptable range"""
        source_count = len(source_events)
        target_count = len(target_events)
        
        # Allow small discrepancy for timing issues
        acceptable_diff = 2
        passed = abs(source_count - target_count) <= acceptable_diff
        
        details = f"Source: {source_count}, Target: {target_count}"
        
        return "event_count_match", passed, details
    
    def _validate_no_private_events(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Ensure no private events leaked to public calendar"""
        private_in_target = []
        
        for event in target_events:
            categories = event.get('categories', [])
            
            # Check for any privacy-indicating categories
            private_categories = {'Private', 'Confidential', 'Personal'}
            if any(cat in private_categories for cat in categories):
                private_in_target.append(event.get('subject', 'Unknown'))
            
            # Also check sensitivity field
            if event.get('sensitivity') in ['private', 'confidential']:
                private_in_target.append(event.get('subject', 'Unknown'))
        
        passed = len(private_in_target) == 0
        details = f"Found {len(private_in_target)} private events" if not passed else "No private events found"
        
        if private_in_target:
            details += f": {', '.join(private_in_target[:3])}"
        
        return "no_private_events", passed, details
    
    def _validate_event_categories(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate that public events have correct categories"""
        source_public = [e for e in source_events if 'Public' in e.get('categories', [])]
        target_public = [e for e in target_events if 'Public' in e.get('categories', [])]
        
        source_count = len(source_public)
        target_count = len(target_public)
        
        passed = source_count == target_count
        details = f"Source public: {source_count}, Target public: {target_count}"
        
        return "public_category_match", passed, details
    
    def _validate_event_dates(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate that event dates are reasonable"""
        now = DateTimeUtils.get_central_time()
        cutoff_date = now - timedelta(days=config.SYNC_CUTOFF_DAYS)
        
        # Check for events too far in the past
        old_events = []
        for event in target_events:
            start_time = event.get('start', {}).get('dateTime', '')
            if start_time:
                try:
                    event_date = DateTimeUtils.parse_graph_datetime(event.get('start', {}))
                    if event_date and event_date < cutoff_date:
                        old_events.append(event.get('subject', 'Unknown'))
                except:
                    pass
        
        passed = len(old_events) == 0
        details = f"Found {len(old_events)} events older than cutoff" if not passed else "All events within date range"
        
        if old_events:
            details += f": {', '.join(old_events[:3])}"
        
        return "event_date_range", passed, details
    
    def _validate_no_duplicates(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Check for duplicate events"""
        seen_subjects = set()
        duplicates = []
        
        for event in target_events:
            subject = event.get('subject', '').strip()
            if subject in seen_subjects:
                duplicates.append(subject)
            else:
                seen_subjects.add(subject)
        
        passed = len(duplicates) == 0
        details = f"Found {len(duplicates)} duplicate subjects" if not passed else "No duplicates found"
        
        if duplicates:
            details += f": {', '.join(duplicates[:3])}"
        
        return "no_duplicates", passed, details
    
    def _validate_recurring_events(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate recurring event handling"""
        source_recurring = [e for e in source_events if e.get('type') == 'seriesMaster']
        target_recurring = [e for e in target_events if e.get('type') == 'seriesMaster']
        
        source_count = len(source_recurring)
        target_count = len(target_recurring)
        
        passed = source_count == target_count
        details = f"Source recurring: {source_count}, Target recurring: {target_count}"
        
        return "recurring_event_match", passed, details
    
    def _validate_event_integrity(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate that key event properties are preserved"""
        issues = []
        
        # Create maps for comparison
        source_map = {e.get('subject', ''): e for e in source_events}
        target_map = {e.get('subject', ''): e for e in target_events}
        
        for subject in source_map:
            if subject in target_map:
                source_event = source_map[subject]
                target_event = target_map[subject]
                
                # Check start time
                source_start = source_event.get('start', {}).get('dateTime', '')
                target_start = target_event.get('start', {}).get('dateTime', '')
                
                if source_start != target_start:
                    issues.append(f"Start time mismatch for {subject}")
                
                # Check end time
                source_end = source_event.get('end', {}).get('dateTime', '')
                target_end = target_event.get('end', {}).get('dateTime', '')
                
                if source_end != target_end:
                    issues.append(f"End time mismatch for {subject}")
                
                # Check categories
                source_cats = set(source_event.get('categories', []))
                target_cats = set(target_event.get('categories', []))
                
                if source_cats != target_cats:
                    issues.append(f"Category mismatch for {subject}")
        
        passed = len(issues) == 0
        details = f"Found {len(issues)} integrity issues" if not passed else "All events have integrity"
        
        if issues:
            details += f": {', '.join(issues[:3])}"
        
        return "event_integrity", passed, details
    
    def _validate_all_day_events(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate that all-day events are properly handled"""
        issues = []
        
        # Check that all-day events in source are all-day in target
        source_all_day = [e for e in source_events if e.get('isAllDay', False)]
        target_all_day = [e for e in target_events if e.get('isAllDay', False)]
        
        # Create maps for comparison
        source_map = {e.get('subject', ''): e for e in source_all_day}
        target_map = {e.get('subject', ''): e for e in target_all_day}
        
        for subject in source_map:
            if subject in target_map:
                source_event = source_map[subject]
                target_event = target_map[subject]
                
                # Check that both are marked as all-day
                if not target_event.get('isAllDay', False):
                    issues.append(f"All-day event '{subject}' not marked as all-day in target")
                
                # Check that target uses date-only format for all-day events
                target_start = target_event.get('start', {})
                target_end = target_event.get('end', {})
                
                if 'dateTime' in target_start or 'dateTime' in target_end:
                    issues.append(f"All-day event '{subject}' uses dateTime format instead of date-only")
            else:
                issues.append(f"All-day event '{subject}' missing from target")
        
        passed = len(issues) == 0
        details = f"Found {len(issues)} all-day event issues" if not passed else "All all-day events properly formatted"
        
        if issues:
            details += f": {', '.join(issues[:3])}"
        
        return "all_day_event_handling", passed, details
    
    def _create_event_signature(self, event: Dict) -> str:
        """Create a unique signature for an event - Uses shared signature utilities"""
        return generate_event_signature(event)
    
    def _normalize_subject(self, subject: str) -> str:
        """Normalize event subject for matching - MUST MATCH ChangeTracker/SyncEngine"""
        if not subject:
            return ""
        # More aggressive normalization to handle Microsoft Graph variations
        normalized = ' '.join(subject.strip().lower().split())
        # Remove common punctuation that might vary
        normalized = normalized.replace('.', '').replace(',', '').replace(':', '').replace(';', '')
        return normalized
    
    def _normalize_datetime(self, dt_str: str) -> str:
        """Normalize datetime string for matching - MUST MATCH ChangeTracker/SyncEngine"""
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
    
    def validate_sync_operation(
        self,
        operation: str,
        source_count: int,
        target_count: int,
        operations_performed: Dict[str, int]
    ) -> bool:
        """Validate a specific sync operation"""
        if operation == 'create':
            expected_added = source_count - target_count
            actual_added = operations_performed.get('added', 0)
            return abs(expected_added - actual_added) <= 2
        
        elif operation == 'update':
            # Updates are harder to validate - just check if any were performed
            return operations_performed.get('updated', 0) >= 0
        
        elif operation == 'delete':
            expected_deleted = target_count - source_count
            actual_deleted = operations_performed.get('deleted', 0)
            return abs(expected_deleted - actual_deleted) <= 2
        
        return True
    
    def generate_validation_report(
        self,
        source_events: List[Dict],
        target_events: List[Dict],
        sync_result: Dict
    ) -> Dict:
        """Generate a comprehensive validation report"""
        is_valid, validations = self.validate_sync_result(source_events, target_events)
        
        return {
            'is_valid': is_valid,
            'validations': validations,
            'source_count': len(source_events),
            'target_count': len(target_events),
            'sync_operations': sync_result.get('operation_details', {}),
            'timestamp': DateTimeUtils.get_central_time().isoformat(),
            'warnings': [name for name, passed in validations if not passed]
        } 


class SyncEngine:
    """Core engine for calendar synchronization"""
    
    def generate_weekly_ranges(self, start_date, end_date):
        """Generate weekly date ranges for chunked syncing"""
        current = start_date
        while current < end_date:
            yield (current, min(current + timedelta(days=7), end_date))
            current += timedelta(days=7)
    
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
        self.sync_request_times = []  # Stores timestamps of sync requests within the last hour
        
        # Enhanced features
        self.structured_logger = structured_logger
        self.metrics = None  # Will be initialized if needed
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
        """Simple sync: Calendar A public events → Calendar B"""
        start_time = DateTimeUtils.get_central_time()
        
        # Track this sync request for rate limiting
        current_time = DateTimeUtils.get_central_time()
        self.sync_request_times.append(current_time)
        
        # Clean up old entries (older than 1 hour)
        cutoff_time = current_time - timedelta(hours=1)
        self.sync_request_times = [t for t in self.sync_request_times if t > cutoff_time]
        
        # Check rate limit
        if len(self.sync_request_times) > config.MAX_SYNC_REQUESTS_PER_HOUR:
            return {
                "error": f"Rate limit exceeded. Maximum {config.MAX_SYNC_REQUESTS_PER_HOUR} syncs per hour.",
                "rate_limit_remaining": 0,
                "retry_after": 3600  # seconds
            }
        
        # Check if already syncing
        with self.sync_lock:
            if self.sync_in_progress:
                return {"error": "Sync already in progress"}
            self.sync_in_progress = True
        
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
            
            # Define overall date range for sync
            start_date = DateTimeUtils.get_central_time() - timedelta(days=config.SYNC_CUTOFF_DAYS)
            end_date = DateTimeUtils.get_central_time() + timedelta(days=config.SYNC_LOOKAHEAD_DAYS)
            
            # Fetch events in weekly chunks
            source_events = []
            target_events = []
            
            for start, end in self.generate_weekly_ranges(start_date, end_date):
                logger.info(f"[Sync] Querying from {start.isoformat()} to {end.isoformat()}")
                week_source = self.reader.get_public_events(source_id, start=start, end=end, include_instances=False) or []
                week_target = self.reader.get_calendar_events(target_id, start=start, end=end) or []
                
                source_events.extend(week_source)
                target_events.extend(week_target)
            
            if source_events is None:
                return {"error": "Failed to retrieve source calendar events"}
            
            if target_events is None:
                return {"error": "Failed to retrieve target calendar events"}
            
            logger.info(f"📊 Retrieved {len(source_events)} source events and {len(target_events)} target events")
            
            # DIAGNOSTIC: Detailed sync analysis
            logger.info(f"🔍 Sync Analysis:")
            logger.info(f"  Source events (filtered): {len(source_events)}")
            logger.info(f"  Target events (from calendar): {len(target_events)}")
            logger.info(f"  Cached events: {len(self.change_tracker.event_cache)}")

            # When comparing signatures
            source_sigs = set([self._create_event_signature(e) for e in source_events])
            target_sigs = set([self._create_event_signature(e) for e in target_events])
            cached_sigs = set([self._create_event_signature(e) for e in self.change_tracker.event_cache.values()])

            logger.info(f"  Source signatures: {len(source_sigs)}")
            logger.info(f"  Target signatures: {len(target_sigs)}")
            logger.info(f"  Cached signatures: {len(cached_sigs)}")

            # What's the overlap?
            logger.info(f"  Source ∩ Target: {len(source_sigs & target_sigs)}")
            logger.info(f"  Source ∩ Cached: {len(source_sigs & cached_sigs)}")

            # What should be added?
            to_add_theoretical = source_sigs - target_sigs - cached_sigs
            logger.info(f"  Events to add (not in target or cache): {len(to_add_theoretical)}")
            
            # Log all-day event statistics
            source_all_day_count = sum(1 for e in source_events if e.get('isAllDay', False))
            target_all_day_count = sum(1 for e in target_events if e.get('isAllDay', False))
            logger.info(f"📅 All-day events - Source: {source_all_day_count}, Target: {target_all_day_count}")
            
            # Build target map for quick lookup and collect duplicate targets to delete
            target_map, duplicate_targets = self._build_event_map(target_events)
            
            # Determine operations needed
            to_add, to_update, to_delete = self._determine_sync_operations(
                source_events, target_events, target_map, check_instances=False
            )

            # Safely append duplicates detected in target to deletion list
            if duplicate_targets:
                logger.info(f"🧹 Found {len(duplicate_targets)} duplicate target events to clean up")
                # Ensure we only add events that still exist and have an id
                to_delete.extend([e for e in duplicate_targets if e.get('id')])
            
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
            result = self._execute_sync_operations_batch(target_id, to_add, to_update, to_delete)
            
            # Handle cancelled occurrences of recurring events
            if config.SYNC_OCCURRENCE_EXCEPTIONS:
                cancelled_deleted = self._handle_cancelled_occurrences(source_id, target_id)
                if cancelled_deleted:
                    result['deleted'] += cancelled_deleted
                    if 'operation_details' in result:
                        result['operation_details']['delete_success'] += cancelled_deleted
            
            # Handle modified occurrences (exceptions to recurrence patterns)
            if config.SYNC_OCCURRENCE_EXCEPTIONS:
                modified_occurrences = self._handle_modified_occurrences(source_id, target_id)
                if modified_occurrences:
                    result['updated'] += modified_occurrences
                    if 'operation_details' in result:
                        result['operation_details']['update_success'] += modified_occurrences
            
            # Update cache if change tracking is enabled
            if hasattr(self, 'change_tracker'):
                self.change_tracker.update_cache(source_events)
            
            # Calculate duration
            duration = (DateTimeUtils.get_central_time() - start_time).total_seconds()
            result['duration'] = duration
            
            # Update sync state
            with self.sync_lock:
                self.last_sync_time = DateTimeUtils.get_central_time()
                self.last_sync_result = result
            
            # Add successful sync to history
            self.history.add_entry(result)
            
            # Log all-day event summary
            if 'all_day_events' in result:
                all_day_summary = result['all_day_events']
                logger.info(f"📅 All-day events processed: {all_day_summary['total_processed']} (Added: {all_day_summary['added']}, Updated: {all_day_summary['updated']})")
            
            logger.info(f"🎉 Sync completed in {duration:.2f} seconds: {result}")
            return result
            
        except Exception as e:
            import traceback
            duration = (DateTimeUtils.get_central_time() - start_time).total_seconds()
            
            error_result = {
                'success': False,
                'message': f'Sync failed: {str(e)}',
                'error': str(e),
                'traceback': traceback.format_exc(),
                'duration': duration
            }
            
            with self.sync_lock:
                self.last_sync_result = error_result
            
            # Add failed sync to history
            self.history.add_entry(error_result)
            
            logger.error(f"❌ Sync failed: {e}")
            return error_result
            
        finally:
            # Always clear sync state when done
            with self.sync_lock:
                self.sync_in_progress = False
                self.sync_state['in_progress'] = False
                self.sync_state['phase'] = None
                self.sync_state['progress'] = 0
    
    def _normalize_subject(self, subject: str) -> str:
        """Normalize event subject for matching - Uses shared utilities"""
        return normalize_subject(subject)
    
    def _normalize_datetime(self, dt_str: str) -> str:
        """Normalize datetime string for matching - Uses shared utilities"""
        return normalize_datetime(dt_str)
    
    def _is_synced_event(self, event: Dict) -> bool:
        """Check if this event was created by our sync system"""
        # Check for our sync marker in the body content
        body_content = event.get('body', {}).get('content', '')
        if 'SYNC_ID:' in body_content:
            return True
        
        # Also check for legacy markers
        if 'Auto-synced from' in body_content:
            return True
            
        return False
    
    def _create_event_signature(self, event: Dict) -> str:
        """Create unique signature for an event - Uses shared signature utilities"""
        return generate_event_signature(event)
    
    def _build_event_map(self, events: List[Dict]) -> Tuple[Dict[str, Dict], List[Dict]]:
        """Build a map of events by signature and collect duplicates to remove.

        Returns a tuple of (event_map, duplicates_to_delete).
        - event_map keeps a single canonical event per signature
        - duplicates_to_delete contains extra events sharing the same signature
        """
        event_map: Dict[str, Dict] = {}
        duplicates_to_delete: List[Dict] = []

        for event in events:
            signature = self._create_event_signature(event)

            # Process all events now (including occurrences)
            if signature in event_map:
                # Keep the newer event based on creation time; mark the other as duplicate
                existing = event_map[signature]
                existing_created = existing.get('createdDateTime', '')
                new_created = event.get('createdDateTime', '')
                if new_created < existing_created:
                    logger.info(f"Duplicate detected for signature '{signature}' - keeping OLDER event (new)")
                    duplicates_to_delete.append(existing)
                    event_map[signature] = event
                else:
                    logger.info(f"Duplicate detected for signature '{signature}' - keeping OLDER event (existing)")
                    duplicates_to_delete.append(event)
            else:
                event_map[signature] = event

        return event_map, duplicates_to_delete
    
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
        
        # CRITICAL FIX: Only compare against events that were synced by our system
        synced_target_events = [event for event in target_events if self._is_synced_event(event)]
        synced_target_map = {}
        for event in synced_target_events:
            sig = self._create_event_signature(event)
            synced_target_map[sig] = event
        
        # Make a copy of synced_target_map for tracking deletions
        remaining_targets = synced_target_map.copy()
        
        logger.info(f"🔍 Analyzing {len(source_events)} source events against {len(synced_target_map)} SYNCED target events (out of {len(target_map)} total)")
        
        # DIAGNOSTIC: Signature Analysis
        logger.info(f"🔍 Signature Analysis:")
        logger.info(f"  Source events with signatures: {len(source_events)}")
        logger.info(f"  Total target events: {len(target_map)}")
        logger.info(f"  Synced target events: {len(synced_target_map)}")

        # Log sample signatures
        if source_events and synced_target_map:
            source_sample = []
            target_sample = []
            for i, event in enumerate(source_events[:5]):
                sig = self._create_event_signature(event)
                source_sample.append(f"{event.get('subject', 'No Subject')[:20]} -> {sig[:50]}")
            for i, (sig, event) in enumerate(list(synced_target_map.items())[:5]):
                target_sample.append(f"{event.get('subject', 'No Subject')[:20]} -> {sig[:50]}")
            
            logger.info(f"  Sample source signatures: {source_sample}")
            logger.info(f"  Sample synced target signatures: {target_sample}")

        # Find matching signatures
        source_signatures = set()
        for event in source_events:
            sig = self._create_event_signature(event)
            if not sig.startswith("skip:occurrence:"):
                source_signatures.add(sig)
        
        synced_target_signatures = set(synced_target_map.keys())
        
        # DEBUG: Add detailed signature comparison logging
        logger.info("="*60)
        logger.info("SIGNATURE COMPARISON DEBUG")
        logger.info("="*60)
        
        # Log first 10 source signatures
        logger.info("\nSource calendar signatures (first 10):")
        source_sample = []
        for i, event in enumerate(source_events[:10]):
            sig = self._create_event_signature(event)
            source_sample.append(sig)
            logger.info(f"  {i+1}. {sig}")
            logger.info(f"      Event: {event.get('subject')}")
            logger.info(f"      Type: {event.get('type')}")
            logger.info(f"      Start: {event.get('start')}")
        
        # Log first 10 target signatures
        logger.info("\nTarget calendar signatures (first 10):")
        target_sample = []
        for i, (sig, event) in enumerate(list(synced_target_map.items())[:10]):
            target_sample.append(sig)
            logger.info(f"  {i+1}. {sig}")
            logger.info(f"      Event: {event.get('subject')}")
            logger.info(f"      Type: {event.get('type')}")
            logger.info(f"      Start: {event.get('start')}")
        
        # Check if ANY match
        matches = set(source_sample) & set(target_sample)
        logger.info(f"\nMatches in sample: {len(matches)}")
        if matches:
            logger.info(f"Sample matches: {matches}")
        
        logger.info("="*60)
        
        matching_sigs = source_signatures & synced_target_signatures
        logger.info(f"  Matching signatures found: {len(matching_sigs)}")
        if matching_sigs:
            logger.info(f"  First 5 matches: {list(matching_sigs)[:5]}")

        # Find events that should be added
        should_add = source_signatures - synced_target_signatures
        logger.info(f"  Signatures that should be added: {len(should_add)}")
        if should_add:
            logger.info(f"  First 5 to add: {list(should_add)[:5]}")
        
        # Build a comprehensive lookup of existing events in target calendar
        # Use the same signature logic for consistent duplicate detection
        existing_signatures = set()
        for event in target_events:
            sig = self._create_event_signature(event)
            existing_signatures.add(sig)
        
        logger.info(f"🔍 Duplicate detection: Found {len(existing_signatures)} existing event signatures in target calendar")
        
        for source_event in source_events:
            signature = self._create_event_signature(source_event)
            subject = source_event.get('subject', 'No subject')
            
            # Skip occurrences entirely
            if signature.startswith("skip:occurrence:"):
                continue
            
            # Log all-day status for debugging
            is_all_day = source_event.get('isAllDay', False)
            if is_all_day:
                logger.debug(f"📅 Processing all-day event: {subject}")
            
            # Check if this event already exists in target calendar (any event, not just synced ones)
            if signature in existing_signatures:
                logger.debug(f"🔄 Event already exists in target: {subject} (All-day: {is_all_day})")
                # Check if it's a synced event that needs updating
                if signature in remaining_targets:
                    target_event = remaining_targets[signature]
                    if self._needs_update(source_event, target_event):
                        logger.debug(f"📝 UPDATE needed: {subject} (All-day: {is_all_day})")
                        to_update.append((source_event, target_event))
                    else:
                        logger.debug(f"✅ No change needed: {subject} (All-day: {is_all_day})")
                    # Remove from remaining targets
                    del remaining_targets[signature]
                else:
                    # Event exists but wasn't synced by us - skip it
                    logger.debug(f"⏭️ Skipping existing non-synced event: {subject}")
            else:
                # Event doesn't exist - add it
                logger.debug(f"➕ ADD needed: {subject} (All-day: {is_all_day}) (signature: {signature})")
                to_add.append(source_event)
        
        # Remaining events in target should be deleted (not in source anymore)
        to_delete = list(remaining_targets.values())
        
        logger.info(f"📊 Operation summary:")
        logger.info(f"  - {len(to_add)} events to ADD")
        logger.info(f"  - {len(to_update)} events to UPDATE") 
        logger.info(f"  - {len(to_delete)} events to DELETE")
        logger.info(f"  - {len(remaining_targets)} unmatched target events")
        
        return to_add, to_update, to_delete
    
    def _needs_update(self, source_event: Dict, target_event: Dict) -> bool:
        """Check if an event needs updating - COMPARE PREPARED DATA WITH TARGET"""
        subject = source_event.get('subject', 'Unknown')
        
        # Prepare the source event as it would be in the target calendar
        prepared_source_data = self.writer._prepare_event_data(source_event)
        
        # Skip update if only modification times are different and no actual content changed
        # Store original modification times for comparison
        source_modified_original = source_event.get('lastModifiedDateTime')
        target_modified_original = target_event.get('lastModifiedDateTime')

        # Temporarily set them to the same value for comparison
        source_event_copy = source_event.copy()
        target_event_copy = target_event.copy()
        source_event_copy['lastModifiedDateTime'] = 'SAME'
        target_event_copy['lastModifiedDateTime'] = 'SAME'

        # If events are identical except for modification time, skip update
        if source_event_copy == target_event_copy:
            logger.debug(f"Skipping update for '{subject}' - only modification time differs")
            return False
        
        # Quick check: compare modification times first (fastest check)
        source_modified = source_event.get('lastModifiedDateTime')
        target_modified = target_event.get('lastModifiedDateTime')
        
        if source_modified == target_modified:
            # If modification times match, no changes needed
            return False
        
        # If we get here, there are changes - do detailed comparison using prepared data
        logger.info(f"🔍 Checking if '{subject}' needs update (modified times differ)")
        
        # Compare normalized subject
        prepared_subject = prepared_source_data.get('subject', '').strip().lower()
        target_subject = target_event.get('subject', '').strip().lower()
        
        if prepared_subject != target_subject:
            logger.info(f"  📝 Subject changed: '{prepared_subject}' != '{target_subject}'")
            return True
        
        # Compare start/end times
        if prepared_source_data.get('start') != target_event.get('start'):
            logger.info(f"  🕐 Start time changed for '{subject}'")
            return True
        
        if prepared_source_data.get('end') != target_event.get('end'):
            logger.info(f"  🕐 End time changed for '{subject}'")
            return True
        
        if prepared_source_data.get('isAllDay') != target_event.get('isAllDay'):
            logger.info(f"  📅 All-day flag changed for '{subject}'")
            return True
        
        # Compare categories
        prepared_categories = set(prepared_source_data.get('categories', []))
        target_categories = set(target_event.get('categories', []))
        
        if prepared_categories != target_categories:
            logger.info(f"  ✅ CATEGORIES CHANGED for '{subject}': {prepared_categories} != {target_categories}")
            return True
        
        # Compare location (prepared vs target)
        prepared_location = prepared_source_data.get('location', {})
        target_location = target_event.get('location', {})
        
        # Normalize location comparison (could be string or dict)
        prepared_loc_str = prepared_location.get('displayName', '') if isinstance(prepared_location, dict) else str(prepared_location)
        target_loc_str = target_location.get('displayName', '') if isinstance(target_location, dict) else str(target_location)
        
        if prepared_loc_str.strip().lower() != target_loc_str.strip().lower():
            logger.info(f"  ✅ LOCATION CHANGED for '{subject}': '{prepared_loc_str}' != '{target_loc_str}'")
            return True
        
        # Compare body content (prepared vs target)
        prepared_body = prepared_source_data.get('body', {}).get('content', '') if isinstance(prepared_source_data.get('body'), dict) else ''
        target_body = target_event.get('body', {}).get('content', '') if isinstance(target_event.get('body'), dict) else ''

        # Normalize body content for comparison
        prepared_body_normalized = prepared_body.strip().lower()
        target_body_normalized = target_body.strip().lower()
        
        if prepared_body_normalized != target_body_normalized:
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
        BATCH_SIZE = 20  # Changed from 5
        
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
                self.sync_state['last_checkpoint'] = DateTimeUtils.get_central_time()
                
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
                self.sync_state['last_checkpoint'] = DateTimeUtils.get_central_time()
                
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
                self.sync_state['last_checkpoint'] = DateTimeUtils.get_central_time()
                
                time.sleep(0.1)
        
        # Clear state
        self.sync_state['phase'] = None
        self.sync_state['progress'] = 0
        
        total = len(to_add) + len(to_update) + len(to_delete)
        
        # Update sync state with total operations
        self.sync_state['total'] = total
        
        # Count all-day events processed
        all_day_added = sum(1 for event in to_add if event.get('isAllDay', False))
        all_day_updated = sum(1 for source_event, _ in to_update if source_event.get('isAllDay', False))
        
        return {
            'success': True,
            'message': f'Sync complete: {successful}/{total} operations successful',
            'successful_operations': successful,
            'failed_operations': failed,
            'added': operation_details['add_success'],
            'updated': operation_details['update_success'],
            'deleted': operation_details['delete_success'],
            'operation_details': operation_details,
            'safeguards_active': config.MASTER_CALENDAR_PROTECTION,
            'all_day_events': {
                'added': all_day_added,
                'updated': all_day_updated,
                'total_processed': all_day_added + all_day_updated
            }
        }
    
    def _handle_cancelled_occurrences(self, source_id: str, target_id: str) -> int:
        """Delete occurrences that are cancelled on the master calendar."""
        try:
            window_start = (DateTimeUtils.get_central_time() - timedelta(days=1)).astimezone(pytz.UTC)
            window_end = (DateTimeUtils.get_central_time() + timedelta(days=config.OCCURRENCE_SYNC_DAYS)).astimezone(pytz.UTC)

            # ISO format strings expected by Graph
            start_str = window_start.isoformat()
            end_str = window_end.isoformat()
            
            logger.info(f"[Sync] Querying cancelled occurrences from {start_str} to {end_str}")

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
    
    def _handle_modified_occurrences(self, source_id: str, target_id: str) -> int:
        """Handle modified occurrences (exceptions to recurrence patterns)"""
        try:
            window_start = (DateTimeUtils.get_central_time() - timedelta(days=1)).astimezone(pytz.UTC)
            window_end = (DateTimeUtils.get_central_time() + timedelta(days=config.OCCURRENCE_SYNC_DAYS)).astimezone(pytz.UTC)

            # ISO format strings expected by Graph
            start_str = window_start.isoformat()
            end_str = window_end.isoformat()
            
            logger.info(f"[Sync] Querying modified occurrences from {start_str} to {end_str}")

            # Fetch expanded instances (occurrences) within the window
            source_instances = self.reader.get_calendar_instances(source_id, start_str, end_str) or []
            target_instances = self.reader.get_calendar_instances(target_id, start_str, end_str) or []

            logger.info(f"🔍 Checking for modified occurrences:")
            logger.info(f"  Source instances: {len(source_instances)}")
            logger.info(f"  Target instances: {len(target_instances)}")

            # Build lookup maps for comparison
            def _make_key(inst):
                subj = (inst.get('subject') or '').strip().lower()
                dt = inst.get('originalStart') or inst.get('start', {}).get('dateTime', '')
                # Normalise datetime string (strip timezone markers to compare raw moment)
                if dt.endswith('Z'):
                    dt = dt[:-1]
                return f"{subj}|{dt}"

            source_map = {_make_key(i): i for i in source_instances if not i.get('isCancelled', False)}
            target_map = {_make_key(i): i for i in target_instances if not i.get('isCancelled', False)}

            updated_count = 0

            # Check for modified occurrences (same subject, different times)
            for source_key, source_inst in source_map.items():
                if source_key in target_map:
                    target_inst = target_map[source_key]
                    
                    # Check if this is a modified occurrence (different start/end times)
                    source_start = source_inst.get('start', {}).get('dateTime', '')
                    target_start = target_inst.get('start', {}).get('dateTime', '')
                    source_end = source_inst.get('end', {}).get('dateTime', '')
                    target_end = target_inst.get('end', {}).get('dateTime', '')
                    
                    if source_start != target_start or source_end != target_end:
                        # This is a modified occurrence - update it
                        series_id = target_inst.get('seriesMasterId')
                        occ_date = target_inst.get('originalStart') or target_inst.get('start', {}).get('dateTime', '')
                        
                        if series_id and occ_date:
                            logger.info(f"  🔄 Updating modified occurrence: {source_inst.get('subject')} on {occ_date}")
                            logger.info(f"    Source: {source_start} - {source_end}")
                            logger.info(f"    Target: {target_start} - {target_end}")
                            
                            # Prepare update data
                            update_data = {
                                'subject': source_inst.get('subject'),
                                'start': source_inst.get('start'),
                                'end': source_inst.get('end'),
                                'body': source_inst.get('body'),
                                'location': source_inst.get('location'),
                                'categories': source_inst.get('categories', [])
                            }
                            
                            if self.writer.update_occurrence(target_id, series_id, occ_date, update_data):
                                updated_count += 1

            if updated_count:
                logger.info(f"🔄 Updated {updated_count} modified occurrences to keep calendars in sync")

            return updated_count
        except Exception as e:
            logger.error(f"Error while handling modified occurrences: {e}")
            return 0
    
    def get_status(self) -> Dict:
        """Get current sync status"""
        with self.sync_lock:
            # Handle last_sync_time safely - it might be a string or datetime
            last_sync_time_iso = None
            if self.last_sync_time:
                if isinstance(self.last_sync_time, str):
                    last_sync_time_iso = self.last_sync_time
                else:
                    try:
                        last_sync_time_iso = self.last_sync_time.isoformat()
                    except AttributeError:
                        last_sync_time_iso = str(self.last_sync_time)
            
            # Handle current_time safely
            try:
                current_time_iso = DateTimeUtils.get_central_time().isoformat()
            except AttributeError:
                current_time_iso = str(DateTimeUtils.get_central_time())
            
            status = {
                'last_sync_time': last_sync_time_iso,
                'last_sync_time_display': DateTimeUtils.format_central_time(self.last_sync_time) if self.last_sync_time else 'Never',
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
                'current_time': current_time_iso,
                'occurrence_sync_enabled': config.SYNC_OCCURRENCE_EXCEPTIONS,
                'change_tracker': self.change_tracker.get_cache_stats()
            }
            return status
    
    def get_progress_percent(self):
        """Calculate progress percentage"""
        total = self.sync_state.get('total', 0)
        progress = self.sync_state.get('progress', 0)
        if total == 0:
            return 0
        return int((progress / total) * 100)


class SyncScheduler:
    """Manages background sync scheduling"""
    
    def __init__(self, sync_engine):
        self.sync_engine = sync_engine
        self.scheduler_lock = Lock()
        self.scheduler_running = False
        self.scheduler_thread = None
        # ADD THESE LINES:
        self.last_scheduled_sync = None
        self.next_scheduled_sync = None
        self.scheduled_sync_count = 0
        self.scheduled_sync_history = []  # Keep last 10 scheduled syncs
    
    def start(self):
        """Start the scheduler"""
        with self.scheduler_lock:
            if self.scheduler_thread is None or not self.scheduler_thread.is_alive():
                logger.info(f"Starting scheduler thread at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}...")
                self.scheduler_running = True
                self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
                self.scheduler_thread.start()
            else:
                logger.info("Scheduler already running")
    
    def stop(self):
        """Stop the scheduler"""
        with self.scheduler_lock:
            self.scheduler_running = False
        
        logger.info(f"Stopping scheduler at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}...")
    
    def is_running(self):
        """Check if scheduler is running"""
        with self.scheduler_lock:
            return self.scheduler_running and self.scheduler_thread and self.scheduler_thread.is_alive()
    
    def _run_scheduler(self):
        """Run the scheduler loop"""
        # Schedule sync to run every 23 minutes with built-in health check
        schedule.every(23).minutes.do(self._scheduled_sync_with_health_check)
        
        logger.info(f"Scheduler started - sync with health check every 23 minutes (CT) - started at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
        
        # Add startup delay to prevent immediate sync after deployment
        logger.info("⏳ Waiting 2 minutes before first scheduled sync to allow deployment to stabilize...")
        time.sleep(120)  # Wait 2 minutes before first sync
        
        while True:
            with self.scheduler_lock:
                if not self.scheduler_running:
                    break
            
            schedule.run_pending()
            time.sleep(60)  # Check every minute
        
        logger.info(f"Scheduler stopped at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
    
    def get_scheduler_status(self):
        """Get detailed scheduler status including sync times"""
        return {
            'running': self.is_running(),
            'last_scheduled_sync': self.last_scheduled_sync.isoformat() if self.last_scheduled_sync else None,
            'last_scheduled_sync_display': DateTimeUtils.format_central_time(self.last_scheduled_sync) if self.last_scheduled_sync else 'Never',
            'next_scheduled_sync': self.next_scheduled_sync.isoformat() if self.next_scheduled_sync else None,
            'next_scheduled_sync_display': DateTimeUtils.format_central_time(self.next_scheduled_sync) if self.next_scheduled_sync else 'Unknown',
            'scheduled_sync_count': self.scheduled_sync_count,
            'recent_scheduled_syncs': self.scheduled_sync_history[-5:]  # Last 5 syncs
        }
    
    def _scheduled_sync(self):
        """Function called by scheduler - IMPROVED with error handling"""
        try:
            logger.info(f"Running scheduled sync (every 23 minutes) at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
            
            # Proactively refresh token before sync
            if not self.sync_engine.auth.ensure_valid_token():
                logger.error("Failed to refresh token before scheduled sync")
                return
            
            # Check if authenticated before trying to sync
            if not self.sync_engine.auth.is_authenticated():
                logger.warning(f"⚠️ Scheduled sync skipped - not authenticated at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
                return
            
            result = self.sync_engine.sync_calendars()
            
            if result.get('needs_auth'):
                logger.warning(f"⚠️ Scheduled sync indicates authentication needed at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
            elif result.get('success'):
                logger.info(f"✅ Scheduled sync completed successfully at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
            else:
                logger.warning(f"⚠️ Scheduled sync completed with issues at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}: {result.get('message')}")
                
        except Exception as e:
            # Don't let sync errors crash the scheduler
            logger.error(f"❌ Scheduled sync failed at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}: {e}")
    
    def _scheduled_sync_with_health_check(self):
        """Function called by scheduler - runs health check before sync"""
        try:
            # Record the scheduled sync attempt
            self.last_scheduled_sync = DateTimeUtils.get_central_time()
            self.scheduled_sync_count += 1
            
            # Calculate next sync time (23 minutes from now)
            self.next_scheduled_sync = DateTimeUtils.get_central_time() + timedelta(minutes=23)
            
            # Add to history
            sync_record = {
                'timestamp': self.last_scheduled_sync.isoformat(),
                'success': False,  # Will update after sync
                'type': 'scheduled'
            }
            self.scheduled_sync_history.append(sync_record)
            if len(self.scheduled_sync_history) > 10:
                self.scheduled_sync_history.pop(0)
            
            logger.info("="*60)
            logger.info(f"🔄 SCHEDULED SYNC TRIGGERED at {DateTimeUtils.format_central_time(self.last_scheduled_sync)}")
            logger.info(f"⏰ Next scheduled sync will be at: {DateTimeUtils.format_central_time(self.next_scheduled_sync)}")
            logger.info("="*60)
            
            # Step 1: Run health check first
            logger.info("💓 Running pre-sync health check...")
            
            if not self._run_health_check():
                logger.warning("⚠️ Health check failed, skipping scheduled sync")
                return
            
            # Step 2: Health check passed, run sync
            logger.info("✅ Health check passed, proceeding with sync...")
            self._scheduled_sync()
            
            # Update the record as successful
            if self.scheduled_sync_history:
                self.scheduled_sync_history[-1]['success'] = True
            
            logger.info("="*60)
            logger.info(f"✅ SCHEDULED SYNC COMPLETED at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
            logger.info("="*60)
            
        except Exception as e:
            logger.error(f"❌ Scheduled sync with health check failed: {e}")
    
    def _run_health_check(self):
        """Run health check before sync"""
        try:
            # Check authentication
            if not self.sync_engine.auth.is_authenticated():
                logger.warning("Health check failed: Not authenticated")
                return False
            
            # Check if sync engine is ready
            if not self.sync_engine.reader or not self.sync_engine.writer:
                logger.warning("Health check failed: Sync engine not ready")
                return False
            
            # Check if calendars are accessible
            try:
                calendars = self.sync_engine.reader.get_calendars()
                if not calendars:
                    logger.warning("Health check failed: Cannot access calendars")
                    return False
            except Exception as e:
                logger.warning(f"Health check failed: Calendar access error: {e}")
                return False
            
            logger.info("✅ Health check passed")
            return True
            
        except Exception as e:
            logger.error(f"Health check failed with exception: {e}")
            return False