#!/usr/bin/env python3
"""
CRITICAL: Signature Consistency Test

This script validates that all signature generation methods produce IDENTICAL results.
Signature mismatches cause thousands of duplicate events to be created.

Run this before ANY commit that touches signature generation code.
"""

import sys
import os
import json
from datetime import datetime, timezone
from typing import Dict, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the classes that have signature methods
from sync import ChangeTracker, SyncValidator, SyncEngine
from auth import AuthManager
import config

def create_test_event(event_type: str = 'single', **overrides) -> Dict:
    """Create a test event with specified type and overrides"""
    base_event = {
        'id': 'test-event-123',
        'subject': 'Test Event',
        'start': {
            'dateTime': '2025-01-15T09:00:00Z',
            'timeZone': 'UTC'
        },
        'end': {
            'dateTime': '2025-01-15T10:00:00Z', 
            'timeZone': 'UTC'
        },
        'location': {
            'displayName': 'Conference Room A'
        },
        'categories': ['Public'],
        'showAs': 'busy',
        'isAllDay': False,
        'lastModifiedDateTime': '2025-01-05T10:30:00Z'
    }
    
    if event_type == 'recurring':
        base_event.update({
            'type': 'seriesMaster',
            'recurrence': {
                'pattern': {
                    'type': 'weekly',
                    'interval': 1,
                    'daysOfWeek': ['monday', 'wednesday', 'friday']
                }
            }
        })
    elif event_type == 'occurrence':
        base_event.update({
            'type': 'occurrence',
            'seriesMasterId': 'series-master-456'
        })
    else:  # single
        base_event['type'] = 'singleInstance'
    
    # Apply any overrides
    base_event.update(overrides)
    return base_event

def test_signature_consistency():
    """Test that all signature methods produce identical results"""
    print("🔍 Testing signature consistency across all classes...")
    
    # Create instances of each class
    change_tracker = ChangeTracker()
    
    # SyncValidator doesn't need auth
    sync_validator = SyncValidator()
    
    # SyncEngine needs auth manager
    auth_manager = AuthManager()
    sync_engine = SyncEngine(auth_manager)
    
    # Test cases
    test_cases = [
        {
            'name': 'Single Event',
            'event': create_test_event('single'),
            'expected_prefix': 'single:'
        },
        {
            'name': 'Single Event with Different Subject',
            'event': create_test_event('single', subject='Mass - Sunday'),
            'expected_prefix': 'single:'
        },
        {
            'name': 'Single Event with Location',
            'event': create_test_event('single', 
                subject='Team Meeting',
                location={'displayName': 'Room 101'}),
            'expected_prefix': 'single:'
        },
        {
            'name': 'Recurring Event',
            'event': create_test_event('recurring', subject='Weekly Meeting'),
            'expected_prefix': 'recurring:'
        },
        {
            'name': 'Occurrence Event',
            'event': create_test_event('occurrence', subject='Meeting Instance'),
            'expected_prefix': 'single:'
        },
        {
            'name': 'All Day Event',
            'event': create_test_event('single', 
                subject='All Day Event',
                isAllDay=True,
                start={'date': '2025-01-15'},
                end={'date': '2025-01-15'}),
            'expected_prefix': 'single:'
        }
    ]
    
    all_tests_passed = True
    
    for test_case in test_cases:
        print(f"\n📋 Testing: {test_case['name']}")
        event = test_case['event']
        
        # Get signatures from each class
        try:
            ct_sig = change_tracker._create_event_signature(event)
            sv_sig = sync_validator._create_event_signature(event)
            se_sig = sync_engine._create_event_signature(event)
            
            print(f"  ChangeTracker: {ct_sig}")
            print(f"  SyncValidator: {sv_sig}")
            print(f"  SyncEngine:    {se_sig}")
            
            # Check if all signatures are identical
            signatures = [ct_sig, sv_sig, se_sig]
            if len(set(signatures)) == 1:
                print(f"  ✅ PASS - All signatures match")
            else:
                print(f"  ❌ FAIL - Signatures differ!")
                all_tests_passed = False
                
                # Show differences
                for i, sig in enumerate(signatures):
                    class_name = ['ChangeTracker', 'SyncValidator', 'SyncEngine'][i]
                    print(f"    {class_name}: {sig}")
            
            # Check expected prefix
            if not ct_sig.startswith(test_case['expected_prefix']):
                print(f"  ⚠️  WARNING - Unexpected prefix. Expected: {test_case['expected_prefix']}")
                
        except Exception as e:
            print(f"  ❌ ERROR - Exception during signature generation: {e}")
            all_tests_passed = False
    
    return all_tests_passed

