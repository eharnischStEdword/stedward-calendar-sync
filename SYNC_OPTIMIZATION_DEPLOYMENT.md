# Calendar Sync Speed Optimization - Deployment Guide

## Quick Pre-Deployment Checklist

**CRITICAL - Complete BEFORE deploying**:

1. ✅ **Backup public calendar** (Settings > Import & Export in Google Calendar UI)
2. ✅ **Check env vars** don't override config.py (180/365 days)
3. ✅ **Verify timezone** matches calendar timezone (America/Chicago)
4. ✅ **Estimate deletions** from first sync (~350 events if cutting 550 days)
5. ✅ **Review safety limit** in sync.py line 1060 (currently 150 deletions)

**Expected first sync**: Slower than normal, potential HTTP 429 rate limits (OK)

---

## Changes Implemented

### 1. Date Range Reduction
**File**: `config.py` (Lines 37-38)
- `SYNC_CUTOFF_DAYS`: 730 → **180** (6 months back)
- `SYNC_LOOKAHEAD_DAYS`: 730 → **365** (12 months ahead)
- **Total window**: 1,460 days → 545 days (63% reduction)

### 2. Logging Optimization
**Files**: `calendar_ops.py`, `sync.py`
- Wrapped diagnostic logging in `if logger.level == logging.DEBUG:` checks
- Disabled verbose event-by-event filter logging
- Wrapped 100+ lines of debug output in `if False:` blocks
- **Preserved**: All error logs, warning logs, and operation summaries

## Pre-Deployment Checklist

### CRITICAL: Backup Public Calendar

**Before deploying, export the public calendar**:

```bash
# Method 1: Google Calendar UI
# 1. Go to calendar.google.com
# 2. Settings > Import & Export
# 3. Export "St. Edward Public Calendar"
# 4. Save .ics file with timestamp: public-calendar-backup-YYYY-MM-DD.ics

# Method 2: Automated (if available)
# Use your existing backup automation
```

**Why**: If deletion logic has an edge case, you need a rollback point for calendar data itself (not just code).

**Store backup**: Keep for at least 2 weeks post-deployment.

### API Quota Impact Assessment

**Microsoft Graph API Limits**:
- Standard: 1,000,000 requests/day
- Burst capacity: ~100 requests/second

**First Sync Deletion Estimate**:
- Historical window being cut: 550 days (730 - 180 days back)
- Estimated events/month: ~200 (adjust based on your calendar)
- **Potential deletions**: ~350 events in first sync

**Calculation for your calendar**:
```
Days being removed: 730 - 180 = 550 days
Average events/month: [YOUR_AVG]
Estimated deletions: (550 / 30) × [YOUR_AVG]
```

**Batch Deletion Protection**:
Your code already handles this via:
- Batch size: 20 events per request (`calendar_ops.py` line 1149)
- Automatic retry with backoff
- Safety limit: 150 deletions without approval (`sync.py` line 1060)

**Rate Limit Handling**:
- If you see **HTTP 429** errors: Expected on first sync
- Code will retry automatically
- Next scheduled sync (23 min) will resume
- **No data loss** - operations are idempotent

**Monitor for**:
```
⚠️ Rate limit exceeded messages
⚠️ Batch request failed: 429
```

### Environment Variable Verification

Check for override conflicts:
```bash
# Check .env files
cat .env | grep SYNC_CUTOFF_DAYS
cat .env | grep SYNC_LOOKAHEAD_DAYS

# Check system environment
env | grep SYNC_CUTOFF_DAYS
env | grep SYNC_LOOKAHEAD_DAYS

# If using Docker/containers
docker inspect <container> | grep SYNC_CUTOFF_DAYS
```

**Action Required**: If env vars exist, either:
- Remove them to use config.py defaults (180/365)
- Update them to match (180/365)
- Document why they're set differently

### Timezone Verification

```python
# Verify server timezone matches calendar timezone
import pytz
from datetime import datetime

server_tz = datetime.now().astimezone().tzinfo
calendar_tz = pytz.timezone('America/Chicago')

print(f"Server TZ: {server_tz}")
print(f"Calendar TZ: {calendar_tz}")
```

**Expected**: Both should be `America/Chicago` (Central Time)

## First Sync After Deployment

### Expected Behavior

