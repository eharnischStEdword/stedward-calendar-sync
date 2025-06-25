# utils/metrics.py
from datetime import datetime
from collections import defaultdict

class MetricsCollector:
    def __init__(self):
        self.metrics = defaultdict(list)
    
    def record_sync_duration(self, duration_seconds):
        self.metrics['sync_duration'].append({
            'timestamp': datetime.utcnow(),
            'duration': duration_seconds
        })
    
    def record_sync_result(self, added, updated, deleted, failed):
        self.metrics['sync_results'].append({
            'timestamp': datetime.utcnow(),
            'added': added,
            'updated': updated,
            'deleted': deleted,
            'failed': failed
        })
    
    def get_metrics_summary(self):
        # Return last 24 hours of metrics
        pass
