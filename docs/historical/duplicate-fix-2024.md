# Duplicate Event Fix - Implementation Guide

## Problem Summary

After fixing missing events in October/November/September, all events AFTER the fix date are now duplicated in the St. Edward Public Calendar. The backfill operation didn't detect existing events, so it added them again.

## Root Cause Analysis

The issue was in the `_is_synced_event()` method in `sync.py`. It was looking for `SingleValueExtendedProperties` with a value of `'StEdwardSync'`, but the actual sync marker is added to the event body as `<!-- SYNC_ID:{source_id} -->`. This caused the system to:

1. Not properly identify which events were created by the sync system
2. Treat all events as potential duplicates
3. Fail to prevent new duplicates from being created

## Solution Implemented

### Step 1: Cleanup Script (`cleanup_duplicates.py`)

**Purpose**: Remove existing duplicate events from the public calendar

**Features**:
- Groups events by signature: `subject + start_time + end_time + location`
- Keeps the OLDEST event (earliest creation date) in each group
- Deletes all newer duplicates
- Supports dry-run mode for safe testing
- Comprehensive logging of all operations

**Usage**:
```bash
# Dry run to see what would be deleted
python cleanup_duplicates.py --dry-run --verbose

# Actually perform the cleanup
python cleanup_duplicates.py --verbose
```

**Safety Features**:
- Uses event creation timestamp (`createdDateTime`) to determine which is oldest
- Logs all operations for audit trail
- Supports dry-run mode
- Only deletes events that are true duplicates (same signature)

### Step 2: Fixed Duplicate Prevention (`sync.py`)

**Changes Made**:

1. **Fixed `_is_synced_event()` method**:
   ```python
   def _is_synced_event(self, event: Dict) -> bool:
       """Check if this event was created by our sync system"""
       # Check for our sync marker in the body content
       body_content = event.get('body', {}).get('content', '')
       if 'SYNC_ID:' in body_content:
           return True
       
       # Also check for legacy markers
       if 'Auto-synced from' in body_content:
           return True
           
       return False
   ```

2. **Improved duplicate detection in `_determine_sync_operations()`**:
   - Now checks ALL events in target calendar (not just synced ones)
   - Uses consistent signature generation for duplicate detection
   - Properly skips events that already exist
   - Logs when events are skipped due to existing duplicates

**Key Logic**:
```python
# Check if this event already exists in target calendar (any event, not just synced ones)
if signature in existing_signatures:
    logger.debug(f"🔄 Event already exists in target: {subject}")
    # Skip adding this event
else:
    # Event doesn't exist - add it
    to_add.append(source_event)
```

### Step 3: Validation Script (`validate_duplicate_fix.py`)

**Purpose**: Validate that the fixes are working correctly

**Tests**:
1. **Synced event detection**: Verifies `_is_synced_event()` works correctly
2. **Duplicate detection**: Tests signature generation for duplicate events
3. **Signature generation**: Validates consistent signature creation
4. **Calendar access**: Ensures we can access both calendars

**Usage**:
```bash
python validate_duplicate_fix.py --verbose
```

## Implementation Steps

### 1. Run Cleanup (One-time)

```bash
# First, run a dry-run to see what would be cleaned up
python cleanup_duplicates.py --dry-run --verbose

# If the dry-run looks good, run the actual cleanup
python cleanup_duplicates.py --verbose
```

### 2. Validate Fixes

```bash
# Run validation to ensure fixes are working
python validate_duplicate_fix.py --verbose
```

### 3. Test Sync

```bash
# Run a sync to ensure no new duplicates are created
# (This will be done automatically by the scheduler, or you can trigger manually)
```

## Expected Results

### After Cleanup:
- Duplicate events removed from public calendar
- Only the oldest version of each event remains
- Event count in public calendar should decrease by the number of duplicates

### After Fix:
- New syncs should not create duplicates
- Events that already exist should be skipped with log message: "Event already exists in target"
- Only truly new events should be added

## Monitoring

### Log Messages to Watch For:
- `🔄 Event already exists in target: {subject}` - Good! Duplicate prevention working
- `➕ ADD needed: {subject}` - New event being added (expected for new events)
- `⏭️ Skipping existing non-synced event: {subject}` - Manual event being preserved

### Validation Checks:
1. Run cleanup script periodically to check for new duplicates
2. Monitor sync logs for "Event already exists" messages
3. Verify event counts don't increase unexpectedly

## Safety Considerations

1. **Backup**: The cleanup script only deletes true duplicates (same signature)
2. **Dry Run**: Always test with `--dry-run` first
3. **Logging**: All operations are logged for audit trail
4. **Gradual**: The fix prevents new duplicates without disrupting existing functionality

## Files Modified

1. **`sync.py`**: Fixed `_is_synced_event()` and improved duplicate detection
2. **`cleanup_duplicates.py`**: New script for removing existing duplicates
3. **`validate_duplicate_fix.py`**: New script for validating fixes

## Rollback Plan

If issues occur:
1. The sync system will continue to work (just may create duplicates again)
2. The cleanup script can be run again if needed
3. Original sync logic is preserved in git history

## Testing Checklist

- [ ] Run cleanup script in dry-run mode
- [ ] Run cleanup script for real
- [ ] Run validation script
- [ ] Trigger a manual sync
- [ ] Verify no new duplicates created
- [ ] Check logs for "Event already exists" messages
- [ ] Monitor for 24 hours to ensure stability
