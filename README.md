# Calendar Sync Service

A secure, reliable calendar synchronization service that automatically syncs events from internal calendars to a public-facing calendar using Microsoft Graph API.

## 🚀 Quick Start

The application is deployed and running with automatic synchronization every 23 minutes.

### Health Check Endpoints
- **Basic Health**: `GET /health` - Responds immediately for deployment health checks
- **Readiness Check**: `GET /ready` - Checks if sync services are fully initialized
- **Detailed Health**: `GET /health/detailed` - Comprehensive system status

## 🏗️ Architecture

### Simplified Code Structure
The application has been consolidated from 36+ files to 8 core files for better maintainability:

```
calendar-sync-service/
├── app.py              # Main Flask application with routes
├── auth.py             # Microsoft OAuth authentication
├── calendar_ops.py     # Calendar reading/writing operations
├── sync.py             # Core synchronization engine
├── utils.py            # Consolidated utilities (timezone, retry, logging)
├── config.py           # Environment-based configuration
├── gunicorn.conf.py    # Production server configuration
└── requirements.txt    # Python dependencies
```

### Key Components

#### **Authentication (`auth.py`)**
- Microsoft OAuth 2.0 authentication flow
- Token refresh and management
- Persistent token storage
- Bot detection for security

#### **Calendar Operations (`calendar_ops.py`)**
- `CalendarReader`: Fetches events from source calendars
- `CalendarWriter`: Creates/updates events in target calendars
- Timezone-aware date handling
- Batch operations for performance

#### **Synchronization Engine (`sync.py`)**
- `SyncEngine`: Core sync logic and coordination
- `SyncScheduler`: Automated sync scheduling
- Change tracking and conflict resolution
- Comprehensive error handling and retry logic

#### **Utilities (`utils.py`)**
- `DateTimeUtils`: Timezone handling and formatting
- `RetryUtils`: Exponential backoff retry logic
- `StructuredLogger`: JSON-formatted logging
- Circuit breaker pattern for API resilience

## 🔒 Security Features

### OAuth Security
- **HTTPS Enforcement**: All OAuth flows use HTTPS exclusively
- **Bot Detection**: Prevents automated attacks on OAuth endpoints
- **State Validation**: CSRF protection for OAuth callbacks
- **Token Security**: Secure storage and automatic refresh

### Security Headers
- **HSTS**: HTTP Strict Transport Security (1-year max-age)
- **CSP**: Content Security Policy with strict resource controls
- **X-Frame-Options**: DENY (prevents clickjacking)
- **X-Content-Type-Options**: nosniff
- **X-XSS-Protection**: 1; mode=block

### Deployment Security
- **Graceful Shutdown**: Proper signal handling for deployments
- **Background Initialization**: Heavy operations don't block startup
- **Health Check Isolation**: Fast health endpoints for deployment monitoring

## 📊 Monitoring & Observability

### Health Endpoints
```bash
# Basic health check (responds immediately)
curl /health

# Readiness check (sync services status)
curl /ready

# Detailed system status
curl /status
```

### Debug Endpoints
- `GET /debug` - Basic system information
- `GET /metrics` - Performance metrics
- `GET /history` - Sync operation history
- `POST /validate-sync` - Manual sync validation

### Logging
- **Structured JSON logs** for easy parsing
- **Security event logging** for audit trails
- **Performance metrics** for optimization
- **Error tracking** with full context

## ⚙️ Configuration

### Environment Variables

#### **Required for OAuth**
```bash
CLIENT_ID=your_client_id_here
CLIENT_SECRET=your_microsoft_app_secret_here
TENANT_ID=your_tenant_id_here
REDIRECT_URI=https://your-app-domain.onrender.com/auth/callback
```

#### **Calendar Configuration**
```bash
SHARED_MAILBOX=your_shared_mailbox@yourdomain.org
SOURCE_CALENDAR=Calendar
TARGET_CALENDAR=Public Calendar
```

#### **Sync Settings**
```bash
SYNC_CUTOFF_DAYS=1825          # 5 years of events
SYNC_INTERVAL_MIN=23           # Sync every 23 minutes
DRY_RUN_MODE=False             # Set to True for testing
```

### Production Configuration
- **Gunicorn Timeout**: 300 seconds for long sync operations
- **Graceful Shutdown**: 30 seconds for cleanup
- **Health Check**: Responds in <5 seconds
- **Background Initialization**: Heavy operations in daemon threads

