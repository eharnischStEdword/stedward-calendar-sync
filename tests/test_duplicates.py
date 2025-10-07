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
from sync import SyncEngine


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
