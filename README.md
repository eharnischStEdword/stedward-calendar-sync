# Calendar Sync Enhancement Documentation

## Overview

This document outlines the comprehensive improvements made to the St. Edward Calendar Sync application. These enhancements focus on reliability, observability, security, and performance.

## Key Improvements

### 1. **Reliability Enhancements**

#### Retry Logic with Exponential Backoff
- **Location**: `utils/retry.py`
- **Implementation**: All API calls now automatically retry on failure
- **Features**:
  - Configurable retry attempts (default: 3)
  - Exponential backoff to prevent overwhelming the API
  - Jitter support to avoid thundering herd
  - Decorator pattern for easy application

#### Circuit Breaker Pattern
- **Location**: `utils/circuit_breaker.py`
- **Purpose**: Prevents cascading failures when Microsoft Graph API is down
- **States**:
  - CLOSED: Normal operation
  - OPEN: Too many failures, requests fail fast
  - HALF_OPEN: Testing recovery
- **Configuration**: 5 failures trigger open state, 5-minute recovery timeout

### 2. **Observability Improvements**

#### Structured Logging
- **Location**: `utils/logger.py`
- **Benefits**:
  - JSON-formatted logs for easy parsing
  - Consistent log structure across the application
  - Integration with log aggregation tools
  - Performance tracking built-in

#### Metrics Collection
- **Location**: `utils/metrics.py`
- **Tracked Metrics**:
  - Sync duration and performance percentiles
  - API call latencies and success rates
  - Operation counts (added/updated/deleted)
  - Error frequencies and types
- **Available Endpoints**:
  - `/metrics` - Current metrics summary
  - `/history` - Historical sync data

#### Sync History
- **Location**: `sync/history.py`
- **Features**:
  - Tracks last 100 sync operations
  - Provides trend analysis
  - Hourly breakdown of sync activity
  - Success rate calculations

### 3. **Security Enhancements**

#### Audit Logging
- **Location**: `utils/audit.py`
- **Logged Events**:
  - Authentication attempts
  - Configuration changes
  - Sync operations
  - Security events
- **Features**:
  - Tamper-evident logging with checksums
  - Sensitive data redaction
  - Structured format for SIEM integration

#### Request Signing
- **Location**: `auth/request_signing.py`
- **Use Cases**:
  - Webhook security
  - API authentication
  - Token generation
- **Features**:
  - HMAC-SHA256 signatures
  - Replay attack prevention with timestamps
  - Constant-time comparison

### 4. **Data Integrity**

#### Sync Validator
- **Location**: `sync/validator.py`
- **Validation Checks**:
  - Event count matching
  - No private event leakage
  - All events marked as public
  - No old events beyond cutoff
  - No duplicates
  - Recurring event integrity
  - Event property preservation
- **Available at**: `/validate-sync` endpoint

### 5. **Performance Optimizations**

#### Calendar ID Caching
- **Implementation**: Reader caches calendar IDs for 1 hour
- **Benefit**: Reduces API calls for frequently accessed calendars

#### Batch Operations
- **Location**: `cal_ops/writer.py`
- **Features**:
  - Batch create/delete operations
  - Microsoft Graph batch API usage
  - Configurable batch sizes

#### Enhanced Error Handling
- **Features**:
  - Pre-flight checks before sync
  - Graceful degradation
  - Detailed error reporting
  - Recovery procedures

### 6. **Operational Improvements**

#### Health Checks
- **Endpoints**:
  - `/health` - Basic health status
  - `/health/detailed` - Comprehensive system checks
- **Monitored Components**:
  - Authentication status
  - Microsoft API connectivity
  - Calendar access
  - Scheduler status
  - Circuit breaker state

#### Graceful Shutdown
- **Implementation**: Signal handlers for SIGINT/SIGTERM
- **Features**:
  - Waits for in-progress syncs
  - Clean resource cleanup
  - Prevents data corruption

## New API Endpoints

