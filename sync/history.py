# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Sync History - Track and analyze sync operations over time
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
import statistics
from utils.timezone import get_central_time


class SyncHistory:
    """Manages historical sync data and statistics"""
    
    def __init__(self, max_entries: int = 100):
        self.history: List[Dict] = []
        self.max_entries = max_entries
    
    def add_entry(self, sync_result: Dict):
        """Add a sync result to history"""
        entry = {
            'timestamp': get_central_time(),
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
        cutoff_time = get_central_time() - timedelta(hours=hours)
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
        cutoff_time = get_central_time() - timedelta(hours=hours)
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
            date = get_central_time().date() - timedelta(days=i)
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