def test_normalization_methods():
    """Test that normalization methods are identical"""
    print("\n🔍 Testing normalization method consistency...")
    
    # Create instances
    change_tracker = ChangeTracker()
    auth_manager = AuthManager()
    sync_engine = SyncEngine(auth_manager)
    
    test_cases = [
        ('Test Subject', 'test subject'),
        ('Mass - Sunday', 'mass sunday'),
        ('Team Meeting & Discussion', 'team meeting and discussion'),
        ('Conference Room A', 'conferencerooma'),
        ('2025-01-15T09:00:00Z', '2025-01-15T09:00'),
        ('2025-01-15T09:00:00+00:00', '2025-01-15T09:00')
    ]
    
    normalization_tests_passed = True
    
    for input_val, expected in test_cases:
        print(f"\n📋 Testing normalization: '{input_val}' -> '{expected}'")
        
        try:
            # Test subject normalization
            if 'Subject' in str(type(input_val)) or isinstance(input_val, str) and len(input_val.split()) > 1:
                ct_norm = change_tracker._normalize_subject(input_val)
                se_norm = sync_engine._normalize_subject(input_val)
                
                print(f"  Subject normalization:")
                print(f"    ChangeTracker: '{ct_norm}'")
                print(f"    SyncEngine:    '{se_norm}'")
                
                if ct_norm == se_norm:
                    print(f"  ✅ PASS - Subject normalization matches")
                else:
                    print(f"  ❌ FAIL - Subject normalization differs!")
                    normalization_tests_passed = False
            
            # Test datetime normalization  
            if 'T' in input_val or 'Z' in input_val:
                ct_norm = change_tracker._normalize_datetime(input_val)
                se_norm = sync_engine._normalize_datetime(input_val)
                
                print(f"  Datetime normalization:")
                print(f"    ChangeTracker: '{ct_norm}'")
                print(f"    SyncEngine:    '{se_norm}'")
                
                if ct_norm == se_norm:
                    print(f"  ✅ PASS - Datetime normalization matches")
                else:
                    print(f"  ❌ FAIL - Datetime normalization differs!")
                    normalization_tests_passed = False
                    
        except Exception as e:
            print(f"  ❌ ERROR - Exception during normalization: {e}")
            normalization_tests_passed = False
    
    return normalization_tests_passed

def main():
    """Main test function"""
    print("=" * 80)
    print("🚨 CRITICAL SIGNATURE CONSISTENCY TEST")
    print("=" * 80)
    print("This test validates that all signature generation methods produce")
    print("IDENTICAL results. Signature mismatches cause thousands of")
    print("duplicate events to be created.")
    print("=" * 80)
    
    # Run signature consistency tests
    signature_tests_passed = test_signature_consistency()
    
    # Run normalization tests
    normalization_tests_passed = test_normalization_methods()
    
    # Final result
    print("\n" + "=" * 80)
    if signature_tests_passed and normalization_tests_passed:
        print("✅ ALL TESTS PASSED - Signatures are consistent")
        print("✅ Safe to commit signature-related changes")
        return 0
    else:
        print("❌ TESTS FAILED - Signature mismatch exists")
        print("❌ DO NOT COMMIT until signatures are fixed")
        print("\n🔧 Required fixes:")
        if not signature_tests_passed:
            print("  - Make all _create_event_signature methods identical")
        if not normalization_tests_passed:
            print("  - Make all normalization methods identical")
        return 1

if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
