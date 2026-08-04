# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Delete every event from ONE synced calendar.

Usage:
    python clear_target_calendar.py "Sundays At St. Edward"

The calendar must be named explicitly and must be one of the configured sync
targets. There is no default: this used to hardcode "St. Edward Public
Calendar", so running it with any other intention silently wiped the parish's
public calendar.

Prefer the dashboard's Advanced Actions, which asks for a typed confirmation.
"""
import sys

import config
from auth import AuthManager
from calendar_ops import CalendarReader, CalendarWriter


def clear_target_calendar(target_name):
    allowed = [pair['target'] for pair in config.get_sync_pairs()]
    match = next((name for name in allowed if name.lower() == target_name.lower()), None)

    if not match:
        print(f"Refusing to touch '{target_name}'.")
        print(f"It is not a configured sync target. Configured targets: {allowed}")
        return 1

    auth = AuthManager()
    if not auth.is_authenticated():
        print("Not authenticated. Sign in through the web dashboard first.")
        return 1

    reader = CalendarReader(auth)
    writer = CalendarWriter(auth)

    target_id = reader.find_calendar_id(match)
    if not target_id:
        print(f"Calendar '{match}' not found in {config.SHARED_MAILBOX}")
        return 1

    events = reader.get_calendar_events(target_id) or []
    print(f"About to delete {len(events)} events from '{match}'. This cannot be undone.")
    if input('Type DELETE to continue: ').strip() != 'DELETE':
        print("Cancelled.")
        return 1

    deleted = failed = 0
    for event in events:
        event_id = event.get('id')
        if not event_id:
            continue
        try:
            if writer.delete_event(target_id, event_id):
                deleted += 1
            else:
                failed += 1
        except Exception as exc:
            print(f"Failed to delete event: {exc}")
            failed += 1
        if deleted and deleted % 10 == 0:
            print(f"Deleted {deleted} events...")

    print(f"\nCOMPLETE: deleted {deleted}, failed {failed}, calendar '{match}'")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(clear_target_calendar(sys.argv[1]))