### Monitoring & Debugging
- `GET /metrics` - Real-time metrics
- `GET /history` - Sync history and statistics
- `GET /health/detailed` - Detailed health check
- `POST /validate-sync` - Manual sync validation
- `GET /debug/events/<calendar_name>` - Debug calendar events (dev only)

### Configuration
- `GET /enable-dry-run` - Enable dry run mode
- `GET /disable-dry-run` - Disable dry run mode

## Usage Examples

### Applying Retry Logic to New Functions
```python
from utils.retry import retry_with_backoff

@retry_with_backoff(max_retries=3, base_delay=1)
def my_api_call():
    # Your API call here
    pass
```

### Using Circuit Breaker
```python
from utils.circuit_breaker import circuit_breaker

@circuit_breaker(failure_threshold=3, recovery_timeout=60)
def external_service_call():
    # Your external call here
    pass
```

### Structured Logging
```python
from utils.logger import StructuredLogger

logger = StructuredLogger(__name__)
logger.log_sync_event('sync_started', {
    'source_calendar': 'Calendar',
    'target_calendar': 'Public Calendar'
})
```

### Audit Logging
```python
from utils.audit import audit

audit.log_sync_operation(
    'manual_sync_triggered',
    user='john@stedward.org',
    details={'ip': request.remote_addr}
)
```

## Configuration

### Environment Variables
No new required environment variables. The improvements use existing configuration.

### Optional Tuning
- Circuit breaker thresholds can be adjusted in `sync/engine.py`
- Retry attempts and delays can be configured per-function
- Metrics retention period is 7 days by default
- Sync history keeps last 100 entries

## Monitoring Recommendations

1. **Set up alerts for**:
   - Circuit breaker opening (service degradation)
   - High sync failure rates (>10%)
   - Sync duration exceeding thresholds
   - Authentication failures

2. **Regular monitoring**:
   - Check `/health/detailed` endpoint
   - Review `/metrics` for performance trends
   - Analyze audit logs for security events

3. **Performance baselines**:
   - Normal sync duration: 2-10 seconds
   - API call latency: <500ms average
   - Success rate: >95%

## Troubleshooting

### Circuit Breaker Open
1. Check Microsoft Graph API status
2. Review recent error logs
3. Wait for automatic recovery or manually reset

### High Sync Duration
1. Check number of events being synced
2. Review API call metrics
3. Look for rate limiting

### Validation Failures
1. Run `/validate-sync` endpoint
2. Check for private events in source
3. Verify calendar permissions

## Best Practices

1. **Always use retry decorators** on new API calls
2. **Log security events** to audit logger
3. **Track performance** for new operations
4. **Validate sync results** after major changes
5. **Monitor circuit breaker state** during deployments

## Future Enhancements

1. **Webhook Support** - Real-time sync triggers
2. **Distributed Tracing** - Request correlation across services
3. **Custom Dashboards** - Grafana integration
4. **Automated Recovery** - Self-healing procedures
5. **A/B Testing** - Gradual rollout support

## Migration Notes

These improvements are designed to be backward compatible. No data migration is required. Simply deploy the new code and the enhancements will take effect immediately.

### Rollback Procedure
If issues arise, the previous version can be restored without data loss. The new features will simply be unavailable.

## Support

For questions or issues with these enhancements:
1. Check the audit logs for security events
2. Review metrics for performance issues
3. Examine structured logs for detailed errors
4. Use debug endpoints for investigation

Remember: These improvements are designed to make the system more reliable and easier to operate. Take advantage of the new observability features to understand system behavior better.

## Ownership & Licensing

This project was developed by **Harnisch LLC**.

All source code and related assets are © 2024–2025 Harnisch LLC. All rights reserved.

**Exclusive usage rights have been granted to St. Edward Church & School (Nashville, TN)**. This software may not be used, copied, modified, distributed, or deployed outside of that context without explicit written permission from Harnisch LLC.

If you are interested in licensing this tool for your organization, contact: eric@ericharnisch.com
