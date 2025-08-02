# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Change Tracker - Efficient change detection for calendar sync
"""
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
from utils.timezone import get_central_time

logger = logging.getLogger(__name__)


class ChangeTracker:
    """Tracks changes to calendar events for efficient syncing"""
    
    def __init__(self, cache_file: str = '/data/event_cache.json'):
        self.cache_file = cache_file
        self.event_cache = {}  # signature -> event_data
        self.last_sync_time = None
        self._load_cache()
    
    def _load_cache(self):
        """Load cached event data from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    self.event_cache = data.get('events', {})
                    self.last_sync_time = data.get('last_sync_time')
                    logger.info(f"✅ Loaded {len(self.event_cache)} cached events")
            else:
                logger.info("No event cache found - will build on first sync")
        except Exception as e:
            logger.warning(f"Failed to load event cache: {e}")
            self.event_cache = {}
    
    def _save_cache(self):
        """Save event cache to disk"""
        try:
            data = {
                'events': self.event_cache,
                'last_sync_time': get_central_time().isoformat(),
                'cache_version': '1.0'
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"✅ Saved {len(self.event_cache)} events to cache")
        except Exception as e:
            logger.error(f"Failed to save event cache: {e}")
    
    def _create_event_signature(self, event: Dict) -> str:
        """Create a unique signature for an event"""
        subject = event.get('subject', '').strip()
        start_time = event.get('start', {}).get('dateTime', '')
        end_time = event.get('end', {}).get('dateTime', '')
        location = event.get('location', {}).get('displayName', '')
        
        # Normalize the data
        subject = subject.lower().replace(' ', '')
        start_time = start_time.split('T')[0] if start_time else ''  # Just the date
        end_time = end_time.split('T')[0] if end_time else ''
        location = location.lower().replace(' ', '')
        
        signature = f"{subject}|{start_time}|{end_time}|{location}"
        return signature
    
    def detect_changes(self, current_events: List[Dict]) -> Dict:
        """
        Detect changes between cached events and current events
        
        Returns:
            Dict with 'added', 'updated', 'deleted', 'unchanged' lists
        """
        current_signatures = set()
        changes = {
            'added': [],
            'updated': [],
            'deleted': [],
            'unchanged': []
        }
        
        # Process current events
        for event in current_events:
            signature = self._create_event_signature(event)
            current_signatures.add(signature)
            
            if signature in self.event_cache:
                # Event exists - check if changed
                cached_event = self.event_cache[signature]
                if self._event_changed(event, cached_event):
                    changes['updated'].append(event)
                    logger.debug(f"📝 Event changed: {event.get('subject')}")
                else:
                    changes['unchanged'].append(event)
                    logger.debug(f"✅ Event unchanged: {event.get('subject')}")
            else:
                # New event
                changes['added'].append(event)
                logger.debug(f"➕ New event: {event.get('subject')}")
        
        # Find deleted events (in cache but not in current)
        for signature, cached_event in self.event_cache.items():
            if signature not in current_signatures:
                changes['deleted'].append(cached_event)
                logger.debug(f"🗑️ Deleted event: {cached_event.get('subject')}")
        
        logger.info(f"🔍 Change detection summary:")
        logger.info(f"  - {len(changes['added'])} new events")
        logger.info(f"  - {len(changes['updated'])} modified events")
        logger.info(f"  - {len(changes['deleted'])} deleted events")
        logger.info(f"  - {len(changes['unchanged'])} unchanged events")
        
        return changes
    
    def _event_changed(self, event1: Dict, event2: Dict) -> bool:
        """Compare two events to see if they're different"""
        # Compare key fields that matter for sync
        fields_to_compare = [
            'subject', 'body', 'start', 'end', 'location', 
            'categories', 'showAs', 'isCancelled'
        ]
        
        for field in fields_to_compare:
            val1 = event1.get(field)
            val2 = event2.get(field)
            
            if val1 != val2:
                logger.debug(f"Field '{field}' changed: {val1} != {val2}")
                return True
        
        return False
    
    def update_cache(self, events: List[Dict]):
        """Update the cache with current events"""
        new_cache = {}
        
        for event in events:
            signature = self._create_event_signature(event)
            new_cache[signature] = event
        
        self.event_cache = new_cache
        self.last_sync_time = get_central_time()
        self._save_cache()
        
        logger.info(f"✅ Updated cache with {len(new_cache)} events")
    
    def get_cache_stats(self) -> Dict:
        """Get statistics about the cache"""
        # Handle last_sync_time safely - it might be a string or datetime
        last_sync_time_iso = None
        if self.last_sync_time:
            if isinstance(self.last_sync_time, str):
                last_sync_time_iso = self.last_sync_time
            else:
                try:
                    last_sync_time_iso = self.last_sync_time.isoformat()
                except AttributeError:
                    last_sync_time_iso = str(self.last_sync_time)
        
        return {
            'cached_events': len(self.event_cache),
            'last_sync_time': last_sync_time_iso,
            'cache_file': self.cache_file,
            'cache_exists': os.path.exists(self.cache_file)
        }
    
    def clear_cache(self):
        """Clear the event cache"""
        self.event_cache = {}
        self.last_sync_time = None
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
                logger.info("✅ Cleared event cache")
        except Exception as e:
            logger.error(f"Failed to clear cache file: {e}")
    
    def is_cache_valid(self) -> bool:
        """Check if the cache is valid and recent"""
        if not self.last_sync_time:
            return False
        
        try:
            last_sync = datetime.fromisoformat(self.last_sync_time.replace('Z', '+00:00'))
            now = get_central_time()
            age = now - last_sync
            
            # Cache is valid if less than 24 hours old
            return age < timedelta(hours=24)
        except:
            return False 