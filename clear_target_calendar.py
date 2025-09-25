import os
import asyncio
from calendar_ops import CalendarOperations

async def clear_target_calendar():
    """Delete ALL events from the target calendar"""
    ops = CalendarOperations()
    
    # Target calendar name
    target_calendar_name = "St. Edward Public Calendar"
    
    print(f"Getting calendar ID for {target_calendar_name}...")
    target_id = await ops.get_calendar_id(target_calendar_name)
    
    if not target_id:
        print(f"Calendar {target_calendar_name} not found!")
        return
    
    print(f"Fetching all events from target calendar...")
    # Get ALL events (2 years to be sure)
    all_events = await ops.get_calendar_events(target_id, days_back=730)
    
    print(f"Found {len(all_events)} events to delete")
    
    # Delete each event
    deleted = 0
    failed = 0
    for event in all_events:
        try:
            event_id = event.get('id')
            if event_id:
                await ops.delete_event(event_id, target_id)
                deleted += 1
                if deleted % 10 == 0:
                    print(f"Deleted {deleted} events...")
        except Exception as e:
            print(f"Failed to delete event: {e}")
            failed += 1
    
    print(f"\nCOMPLETE: Deleted {deleted} events, {failed} failures")
    print(f"Target calendar is now empty")

if __name__ == "__main__":
    asyncio.run(clear_target_calendar())
