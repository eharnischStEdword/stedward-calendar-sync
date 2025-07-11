# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Retry Utilities - Implement retry logic with exponential backoff
"""
import time
import logging
from functools import wraps
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    retry_on: tuple = (Exception,)
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator that implements retry logic with exponential backoff
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        exponential_base: Base for exponential backoff calculation
        retry_on: Tuple of exceptions to retry on
    
    Returns:
        Decorated function that will retry on specified exceptions
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            last_exception = None
            
            while attempt <= max_retries:
                try:
                    # Log attempt if it's a retry
                    if attempt > 0:
                        logger.info(f"Retry attempt {attempt}/{max_retries} for {func.__name__}")
                    
                    # Try to execute the function
                    result = func(*args, **kwargs)
                    
                    # If successful and it was a retry, log success
                    if attempt > 0:
                        logger.info(f"Retry successful for {func.__name__} after {attempt} attempts")
                    
                    return result
                    
                except retry_on as e:
                    last_exception = e
                    
                    # If we've exhausted retries, raise the exception
                    if attempt >= max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}. "
                            f"Final error: {str(e)}"
                        )
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)
                    
                    logger.warning(
                        f"Error in {func.__name__} (attempt {attempt + 1}/{max_retries + 1}): "
                        f"{type(e).__name__}: {str(e)}. Retrying in {delay:.1f} seconds..."
                    )
                    
                    # Wait before retrying
                    time.sleep(delay)
                    attempt += 1
                    
                except Exception as e:
                    # If it's not a retryable exception, raise immediately
                    logger.error(f"Non-retryable error in {func.__name__}: {type(e).__name__}: {str(e)}")
                    raise
            
            # This should never be reached, but just in case
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


def retry_with_jitter(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_factor: float = 0.1
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator that implements retry logic with exponential backoff and jitter
    
    Jitter helps prevent thundering herd problem by adding randomness to retry delays
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        jitter_factor: Factor for random jitter (0.1 = ±10% of calculated delay)
    
    Returns:
        Decorated function that will retry with jittered delays
    """
    import random
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            
            while attempt <= max_retries:
                try:
                    return func(*args, **kwargs)
                    
                except Exception as e:
                    if attempt >= max_retries:
                        logger.error(f"Max retries exceeded for {func.__name__}: {str(e)}")
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    
                    # Add jitter
                    jitter = delay * jitter_factor * (2 * random.random() - 1)
                    actual_delay = max(0, delay + jitter)
                    
                    logger.warning(
                        f"Retrying {func.__name__} in {actual_delay:.1f}s "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
                    
                    time.sleep(actual_delay)
                    attempt += 1
                    
        return wrapper
    return decorator


class RetryContext:
    """
    Context manager for retry logic, useful for retrying blocks of code
    
    Example:
        with RetryContext(max_retries=3) as retry:
            while retry.should_retry():
                try:
                    # Your code here
                    break
                except Exception as e:
                    retry.record_failure(e)
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.attempt = 0
        self.last_exception = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        return False
    
    def should_retry(self) -> bool:
        """Check if we should continue retrying"""
        return self.attempt <= self.max_retries
    
    def record_failure(self, exception: Exception):
        """Record a failure and sleep if we should retry"""
        self.last_exception = exception
        
        if self.attempt >= self.max_retries:
            logger.error(f"Max retries ({self.max_retries}) exceeded. Final error: {str(exception)}")
            raise exception
        
        # Calculate delay
        delay = min(self.base_delay * (self.exponential_base ** self.attempt), self.max_delay)
        
        logger.warning(
            f"Retry attempt {self.attempt + 1}/{self.max_retries + 1} "
            f"failed: {str(exception)}. Retrying in {delay:.1f} seconds..."
        )
        
        time.sleep(delay)
        self.attempt += 1
    
    def reset(self):
        """Reset the retry context for reuse"""
        self.attempt = 0
        self.last_exception = None
