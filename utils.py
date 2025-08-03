# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Consolidated utilities for St. Edward Calendar Sync
Combines all utility functions into a single, well-organized module
"""
import json
import logging
import os
import random
import time
import threading
import subprocess
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, List, Optional, Any, Callable
import pytz
import requests

import config

# =============================================================================
# CORE UTILITIES
# =============================================================================

class DateTimeUtils:
    """Core datetime and timezone utilities"""
    
    @staticmethod
    def get_central_time() -> datetime:
        """Get current time in Central timezone"""
        central_tz = pytz.timezone('America/Chicago')
        return datetime.now(central_tz)
    
    @staticmethod
    def format_central_time(dt: datetime) -> str:
        """Format datetime for display in Central timezone"""
        if dt is None:
            return 'Never'
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        central_tz = pytz.timezone('America/Chicago')
        if dt.tzinfo is None:
            dt = central_tz.localize(dt)
        return dt.astimezone(central_tz).strftime('%b %d, %Y at %I:%M %p CT')
    
    @staticmethod
    def parse_graph_datetime(dt_dict: Dict) -> datetime:
        """Parse Microsoft Graph datetime with timezone handling"""
        if not dt_dict:
            return None
        
        dt_str = dt_dict.get('dateTime', '')
        tz_str = dt_dict.get('timeZone', 'UTC')
        
        if not dt_str:
            return None
        
        # Handle timezone conversion
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        
        try:
            dt = datetime.fromisoformat(dt_str)
            if tz_str != 'UTC':
                tz = pytz.timezone(tz_str)
                dt = tz.localize(dt)
            return dt
        except Exception as e:
            logging.error(f"Error parsing datetime {dt_str}: {e}")
            return None
    
    @staticmethod
    def utc_to_central(utc_dt: datetime) -> datetime:
        """Convert UTC datetime to Central Time"""
        if utc_dt is None:
            return None
        
        # If the datetime is naive (no timezone), assume it's UTC
        if utc_dt.tzinfo is None:
            utc_dt = pytz.UTC.localize(utc_dt)
        elif utc_dt.tzinfo != pytz.UTC:
            # Convert to UTC first if it has a different timezone
            utc_dt = utc_dt.astimezone(pytz.UTC)
        
        central = pytz.timezone('America/Chicago')
        return utc_dt.astimezone(central)
    
    @staticmethod
    def central_to_utc(central_dt: datetime) -> datetime:
        """Convert Central Time datetime to UTC"""
        if central_dt is None:
            return None
        
        # If the datetime is naive, assume it's Central Time
        if central_dt.tzinfo is None:
            central = pytz.timezone('America/Chicago')
            central_dt = central.localize(central_dt)
        
        return central_dt.astimezone(pytz.UTC)
    
    @staticmethod
    def iso_to_central_display(iso_string: str, include_timezone: bool = True) -> str:
        """Convert ISO string to Central Time display format"""
        if not iso_string:
            return "Never"
        
        try:
            # Parse ISO string
            if iso_string.endswith('Z'):
                dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(iso_string)
            
            return DateTimeUtils.format_central_time(dt, include_timezone)
        except:
            return iso_string
    
    @staticmethod
    def get_timezone_offset() -> str:
        """Get current Central Time offset from UTC"""
        central = pytz.timezone('America/Chicago')
        now = datetime.now(central)
        offset = now.strftime('%z')
        return f"UTC{offset[:3]}:{offset[3:]}"
    
    @staticmethod
    def random_interval(min_minutes: int = 15, max_minutes: int = 23) -> int:
        """Generate random sync interval to prevent thundering herd"""
        return random.randint(min_minutes, max_minutes)

class ValidationUtils:
    """Data validation utilities"""
    
    @staticmethod
    def validate_calendar_data(data: Dict) -> bool:
        """Validate calendar sync data"""
        required_fields = ['subject', 'start', 'end']
        return all(field in data for field in required_fields)
    
    @staticmethod
    def validate_event_integrity(source_event: Dict, target_event: Dict) -> List[str]:
        """Validate key event properties are preserved"""
        issues = []
        
        # Check subject
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
        
        return issues

class MetricsUtils:
    """Performance metrics and monitoring utilities"""
    
    @staticmethod
    def record_sync_metrics(user_id: str, events_count: int, duration: float):
        """Record sync performance metrics"""
        # In a full implementation, this would send to a metrics service
        logging.info(f"Sync metrics: user={user_id}, events={events_count}, duration={duration:.2f}s")
    
    @staticmethod
    def record_api_call(method: str, endpoint: str, status_code: int, duration_ms: float):
        """Record API call metrics"""
        logging.info(f"API call: {method} {endpoint} -> {status_code} ({duration_ms:.0f}ms)")

# =============================================================================
# EXTERNAL SERVICE UTILITIES
# =============================================================================

class ResilientAPIClient:
    """Base class with circuit breaker and retry logic"""
    
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url
        self.timeout = timeout
        self.circuit_breaker = CircuitBreaker(
            fail_max=config.CIRCUIT_BREAKER_FAIL_MAX,
            reset_timeout=config.CIRCUIT_BREAKER_RESET_TIMEOUT
        )
    
    def execute_with_retry(self, operation: Callable, max_retries: int = None, base_delay: float = None) -> Any:
        """Execute operation with circuit breaker and exponential backoff"""
        max_retries = max_retries or config.MAX_RETRIES
        base_delay = base_delay or config.BASE_DELAY
        
        try:
            # Try circuit breaker first
            return self.circuit_breaker.call(operation)
        except Exception as e:
            # If circuit is open, use cached data
            if self.circuit_breaker.current_state == 'open':
                return self.get_cached_result()
            
            # Otherwise, implement retry with exponential backoff
            for attempt in range(max_retries):
                try:
                    time.sleep(base_delay * (2 ** attempt))
                    return operation()
                except Exception:
                    if attempt == max_retries - 1:
                        raise
    
    def get_cached_result(self) -> Any:
        """Get cached result when circuit breaker is open"""
        # In a full implementation, this would return cached data
        raise Exception("Circuit breaker open - no cached data available")

class RetryUtils:
    """Retry and backoff utilities"""
    
    @staticmethod
    def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
        """Decorator for retry logic with exponential backoff"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        if attempt == max_retries - 1:
                            raise
                        time.sleep(base_delay * (2 ** attempt))
                return None
            return wrapper
        return decorator


