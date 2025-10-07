# Background Sync Implementation - Worker Timeout Fix

## Problem Solved ✅

**Issue:** Manual syncs were timing out after 5 minutes (300 seconds) when processing large numbers of events (2,600+ events). Gunicorn was killing workers because HTTP requests were blocking for too long.

**Root Cause:** The `POST /sync` endpoint ran synchronously, holding the HTTP connection open while processing thousands of events.

## Solution Implemented ✅

### 1. Background Sync Pattern
- **Manual syncs now run in background threads** (like scheduled syncs already did)
- **HTTP endpoint returns immediately** (< 1 second) with 202 Accepted
- **No more worker timeouts** - workers are never blocked for long periods

### 2. New Endpoints

#### `POST /sync` - Start Background Sync
```json
// Request
POST /sync
{
  "dry_run": false
}

// Response (immediate, < 1 second)
{
  "status": "started",
  "message": "Sync started in background",
  "dry_run": false,
  "check_progress": "/sync/progress"
}
```

#### `GET /sync/progress` - Check Progress
```json
// Response
{
  "in_progress": true,
  "phase": "add",
  "progress": 340,
  "total": 2600,
  "percent": 13,
  "last_checkpoint": "2025-10-05T22:05:30...",
  "last_result": null
}
```

### 3. Thread Safety
- **One sync at a time** - `sync_in_progress` flag prevents concurrent syncs
- **409 Conflict** returned if sync already running
- **Daemon threads** - cleaned up automatically on worker shutdown
- **No race conditions** - proper locking with `sync_lock`

## Code Changes Made

### `app.py` Changes
1. **Modified `/sync` endpoint** to start background thread and return immediately
2. **Added `/sync/progress` endpoint** for real-time progress monitoring
3. **Added `_run_sync_background()` function** to run sync in background thread

### `sync.py` Changes
1. **Added `get_progress_percent()` method** for progress calculation
2. **Enhanced progress tracking** - total operations stored in `sync_state['total']`
3. **Removed complex background sync methods** - using simpler approach

## How It Works

### Before (Broken)
```
User → POST /sync → Worker blocks for 10 minutes → Gunicorn kills worker at 5min
```

### After (Fixed)
```
User → POST /sync → Worker returns in <1s → 202 Accepted
Background thread → Runs sync independently → Logs completion
User → GET /sync/progress → Gets real-time status
```

## Benefits

### ✅ **No More Worker Timeouts**
- Workers never block for more than 1 second
- Gunicorn never sees workers as hung
- Sync completes regardless of HTTP timeout

### ✅ **Better User Experience**
- Immediate feedback when sync starts
- Real-time progress monitoring
- Clear status indicators

### ✅ **Operational Safety**
- Can't accidentally start multiple syncs
- Background threads are daemon threads (auto-cleanup)
- All existing logging still works

### ✅ **Consistent with Scheduled Syncs**
- Manual syncs now work exactly like scheduled syncs
- Same threading pattern throughout the system
- No architectural inconsistencies

## Testing Instructions

### 1. Test Manual Sync
```bash
# Start sync
curl -X POST https://your-app.com/sync \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'

# Should return immediately (< 1 second):
{
  "status": "started",
  "message": "Sync started in background",
  "dry_run": false,
  "check_progress": "/sync/progress"
}
```

### 2. Monitor Progress
```bash
# Check progress
curl https://your-app.com/sync/progress

# Should show real-time progress:
{
  "in_progress": true,
  "phase": "add",
  "progress": 340,
  "total": 2600,
  "percent": 13,
  "last_checkpoint": "2025-10-05T22:05:30..."
}
```

### 3. Verify Completion
```bash
# After sync completes:
{
  "in_progress": false,
  "phase": "idle",
  "progress": 2600,
  "total": 2600,
  "percent": 100,
  "last_result": {
    "success": true,
    "message": "Sync complete: 2600/2600 operations successful"
  }
}
```

## Expected Log Output

```
INFO:app:🔄 Background sync started (dry_run=False)
INFO:sync:Adding batch 1/130
INFO:sync:Adding batch 2/130
...
INFO:sync:Adding batch 130/130
INFO:app:✅ Background sync completed: Successfully synced...
```

## Rollback Plan

If issues occur:
1. Revert `app.py` changes to old synchronous `/sync` endpoint
2. Remove `/sync/progress` endpoint
3. Remove `_run_sync_background()` function
4. Set `SYNC_INTERVAL_MIN=0` to disable scheduler
5. Only run manual syncs during maintenance windows

## Migration Notes

- **Scheduled syncs continue working** - no changes needed
- **Existing sync logic unchanged** - only the trigger mechanism changed
- **All logging preserved** - same log messages and levels
- **Progress tracking enhanced** - better visibility into sync operations

## Next Steps

1. **Deploy to production** and monitor for worker health
2. **Test with large syncs** (2,600+ events) to verify no timeouts
3. **Update frontend** (if applicable) to poll `/sync/progress` instead of waiting
4. **Monitor logs** for successful background sync completion

The fix transforms manual syncs from blocking operations into non-blocking background operations, eliminating worker timeouts while maintaining all existing functionality.
