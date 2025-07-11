# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Circuit Breaker - Prevent cascading failures by stopping requests to failing services
"""
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, TypeVar, Any, Optional
import logging
from threading import Lock

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Possible states of the circuit breaker"""
    CLOSED = "closed"      # Normal operation, requests allowed
    OPEN = "open"          # Failure threshold exceeded, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


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
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._success_count = 0
        self._lock = Lock()
        
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
            return self._state.value
    
    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
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
            
            if self._state == CircuitState.OPEN:
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
        if self._state == CircuitState.OPEN:
            # Check if we should transition to half-open
            if self._last_failure_time and \
               datetime.now() - self._last_failure_time > timedelta(seconds=self.recovery_timeout):
                logger.info(f"{self.name}: Transitioning from OPEN to HALF_OPEN")
                self._state = CircuitState.HALF_OPEN
                self._failure_count = 0
                self._success_count = 0
    
    def _on_success(self):
        """Handle successful function call"""
        self._total_successes += 1
        self._success_count += 1
        
        if self._state == CircuitState.HALF_OPEN:
            # In half-open state, we need enough successes to close
            if self._success_count >= 3:  # Require 3 successes to fully close
                logger.info(f"{self.name}: Transitioning from HALF_OPEN to CLOSED")
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        elif self._state == CircuitState.CLOSED:
            # Reset failure count on success in closed state
            self._failure_count = 0
    
    def _on_failure(self):
        """Handle failed function call"""
        self._total_failures += 1
        self._failure_count += 1
        self._last_failure_time = datetime.now()
        
        if self._state == CircuitState.HALF_OPEN:
            # Any failure in half-open state reopens the circuit
            logger.warning(f"{self.name}: Failure in HALF_OPEN state, reopening circuit")
            self._state = CircuitState.OPEN
            self._circuit_opened_count += 1
            
        elif self._state == CircuitState.CLOSED:
            # Check if we've exceeded the failure threshold
            if self._failure_count >= self.failure_threshold:
                logger.error(
                    f"{self.name}: Failure threshold ({self.failure_threshold}) exceeded, "
                    f"opening circuit"
                )
                self._state = CircuitState.OPEN
                self._circuit_opened_count += 1
    
    def reset(self):
        """Manually reset the circuit breaker to closed state"""
        with self._lock:
            logger.info(f"{self.name}: Manually resetting circuit breaker")
            self._state = CircuitState.CLOSED
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
                'state': self._state.value,
                'total_calls': self._total_calls,
                'total_successes': self._total_successes,
                'total_failures': self._total_failures,
                'success_rate': round(success_rate, 2),
                'current_failure_count': self._failure_count,
                'circuit_opened_count': self._circuit_opened_count,
                'last_failure_time': self._last_failure_time.isoformat() if self._last_failure_time else None
            }
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if exc_type is not None:
            with self._lock:
                if issubclass(exc_type, self.expected_exception):
                    self._on_failure()
        else:
            with self._lock:
                self._on_success()
        return False


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
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        breaker_name = name or f"CircuitBreaker-{func.__name__}"
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
            name=breaker_name
        )
        
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return breaker.call(func, *args, **kwargs)
        
        # Add methods to access breaker
        wrapper.reset = breaker.reset
        wrapper.get_statistics = breaker.get_statistics
        wrapper.circuit_breaker = breaker
        
        return wrapper
    
    return decorator
