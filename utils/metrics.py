"""
Metrics Collector - Track and analyze sync performance metrics
"""
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional
import statistics
from utils.timezone import get_central_time


class MetricsCollector:
    """Collects and analyzes metrics for sync operations"""
    
    def __init__(self):
        self.metrics = defaultdict(list)
        self.max_metrics_age_days = 7  # Keep metrics for 7 days
    
    def record_sync_duration(self, duration_seconds: float):
        """Record the duration of a sync operation"""
        self._add_metric('sync_duration', {
            'timestamp': get_central_time(),
            'duration': duration_seconds
        })
    
    def record_sync_result(self, added: int, updated: int, deleted: int, failed: int):
        """Record the results of a sync operation"""
        self._add_metric('sync_results', {
            'timestamp': get_central_time(),
            'added': added,
            'updated': updated,
            'deleted': deleted,
            'failed': failed,
            'total_operations': added + updated + deleted + failed
        })
    
    def record_api_call(self, endpoint: str, duration_ms: float, status_code: int):
        """Record API call metrics"""
        self._add_metric('api_calls', {
            'timestamp': get_central_time(),
            'endpoint': endpoint,
            'duration_ms': duration_ms,
            'status_code': status_code,
            'success': 200 <= status_code < 300
        })
    
    def record_error(self, error_type: str, error_message: str):
        """Record error occurrences"""
        self._add_metric('errors', {
            'timestamp': get_central_time(),
            'error_type': error_type,
            'error_message': error_message[:200]  # Truncate long messages
        })
    
    def record_calendar_stats(self, source_events: int, target_events: int, public_events: int):
        """Record calendar statistics"""
        self._add_metric('calendar_stats', {
            'timestamp': get_central_time(),
            'source_events': source_events,
            'target_events': target_events,
            'public_events': public_events
        })
    
    def get_metrics_summary(self, hours: int = 24) -> Dict:
        """Get a summary of metrics for the specified time period"""
        self._cleanup_old_metrics()
        
        cutoff_time = get_central_time() - timedelta(hours=hours)
        summary = {
            'period_hours': hours,
            'sync_metrics': self._get_sync_metrics(cutoff_time),
            'api_metrics': self._get_api_metrics(cutoff_time),
            'error_metrics': self._get_error_metrics(cutoff_time),
            'calendar_metrics': self._get_calendar_metrics(cutoff_time)
        }
        
        return summary
    
    def get_sync_performance_trend(self, days: int = 7) -> List[Dict]:
        """Get daily sync performance trends"""
        trends = []
        
        for i in range(days):
            date = get_central_time().date() - timedelta(days=i)
            start_time = datetime.combine(date, datetime.min.time())
            end_time = start_time + timedelta(days=1)
            
            day_durations = [
                m['duration'] for m in self.metrics.get('sync_duration', [])
                if start_time <= m['timestamp'] < end_time
            ]
            
            day_results = [
                m for m in self.metrics.get('sync_results', [])
                if start_time <= m['timestamp'] < end_time
            ]
            
            trends.append({
                'date': date.isoformat(),
                'sync_count': len(day_durations),
                'average_duration': statistics.mean(day_durations) if day_durations else 0,
                'min_duration': min(day_durations) if day_durations else 0,
                'max_duration': max(day_durations) if day_durations else 0,
                'total_operations': sum(r['total_operations'] for r in day_results),
                'failed_operations': sum(r['failed'] for r in day_results)
            })
        
        return list(reversed(trends))
    
    def _add_metric(self, metric_type: str, metric_data: Dict):
        """Add a metric to the collection"""
        self.metrics[metric_type].append(metric_data)
        
        # Keep only recent metrics (sliding window)
        max_entries = 10000  # Prevent unbounded growth
        if len(self.metrics[metric_type]) > max_entries:
            self.metrics[metric_type] = self.metrics[metric_type][-max_entries:]
    
    def _cleanup_old_metrics(self):
        """Remove metrics older than max_metrics_age_days"""
        cutoff_time = get_central_time() - timedelta(days=self.max_metrics_age_days)
        
        for metric_type in self.metrics:
            self.metrics[metric_type] = [
                m for m in self.metrics[metric_type]
                if m.get('timestamp', datetime.min) > cutoff_time
            ]
    
    def _get_sync_metrics(self, cutoff_time: datetime) -> Dict:
        """Calculate sync-related metrics"""
        durations = [
            m['duration'] for m in self.metrics.get('sync_duration', [])
            if m['timestamp'] > cutoff_time
        ]
        
        results = [
            m for m in self.metrics.get('sync_results', [])
            if m['timestamp'] > cutoff_time
        ]
        
        if not durations:
            return {
                'sync_count': 0,
                'average_duration': 0,
                'percentiles': {},
                'total_operations': 0,
                'success_rate': 0
            }
        
        total_ops = sum(r['total_operations'] for r in results)
        failed_ops = sum(r['failed'] for r in results)
        
        return {
            'sync_count': len(durations),
            'average_duration': statistics.mean(durations),
            'median_duration': statistics.median(durations),
            'percentiles': {
                'p50': self._percentile(durations, 50),
                'p90': self._percentile(durations, 90),
                'p95': self._percentile(durations, 95),
                'p99': self._percentile(durations, 99)
            },
            'min_duration': min(durations),
            'max_duration': max(durations),
            'total_operations': total_ops,
            'failed_operations': failed_ops,
            'success_rate': ((total_ops - failed_ops) / total_ops * 100) if total_ops > 0 else 100,
            'operations_breakdown': {
                'added': sum(r['added'] for r in results),
                'updated': sum(r['updated'] for r in results),
                'deleted': sum(r['deleted'] for r in results)
            }
        }
    
    def _get_api_metrics(self, cutoff_time: datetime) -> Dict:
        """Calculate API-related metrics"""
        api_calls = [
            m for m in self.metrics.get('api_calls', [])
            if m['timestamp'] > cutoff_time
        ]
        
        if not api_calls:
            return {
                'total_calls': 0,
                'success_rate': 0,
                'average_duration_ms': 0,
                'by_endpoint': {}
            }
        
        successful_calls = [m for m in api_calls if m['success']]
        
        # Group by endpoint
        by_endpoint = defaultdict(list)
        for call in api_calls:
            by_endpoint[call['endpoint']].append(call)
        
        endpoint_stats = {}
        for endpoint, calls in by_endpoint.items():
            durations = [c['duration_ms'] for c in calls]
            endpoint_stats[endpoint] = {
                'count': len(calls),
                'average_duration_ms': statistics.mean(durations),
                'success_rate': sum(1 for c in calls if c['success']) / len(calls) * 100
            }
        
        all_durations = [c['duration_ms'] for c in api_calls]
        
        return {
            'total_calls': len(api_calls),
            'success_rate': len(successful_calls) / len(api_calls) * 100,
            'average_duration_ms': statistics.mean(all_durations),
            'median_duration_ms': statistics.median(all_durations),
            'by_endpoint': endpoint_stats
        }
    
    def _get_error_metrics(self, cutoff_time: datetime) -> Dict:
        """Calculate error-related metrics"""
        errors = [
            m for m in self.metrics.get('errors', [])
            if m['timestamp'] > cutoff_time
        ]
        
        if not errors:
            return {
                'total_errors': 0,
                'by_type': {}
            }
        
        # Group by error type
        by_type = defaultdict(int)
        for error in errors:
            by_type[error['error_type']] += 1
        
        return {
            'total_errors': len(errors),
            'errors_per_hour': len(errors) / ((get_central_time() - cutoff_time).total_seconds() / 3600),
            'by_type': dict(by_type),
            'recent_errors': [
                {
                    'timestamp': e['timestamp'].isoformat(),
                    'type': e['error_type'],
                    'message': e['error_message']
                }
                for e in sorted(errors, key=lambda x: x['timestamp'], reverse=True)[:5]
            ]
        }
    
    def _get_calendar_metrics(self, cutoff_time: datetime) -> Dict:
        """Calculate calendar-related metrics"""
        stats = [
            m for m in self.metrics.get('calendar_stats', [])
            if m['timestamp'] > cutoff_time
        ]
        
        if not stats:
            return {
                'samples': 0,
                'average_source_events': 0,
                'average_target_events': 0,
                'average_public_events': 0
            }
        
        return {
            'samples': len(stats),
            'average_source_events': statistics.mean(s['source_events'] for s in stats),
            'average_target_events': statistics.mean(s['target_events'] for s in stats),
            'average_public_events': statistics.mean(s['public_events'] for s in stats),
            'latest': {
                'source_events': stats[-1]['source_events'],
                'target_events': stats[-1]['target_events'],
                'public_events': stats[-1]['public_events'],
                'timestamp': stats[-1]['timestamp'].isoformat()
            }
        }
    
    def _percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile of a list"""
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
    
    def clear_metrics(self):
        """Clear all metrics"""
        self.metrics.clear()
