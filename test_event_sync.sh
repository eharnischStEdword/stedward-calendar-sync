#!/bin/bash
# Quick test script for verifying calendar sync fixes

set -e

echo "=================================================="
echo "Calendar Sync Fix - Testing Script"
echo "=================================================="
echo ""

# Check if event ID is provided
if [ -z "$1" ]; then
    echo "Usage: ./test_event_sync.sh <EVENT_ID>"
    echo ""
    echo "To find event ID for 'Breakfast with Santa':"
    echo "  curl http://localhost:10000/debug/event-details/breakfast | jq '.events[0].raw_event.id'"
    echo ""
    exit 1
fi

EVENT_ID="$1"
BASE_URL="${BASE_URL:-http://localhost:10000}"

echo "📋 Testing Event ID: $EVENT_ID"
echo "🌐 Base URL: $BASE_URL"
echo ""

# Step 1: Debug the specific event
echo "Step 1: Fetching event details from Graph API..."
echo "=================================================="
curl -s "$BASE_URL/debug/event/$EVENT_ID" | jq '.'
echo ""

# Step 2: Check sync analysis
echo "Step 2: Checking sync analysis..."
echo "=================================================="
WOULD_SYNC=$(curl -s "$BASE_URL/debug/event/$EVENT_ID" | jq -r '.sync_analysis.new_logic.would_sync')
echo "Would sync with NEW logic: $WOULD_SYNC"
echo ""

if [ "$WOULD_SYNC" = "true" ]; then
    echo "✅ Event WILL sync with new filtering logic!"
    echo ""
    
    # Step 3: Run sync
    echo "Step 3: Running manual sync..."
    echo "=================================================="
    read -p "Press Enter to run sync (or Ctrl+C to cancel)..."
    curl -s -X POST "$BASE_URL/sync" | jq '.'
    echo ""
    
    echo "✅ Sync complete! Check the public calendar for the event."
else
    echo "❌ Event will NOT sync. Check the analysis above:"
    echo ""
    curl -s "$BASE_URL/debug/event/$EVENT_ID" | jq '.sync_analysis'
    echo ""
    echo "Recommendation:"
    curl -s "$BASE_URL/debug/event/$EVENT_ID" | jq -r '.sync_analysis.recommendation'
fi

echo ""
echo "=================================================="
echo "Testing Complete"
echo "=================================================="

