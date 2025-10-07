#!/usr/bin/env python3
# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Duplicate Event Cleanup Script

This script removes duplicate events from the St. Edward Public Calendar.
It groups events by subject + start_time + end_time + location and keeps
the oldest event (earliest creation date) while deleting newer duplicates.

Usage:
    python cleanup_duplicates.py [--dry-run] [--verbose]

Options:
    --dry-run    Show what would be deleted without actually deleting
    --verbose    Show detailed logging
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from collections import defaultdict

# Import our modules
import config
from auth import AuthManager
from calendar_ops import CalendarReader, CalendarWriter
from utils import DateTimeUtils

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class DuplicateCleanup:
    """Handles cleanup of duplicate events from the public calendar"""
    
    def __init__(self, auth_manager: AuthManager, dry_run: bool = False):
        self.auth = auth_manager
        self.reader = CalendarReader(auth_manager)
        self.writer = CalendarWriter(auth_manager)
        self.dry_run = dry_run
        
    def cleanup_duplicates(self) -> Dict:
        """
        Main cleanup function that removes duplicate events
        
        Returns:
            Dict with cleanup statistics
        """
        logger.info("🧹 Starting duplicate event cleanup...")
        
        # Get target calendar ID
        target_calendar_id = self.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_calendar_id:
            return {"error": f"Target calendar '{config.TARGET_CALENDAR}' not found"}
        
        logger.info(f"📅 Target calendar ID: {target_calendar_id}")
        
        # Get all events from the public calendar
        logger.info("📥 Fetching all events from public calendar...")
        all_events = self.reader.get_calendar_events(target_calendar_id)
        
        if not all_events:
            logger.warning("⚠️ No events found in target calendar")
            return {"error": "No events found in target calendar"}
        
        logger.info(f"📊 Found {len(all_events)} total events")
        
        # Group events by signature (subject + start + end + location)
        event_groups = self._group_events_by_signature(all_events)
        
        # Find groups with duplicates
        duplicate_groups = {sig: events for sig, events in event_groups.items() if len(events) > 1}
        
        logger.info(f"🔍 Found {len(duplicate_groups)} groups with duplicates")
        
        if not duplicate_groups:
            logger.info("✅ No duplicates found - calendar is clean!")
            return {
                "success": True,
                "message": "No duplicates found",
                "total_events": len(all_events),
                "duplicate_groups": 0,
                "events_to_delete": 0,
                "events_kept": 0
            }
        
        # Process each duplicate group
        cleanup_stats = {
            "total_events": len(all_events),
            "duplicate_groups": len(duplicate_groups),
            "events_to_delete": 0,
            "events_kept": 0,
            "deletion_errors": 0,
            "groups_processed": 0,
            "deleted_events": [],
            "kept_events": []
        }
        
        for signature, events in duplicate_groups.items():
            result = self._process_duplicate_group(signature, events, target_calendar_id)
            cleanup_stats["events_to_delete"] += result["to_delete_count"]
            cleanup_stats["events_kept"] += result["kept_count"]
            cleanup_stats["deletion_errors"] += result["error_count"]
            cleanup_stats["groups_processed"] += 1
            cleanup_stats["deleted_events"].extend(result["deleted_events"])
            cleanup_stats["kept_events"].extend(result["kept_events"])
        
        # Log summary
        logger.info("="*60)
        logger.info("📊 CLEANUP SUMMARY:")
        logger.info(f"  Total events in calendar: {cleanup_stats['total_events']}")
        logger.info(f"  Duplicate groups found: {cleanup_stats['duplicate_groups']}")
        logger.info(f"  Events to delete: {cleanup_stats['events_to_delete']}")
        logger.info(f"  Events to keep: {cleanup_stats['events_kept']}")
        logger.info(f"  Deletion errors: {cleanup_stats['deletion_errors']}")
        logger.info("="*60)
        
        cleanup_stats["success"] = True
        cleanup_stats["message"] = f"Cleanup completed: {cleanup_stats['events_to_delete']} duplicates removed"
        
        return cleanup_stats
    
    def _group_events_by_signature(self, events: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Group events by their signature (subject + start + end + location)
        
        Args:
            events: List of event dictionaries
            
        Returns:
            Dict mapping signatures to lists of events
        """
        groups = defaultdict(list)
        
        for event in events:
            signature = self._create_event_signature(event)
            groups[signature].append(event)
        
        return dict(groups)
    
    def _create_event_signature(self, event: Dict) -> str:
        """
        Create a signature for an event based on key identifying fields
        
        Args:
            event: Event dictionary
            
        Returns:
            String signature for the event
        """
        subject = event.get('subject', '').strip().lower()
        start_time = event.get('start', {}).get('dateTime', '')
        end_time = event.get('end', {}).get('dateTime', '')
        location = event.get('location', {}).get('displayName', '') if isinstance(event.get('location'), dict) else str(event.get('location', ''))
        
        # Normalize the data for consistent comparison
        subject = ' '.join(subject.split())  # Normalize whitespace
        start_time = start_time.split('T')[0] if start_time else ''  # Just the date part
        end_time = end_time.split('T')[0] if end_time else ''
        location = location.strip().lower()
        
        # Create signature
        signature = f"{subject}|{start_time}|{end_time}|{location}"
        return signature
    
    def _process_duplicate_group(self, signature: str, events: List[Dict], target_calendar_id: str) -> Dict:
        """
        Process a group of duplicate events, keeping the oldest and deleting the rest
        
        Args:
            signature: The signature for this group
            events: List of duplicate events
            
        Returns:
            Dict with processing results
        """
        logger.info(f"🔍 Processing duplicate group: {signature}")
        logger.info(f"   Found {len(events)} duplicate events")
        
        # Sort events by creation date (oldest first), with fallback methods
        events_with_creation = []
        events_without_creation = []
        
        for event in events:
            created = event.get('createdDateTime', '')
            if created:
                events_with_creation.append((created, event))
            else:
                events_without_creation.append(event)
        
        # If we have events with creation dates, use those
        if events_with_creation:
            # Sort by creation date (oldest first)
            events_with_creation.sort(key=lambda x: x[0])
            events_to_process = events_with_creation
            logger.info(f"   📅 Using creation dates to determine oldest event")
        elif events_without_creation:
            # Fallback: use lastModifiedDateTime if available
            events_with_modification = []
            for event in events_without_creation:
                modified = event.get('lastModifiedDateTime', '')
                if modified:
                    events_with_modification.append((modified, event))
            
            if events_with_modification:
                # Sort by modification date (oldest first)
                events_with_modification.sort(key=lambda x: x[0])
                events_to_process = events_with_modification
                logger.info(f"   📝 Using modification dates to determine oldest event")
            else:
                # Final fallback: use event ID (assuming shorter IDs are older)
                events_with_id = []
                for event in events_without_creation:
                    event_id = event.get('id', '')
                    if event_id:
                        events_with_id.append((len(event_id), event_id, event))  # Sort by ID length
                
                if events_with_id:
                    events_with_id.sort(key=lambda x: x[0])  # Sort by ID length (shorter = older)
                    events_to_process = [(event_id, event) for _, event_id, event in events_with_id]
                    logger.info(f"   🆔 Using event ID length to determine oldest event")
                else:
                    logger.warning(f"   ⚠️ No events with usable dates or IDs in group - skipping")
                    return {"to_delete_count": 0, "kept_count": 0, "error_count": 0, "deleted_events": [], "kept_events": []}
        else:
            logger.warning(f"   ⚠️ No events to process in group - skipping")
            return {"to_delete_count": 0, "kept_count": 0, "error_count": 0, "deleted_events": [], "kept_events": []}
        
        # Keep the oldest event, delete the rest
        oldest_event = events_to_process[0][1]
        events_to_delete = [event for _, event in events_to_process[1:]]
        
        # Determine which date field was used for sorting
        date_used = "created"
        if oldest_event.get('createdDateTime'):
            date_used = "created"
        elif oldest_event.get('lastModifiedDateTime'):
            date_used = "modified"
        else:
            date_used = "ID length"
        
        logger.info(f"   ✅ Keeping oldest event: '{oldest_event.get('subject', 'Unknown')}' (using {date_used} date: {oldest_event.get('createdDateTime', oldest_event.get('lastModifiedDateTime', 'N/A'))})")
        
        result = {
            "to_delete_count": len(events_to_delete),
            "kept_count": 1,
            "error_count": 0,
            "deleted_events": [],
            "kept_events": [oldest_event]
        }
        
        # Delete the duplicate events
        for event in events_to_delete:
            event_id = event.get('id')
            subject = event.get('subject', 'Unknown')
            created = event.get('createdDateTime', 'Unknown')
            
            logger.info(f"   🗑️ Deleting duplicate: '{subject}' (created: {created})")
            
            if self.dry_run:
                logger.info(f"   🧪 DRY RUN: Would delete event ID {event_id}")
                result["deleted_events"].append({
                    "id": event_id,
                    "subject": subject,
                    "created": created,
                    "action": "would_delete"
                })
            else:
                try:
                    success = self.writer.delete_event(target_calendar_id, event_id)
                    if success:
                        logger.info(f"   ✅ Successfully deleted: '{subject}'")
                        result["deleted_events"].append({
                            "id": event_id,
                            "subject": subject,
                            "created": created,
                            "action": "deleted"
                        })
                        time.sleep(2)  # Rate limiting: 2 sec delay between deletions
                    else:
                        logger.error(f"   ❌ Failed to delete: '{subject}'")
                        result["error_count"] += 1
                        result["deleted_events"].append({
                            "id": event_id,
                            "subject": subject,
                            "created": created,
                            "action": "failed"
                        })
                except Exception as e:
                    logger.error(f"   ❌ Error deleting '{subject}': {e}")
                    result["error_count"] += 1
                    result["deleted_events"].append({
                        "id": event_id,
                        "subject": subject,
                        "created": created,
                        "action": "error",
                        "error": str(e)
                    })
        
        return result


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Clean up duplicate events from St. Edward Public Calendar')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted without actually deleting')
    parser.add_argument('--verbose', action='store_true', help='Show detailed logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.dry_run:
        logger.info("🧪 DRY RUN MODE - No events will actually be deleted")
    
    try:
        # Initialize authentication
        logger.info("🔐 Initializing authentication...")
        auth_manager = AuthManager()
        
        if not auth_manager.is_authenticated():
            logger.error("❌ Authentication failed")
            return 1
        
        logger.info("✅ Authentication successful")
        
        # Initialize cleanup
        cleanup = DuplicateCleanup(auth_manager, dry_run=args.dry_run)
        
        # Run cleanup
        result = cleanup.cleanup_duplicates()
        
        if result.get("success"):
            logger.info(f"✅ {result['message']}")
            return 0
        else:
            logger.error(f"❌ Cleanup failed: {result.get('error', 'Unknown error')}")
            return 1
            
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