**Duration**: First sync will likely take **longer** than subsequent syncs because:
1. Reconciling new 18-month window with existing state
2. Deleting events outside the new window (events older than 6 months)
3. Processing boundary cases for recurring events

**Operations to Expect**:
- **Deletions**: Events older than 6 months back will be removed
- **Additions**: None (unless new events exist)
- **Updates**: Events within window that changed

### Monitoring Metrics

Track these key metrics from logs:

#### API Request Patterns
```
Expected values:
✓ Weekly chunks: ~77 (was ~208) - 63% reduction
✓ Events per chunk: 10-50 average
✓ Total events processed: 60-65% fewer than before
```

#### Log Lines to Watch

**Operation Summary** (lines 1438-1442 in sync.py):
```
📋 SYNC PLAN: X to add, Y to update, Z to delete
```

**Duration**:
```
🎉 Sync completed in X.XX seconds
```

**Event Counts**:
```
📊 Retrieved X source events and Y target events
```

**Date Range Confirmation**:
```
📅 Date range: XXX days from YYYY-MM-DD to YYYY-MM-DD
```

Look for this to show **~545 days** instead of previous 1460 days.

#### Error Indicators

Watch for these **WARNING** signs:
```
⚠️ Date range XXX days exceeds our limit of 730 days!
❌ Failed to retrieve source calendar events
❌ REJECTED (cancelled): [event name]
🚨 SAFETY TRIGGERED: XXX deletions exceeds threshold
```

## Edge Cases & Expected Behavior

### Event Boundary Handling

**Events spanning the 6-month lookback**:
- Events are synced based on their **start date**
- If start date is within window → synced completely
- If start date is before window → not synced at all
- **No partial syncs** occur

Example:
- Event: Oct 1, 2024 - Oct 15, 2024
- Current date: April 15, 2025 (6 months = Oct 15, 2024)
- **Behavior**: Event starts Oct 1 (before cutoff) → **NOT synced**

### Recurring Events

**Series starting before 6-month window**:
- Only **instances within the window** will sync
- Series master won't sync if it's outside the window
- Individual occurrences are handled correctly

Example:
- Weekly meeting started 2 years ago
- Current window: 6 months back to 12 months ahead
- **Behavior**: Only meetings within 18-month window sync

### Multi-Day Events

**Events crossing day boundaries**:
- Handled normally - synced as single events
- Start date determines if they're in window
- Duration preserved correctly

## Performance Expectations

### Baseline Metrics (Before Optimization)
- Date range: ~1,460 days (4 years)
- API chunks: ~208 weekly requests
- Events processed: All events in 4-year span
- Sync duration: [Your baseline - measure first]

### Target Metrics (After Optimization)
- Date range: ~545 days (18 months)
- API chunks: ~77 weekly requests (63% reduction)
- Events processed: 60-65% fewer
- Sync duration: **35-40% of baseline** (target)

### Calculation
If baseline sync took 100 seconds:
- Expected optimized sync: **35-40 seconds**

If baseline took 5 minutes:
- Expected optimized sync: **~2 minutes**

## Rollback Procedure

If issues arise or performance doesn't improve:

### Option 1: Quick Rollback (Restore Logging)
1. Change `if False:` to `if True:` in sync.py (lines 1015, 1245, 1317)
2. Change `if logger.level == logging.DEBUG:` to unconditional in calendar_ops.py (line 478)
3. Restart application

### Option 2: Full Rollback (Restore Date Range)
1. Edit `config.py` lines 37-38:
   ```python
   SYNC_CUTOFF_DAYS = int(os.environ.get('SYNC_CUTOFF_DAYS', 730))  # 2 years
   SYNC_LOOKAHEAD_DAYS = int(os.environ.get('SYNC_LOOKAHEAD_DAYS', 730))  # 2 years ahead
   ```
2. Restart application
3. Document why optimization didn't work

### Option 3: Git Revert
```bash
git log --oneline -5  # Find the optimization commit
git revert <commit-hash>
git push
```

## Testing Checklist

### Immediate Verification (After First Sync Completes)

- [ ] Sync completed without errors
- [ ] Events with "Public" + "Busy" appear in public calendar
- [ ] Events without "Public" are NOT copied
- [ ] Events with "Public" but not "Busy" are NOT copied
- [ ] Updates to qualifying events propagate correctly
- [ ] Deletions from primary calendar remove events from public calendar
- [ ] Primary calendar remains READ ONLY (no modifications)
- [ ] Sync duration is significantly reduced
- [ ] Log output is cleaner (less noise)
- [ ] No unexpected deletions of recent events

