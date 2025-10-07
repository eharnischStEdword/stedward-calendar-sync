# Calendar Sync Service - System Architecture

## Overview

The Calendar Sync Service automatically synchronizes events from internal calendars to a public-facing calendar using Microsoft Graph API.

## Core Components

### Authentication (`auth.py`)
- Microsoft OAuth 2.0 authentication flow
- Token refresh and management
- Persistent token storage in `/data/token.json`
- Bot detection for security

### Calendar Operations (`calendar_ops.py`)

#### CalendarReader
- Fetches events from source calendars
- Handles pagination (`@odata.nextLink`)
- Filters by date range
- Returns structured event data

#### CalendarWriter
- Creates events in target calendar
- Updates existing events
- Deletes events no longer in source
- Batch operations for performance

### Synchronization Engine (`sync.py`)

#### SyncEngine
Core sync logic that:
1. Fetches events from source calendar
2. Filters events (only Public + Busy)
3. Generates signatures for duplicate detection
4. Determines ADD/UPDATE/DELETE operations
5. Executes operations via CalendarWriter
6. Maintains sync state

#### Key Methods
- `sync_calendars()` - Main sync orchestration
- `_determine_sync_operations()` - Change detection
- `_is_synced_event()` - Identifies synced vs manual events
- `_needs_update()` - Determines if event changed

### Utilities (`utils.py`)

#### DateTimeUtils
- Timezone conversion (UTC ↔ Central Time)
- Date formatting
- Timezone-aware operations

#### RetryUtils
- Exponential backoff for API calls
- Rate limit handling
- Transient error recovery

#### CircuitBreaker
- Prevents cascading failures
- Automatic recovery after cooldown
- Monitors error rates

#### StructuredLogger
- JSON formatted logs
- Timezone-aware timestamps
- Event tracking

### Signature Generation (`signature_utils.py`)

CRITICAL module for duplicate detection. Generates unique signatures from:
- Event subject
- Start date/time
- End date/time
- Location
- All-day flag

**DO NOT MODIFY** without comprehensive testing.

## Data Flow

```
1. Scheduler triggers sync
   ↓
2. SyncEngine.sync_calendars()
   ↓
3. CalendarReader fetches source events
   ↓
4. Filter: Only Public + Busy events
   ↓
5. Generate signatures for all events
   ↓
6. Compare source vs target signatures
   ↓
7. Determine ADD/UPDATE/DELETE operations
   ↓
8. CalendarWriter executes operations
   ↓
9. Log results and update state
```

## Configuration

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `CLIENT_ID` | Azure AD app client ID | `abc123...` |
| `CLIENT_SECRET` | Azure AD app secret | `xyz789...` |
| `TENANT_ID` | Azure AD tenant ID | `def456...` |
| `REDIRECT_URI` | OAuth callback URL | `https://domain.com/callback` |
| `SHARED_MAILBOX` | Calendar mailbox | `calendar@stedward.org` |
| `SOURCE_CALENDAR` | Source calendar name | `Calendar` |
| `TARGET_CALENDAR` | Target calendar name | `St. Edward Public Calendar` |
| `SYNC_CUTOFF_DAYS` | Days to sync (past + future) | `1825` (5 years) |
| `SYNC_INTERVAL_MIN` | Minutes between syncs | `23` |
| `MASTER_CALENDAR_PROTECTION` | Write protection | `true` |

### File Locations

- **Token Storage**: `/data/token.json`
- **Cache**: `/data/cache/`
- **Logs**: stdout/stderr (captured by hosting platform)

## Security Features

### Master Calendar Protection
`MASTER_CALENDAR_PROTECTION=true` prevents ANY writes to source calendar.

### Bot Detection
Checks `User-Agent` headers to block automated scrapers.

### Rate Limiting
- Respects Microsoft Graph API rate limits
- Exponential backoff on rate limit responses
- Circuit breaker prevents cascading failures

### OAuth Security
- Secure token storage
- Automatic token refresh
- No credentials in logs

## Error Handling

### Retry Strategy
1. Immediate retry for transient errors
2. Exponential backoff for rate limits
3. Circuit breaker for persistent failures

### Error Categories
- **Transient**: Network errors, timeouts → Retry
- **Rate Limit**: 429 responses → Backoff and retry
- **Auth**: 401/403 → Token refresh, re-auth
- **Permanent**: 400/404 → Log and skip

## Performance Considerations

### Pagination
- Microsoft Graph returns max 100 events per page
- Always follow `@odata.nextLink` for complete results

### Batch Operations
- Group operations to reduce API calls
- Balance between throughput and error isolation

### Caching
- Token caching reduces auth overhead
- Event caching (optional) for faster sync

## Monitoring

### Health Endpoints
- `GET /health` - Basic health check
- `GET /ready` - Readiness check (sync initialized)
- `GET /health/detailed` - Full system status

### Key Metrics
- Sync success rate
- Events added/updated/deleted per sync
- API call response times
- Error rates by category

### Logging
All logs in JSON format with:
- Timestamp (Central Time)
- Event type
- Service identifier
- Structured details
