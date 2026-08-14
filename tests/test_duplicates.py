"""
Duplicate detection tests - converted from validate_duplicate_fix.py

Tests that duplicate detection works correctly and no duplicates are created.
"""

import pytest
from datetime import datetime
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signature_utils import generate_event_signature
from sync import SyncEngine, dedupe_events_by_id


# The exact extended-property id calendar_ops writes on every create and
# $expands on every read. _is_synced_event matches on the 'Name sourceEventId'
# suffix, so the GUID has to be here verbatim.
SOURCE_ID_PROP = 'String {66f5a359-4659-4830-9070-00047ec6ac6e} Name sourceEventId'
LAST_SYNCED_PROP = 'String {66f5a359-4659-4830-9070-00047ec6ac6e} Name lastSynced'


def graph_target_row(event_id, source_id='src-vigil-1', subject='Mass- Vigil',
                     start='2026-02-21T22:00:00.0000000',
                     end='2026-02-21T23:00:00.0000000',
                     body_content='', hand_created=False):
    """A target-calendar row shaped the way Microsoft Graph actually returns it.

    Built from the calendarView $select in calendar_ops.get_calendar_events, NOT
    from _prepare_event_for_api. Building both sides with the writer's helper is
    how this repo has previously shipped green tests over a broken service.

    Three consequences of that $select are load-bearing here:
      - createdDateTime is not requested, so it is ABSENT. That is why the old
        creation-time tie-break compared '' against '' on every collision.
      - location is not requested, so every target row is location-blind and the
        signature reduces to subject plus start.
      - the public calendar's body is written marker-only and reads back as an
        EMPTY string, so a synced public event's only identity is the expanded
        sourceEventId property.
    """
    row = {
        'id': event_id,
        'subject': subject,
        'body': {'contentType': 'html', 'content': body_content},
        'start': {'dateTime': start, 'timeZone': 'UTC'},
        'end': {'dateTime': end, 'timeZone': 'UTC'},
        'categories': [],
        'showAs': 'busy',
        'type': 'singleInstance',
        'isCancelled': False,
        'isAllDay': False,
        'sensitivity': 'normal',
        'responseStatus': {'response': 'organizer', 'time': '0001-01-01T00:00:00Z'},
        'organizer': {'emailAddress': {'name': 'Calendar', 'address': 'calendar@stedward.org'}},
    }
    if not hand_created:
        row['singleValueExtendedProperties'] = [
            {'id': SOURCE_ID_PROP, 'value': source_id},
            {'id': LAST_SYNCED_PROP, 'value': '2026-08-14T15:31:00Z'},
        ]
    return row