class CircuitBreaker:
    """
    Implements the Circuit Breaker pattern to prevent cascading failures
    
    The circuit breaker has three states:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: Too many failures, all requests fail fast
    - HALF_OPEN: Testing if the service has recovered
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 300,
        expected_exception: type = Exception,
        name: Optional[str] = None
    ):
        """
        Initialize the circuit breaker
        
        Args:
            failure_threshold: Number of failures before opening the circuit
            recovery_timeout: Seconds to wait before trying half-open state
            expected_exception: Exception type to catch (others pass through)
            name: Optional name for logging
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name or "CircuitBreaker"
        
        self._state = "closed"  # closed, open, half_open
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._success_count = 0
        self._lock = threading.Lock()
        
        # Statistics
        self._total_calls = 0
        self._total_failures = 0
        self._total_successes = 0
        self._circuit_opened_count = 0
    
    @property
    def state(self) -> str:
        """Get the current state of the circuit breaker"""
        with self._lock:
            self._update_state()
            return self._state
    
    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Call the protected function through the circuit breaker
        
        Args:
            func: Function to call
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of the function call
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: If function fails
        """
        with self._lock:
            self._update_state()
            self._total_calls += 1
            
            if self._state == "open":
                error_msg = f"{self.name}: Circuit breaker is OPEN"
                logger.warning(error_msg)
                raise CircuitBreakerOpenError(error_msg)
        
        try:
            # Execute the function
            result = func(*args, **kwargs)
            
            # Record success
            with self._lock:
                self._on_success()
            
            return result
            
        except self.expected_exception as e:
            # Record failure
            with self._lock:
                self._on_failure()
            raise
    
    def _update_state(self):
        """Update circuit breaker state based on current conditions"""
        if self._state == "open":
            # Check if we should transition to half-open
            if self._last_failure_time and \
               datetime.now() - self._last_failure_time > timedelta(seconds=self.recovery_timeout):
                logger.info(f"{self.name}: Transitioning from OPEN to HALF_OPEN")
                self._state = "half_open"
                self._failure_count = 0
                self._success_count = 0
    
    def _on_success(self):
        """Handle successful function call"""
        self._total_successes += 1
        self._success_count += 1
        
        if self._state == "half_open":
            # In half-open state, we need enough successes to close
            if self._success_count >= 3:  # Require 3 successes to fully close
                logger.info(f"{self.name}: Transitioning from HALF_OPEN to CLOSED")
                self._state = "closed"
                self._failure_count = 0
                self._success_count = 0
        elif self._state == "closed":
            # Reset failure count on success in closed state
            self._failure_count = 0
    
    def _on_failure(self):
        """Handle failed function call"""
        self._total_failures += 1
        self._failure_count += 1
        self._last_failure_time = datetime.now()
        
        if self._state == "half_open":
            # Any failure in half-open state reopens the circuit
            logger.warning(f"{self.name}: Failure in HALF_OPEN state, reopening circuit")
            self._state = "open"
            self._circuit_opened_count += 1
            
        elif self._state == "closed":
            # Check if we've exceeded the failure threshold
            if self._failure_count >= self.failure_threshold:
                logger.error(
                    f"{self.name}: Failure threshold ({self.failure_threshold}) exceeded, "
                    f"opening circuit"
                )
                self._state = "open"
                self._circuit_opened_count += 1
    
    def reset(self):
        """Manually reset the circuit breaker to closed state"""
        with self._lock:
            logger.info(f"{self.name}: Manually resetting circuit breaker")
            self._state = "closed"
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
    
    def get_statistics(self) -> dict:
        """Get statistics about the circuit breaker"""
        with self._lock:
            self._update_state()
            
            success_rate = 0
            if self._total_calls > 0:
                success_rate = (self._total_successes / self._total_calls) * 100
            
            return {
                'name': self.name,
                'state': self._state,
                'total_calls': self._total_calls,
                'total_successes': self._total_successes,
                'total_failures': self._total_failures,
                'success_rate': round(success_rate, 2),
                'current_failure_count': self._failure_count,
                'circuit_opened_count': self._circuit_opened_count,
                'last_failure_time': self._last_failure_time.isoformat() if self._last_failure_time else None
            }


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open"""
    pass


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: int = 300,
    expected_exception: type = Exception,
    name: Optional[str] = None
) -> Callable:
    """
    Decorator to apply circuit breaker pattern to a function
    
    Example:
        @circuit_breaker(failure_threshold=3, recovery_timeout=60)
        def call_external_api():
            # API call that might fail
            pass
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        breaker_name = name or f"CircuitBreaker-{func.__name__}"
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
            name=breaker_name
        )
        
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return breaker.call(func, *args, **kwargs)
        
        # Add methods to access breaker
        wrapper.reset = breaker.reset
        wrapper.get_statistics = breaker.get_statistics
        wrapper.circuit_breaker = breaker
        
        return wrapper
    
    return decorator

# =============================================================================
# LOGGING UTILITIES
# =============================================================================

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
            "timestamp": DateTimeUtils.get_central_time().isoformat(),
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
        """Log API call with performance metrics"""
        log_entry = {
            "timestamp": DateTimeUtils.get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "event_type": "api_call",
            "service": "calendar-sync",
            "logger": self.name,
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "error": error
        }
        
        if error or (status_code and status_code >= 400):
            self.logger.error(json.dumps(log_entry))
        else:
            self.logger.info(json.dumps(log_entry))

class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record):
        log_entry = {
            "timestamp": DateTimeUtils.get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
        }
        
        if hasattr(record, 'event_type'):
            log_entry['event_type'] = record.event_type
        
        return json.dumps(log_entry)

# =============================================================================
# CACHE UTILITIES
# =============================================================================

class CacheManager:
    """Simple file-based cache manager"""
    
    def __init__(self, cache_dir: str = '/data'):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def get(self, key: str) -> Optional[Dict]:
        """Get cached data"""
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    # Check if cache is expired
                    if 'expires_at' in data:
                        expires_at = datetime.fromisoformat(data['expires_at'])
                        if datetime.now() < expires_at:
                            return data['value']
            return None
        except Exception as e:
            logging.error(f"Error reading cache {key}: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl_hours: int = None):
        """Set cached data with TTL"""
        ttl_hours = ttl_hours or config.CACHE_TTL_HOURS
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        try:
            data = {
                'value': value,
                'expires_at': (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
            }
            with open(cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logging.error(f"Error writing cache {key}: {e}")
    
    def clear(self, key: str):
        """Clear cached data"""
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        try:
            if os.path.exists(cache_file):
                os.remove(cache_file)
        except Exception as e:
            logging.error(f"Error clearing cache {key}: {e}")

# =============================================================================
# DECORATORS
# =============================================================================

def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import g, jsonify
        if not g.user:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated

def handle_api_errors(f):
    """Decorator to handle API errors consistently"""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import jsonify, current_app
        try:
            return f(*args, **kwargs)
        except Exception as e:
            current_app.logger.error(f"API error in {f.__name__}: {str(e)}")
            return jsonify({'error': 'Internal server error'}), 500
    return decorated

def rate_limit(max_requests: int = None, window_hours: int = 1):
    """Simple rate limiting decorator"""
    max_requests = max_requests or config.MAX_SYNC_REQUESTS_PER_HOUR
    
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # In a full implementation, this would check rate limits
            # For now, just log the request
            logging.info(f"Rate limit check for {f.__name__}")
            return f(*args, **kwargs)
        return decorated
    return decorator

# =============================================================================
# VERSION MANAGEMENT
# =============================================================================

def get_version_info():
    """Get version info automatically"""
    now = datetime.utcnow()
    version_info = {
        "version": now.strftime("%Y.%m.%d"),
        "build_number": 1,
        "commit_hash": "unknown",
        "build_date": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "environment": "unknown"
    }
    
    try:
        # Try to get git info
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version_info["commit_hash"] = result.stdout.strip()
        
        # Get commit count for build number
        result = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            count = int(result.stdout.strip())
            version_info["build_number"] = count
            version_info["version"] = f"{now.strftime('%Y.%m.%d')}.{count}"
        
        # Get branch name
        result = subprocess.run(['git', 'branch', '--show-current'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            branch = result.stdout.strip()
            version_info["branch"] = branch
            version_info["environment"] = "production" if branch == "main" else "development"
    
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        # Git not available
        timestamp = int(now.timestamp())
        version_info["version"] = f"{now.strftime('%Y.%m.%d')}.{timestamp}"
        version_info["build_number"] = timestamp
        logger.info("Git not available, using date-based version")
    
    # Add deployment info
    if "RENDER" in os.environ:
        version_info["deployment_platform"] = "Render.com"
        version_info["service_name"] = os.environ.get("RENDER_SERVICE_NAME", "calendar-sync")
        version_info["environment"] = "production"
    else:
        version_info["deployment_platform"] = "Local Development"
        version_info["environment"] = "local"
    
    # Add display strings
    version_info["version_string"] = f"v{version_info['version']}"
    version_info["build_string"] = f"Build #{version_info['build_number']}"
    version_info["full_version"] = f"Calendar Sync v{version_info['version']} (Build #{version_info['build_number']})"
    
    if version_info["commit_hash"] != "unknown":
        version_info["full_version"] += f" [{version_info['commit_hash']}]"
    
    return version_info


# =============================================================================
# INITIALIZATION
# =============================================================================

# Initialize global cache manager
cache_manager = CacheManager()

# Initialize structured logger
structured_logger = StructuredLogger('calendar-sync')

# Export main utilities
__all__ = [
    'DateTimeUtils', 'ValidationUtils', 'MetricsUtils',
    'ResilientAPIClient', 'RetryUtils', 'StructuredLogger',
    'CacheManager', 'require_auth', 'handle_api_errors', 'rate_limit',
    'cache_manager', 'structured_logger', 'get_version_info'
] 