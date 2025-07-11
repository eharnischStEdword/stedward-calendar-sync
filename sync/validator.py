# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Sync Validator - Validate sync operations to ensure data integrity
"""
import logging
from typing import List, Dict, Tuple, Set
from datetime import datetime, timedelta
from collections import defaultdict
from utils.timezone import get_central_time

logger = logging.getLogger(__name__)


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
            self._validate_event_integrity
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
            if len(private_in_target) > 3:
                details += f" and {len(private_in_target) - 3} more"
        
        return "no_private_events", passed, details
    
    def _validate_event_categories(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate all target events have Public category"""
        events_without_public = []
        
        for event in target_events:
            categories = event.get('categories', [])
            if 'Public' not in categories:
                events_without_public.append(event.get('subject', 'Unknown'))
        
        passed = len(events_without_public) == 0
        details = f"Found {len(events_without_public)} events without Public category"
        
        if events_without_public:
            details += f": {', '.join(events_without_public[:3])}"
            if len(events_without_public) > 3:
                details += f" and {len(events_without_public) - 3} more"
        
        return "all_events_public", passed, details
    
    def _validate_event_dates(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate no past events beyond cutoff date"""
        from datetime import datetime, timedelta
        import config
        
        cutoff_date = get_central_time() - timedelta(days=config.SYNC_CUTOFF_DAYS)
        past_events = []
        
        for event in target_events:
            try:
                start_str = event.get('start', {}).get('dateTime', '')
                if start_str:
                    event_date = datetime.fromisoformat(start_str.replace('Z', ''))
                    if event_date < cutoff_date:
                        past_events.append({
                            'subject': event.get('subject', 'Unknown'),
                            'date': event_date.strftime('%Y-%m-%d')
                        })
            except:
                pass
        
        passed = len(past_events) == 0
        details = f"Found {len(past_events)} events older than {config.SYNC_CUTOFF_DAYS} days"
        
        if past_events:
            details += f": {past_events[0]['subject']} ({past_events[0]['date']})"
            if len(past_events) > 1:
                details += f" and {len(past_events) - 1} more"
        
        return "no_old_events", passed, details
    
    def _validate_no_duplicates(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Check for duplicate events in target"""
        # Create signatures for all events
        signatures = defaultdict(list)
        
        for event in target_events:
            sig = self._create_event_signature(event)
            signatures[sig].append(event.get('subject', 'Unknown'))
        
        duplicates = {sig: subjects for sig, subjects in signatures.items() if len(subjects) > 1}
        
        passed = len(duplicates) == 0
        details = f"Found {len(duplicates)} duplicate event groups"
        
        if duplicates:
            first_dup = list(duplicates.values())[0]
            details += f": '{first_dup[0]}' appears {len(first_dup)} times"
        
        return "no_duplicates", passed, details
    
    def _validate_recurring_events(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate recurring events are properly synced"""
        source_recurring = [e for e in source_events if e.get('type') == 'seriesMaster']
        target_recurring = [e for e in target_events if e.get('type') == 'seriesMaster']
        
        source_subjects = {e.get('subject') for e in source_recurring}
        target_subjects = {e.get('subject') for e in target_recurring}
        
        missing = source_subjects - target_subjects
        extra = target_subjects - source_subjects
        
        passed = len(missing) == 0 and len(extra) == 0
        details = ""
        
        if missing:
            details += f"Missing {len(missing)} recurring events. "
        if extra:
            details += f"Extra {len(extra)} recurring events. "
        
        if not details:
            details = f"All {len(source_recurring)} recurring events properly synced"
        
        return "recurring_events_match", passed, details
    
    def _validate_event_integrity(
        self,
        source_events: List[Dict],
        target_events: List[Dict]
    ) -> Tuple[str, bool, str]:
        """Validate key event properties are preserved"""
        issues = []
        
        # Create lookup maps
        source_map = {self._create_event_signature(e): e for e in source_events}
        target_map = {self._create_event_signature(e): e for e in target_events}
        
        # Check each event that exists in both
        for sig, source_event in source_map.items():
            if sig in target_map:
                target_event = target_map[sig]
                
                # Validate key fields match
                if source_event.get('subject') != target_event.get('subject'):
                    issues.append(f"Subject mismatch for {source_event.get('subject')}")
                
                # Check start/end times
                if source_event.get('start') != target_event.get('start'):
                    issues.append(f"Start time mismatch for {source_event.get('subject')}")
                
                if source_event.get('end') != target_event.get('end'):
                    issues.append(f"End time mismatch for {source_event.get('subject')}")
                
                # Check all-day flag
                if source_event.get('isAllDay') != target_event.get('isAllDay'):
                    issues.append(f"All-day flag mismatch for {source_event.get('subject')}")
        
        passed = len(issues) == 0
        details = f"Found {len(issues)} integrity issues" if issues else "All events have matching properties"
        
        if issues:
            details += f": {issues[0]}"
            if len(issues) > 1:
                details += f" and {len(issues) - 1} more"
        
        return "event_integrity", passed, details
    
    def _create_event_signature(self, event: Dict) -> str:
        """Create a signature for event matching"""
        subject = event.get('subject', '').strip().lower()
        event_type = event.get('type', 'singleInstance')
        
        if event_type == 'seriesMaster':
            # For recurring events, include recurrence pattern
            recurrence = event.get('recurrence', {})
            pattern = recurrence.get('pattern', {})
            pattern_type = pattern.get('type', 'unknown')
            
            return f"recurring:{subject}:{pattern_type}"
        else:
            # For single events, include date
            start = event.get('start', {}).get('dateTime', '')
            if start:
                try:
                    date_part = start.split('T')[0]
                    return f"single:{subject}:{date_part}"
                except:
                    pass
            
            return f"single:{subject}:{start}"
    
    def validate_sync_operation(
        self,
        operation: str,
        source_count: int,
        target_count: int,
        operations_performed: Dict[str, int]
    ) -> bool:
        """
        Quick validation of sync operation metrics
        
        Args:
            operation: Type of sync operation
            source_count: Number of source events
            target_count: Number of target events before sync
            operations_performed: Dict with 'added', 'updated', 'deleted' counts
            
        Returns:
            True if metrics seem reasonable
        """
        added = operations_performed.get('added', 0)
        updated = operations_performed.get('updated', 0)
        deleted = operations_performed.get('deleted', 0)
        
        # Basic sanity checks
        if added < 0 or updated < 0 or deleted < 0:
            logger.error("Negative operation counts detected")
            return False
        
        # Check if operations align with counts
        expected_final = target_count + added - deleted
        
        # Log warning for large operations
        total_ops = added + updated + deleted
        if total_ops > 100:
            logger.warning(f"Large sync operation: {total_ops} total changes")
        
        # Warn if deleting more than 50% of events
        if target_count > 0 and deleted > target_count * 0.5:
            logger.warning(f"Sync would delete {deleted}/{target_count} events (>50%)")
        
        return True
    
    def generate_validation_report(
        self,
        source_events: List[Dict],
        target_events: List[Dict],
        sync_result: Dict
    ) -> Dict:
        """Generate detailed validation report"""
        is_valid, validations = self.validate_sync_result(source_events, target_events)
        
        report = {
            'timestamp': get_central_time().isoformat(),
            'timezone': 'America/Chicago',
            'is_valid': is_valid,
            'source_event_count': len(source_events),
            'target_event_count': len(target_events),
            'validation_results': {
                check: passed for check, passed in validations
            },
            'sync_operations': sync_result.get('operation_details', {}),
            'warnings': [],
            'errors': []
        }
        
        # Add warnings and errors
        for check, passed in validations:
            if not passed:
                if check in ['no_private_events', 'event_integrity']:
                    report['errors'].append(f"Critical validation failed: {check}")
                else:
                    report['warnings'].append(f"Validation warning: {check}")
        
        # Additional analysis
        if len(target_events) == 0 and len(source_events) > 0:
            report['warnings'].append("Target calendar is empty after sync")
        
        if sync_result.get('failed_operations', 0) > 0:
            report['warnings'].append(
                f"{sync_result['failed_operations']} operations failed during sync"
            )
        
        return report
