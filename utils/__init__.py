# Import and expose all utilities from the original utils_original.py module
# This maintains compatibility while allowing the utils package structure

# Import everything from the renamed utils_original.py file
import sys
import os

# Get the current directory (where utils package is)
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (where utils_original.py is)
parent_dir = os.path.dirname(current_dir)

# Import the original utils module directly
import importlib.util
spec = importlib.util.spec_from_file_location("utils_original", os.path.join(parent_dir, "utils_original.py"))
utils_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils_module)

# Expose all the classes and functions that other modules import
DateTimeUtils = utils_module.DateTimeUtils
RetryUtils = utils_module.RetryUtils
CircuitBreaker = utils_module.CircuitBreaker
CircuitBreakerOpenError = utils_module.CircuitBreakerOpenError
structured_logger = utils_module.structured_logger

# Expose other commonly used utilities
ValidationUtils = utils_module.ValidationUtils
MetricsUtils = utils_module.MetricsUtils
ResilientAPIClient = utils_module.ResilientAPIClient
StructuredLogger = utils_module.StructuredLogger
JsonFormatter = utils_module.JsonFormatter
CacheManager = utils_module.CacheManager

# Expose module-level functions
circuit_breaker = utils_module.circuit_breaker
require_auth = utils_module.require_auth
handle_api_errors = utils_module.handle_api_errors
rate_limit = utils_module.rate_limit
get_version_info = utils_module.get_version_info