### Post-First-Sync Boundary Validation

**Critical**: Spot-check these specific boundary cases:

#### 1. 6-Month Lookback Boundary (180 days ago)
```
Current date: [TODAY]
Boundary date: [TODAY - 180 days] 
Test: Find event from exactly 180 days ago
Expected: Event SHOULD be synced to public calendar
```

**How to check**:
1. Calculate: Today's date - 180 days = Boundary date
2. Look for event on that date in primary calendar
3. Verify it exists in public calendar
4. If missing: **ISSUE** - boundary calculation off

#### 2. Forward Boundary (365 days ahead)
```
Current date: [TODAY]
Boundary date: [TODAY + 365 days]
Test: Find event from exactly 365 days ahead
Expected: Event SHOULD be synced to public calendar
```

**How to check**:
1. Calculate: Today's date + 365 days = Forward boundary
2. Look for event on that date in primary calendar
3. Verify it exists in public calendar
4. If missing: **ISSUE** - lookahead calculation off

#### 3. Outside Window Validation (7+ months ago)
```
Current date: [TODAY]
Test date: [TODAY - 210+ days] (7 months)
Test: Find event from 7+ months ago
Expected: Event should be DELETED from public calendar
```

**How to check**:
1. Calculate: Today's date - 210 days = Outside window
2. Look for old event on that date
3. Verify it's GONE from public calendar (but still in primary)
4. If still present: **ISSUE** - deletion logic not working

#### 4. Timezone Edge Case
```
Test: Event at midnight on boundary date
Expected: Properly included/excluded based on server timezone
```

**Example**:
- Boundary: Oct 15, 2024
- Event: Oct 15, 2024 at 12:00 AM CT
- Server TZ: America/Chicago
- Expected: Should be INCLUDED (exactly on boundary)

## Troubleshooting

### Issue: Sync still takes same amount of time

**Diagnosis**:
1. Check logs for actual date range being queried
2. Verify env vars aren't overriding config.py
3. Check if network latency is the bottleneck (not date range)

**Solution**: Review environment variable settings and network logs

### Issue: Recent events missing from sync

**Diagnosis**:
1. Check event start dates - are they within 6 months back?
2. Verify events have "Public" category AND "Busy" status
3. Check timezone alignment

**Solution**: Review event filtering logic and timezone settings

### Issue: Too many deletions on first sync

**Expected**: Events older than 6 months will be deleted
**Unexpected**: Recent events being deleted

**Diagnosis**: Check deletion list in logs before they execute
**Solution**: Use DRY_RUN_MODE=True first to preview

### Issue: Recurring events behaving unexpectedly

**Diagnosis**:
1. Check series start date vs window
2. Review occurrence handling in logs
3. Verify SYNC_OCCURRENCE_EXCEPTIONS setting

**Solution**: May need to adjust occurrence sync logic

## Success Criteria

Optimization is successful if:

1. ✅ Sync duration reduced by 60-65%
2. ✅ API requests reduced from ~208 to ~77 chunks
3. ✅ Log output is significantly cleaner
4. ✅ No errors during sync
5. ✅ All recent events (within 6 months) sync correctly
6. ✅ Primary calendar remains unmodified
7. ✅ Public + Busy filter still works correctly

## Next Steps

After successful first sync:

1. **Monitor**: Watch next 3-5 scheduled syncs
2. **Measure**: Document actual performance improvement
3. **Document**: Update this file with real metrics
4. **Clean up**: After 1 week of stable operation, consider removing `if False:` blocks entirely
5. **Optimize further**: If needed, consider enabling event cache (currently disabled)

## Notes

- Event cache remains disabled as requested
- All error and warning logs preserved
- Core sync logic (Public + Busy filter) unchanged
- Primary calendar READ ONLY constraint maintained
- `if False:` blocks make restoration trivial if needed

## Deployment Timeline & Monitoring

### First Sync (Manual Monitoring Required)

**Be present for the first sync**:

