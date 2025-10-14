# Dashboard UI - Quick Reference Guide

## Visual Hierarchy at a Glance

### Button Priority System
```
┌─────────────────────────────────────────┐
│  PRIMARY ACTIONS (most important)       │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │   Preview   │  │  ▶️  Sync Now    │  │
│  │   Changes   │  │   (PROMINENT)    │  │
│  └─────────────┘  └──────────────────┘  │
│                                          │
│  SECONDARY ACTIONS (supporting)         │
│  ┌─────┐ ┌──────┐ ┌────────┐ ┌────┐    │
│  │Pause│ │Status│ │Search  │ │etc.│    │
│  └─────┘ └──────┘ └────────┘ └────┘    │
└─────────────────────────────────────────┘
```

## Color Code Reference

### Status Colors
- 🟢 **Green (#10B981)** - System healthy, sync successful
- 🟡 **Amber (#F59E0B)** - Warning, paused, in progress  
- 🔴 **Red (#EF4444)** - Error, disconnected, failure

### Action Colors
- **Forest Green (#2E7D32)** - Primary actions (Sync Now)
- **Slate Gray (#4A5568)** - Secondary actions (Status, Debug)
- **Red (#DC2626)** - Destructive actions (Clear, Delete)

## Key Features

### Status Bar
```
● System Status: All Systems Go
🔄 Auto-sync: Active (every 15 min)
⏱️  Last sync: 2 minutes ago
📅 Next sync: in 13 minutes
✓  Events synced today: 47
```

**What each icon means:**
- ● (Dot) - Overall system health
- 🔄 - Auto-sync status
- ⏱️ - Time since last sync (relative)
- 📅 - Next scheduled sync (countdown)
- ✓ - Daily sync count

### Primary Actions

#### 🔍 Preview Changes
- Shows what will change before syncing
- Safe to use anytime
- Outlined button (white/green)

#### ▶️ Sync Now (MAIN ACTION)
- Executes synchronization immediately
- Largest, most prominent button
- Solid green (#2E7D32)
- Shows spinner during operation

### Secondary Actions

| Button | Purpose | When to Use |
|--------|---------|-------------|
| ⏸️ Pause | Pause auto-sync | When making manual changes |
| 📊 Status | System details | Troubleshooting |
| 🔍 Search Events | Find specific events | Looking for particular event |
| 📰 Weekly Bulletin | Bulletin formats | Generating weekly bulletin |
| 🐛 Debug | Debug information | Technical issues |
| 🧪 Test Fix | Test sync functionality | Development/testing |

### 🔍 Search Events Modal

**How to use:**
1. Click "🔍 Search Events" button
2. Enter search term (e.g., "scout", "mass", "council")
3. Select date range:
   - Next 7 days
   - Next 30 days (default)
   - Next 90 days
   - Until June 30
4. Click "Search" or press Enter
5. Results open in new tab

**Quick actions:**
- ESC to close
- Click outside to close
- Enter to search

### ⚠️ Advanced Actions (Danger Zone)

**Location:** Bottom of dashboard, collapsed by default

**How to access:**
1. Click "⚠️ Advanced Actions" header
2. Section expands showing options

**Safety features:**
- ✅ Must check desired action
- ✅ Must type "DELETE" exactly
- ✅ Button only enabled when both complete
- ✅ Final confirmation dialog

**Options:**
1. **Clear all target calendar events**
   - Deletes ALL events from public calendar
   - Use with extreme caution

2. **Clear only synced events**
   - Deletes only events created by sync system
   - Safer option for cleanup

**To execute:**
```
☐ Check one option
Type "DELETE" → [        ]
[Cancel] [Confirm Deletion]
```

## Mobile Usage

### Phone Layout (<768px)
- Primary actions stack vertically
- Sync Now becomes full width
- Secondary actions in single column
- All touch targets 44x44px minimum
- Search modal adapts to 95% width

### Best Practices on Mobile
- Use thumb-friendly primary action (Sync Now)
- Landscape mode for better button access
- Modal interactions optimized for touch

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| ESC | Close search modal |
| Enter (in search) | Execute search |
| Tab | Navigate buttons |
| Space/Enter (on button) | Activate |

## Status Messages

### Success (Green)
```
✅ Sync completed! Added: 3, Updated: 5, Deleted: 0
```
- Auto-dismisses after 5 seconds
- Sparkle animation ✨

### Warning (Amber)
```
⚠️ Large sync detected: 52 changes planned
```
- Requires acknowledgment

### Error (Red)
```
❌ Network error: Connection timeout
```
- Stays visible until dismissed
- Action required

## Loading States

### During Sync
```
[⏳ Syncing... (disabled)]
Progress: Checking 234 source events...
          Updated 3 events
          Created 1 new event
```

### Button States
- **Normal:** Enabled, full color
- **Hover:** Lifted shadow, darker color
- **Disabled:** 60% opacity, no hover
- **Loading:** Spinner icon, disabled

## Troubleshooting Quick Reference

### If sync fails:
1. Check status indicator (red = error)
2. Click "📊 Status" for details
3. Check "🐛 Debug" for technical info
4. Verify authentication (status bar)

### If auto-sync stops:
1. Check if paused (amber status)
2. Click "▶️ Resume" if paused
3. Check "📊 Status" for scheduler state

### If buttons are disabled:
- Wait for current operation to complete
- Check for sync in progress
- Reload page if stuck

## Design Specifications

### Spacing
- Primary button: 1.25rem × 2rem padding
- Secondary button: 0.75rem × 1rem padding
- Section margins: 2rem
- Grid gap: 1rem

### Typography
- Primary button: 1.1rem, 600 weight
- Secondary button: 0.9rem, 600 weight
- Status text: 0.95rem, 400 weight
- Headers: 1.1-1.3rem, 600 weight

### Shadows
- Primary button: 0 4px 12px rgba(46, 125, 50, 0.3)
- Secondary button: 0 2px 8px rgba(74, 85, 104, 0.3)
- Modal: 0 20px 60px rgba(0, 0, 0, 0.3)

### Transitions
- All buttons: 0.3s ease
- Hover lift: translateY(-2px)
- Color changes: 0.2s

## Accessibility

### Color Contrast Ratios
- Primary text: 4.5:1 minimum ✓
- Secondary text: 4.5:1 minimum ✓
- Button text: 7:1+ (exceeds AA) ✓

### Screen Reader Support
- All buttons have descriptive labels
- Status changes announced
- Modal properly trapped focus
- Semantic HTML structure

### Keyboard Navigation
- All interactive elements focusable
- Visible focus indicators
- Logical tab order
- No keyboard traps

## Tips & Best Practices

1. **Daily Usage:**
   - Check status bar for system health
   - Use Preview before important syncs
   - Monitor daily sync count

2. **Safety:**
   - Never rush danger zone actions
   - Preview large changes first
   - Keep auto-sync enabled normally

3. **Efficiency:**
   - Use keyboard shortcuts when possible
   - Search modal for quick event lookup
   - Status button for quick system check

4. **Mobile:**
   - Add to home screen for app-like experience
   - Use landscape for easier access
   - Primary actions thumb-friendly

## Support

For issues or questions:
- Check Debug panel for technical details
- Review system status for health check
- Consult troubleshooting section above

---

*Last Updated: October 2024*
*Dashboard Version: 2.0 (UI Redesign)*

