"""
Structured Logger - Enhanced logging with JSON output for better observability
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from utils.timezone import get_central_time


class StructuredLogger:
    """Provides structured logging in JSON format for better parsing and analysis"""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.name = name
        
        # Add JSON formatter if not already present
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JsonFormatter())
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
    
    def log_sync_event(self, event_type: str, details: Dict[str, Any]):
        """Log a sync-related event with structured data"""
        log_entry = {
            "timestamp": get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "event_type": event_type,
            "service": "calendar-sync",
            "logger": self.name,
            **details
        }
        
        # Choose log level based on event type
        if "error" in event_type.lower() or "failed" in event_type.lower():
            self.logger.error(json.dumps(log_entry))
        elif "warning" in event_type.lower():
            self.logger.warning(json.dumps(log_entry))
        else:
            self.logger.info(json.dumps(log_entry))
    
    def log_api_call(self, method: str, endpoint: str, status_code: Optional[int] = None, 
                     duration_ms: Optional[float] = None, error: Optional[str] = None):
        """Log API call details"""
        log_entry = {
            "timestamp": get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "event_type": "api_call",
            "service": "calendar-sync",
            "logger": self.name,
            "method": method,
            "endpoint": endpoint
        }
        
        if status_code is not None:
            log_entry["status_code"] = status_code
        if duration_ms is not None:
            log_entry["duration_ms"] = duration_ms
        if error:
            log_entry["error"] = error
        
        if error or (status_code and status_code >= 400):
            self.logger.error(json.dumps(log_entry))
        else:
            self.logger.info(json.dumps(log_entry))
    
    def log_performance(self, operation: str, duration_seconds: float, 
                       item_count: Optional[int] = None, success: bool = True):
        """Log performance metrics"""
        log_entry = {
            "timestamp": get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "event_type": "performance",
            "service": "calendar-sync",
            "logger": self.name,
            "operation": operation,
            "duration_seconds": duration_seconds,
            "success": success
        }
        
        if item_count is not None:
            log_entry["item_count"] = item_count
            log_entry["items_per_second"] = item_count / duration_seconds if duration_seconds > 0 else 0
        
        self.logger.info(json.dumps(log_entry))
    
    def log_security_event(self, event: str, user: Optional[str] = None, 
                          ip_address: Optional[str] = None, details: Optional[Dict] = None):
        """Log security-related events"""
        log_entry = {
            "timestamp": get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "event_type": "security",
            "service": "calendar-sync",
            "logger": self.name,
            "security_event": event
        }
        
        if user:
            log_entry["user"] = user
        if ip_address:
            log_entry["ip_address"] = ip_address
        if details:
            log_entry["details"] = details
        
        self.logger.warning(json.dumps(log_entry))


class JsonFormatter(logging.Formatter):
    """Custom formatter that outputs JSON"""
    
    def format(self, record):
        # If the message is already JSON, return it as-is
        try:
            json.loads(record.getMessage())
            return record.getMessage()
        except (json.JSONDecodeError, TypeError):
            # Otherwise, create a JSON log entry
            log_entry = {
                "timestamp": get_central_time().isoformat(),
                "timezone": "America/Chicago",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()
            }
            
            if hasattr(record, 'exc_info') and record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)
            
            return json.dumps(log_entry)
