# sync/history.py
class SyncHistory:
    def __init__(self, max_entries=100):
        self.history = []
        self.max_entries = max_entries
    
    def add_entry(self, sync_result):
        entry = {
            'timestamp': datetime.now(),
            'result': sync_result,
            'duration': sync_result.get('duration'),
            'operations': {
                'added': sync_result.get('added', 0),
                'updated': sync_result.get('updated', 0),
                'deleted': sync_result.get('deleted', 0)
            }
        }
        self.history.append(entry)
        if len(self.history) > self.max_entries:
            self.history.pop(0)
    
    def get_statistics(self):
        # Calculate success rate, average duration, etc.
        pass