## 🔄 Sync Process

### Automatic Sync
1. **Scheduler**: Runs every 23 minutes automatically
2. **Authentication**: Validates Microsoft Graph tokens
3. **Source Reading**: Fetches events from internal calendar
4. **Filtering**: Only public events within date range
5. **Target Writing**: Creates/updates events in public calendar
6. **Change Tracking**: Monitors for conflicts and duplicates

### Manual Sync
- **Web Interface**: Click "Sync Now" button
- **API Endpoint**: `POST /sync`
- **Real-time Status**: Immediate feedback on sync progress

### Sync Validation
- **Event Count Matching**: Ensures no data loss
- **Privacy Checks**: Confirms no private events leaked
- **Date Range Validation**: Events within configured limits
- **Duplicate Detection**: Prevents duplicate events

## 🚨 Troubleshooting

### Common Issues

#### **Authentication Failures**
- **Check**: Token refresh and Microsoft Graph connectivity
- **Debug**: Use `/debug` endpoint for detailed status
- **Recovery**: Automatic retry with exponential backoff

#### **Sync Performance Issues**
- **Monitor**: `/metrics` endpoint for performance data
- **Optimize**: Batch operations and caching
- **Scale**: Adjust sync intervals if needed

### Debug Commands
```bash
# Check system status
curl /status

# View recent metrics
curl /metrics

# Validate current sync
curl -X POST /validate-sync

# Check sync history
curl /history
```

## 📈 Performance

### Optimizations
- **Batch Operations**: Microsoft Graph batch API for efficiency
- **Caching**: Calendar IDs cached for 1 hour
- **Delta Queries**: Incremental sync when possible
- **Background Processing**: Non-blocking web requests

### Benchmarks
- **Health Check**: <100ms response time
- **Sync Operation**: 30-100 seconds for full sync
- **API Calls**: <500ms average latency
- **Success Rate**: >95% under normal conditions

## 🔧 Development

### Local Setup
```bash
# Clone repository
git clone <repository-url>
cd calendar-sync-service

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export CLIENT_ID=your_client_id
export CLIENT_SECRET=your_client_secret
export TENANT_ID=your_tenant_id

# Run development server
python app.py
```

### Code Structure
- **Flask Routes**: All endpoints in `app.py`
- **Business Logic**: Separated into focused modules
- **Configuration**: Environment-based in `config.py`
- **Utilities**: Consolidated in `utils.py`

## 📋 API Reference

### Authentication
- `GET /` - Main dashboard with OAuth login
- `GET /auth/callback` - OAuth callback (with bot detection)
- `GET /logout` - Clear session and tokens

### Sync Operations
- `POST /sync` - Trigger manual sync
- `GET /sync/status` - Check sync status
- `POST /scheduler/toggle` - Pause/resume automatic sync

### Monitoring
- `GET /health` - Basic health check
- `GET /ready` - Service readiness check
- `GET /status` - Detailed system status
- `GET /metrics` - Performance metrics
- `GET /history` - Sync history

### Debug (Development)
- `GET /debug` - System information
- `POST /validate-sync` - Manual validation
- `GET /debug/calendars` - Calendar listing
- `GET /debug/events/<calendar>` - Event debugging

## 🛡️ Security Considerations

### OAuth Security
- **HTTPS Only**: All OAuth flows use HTTPS
- **State Validation**: CSRF protection implemented
- **Token Security**: Secure storage and refresh
- **Bot Detection**: Prevents automated attacks

### Data Protection
- **Event Privacy**: Only public events synced
- **No Sensitive Data**: Location and details filtered
- **Audit Logging**: Security events tracked
- **Access Control**: Microsoft Graph permissions only

### Deployment Security
- **Security Headers**: Comprehensive HTTP security
- **Graceful Shutdown**: Prevents data corruption
- **Health Monitoring**: Continuous security checks
- **Error Handling**: Secure error responses

## 📞 Support

### Monitoring
- **Health Checks**: Automatic monitoring via Render
- **Logs**: Structured logging for debugging
- **Metrics**: Performance tracking and alerting
- **Audit Trail**: Security event logging

## 📄 License

**© 2024–2025 Harnisch LLC. All Rights Reserved.**

This software is licensed exclusively for use by the authorized organization. Unauthorized use, distribution, or modification is prohibited.
