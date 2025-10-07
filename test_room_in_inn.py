"""
Test signature generation for Room in the Inn event.
"""
from signature_utils import generate_event_signature, normalize_subject, normalize_datetime, normalize_location

# Test with variations that might exist
test_events = [
    {
        'name': 'Standard format',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': {'displayName': ''}
        }
    },
    {
        'name': 'With location',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': {'displayName': 'Church Hall'}
        }
    },
    {
        'name': 'Different time format',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00Z'},
            'location': {'displayName': ''}
        }
    },
    {
        'name': 'As occurrence',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'occurrence',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': {'displayName': ''}
        }
    },
    {
        'name': 'Different capitalization',
        'event': {
            'subject': 'Room In The Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': {'displayName': ''}
        }
    },
    {
        'name': 'With extra spaces',
        'event': {
            'subject': 'Room in the Inn ',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': {'displayName': ''}
        }
    }
]

print("="*60)
print("TESTING 'ROOM IN THE INN' SIGNATURES")
print("="*60)

signatures = []
for test in test_events:
    sig = generate_event_signature(test['event'])
    signatures.append((test['name'], sig))
    print(f"\n{test['name']}:")
    print(f"  Signature: {sig}")

# Check for duplicates
print("\n" + "="*60)
print("CHECKING FOR SIGNATURE COLLISIONS")
print("="*60)

sig_map = {}
for name, sig in signatures:
    if sig not in sig_map:
        sig_map[sig] = []
    sig_map[sig].append(name)

for sig, names in sig_map.items():
    if len(names) > 1:
        print(f"\n✅ Multiple variations produce same signature:")
        print(f"   Signature: {sig}")
        print(f"   Variations: {', '.join(names)}")
    else:
        print(f"\n❌ Unique signature for: {names[0]}")

# Detailed breakdown of signature components
print("\n" + "="*60)
print("SIGNATURE COMPONENT BREAKDOWN")
print("="*60)

# Test with a typical event
event = {
    'subject': 'Room in the Inn',
    'type': 'singleInstance',
    'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
    'location': {'displayName': 'Church Hall'}
}

print("\nDetailed breakdown for:")
print(f"  Subject: '{event['subject']}'")
print(f"  Type: '{event['type']}'")
print(f"  Start: {event['start']}")
print(f"  Location: {event['location']}")

print(f"\nNormalized components:")
print(f"  Subject: '{normalize_subject(event['subject'])}'")

# Extract datetime string for normalization
start_datetime = event['start']['dateTime']
normalized_dt = normalize_datetime(start_datetime)
print(f"  DateTime: '{start_datetime}' -> '{normalized_dt}'")

# Extract location string for normalization
location_str = event['location']['displayName']
normalized_loc = normalize_location(location_str)
print(f"  Location: '{location_str}' -> '{normalized_loc}'")

sig = generate_event_signature(event)
print(f"\nFull signature: {sig}")

# Test edge cases that might cause issues
print("\n" + "="*60)
print("EDGE CASE TESTING")
print("="*60)

edge_cases = [
    {
        'name': 'Empty location dict',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': {}
        }
    },
    {
        'name': 'Missing location field',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'}
        }
    },
    {
        'name': 'Location as string (old format)',
        'event': {
            'subject': 'Room in the Inn',
            'type': 'singleInstance',
            'start': {'dateTime': '2026-02-08T20:30:00.0000000', 'timeZone': 'UTC'},
            'location': 'Church Hall'
        }
    }
]

for test in edge_cases:
    try:
        sig = generate_event_signature(test['event'])
        print(f"\n{test['name']}: {sig}")
    except Exception as e:
        print(f"\n{test['name']}: ERROR - {e}")

print("\n" + "="*60)
