# Deployment Guide

## Prerequisites

1. **Azure AD App Registration**
   - Client ID
   - Client Secret
   - Tenant ID
   - Redirect URI configured

2. **Calendar Access**
   - Shared mailbox: `calendar@stedward.org`
   - Source calendar: `Calendar`
   - Target calendar: `St. Edward Public Calendar`
   - Appropriate permissions granted

3. **Hosting Platform**
   - Python 3.9+
   - Persistent storage at `/data`
   - Environment variable support

## Environment Variables

Set these in your hosting platform:

```bash
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
TENANT_ID=your-tenant-id
REDIRECT_URI=https://your-domain.com/callback

SHARED_MAILBOX=calendar@stedward.org
SOURCE_CALENDAR=Calendar
TARGET_CALENDAR=St. Edward Public Calendar

# Optional: additional category -> calendar pairs, semicolon separated.
# Events tagged "SAS" in the source calendar are copied to the
# "Sundays At St. Edward" calendar, in addition to the Public pair above.
# EXTRA_SYNC_PAIRS=SAS=Sundays At St. Edward

# Optional: comma-separated emails allowed to use dashboard (e.g. rcarroll@stedward.org,eharnisch@stedward.org,ckloss@stedward.org)
# ALLOWED_DASHBOARD_USERS=rcarroll@stedward.org,eharnisch@stedward.org,ckloss@stedward.org

SYNC_CUTOFF_DAYS=1825
SYNC_INTERVAL_MIN=23
MAX_SYNC_REQUESTS_PER_HOUR=20

MASTER_CALENDAR_PROTECTION=true

PORT=10000
TIMEOUT=300
```

## Changing the sync window

Reducing `SYNC_CUTOFF_DAYS` or `SYNC_LOOKAHEAD_DAYS` makes the next sync delete
every previously synced event that now falls outside the window. Treat it as a
data operation, not a config tweak.

Before deploying the change:

1. Export the target calendar to .ics and keep it for two weeks. Code rollback
   does not bring deleted events back.
2. Estimate the damage: `(days_removed / 30) x average_events_per_month`. If
   that exceeds `MAX_RUN_DELETIONS` (150, `sync.py`, overridable by env), the
   sync aborts and changes nothing, which is the safe outcome.

Two behaviours that surprise people:

- **Window membership is decided by an event's start date only.** An event that
  starts before the cutoff is not synced at all, even if it runs into the
  window. There are no partial syncs.
- **HTTP 429 on the first sync after a window change is expected** and
  self correcting. Graph allows roughly 1,000,000 requests per day and about
  100 per second in burst, and sync operations are idempotent, so the next
  scheduled run resumes safely.

Reference timing, measured October 2025: a full sync took 365 seconds against a
1,460 day window and 130 seconds against 545 days.

## Initial Deployment

### 1. Deploy Application

```bash
# Clone repository
git clone https://github.com/your-repo/calendar-sync-service.git
cd calendar-sync-service

# Install dependencies
pip install -r requirements.txt

# Set environment variables (platform-specific)

# Deploy to hosting platform
```

### 2. Authenticate

After deployment:
1. Visit `https://your-domain.com`
2. Click "Authenticate with Microsoft"
3. Sign in with account that has calendar access
4. Grant requested permissions
5. Verify redirect back to app

Token will be stored in `/data/token.json`.

### 3. Verify Health

```bash
# Basic health
curl https://your-domain.com/health

# Detailed health
curl https://your-domain.com/health/detailed
```

### 4. Test Sync

Trigger first sync via web interface or wait for automatic sync.

Monitor logs for:
- ✅ Events fetched successfully
- ✅ Operations determined
- ✅ Sync completed
- ❌ No errors

## Continuous Deployment

### GitHub Actions Example

```yaml
name: Deploy Calendar Sync

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run tests
        run: pytest tests/ -v
      
      - name: Deploy to production
        run: |
          # Your deployment commands
```

## Monitoring Post-Deployment

### First Hour
- [ ] Health endpoint responding
- [ ] No authentication errors
- [ ] First sync completes successfully
- [ ] Events appear in target calendar

### First 24 Hours
- [ ] Multiple sync cycles complete
- [ ] No rate limit errors
- [ ] Sync timing consistent
- [ ] No duplicate events created

### Ongoing
- [ ] Daily sync success rate > 95%
- [ ] Response times < 30s
- [ ] Error rate < 1%
- [ ] Token refresh working

## Troubleshooting

### Authentication Fails
1. Verify environment variables set correctly
2. Check Azure AD app registration
3. Ensure redirect URI matches exactly
4. Verify account has calendar permissions
5. To add a new dashboard user (e.g. ckloss@stedward.org), see [Adding dashboard users](adding-dashboard-users.md).

### Syncs Not Running
1. Check scheduler is enabled
2. Verify `SYNC_INTERVAL_MIN` set
3. Check application logs
4. Test manual sync via web interface

### Events Not Syncing
1. Verify events have "Public" category
2. Verify events marked as "Busy"
3. Check date range (within `SYNC_CUTOFF_DAYS`)
4. Review sync logs for filtering reasons

### Duplicates Created
1. Run duplicate cleanup: `python tests/utils/cleanup_tools.py`
2. Check signature generation logs
3. Verify `signature_utils.py` unchanged
4. Review `test_signatures.py` results

## Rollback Procedure

If deployment causes issues:

```bash
# Revert to previous version
git revert HEAD
git push origin main

# Or rollback to specific tag
git checkout [previous-tag]
git push origin main --force-with-lease
```

Monitor health endpoints after rollback.

## Updating Dependencies

```bash
# Update requirements
pip list --outdated
pip install --upgrade [package]
pip freeze > requirements.txt

# Test thoroughly before deploying
pytest tests/ -v

# Deploy with updated requirements
```

## Security Updates

1. Review Azure AD permissions regularly
2. Rotate client secret annually
3. Monitor for unusual sync patterns
4. Keep dependencies updated
5. Review access logs monthly
