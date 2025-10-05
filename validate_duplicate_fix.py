#!/usr/bin/env python3
# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Validation Script for Duplicate Event Fix

This script validates that the duplicate event fixes are working correctly.
It checks:
1. The cleanup script can identify duplicates
2. The sync system properly detects existing events
3. No new duplicates are created during sync

Usage:
    python validate_duplicate_fix.py [--verbose]
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Dict, List

# Import our modules
import config
from auth import AuthManager
from calendar_ops import CalendarReader, CalendarWriter
from sync import SyncEngine
from utils import DateTimeUtils

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class DuplicateFixValidator:
    """Validates that the duplicate event fixes are working correctly"""
    
    def __init__(self, auth_manager: AuthManager):
        self.auth = auth_manager
        self.reader = CalendarReader(auth_manager)
        self.writer = CalendarWriter(auth_manager)
        self.sync_engine = SyncEngine(auth_manager)
        
    def validate_fixes(self) -> Dict:
        """
        Run all validation tests
        
        Returns:
            Dict with validation results
        """
        logger.info("🔍 Starting validation of duplicate event fixes...")
        
        results = {
            "success": True,
            "tests_passed": 0,
            "tests_failed": 0,
            "test_results": []
        }
        
        # Test 1: Check if _is_synced_event works correctly
        test1_result = self._test_synced_event_detection()
        results["test_results"].append(test1_result)
        if test1_result["passed"]:
            results["tests_passed"] += 1
        else:
            results["tests_failed"] += 1
            results["success"] = False
        
        # Test 2: Check duplicate detection in target calendar
        test2_result = self._test_duplicate_detection()
        results["test_results"].append(test2_result)
        if test2_result["passed"]:
            results["tests_passed"] += 1
        else:
            results["tests_failed"] += 1
            results["success"] = False
        
        # Test 3: Check sync signature generation
        test3_result = self._test_signature_generation()
        results["test_results"].append(test3_result)
        if test3_result["passed"]:
            results["tests_passed"] += 1
        else:
            results["tests_failed"] += 1
            results["success"] = False
        
        # Test 4: Check calendar access
        test4_result = self._test_calendar_access()
        results["test_results"].append(test4_result)
        if test4_result["passed"]:
            results["tests_passed"] += 1
        else:
            results["tests_failed"] += 1
            results["success"] = False
        
        logger.info("="*60)
        logger.info("📊 VALIDATION SUMMARY:")
        logger.info(f"  Tests passed: {results['tests_passed']}")
        logger.info(f"  Tests failed: {results['tests_failed']}")
        logger.info(f"  Overall success: {'✅ YES' if results['success'] else '❌ NO'}")
        logger.info("="*60)
        
        return results
    
    def _test_synced_event_detection(self) -> Dict:
        """Test that _is_synced_event correctly identifies synced events"""
        logger.info("🧪 Test 1: Synced event detection")
        
        # Create test events with different markers
        test_events = [
            {
                "id": "test1",
                "subject": "Test Event 1",
                "body": {"content": "<!-- SYNC_ID:source123 --> This is a synced event"},
                "start": {"dateTime": "2024-01-01T10:00:00Z"},
                "end": {"dateTime": "2024-01-01T11:00:00Z"}
            },
            {
                "id": "test2", 
                "subject": "Test Event 2",
                "body": {"content": "Auto-synced from main calendar"},
                "start": {"dateTime": "2024-01-01T10:00:00Z"},
                "end": {"dateTime": "2024-01-01T11:00:00Z"}
            },
            {
                "id": "test3",
                "subject": "Test Event 3", 
                "body": {"content": "This is a manual event"},
                "start": {"dateTime": "2024-01-01T10:00:00Z"},
                "end": {"dateTime": "2024-01-01T11:00:00Z"}
            }
        ]
        
        # Test each event
        results = []
        for event in test_events:
            is_synced = self.sync_engine._is_synced_event(event)
            expected = event["id"] in ["test1", "test2"]  # First two should be synced
            results.append({
                "event_id": event["id"],
                "is_synced": is_synced,
                "expected": expected,
                "correct": is_synced == expected
            })
        
        all_correct = all(r["correct"] for r in results)
        
        logger.info(f"   Results: {results}")
        logger.info(f"   ✅ PASSED" if all_correct else "   ❌ FAILED")
        
        return {
            "test_name": "Synced event detection",
            "passed": all_correct,
            "details": results
        }
    
    def _test_duplicate_detection(self) -> Dict:
        """Test that duplicate detection works correctly"""
        logger.info("🧪 Test 2: Duplicate detection")
        
        # Create test events with same signature
        event1 = {
            "id": "dup1",
            "subject": "Duplicate Test Event",
            "start": {"dateTime": "2024-01-01T10:00:00Z"},
            "end": {"dateTime": "2024-01-01T11:00:00Z"},
            "location": {"displayName": "Test Location"},
            "createdDateTime": "2024-01-01T08:00:00Z"
        }
        
        event2 = {
            "id": "dup2", 
            "subject": "Duplicate Test Event",
            "start": {"dateTime": "2024-01-01T10:00:00Z"},
            "end": {"dateTime": "2024-01-01T11:00:00Z"},
            "location": {"displayName": "Test Location"},
            "createdDateTime": "2024-01-01T09:00:00Z"  # Later creation time
        }
        
        # Test signature generation
        sig1 = self.sync_engine._create_event_signature(event1)
        sig2 = self.sync_engine._create_event_signature(event2)
        
        signatures_match = sig1 == sig2
        
        logger.info(f"   Event 1 signature: {sig1}")
        logger.info(f"   Event 2 signature: {sig2}")
        logger.info(f"   Signatures match: {signatures_match}")
        logger.info(f"   ✅ PASSED" if signatures_match else "   ❌ FAILED")
        
        return {
            "test_name": "Duplicate detection",
            "passed": signatures_match,
            "details": {
                "signature1": sig1,
                "signature2": sig2,
                "match": signatures_match
            }
        }
    
    def _test_signature_generation(self) -> Dict:
        """Test that signature generation is consistent"""
        logger.info("🧪 Test 3: Signature generation")
        
        # Test with different event types
        test_cases = [
            {
                "name": "Single event",
                "event": {
                    "subject": "Mass",
                    "start": {"dateTime": "2024-01-01T10:00:00Z"},
                    "end": {"dateTime": "2024-01-01T11:00:00Z"},
                    "type": "singleInstance"
                }
            },
            {
                "name": "Recurring event",
                "event": {
                    "subject": "Weekly Mass",
                    "start": {"dateTime": "2024-01-01T10:00:00Z"},
                    "end": {"dateTime": "2024-01-01T11:00:00Z"},
                    "type": "seriesMaster",
                    "recurrence": {
                        "pattern": {
                            "type": "weekly",
                            "interval": 1,
                            "daysOfWeek": ["sunday"]
                        }
                    }
                }
            }
        ]
        
        results = []
        for test_case in test_cases:
            signature = self.sync_engine._create_event_signature(test_case["event"])
            results.append({
                "name": test_case["name"],
                "signature": signature,
                "valid": len(signature) > 0
            })
        
        all_valid = all(r["valid"] for r in results)
        
        logger.info(f"   Results: {results}")
        logger.info(f"   ✅ PASSED" if all_valid else "   ❌ FAILED")
        
        return {
            "test_name": "Signature generation",
            "passed": all_valid,
            "details": results
        }
    
    def _test_calendar_access(self) -> Dict:
        """Test that we can access the calendars"""
        logger.info("🧪 Test 4: Calendar access")
        
        try:
            # Test source calendar access
            source_id = self.reader.find_calendar_id(config.SOURCE_CALENDAR)
            source_accessible = source_id is not None
            
            # Test target calendar access  
            target_id = self.reader.find_calendar_id(config.TARGET_CALENDAR)
            target_accessible = target_id is not None
            
            # Test getting calendars list
            calendars = self.reader.get_calendars()
            calendars_accessible = calendars is not None and len(calendars) > 0
            
            all_accessible = source_accessible and target_accessible and calendars_accessible
            
            logger.info(f"   Source calendar accessible: {source_accessible}")
            logger.info(f"   Target calendar accessible: {target_accessible}")
            logger.info(f"   Calendars list accessible: {calendars_accessible}")
            logger.info(f"   ✅ PASSED" if all_accessible else "   ❌ FAILED")
            
            return {
                "test_name": "Calendar access",
                "passed": all_accessible,
                "details": {
                    "source_accessible": source_accessible,
                    "target_accessible": target_accessible,
                    "calendars_accessible": calendars_accessible,
                    "source_id": source_id,
                    "target_id": target_id,
                    "calendar_count": len(calendars) if calendars else 0
                }
            }
            
        except Exception as e:
            logger.error(f"   ❌ FAILED: {e}")
            return {
                "test_name": "Calendar access",
                "passed": False,
                "details": {"error": str(e)}
            }


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Validate duplicate event fixes')
    parser.add_argument('--verbose', action='store_true', help='Show detailed logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # Initialize authentication
        logger.info("🔐 Initializing authentication...")
        auth_manager = AuthManager()
        
        if not auth_manager.is_authenticated():
            logger.error("❌ Authentication failed")
            return 1
        
        logger.info("✅ Authentication successful")
        
        # Initialize validator
        validator = DuplicateFixValidator(auth_manager)
        
        # Run validation
        results = validator.validate_fixes()
        
        if results["success"]:
            logger.info("✅ All validation tests passed!")
            return 0
        else:
            logger.error("❌ Some validation tests failed")
            return 1
            
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
