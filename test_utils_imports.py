#!/usr/bin/env python3
"""
Pre-cleanup verification: Test that all utils imports work
Run this before AND after restructure to verify nothing broke
"""

def test_imports():
    """Test all utils imports work correctly"""
    print("Testing utils imports...")
    
    try:
        from utils import DateTimeUtils
        print("✓ DateTimeUtils")
        
        from utils import RetryUtils
        print("✓ RetryUtils")
        
        from utils import CircuitBreaker, CircuitBreakerOpenError
        print("✓ CircuitBreaker, CircuitBreakerOpenError")
        
        from utils import structured_logger
        print("✓ structured_logger")
        
        from utils import ValidationUtils
        print("✓ ValidationUtils")
        
        from utils import MetricsUtils
        print("✓ MetricsUtils")
        
        from utils import ResilientAPIClient
        print("✓ ResilientAPIClient")
        
        from utils import StructuredLogger, JsonFormatter
        print("✓ StructuredLogger, JsonFormatter")
        
        from utils import CacheManager
        print("✓ CacheManager")
        
        from utils import circuit_breaker, require_auth
        print("✓ circuit_breaker, require_auth")
        
        from utils import handle_api_errors, rate_limit
        print("✓ handle_api_errors, rate_limit")
        
        from utils import get_version_info
        print("✓ get_version_info")
        
        print("\n✅ ALL IMPORTS SUCCESSFUL")
        return True
        
    except ImportError as e:
        print(f"\n❌ IMPORT FAILED: {e}")
        return False

def test_basic_functionality():
    """Test basic utils functionality"""
    print("\nTesting basic functionality...")
    
    try:
        from utils import DateTimeUtils
        
        # Test timezone utility works
        current_time = DateTimeUtils.get_central_time()
        print(f"✓ DateTimeUtils.get_central_time() works: {current_time}")
        
        print("\n✅ BASIC FUNCTIONALITY WORKS")
        return True
        
    except Exception as e:
        print(f"\n❌ FUNCTIONALITY TEST FAILED: {e}")
        return False

if __name__ == "__main__":
    imports_ok = test_imports()
    functionality_ok = test_basic_functionality()
    
    if imports_ok and functionality_ok:
        print("\n🎉 PRE-CLEANUP STATE: ALL TESTS PASS")
        exit(0)
    else:
        print("\n⚠️  PRE-CLEANUP STATE: TESTS FAILED")
        exit(1)