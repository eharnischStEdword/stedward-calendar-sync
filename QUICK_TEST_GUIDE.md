# Quick Test Guide - Calendar Sync Fix

## 🚀 Quick Start

### Find Event ID
```bash
# Search for "Breakfast with Santa" event
curl http://localhost:10000/debug/event-details/breakfast | jq '.events[0].raw_event.id'
```

### Test the Event
```bash
# Use the test script (recommended)
./test_event_sync.sh <EVENT_ID>

# Or manually test
curl http://localhost:10000/debug/event/<EVENT_ID> | jq '.sync_analysis'
```

### Run Sync
```bash
curl -X POST http://localhost:10000/sync
```

## 🔍 What Was Fixed

### Before (Broken) ❌
- ❌ Only matched `'Public'` exactly (case-sensitive)
- ❌ Only accepted `'busy'` status
- ❌ "Breakfast with Santa" event rejected

### After (Fixed) ✅
- ✅ Matches `'Public'`, `'public'`, `'PUBLIC'` (case-insensitive)
- ✅ Accepts `'busy'`, `'tentative'`, `'oof'`, `'workingElsewhere'`
- ✅ "Breakfast with Santa" event syncs correctly

## 📊 Debug Endpoints

### 1. Debug Specific Event by ID
```bash
curl http://localhost:10000/debug/event/<EVENT_ID>
```

**Response shows**:
- Full Graph API event data
- Old vs. New filtering logic results
- Whether event would sync
- Recommendation

### 2. Search Events by Subject
```bash
curl http://localhost:10000/debug/event-details/<SEARCH_TERM>
```

**Example**:
```bash
curl http://localhost:10000/debug/event-details/breakfast
```

### 3. View Calendars
```bash
curl http://localhost:10000/debug/calendars
```

## 🔬 Understanding the Output

### Event WILL Sync ✅
```json
{
  "sync_analysis": {
    "categories_raw": ["Public"],
    "show_as": "busy",
    "new_logic": {
      "has_public_category": true,
      "is_busy": true,
      "would_sync": true  ← This should be true
    },
    "recommendation": "Event would sync with NEW logic"
  }
}
```

### Event WON'T Sync ❌
```json
{
  "sync_analysis": {
    "categories_raw": ["Private"],
    "show_as": "free",
    "new_logic": {
      "has_public_category": false,  ← Missing public category
      "is_busy": false,               ← Status is 'free'
      "would_sync": false
    }
  }
}
```

## 📝 Verbose Logging

After fix, sync logs show detailed filtering:

```
[FILTER CHECK] Breakfast with Santa
  Categories: ['Public']
  HasPublic: True
  ShowAs: busy
  IsBusy: True, WillSync: True
✅ Including event: Breakfast with Santa
```

**Rejected events show reason**:
```
[FILTER CHECK] Staff Meeting
  Categories: ['Private']
  HasPublic: False
❌ REJECTED (not public): Staff Meeting - Categories: ['Private']
```

## 🛠️ Troubleshooting

### Event still not syncing?

**Step 1**: Check what Graph API actually returns
```bash
curl http://localhost:10000/debug/event/<EVENT_ID> | jq '.event.categories, .event.showAs'
```

**Step 2**: Verify sync analysis
```bash
curl http://localhost:10000/debug/event/<EVENT_ID> | jq '.sync_analysis.new_logic'
```

**Step 3**: Check if it's a recurring event issue
```bash
curl http://localhost:10000/debug/event/<EVENT_ID> | jq '.event.type'
```

### Common Issues

**Issue**: Categories show `[]` (empty)
- **Solution**: Category not set in Graph API. Open event in Outlook Web, remove and re-add "Public" category.

**Issue**: ShowAs is `'free'`
- **Solution**: Event must be marked as Busy/Tentative/OOF/Working Elsewhere. Change in calendar app.

**Issue**: Event is recurring but not syncing
- **Solution**: Check if it's an occurrence without series master. Orphaned occurrences now handled correctly.

## ⚡ Testing Checklist

- [ ] Find event ID for "Breakfast with Santa"
- [ ] Run debug endpoint: `/debug/event/<EVENT_ID>`
- [ ] Verify `sync_analysis.new_logic.would_sync` is `true`
- [ ] Run manual sync: `POST /sync`
- [ ] Check logs for `[FILTER CHECK] Breakfast with Santa`
- [ ] Verify event appears in "St. Edward Public Calendar"
- [ ] Test with other events (lowercase category, tentative status)

## 📞 Support

If issues persist:

1. **Check Graph API response**: The debug endpoint shows raw data
2. **Review sync logs**: Look for `[FILTER CHECK]` entries
3. **Verify category in Outlook Web**: Desktop apps may show incorrect categories
4. **Try re-adding category**: Remove and re-add "Public" category in Outlook Web

## 🎯 Expected Behavior After Fix

| Event Scenario | Before Fix | After Fix |
|---------------|------------|-----------|
| Category: `'Public'`, Status: `'busy'` | ✅ Syncs | ✅ Syncs |
| Category: `'public'`, Status: `'busy'` | ❌ Rejected | ✅ Syncs |
| Category: `'Public'`, Status: `'tentative'` | ❌ Rejected | ✅ Syncs |
| Category: `'Public'`, Status: `'oof'` | ❌ Rejected | ✅ Syncs |
| Category: `'Private'`, Status: `'busy'` | ❌ Rejected | ❌ Rejected |
| Category: `'Public'`, Status: `'free'` | ❌ Rejected | ❌ Rejected |

---

**Quick Command Reference**:
```bash
# Find event
curl http://localhost:10000/debug/event-details/breakfast

# Debug event
curl http://localhost:10000/debug/event/<ID>

# Run sync
curl -X POST http://localhost:10000/sync

# Or use test script
./test_event_sync.sh <EVENT_ID>
```