**Timeline**:
- **Start**: Deployment triggers first sync
- **Duration**: 15-20 minutes (deletion processing)
- **Watch for**: HTTP 429 rate limits (normal, auto-handled)
- **Red flags**: Errors beyond rate limits → Stop deployment, investigate

**Monitor actively**:
```bash
# Watch logs in real-time
tail -f [your_log_file] | grep -E "ERROR|WARNING|Retrieved|SYNC PLAN|completed"
```

**Decision points during first sync**:
- ✅ HTTP 429 errors: Expected, wait for auto-retry
- ✅ Deletions executing: Normal (old events outside window)
- ⛔ Other errors: Stop deployment, review logs, potentially rollback

### Second Sync (Performance Validation)

**Wait 23 minutes for second sync** - This is your true performance test:

**Why second sync matters**:
- First sync: Heavy deletions, boundary reconciliation
- Second sync: Normal operations, true steady-state performance
- **This validates the optimization**

**Second sync expectations**:
- ✅ Completes in **35-40% of baseline** (60-65% faster)
- ✅ Minimal deletions (boundary maintenance only)
- ✅ Clean logs (no diagnostic noise)
- ✅ ~77 weekly chunks (vs previous ~208)

**Declare success only after second sync** shows expected performance.

### One-Line Health Check

After second sync completes:

```bash
# Extract key metrics from logs
grep -E "Date range|Retrieved|SYNC PLAN|completed in" [your_log_file] | tail -20

# Expected output:
# 📅 Date range: ~545 days from [date] to [date]
# 📊 Retrieved X source events and Y target events  (60-65% fewer)
# 📋 SYNC PLAN: X to add, Y to update, Z to delete  (minimal changes)
# 🎉 Sync completed in X.XX seconds  (35-40% of baseline)
```

**Success indicators**:
1. Date range: ~545 days ✅
2. Events: 60-65% fewer ✅
3. Duration: 35-40% of baseline ✅
4. Chunks: ~77 (down from ~208) ✅

### Deployment Decision Tree

```
First Sync Completes
        │
        ├─ Errors (not HTTP 429)? → STOP → Investigate → Rollback if needed
        │
        ├─ HTTP 429 only? → OK → Continue monitoring
        │
        └─ Success → Wait 23 min
                        │
                        ├─ Second Sync Completes
                        │       │
                        │       ├─ 60-65% faster? → ✅ SUCCESS → Monitor next 3-5 syncs
                        │       │
                        │       └─ No improvement? → Review troubleshooting → Consider rollback
                        │
                        └─ Second Sync Fails → Investigate → Rollback
```

### Post-Deployment Monitoring Schedule

**Day 1** (Deployment day):
- Hour 0: First sync (manual monitoring)
- Hour 0.5: Second sync (performance validation)
- Hour 1-8: Monitor every sync (23 min intervals)
- End of day: Review metrics, document performance

**Day 2-7** (Stabilization):
- Check sync logs daily
- Monitor for any anomalies
- Document actual performance vs expected

**Week 2+** (Steady State):
- Normal operations
- Consider removing `if False:` blocks if stable
- Consider enabling event cache for further optimization

---

## Deployment Log Template

**Deployment Date**: _[Fill in after deployment]_  
**Deployment Time**: _[Time]_

### First Sync Results
- **Duration**: _[X seconds]_
- **Deletions**: _[X events]_
- **HTTP 429 errors**: _[Yes/No - if yes, how many]_
- **Other errors**: _[None / List any]_
- **Status**: _[Success / Failed]_

### Second Sync Results (Performance Validation)
- **Duration**: _[X seconds]_
- **Baseline duration**: _[X seconds from pre-optimization]_
- **Performance improvement**: _[X% faster]_
- **Events processed**: _[X events (vs Y before)]_
- **API chunks**: _[~77 vs ~208 before]_
- **Status**: _[Success / Failed]_

### Boundary Validation
- [ ] 6-month lookback (180 days): Event synced correctly
- [ ] Forward boundary (365 days): Event synced correctly
- [ ] Outside window (7+ months): Event properly deleted
- [ ] Timezone edge case: Handled correctly

### Final Status
- **Overall result**: _[Success / Needs Adjustment / Rolled Back]_
- **Performance gain**: _[X% improvement in sync time]_
- **Issues encountered**: _[None / List any]_
- **Next steps**: _[Continue monitoring / Rollback / Adjust]_

