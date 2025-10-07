# Troubleshooting Guide

## Common Issues

### Authentication Problems

#### Symptom: "Authentication required" message
**Cause:** Token expired or invalid

**Solution:**
1. Visit application web interface
2. Click "Authenticate with Microsoft"
3. Complete OAuth flow
4. Verify token created at `/data/token.json`

#### Symptom: "Insufficient permissions" error
**Cause:** Account lacks calendar access

**Solution:**
1. Verify account has access to shared mailbox
2. Check Azure AD app permissions include:
   - Calendars.ReadWrite.Shared
   - Mail.Send.Shared
3. Grant admin consent if required

### Sync Issues

#### Symptom: Events not syncing
**Possible Causes:**
1. Events missing "Public" category
2. Events not marked "Busy"
3. Events outside date range
4. Event type is "occurrence" without series

**Diagnosis:**
```bash
# Check sync logs
curl https://your-domain.com/health/detailed

# Look for filtering reasons:
# - "Skipping: not public"
# - "Skipping: not busy"
# - "Skipping: outside date range"
```

**Solution:**
- Ensure events have "Public" category
- Verify events marked as "Busy"
- Check `SYNC_CUTOFF_DAYS` setting

#### Symptom: Duplicates appearing
**Cause:** Signature mismatch or backfill issue

**Solution:**
```bash
# Run duplicate cleanup
python tests/utils/cleanup_tools.py --verbose

# Verify signatures match
pytest tests/test_signatures.py -v
```

#### Symptom: Sync running but no changes
**Cause:** No changes detected in source calendar

**Verification:**
- Check source calendar for new/modified events
- Verify events meet sync criteria
- Review logs: "No changes detected"

### Performance Issues

#### Symptom: Sync taking too long
**Possible Causes:**
1. Too many events
2. Rate limiting
3. Network latency

**Diagnosis:**
```bash
# Check event counts
curl https://your-domain.com/health/detailed | grep event_count

# Review sync duration in logs
```

**Solution:**
- Reduce `SYNC_CUTOFF_DAYS` if possible
- Check rate limit headers in logs
- Verify network connectivity

#### Symptom: Rate limit errors (429)
**Cause:** Exceeding Microsoft Graph API limits

**Solution:**
- Increase `SYNC_INTERVAL_MIN`
- Reduce `MAX_SYNC_REQUESTS_PER_HOUR`
- Circuit breaker should handle automatically

### Application Errors

#### Symptom: Application won't start
**Possible Causes:**
1. Missing environment variables
2. Import errors
3. Port already in use

**Diagnosis:**
```bash
# Check environment variables
env | grep CLIENT_ID
env | grep TENANT_ID

# Test imports
python -c "import app; import auth; import sync"

# Check port
lsof -i :10000
```

**Solution:**
- Set all required environment variables
- Verify dependencies installed: `pip install -r requirements.txt`
- Change `PORT` if in use

#### Symptom: Import errors after update
**Cause:** Missing dependencies or code issue

**Solution:**
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall

# Run import tests
python test_utils_imports.py

# If still failing, rollback deployment
git revert HEAD
```

### Data Issues

#### Symptom: Events in wrong timezone
**Cause:** Timezone handling error

**Solution:**
- Verify `DateTimeUtils` handling Central Time correctly
- Check event times in logs
- Review timezone conversion in code

#### Symptom: All-day events syncing incorrectly
**Cause:** All-day event time parsing

**Solution:**
- Check `isAllDay` flag in logs
- Verify signature generation for all-day events
- Review all-day event handling in `sync.py`

## Diagnostic Tools

### Health Check Endpoints

```bash
# Basic health
curl https://your-domain.com/health

# Detailed status
curl https://your-domain.com/health/detailed
```

### Test Suite

```bash
# Run all tests
pytest tests/ -v

# Run specific test category
pytest -m signature
pytest -m duplicate
```

### Log Analysis

Key log patterns to search for:

```bash
# Authentication issues
grep "Auth error" logs.txt

# Sync operations
grep "Sync complete" logs.txt

# Rate limiting
grep "429" logs.txt

# Errors
grep "ERROR" logs.txt
```

## Emergency Procedures

### Immediate Stop Required

If sync causing issues:

```bash
# Option 1: Stop scheduled syncs
# Set SYNC_INTERVAL_MIN to very high value (e.g., 10000)

# Option 2: Disable application
# Stop hosting platform deployment
```

### Data Recovery

If events accidentally deleted:

1. Microsoft Graph keeps deleted items for 30 days
2. Contact Office 365 admin for recovery
3. Review sync logs to identify what was deleted

### Rollback Deployment

```bash
# Revert to previous version
git checkout [previous-tag]
git push origin main --force-with-lease

# Verify health
curl https://your-domain.com/health
```

## Getting Help

### Before Requesting Help

Gather this information:
1. Exact error message
2. Timestamp of issue
3. Relevant log excerpts
4. Recent changes/deployments
5. Steps to reproduce

### Log Files to Provide

- Application startup logs
- Sync operation logs
- Error stack traces
- Health check responses

### Useful Commands

```bash
# Export recent logs
# [Platform-specific log export commands]

# Check current state
curl https://your-domain.com/health/detailed > system-state.json

# Run diagnostics
pytest tests/ -v > test-results.txt
```