class TestDuplicateDetection:
    """Test duplicate event detection"""
    
    @pytest.mark.duplicate
    def test_identical_events_same_signature(self):
        """Identical events should produce same signature"""
        event1 = {
            'subject': 'Team Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        event2 = {
            'subject': 'Team Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        sig1 = generate_event_signature(event1)
        sig2 = generate_event_signature(event2)
        
        assert sig1 == sig2, "Identical events must have same signature"
    
    @pytest.mark.duplicate
    def test_different_subjects_different_signatures(self):
        """Events with different subjects should have different signatures"""
        event1 = {
            'subject': 'Meeting A',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        event2 = {
            'subject': 'Meeting B',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        sig1 = generate_event_signature(event1)
        sig2 = generate_event_signature(event2)
        
        assert sig1 != sig2, "Different subjects must have different signatures"
    
    @pytest.mark.duplicate
    def test_different_times_different_signatures(self):
        """Events at different times should have different signatures"""
        event1 = {
            'subject': 'Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        event2 = {
            'subject': 'Meeting',
            'start': {'dateTime': '2024-03-15T14:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T15:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        sig1 = generate_event_signature(event1)
        sig2 = generate_event_signature(event2)
        
        assert sig1 != sig2, "Different times must have different signatures"
    
    @pytest.mark.duplicate
    def test_different_locations_different_signatures(self):
        """Events in different locations should have different signatures"""
        event1 = {
            'subject': 'Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room A'},
            'isAllDay': False
        }
        
        event2 = {
            'subject': 'Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room B'},
            'isAllDay': False
        }
        
        sig1 = generate_event_signature(event1)
        sig2 = generate_event_signature(event2)
        
        assert sig1 != sig2, "Different locations must have different signatures"


class TestSyncedEventDetection:
    """Test detection of synced vs non-synced events"""
    
    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_detect_synced_event_with_sync_marker(self):
        """Event with SYNC_ID marker should be detected as synced"""
        from sync import SyncEngine
        
        # Create mock sync engine (don't need real auth for this)
        sync_engine = SyncEngine(auth_manager=None)
        
        event = {
            'subject': 'Test Event',
            'body': {
                'content': 'Some content <!-- SYNC_ID:abc123 --> more content'
            }
        }
        
        is_synced = sync_engine._is_synced_event(event)
        assert is_synced, "Event with SYNC_ID should be detected as synced"
    
    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_detect_non_synced_event(self):
        """Event without sync marker should not be detected as synced"""
        from sync import SyncEngine
        
        sync_engine = SyncEngine(auth_manager=None)
        
        event = {
            'subject': 'Test Event',
            'body': {
                'content': 'Regular event content'
            }
        }
        
        is_synced = sync_engine._is_synced_event(event)
        assert not is_synced, "Event without SYNC_ID should not be detected as synced"


class TestWeeklyWindowRepeats:
    """The churn: one real event fetched twice, condemned as its own duplicate.

    Calendars are read a week at a time and generate_weekly_ranges makes each
    window's end instant the next window's start instant. calendarView returns
    everything OVERLAPPING a window, so an event in progress at a boundary comes
    back from both windows under one Graph id. _build_event_map compared only
    signatures, so the second sighting was condemned and its id, which is the
    surviving event's id, went on the deletion list. Every run deleted a batch of
    live events and the next run re-created exactly that batch.
    """

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_same_event_seen_twice_is_not_a_duplicate(self):
        """One id, two sightings, zero deletions."""
        engine = SyncEngine(auth_manager=None)

        sighting = graph_target_row('AAMkAG-vigil')
        # Two separate dicts, as two HTTP responses would produce.
        week_one_view = dict(sighting)
        week_two_view = dict(sighting)

        event_map, duplicates = engine._build_event_map([week_one_view, week_two_view])

        assert duplicates == [], "A boundary-straddling event is one event, not a duplicate"
        assert len(event_map) == 1

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_concatenated_weekly_windows_plan_no_deletions(self):
        """The real shape: two windows concatenated, the straddler in both."""
        engine = SyncEngine(auth_manager=None)

        straddler = graph_target_row('AAMkAG-riti', source_id='src-riti',
                                     subject='RITI',
                                     start='2026-02-22T01:00:00.0000000',
                                     end='2026-02-22T13:00:00.0000000')
        week_one = [
            graph_target_row('AAMkAG-week1', source_id='src-1', subject='Room in the Inn',
                             start='2026-02-18T23:00:00.0000000'),
            dict(straddler),
        ]
        week_two = [
            dict(straddler),
            graph_target_row('AAMkAG-week2', source_id='src-2', subject='Mass- Vigil',
                             start='2026-02-28T22:00:00.0000000'),
        ]

        fetched = dedupe_events_by_id(week_one + week_two)
        event_map, duplicates = engine._build_event_map(fetched)

        assert len(fetched) == 3, "The straddler collapses to one row"
        assert duplicates == []
        assert len(event_map) == 3

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_dedupe_keeps_first_sighting_and_order(self):
        first = graph_target_row('AAMkAG-1', source_id='src-1')
        second = graph_target_row('AAMkAG-2', source_id='src-2', subject='RITI')
        repeat = dict(first)

        result = dedupe_events_by_id([first, second, repeat])

        assert [e['id'] for e in result] == ['AAMkAG-1', 'AAMkAG-2']
        assert result[0] is first, "First sighting wins"

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_dedupe_keeps_rows_that_have_no_id(self):
        """Nothing to compare on, so nothing gets dropped."""
        rows = [{'subject': 'No id'}, {'subject': 'Also no id'}]

        assert dedupe_events_by_id(rows) == rows

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_returns_map_and_duplicates_tuple(self):
        """tests/test_sync_pairs.py stubs this as `lambda events: ({}, [])`."""
        engine = SyncEngine(auth_manager=None)

        result = engine._build_event_map([graph_target_row('AAMkAG-1')])

        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], dict) and isinstance(result[1], list)


class TestHandCreatedTargetEventsAreProtected:
    """A staff member's own event in the target calendar is never ours to delete."""

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_hand_created_event_colliding_on_signature_is_never_condemned(self):
        """No sourceEventId property, no SYNC_ID body marker, same subject and start."""
        engine = SyncEngine(auth_manager=None)

        synced = graph_target_row('AAMkAG-synced')
        by_hand = graph_target_row('AAMkAG-by-hand', hand_created=True,
                                   body_content='Set up chairs at 5pm')

        assert engine._create_event_signature(synced) == engine._create_event_signature(by_hand)
        assert engine._is_synced_event(by_hand) is False

        _, duplicates = engine._build_event_map([synced, by_hand])

        assert duplicates == [], "A hand-created event must never be deleted by the sync"

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_synced_copy_survives_when_the_hand_created_one_is_seen_first(self):
        """Order must not decide whose event gets destroyed."""
        engine = SyncEngine(auth_manager=None)

        by_hand = graph_target_row('AAMkAG-by-hand', hand_created=True,
                                   body_content='Set up chairs at 5pm')
        synced = graph_target_row('AAMkAG-synced')

        _, duplicates = engine._build_event_map([by_hand, synced])

        assert duplicates == [], "A collision with a hand-entered event deletes nothing"


class TestGenuineDuplicatesAreStillCleaned:
    """Duplicate cleanup was kept, not silently removed. Two real copies still go."""

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_second_copy_under_a_different_id_is_still_a_duplicate(self):
        engine = SyncEngine(auth_manager=None)

        original = graph_target_row('AAMkAG-original')
        real_copy = graph_target_row('AAMkAG-second-copy')

        _, duplicates = engine._build_event_map([original, real_copy])

        assert [e['id'] for e in duplicates] == ['AAMkAG-second-copy']

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_first_copy_seen_is_the_one_kept(self):
        """Deterministic, because there is no creation time to compare.

        Graph does not return createdDateTime on a calendarView $select, so the
        tie-break that claimed to keep the older event read '' on both sides.
        """
        engine = SyncEngine(auth_manager=None)

        original = graph_target_row('AAMkAG-original')
        real_copy = graph_target_row('AAMkAG-second-copy')
        assert 'createdDateTime' not in original
        assert 'createdDateTime' not in real_copy

        event_map, duplicates = engine._build_event_map([original, real_copy])
        signature = engine._create_event_signature(original)

        assert event_map[signature]['id'] == 'AAMkAG-original'
        assert duplicates[0]['id'] == 'AAMkAG-second-copy'

    @pytest.mark.duplicate
    @pytest.mark.unit
    def test_marker_only_public_body_is_still_recognised_as_synced(self):
        """Identity on the public calendar rides the extended property, not the body.

        The public calendar is written a marker-only body on purpose, so source
        descriptions (which carry door codes) never reach it, and that body reads
        back from Graph as an empty string. Duplicate cleanup would fail closed if
        it depended on the marker.
        """
        engine = SyncEngine(auth_manager=None)

        public_row = graph_target_row('AAMkAG-public', body_content='')

        assert public_row['body']['content'] == ''
        assert engine._is_synced_event(public_row) is True


@pytest.fixture
def duplicate_events():
    """Fixture providing duplicate event pairs for testing"""
    base_event = {
        'subject': 'Duplicate Test',
        'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
        'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
        'location': {'displayName': 'Test Room'},
        'isAllDay': False
    }
    
    # Create duplicate
    duplicate = base_event.copy()
    
    return [base_event, duplicate]
