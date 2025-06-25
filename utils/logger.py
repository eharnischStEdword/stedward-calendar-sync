# utils/logger.py
import json
import logging
from datetime import datetime

class StructuredLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)
    
    def log_sync_event(self, event_type, details):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "service": "calendar-sync",
            **details
        }
        self.logger.info(json.dumps(log_entry))
