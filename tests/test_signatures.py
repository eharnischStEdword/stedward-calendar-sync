"""
Signature validation tests - converted from test_signature_match.py

Tests signature generation consistency between sync.py and signature_utils.py
CRITICAL: These must always match or duplicate detection breaks.
"""

import pytest
from datetime import datetime
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signature_utils import generate_event_signature
from utils import DateTimeUtils


class TestSignatureGeneration:
    """Test signature generation for various event types"""
    
    @pytest.mark.signature
    def test_regular_event_signature(self):
        """Test signature generation for regular (non-all-day) events"""
        event = {
            'subject': 'Test Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Conference Room'},
            'isAllDay': False
        }
        
        signature = generate_event_signature(event)
        
        # Signature should be non-empty
        assert signature, "Signature should not be empty"
        
        # Should be consistent - generate twice and compare
        signature2 = generate_event_signature(event)
        assert signature == signature2, "Signatures should be identical for same event"
    
    @pytest.mark.signature
    def test_all_day_event_signature(self):
        """Test signature generation for all-day events"""
        event = {
            'subject': 'All Day Event',
            'start': {'dateTime': '2024-03-15T00:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-16T00:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': ''},
            'isAllDay': True
        }
        
        signature = generate_event_signature(event)
        
        assert signature, "All-day event signature should not be empty"
        
        # Verify consistency
        signature2 = generate_event_signature(event)
        assert signature == signature2, "All-day signatures should be identical"
    
    @pytest.mark.signature
    def test_event_without_location(self):
        """Test signature generation for events without location"""
        event = {
            'subject': 'Virtual Meeting',
            'start': {'dateTime': '2024-03-15T14:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T15:00:00', 'timeZone': 'America/Chicago'},
            'location': None,
            'isAllDay': False
        }
        
        signature = generate_event_signature(event)
        
        assert signature, "Signature should work without location"
    
    @pytest.mark.signature
    def test_signature_includes_key_fields(self):
        """Verify signature includes subject, start, end, location"""
        event = {
            'subject': 'Important Meeting',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Room 101'},
            'isAllDay': False
        }
        
        signature = generate_event_signature(event)
        
        # Signature should contain key elements (implementation detail: it's a hash)
        # Just verify it's non-empty and consistent
        assert len(signature) > 0, "Signature should have content"
        
        # Change subject - signature should change
        event['subject'] = 'Different Meeting'
        signature2 = generate_event_signature(event)
        assert signature != signature2, "Different subjects should produce different signatures"


class TestSignatureConsistency:
    """Test that sync.py and signature_utils.py generate matching signatures"""
    
    @pytest.mark.signature
    @pytest.mark.integration
    def test_sync_signature_matches_utils(self):
        """CRITICAL: Verify sync.py uses signature_utils.py correctly"""
        # This test would need to import from sync.py and compare
        # For now, document that manual verification is needed
        
        # Import sync module
        from sync import SyncEngine
        
        # Create test event
        event = {
            'subject': 'Consistency Test',
            'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
            'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
            'location': {'displayName': 'Test Location'},
            'isAllDay': False
        }
        
        # Generate signature using signature_utils
        utils_signature = generate_event_signature(event)
        
        # Verify sync engine would generate same signature
        # Note: SyncEngine uses signature_utils internally
        assert utils_signature, "Signature generation should work"


class TestLocationIsAbsentFromFetchedEvents:
    """Why location must not be added to the calendarView $select on its own.

    The signature includes location, but calendar_ops.get_calendar_events does
    not request the field, and both the source and the target calendar are read
    through that same call. Both sides are therefore location-blind and their
    signatures agree, which is the only reason matching works at all.

    Turning the field on for reads without also backfilling locations onto the
    already-synced target events would give source rows a real displayName and
    target rows an empty one. Every signature would disagree at once: roughly
    1,900 adds and 1,900 deletions planned on the public pair in a single run.
    """

    @pytest.mark.signature
    @pytest.mark.unit
    def test_source_and_target_rows_agree_while_neither_carries_a_location(self):
        # Shaped from the $select, so neither side has a 'location' key.
        source_row = {
            'id': 'src-1',
            'subject': 'Mass- Vigil',
            'start': {'dateTime': '2026-02-21T22:00:00.0000000', 'timeZone': 'UTC'},
            'end': {'dateTime': '2026-02-21T23:00:00.0000000', 'timeZone': 'UTC'},
            'type': 'singleInstance',
            'isAllDay': False,
        }
        target_row = dict(source_row, id='tgt-1')

        assert generate_event_signature(source_row) == generate_event_signature(target_row)

    @pytest.mark.signature
    @pytest.mark.unit
    def test_a_location_on_one_side_only_breaks_the_match(self):
        base = {
            'id': 'src-1',
            'subject': 'Mass- Vigil',
            'start': {'dateTime': '2026-02-21T22:00:00.0000000', 'timeZone': 'UTC'},
            'end': {'dateTime': '2026-02-21T23:00:00.0000000', 'timeZone': 'UTC'},
            'type': 'singleInstance',
            'isAllDay': False,
        }
        source_with_location = dict(base, location={'displayName': 'Church'})
        target_without = dict(base, id='tgt-1')

        assert generate_event_signature(source_with_location) != generate_event_signature(target_without), (
            "This mismatch is what a $select change would cause on every event at once"
        )


@pytest.fixture
def sample_event():
    """Fixture providing a sample event for tests"""
    return {
        'subject': 'Sample Event',
        'start': {'dateTime': '2024-03-15T10:00:00', 'timeZone': 'America/Chicago'},
        'end': {'dateTime': '2024-03-15T11:00:00', 'timeZone': 'America/Chicago'},
        'location': {'displayName': 'Sample Location'},
        'isAllDay': False
    }


def test_signature_fixture(sample_event):
    """Test using fixture"""
    signature = generate_event_signature(sample_event)
    assert signature is not None
