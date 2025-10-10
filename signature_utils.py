# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
CRITICAL: Shared Signature Generation Utilities

This module contains the signature generation logic that MUST be identical
across all classes. Any changes to these methods must be propagated to
ALL classes that use them.

Signature mismatches cause thousands of duplicate events to be created.
"""

import json
import hashlib
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def generate_event_signature(event: Dict) -> str:
    """
    Generate unique signature for event matching.
    
    CRITICAL: This method MUST be identical across all classes.
    Any changes must be propagated to ChangeTracker, SyncEngine, and SyncValidator.
    
    Args:
        event: Event dictionary from Microsoft Graph API
        
    Returns:
        Unique signature string for event matching
    """
    subject = normalize_subject(event.get('subject', ''))
    event_type = event.get('type', 'singleInstance')
    
    # Handle location - can be dict with displayName or string
    location_raw = event.get('location', {})
    if isinstance(location_raw, dict):
        location = location_raw.get('displayName', '')
    else:
        location = location_raw or ''
    location_normalized = normalize_location(location)
    
    # Get normalized start time - handle both dict and string formats
    start_raw = event.get('start', {})
    if isinstance(start_raw, dict):
        start_datetime = start_raw.get('dateTime', '') or start_raw.get('date', '')
    else:
        start_datetime = start_raw or ''
    
    # CRITICAL: All-day events use date-only for signature stability
    is_all_day = event.get('isAllDay', False)
    if is_all_day:
        # Extract date portion only, no time
        if 'T' in start_datetime:
            start_normalized = start_datetime.split('T')[0]
        else:
            start_normalized = start_datetime
    else:
        start_normalized = normalize_datetime(start_datetime)
    
    # For recurring events
    if event_type == 'seriesMaster':
        recurrence = event.get('recurrence', {})
        pattern = recurrence.get('pattern', {})
        
        # Create a stable hash of the recurrence pattern
        pattern_data = {
            'type': pattern.get('type', 'unknown'),
            'interval': pattern.get('interval', 1),
            'daysOfWeek': sorted(pattern.get('daysOfWeek', [])),
            'dayOfMonth': pattern.get('dayOfMonth'),
            'index': pattern.get('index')
        }
        
        # Create hash of pattern for consistency
        pattern_str = json.dumps(pattern_data, sort_keys=True)
        pattern_hash = hashlib.md5(pattern_str.encode()).hexdigest()[:8]
        
        signature = f"recurring:{subject}:{pattern_hash}:{start_normalized}:{location_normalized}"
        
        return signature
    
    elif event_type == 'occurrence':
        # CRITICAL FIX: Treat occurrences as single events for signature matching
        # This ensures orphaned occurrences match previously synced events
        # Use the same format as single events (without seriesMasterId)
        if is_all_day:
            # All-day: date only with ALLDAY marker
            signature = f"single:{subject}:{start_normalized}:ALLDAY:{location_normalized}"
        elif 'T' in start_normalized:
            date_part = start_normalized.split('T')[0]
            time_part = start_normalized.split('T')[1]
            signature = f"single:{subject}:{date_part}:{time_part}:{location_normalized}"
        else:
            signature = f"single:{subject}:{start_normalized}:{location_normalized}"
        
        return signature
    
    # For single events
    if is_all_day:
        # All-day: date only with ALLDAY marker
        signature = f"single:{subject}:{start_normalized}:ALLDAY:{location_normalized}"
    else:
        # Timed: include time component
        if 'T' in start_normalized:
            date_part = start_normalized.split('T')[0]
            time_part = start_normalized.split('T')[1]
            signature = f"single:{subject}:{date_part}:{time_part}:{location_normalized}"
        else:
            signature = f"single:{subject}:{start_normalized}:{location_normalized}"
    
    return signature


def normalize_subject(subject: str) -> str:
    """
    Normalize subject for consistent matching.
    
    CRITICAL: This method MUST be identical across all classes.
    
    Args:
        subject: Event subject string
        
    Returns:
        Normalized subject string
    """
    if not subject:
        return ""
    # More aggressive normalization to handle Microsoft Graph variations
    normalized = ' '.join(subject.strip().lower().split())
    # Remove common punctuation that might vary
    normalized = normalized.replace('.', '').replace(',', '').replace(':', '').replace(';', '')
    return normalized


def normalize_datetime(dt_str: str) -> str:
    """
    Convert datetime to consistent string format.
    
    CRITICAL: This method MUST be identical across all classes.
    
    Args:
        dt_str: DateTime string from Microsoft Graph API
        
    Returns:
        Normalized datetime string
    """
    if not dt_str:
        return ""
    try:
        # Handle different datetime formats from Microsoft Graph API
        # Format 1: '2025-07-28T15:30:00.0000000' (with milliseconds)
        # Format 2: '2025-07-28T15:30:00Z' (with Z suffix)
        # Format 3: '2025-07-28T15:30:00' (basic format)
        
        # Remove milliseconds if present
        if '.' in dt_str:
            dt_str = dt_str.split('.')[0]
        
        # Remove timezone indicators
        clean_dt = dt_str.replace('Z', '').replace('+00:00', '')
        if '+' in clean_dt:
            clean_dt = clean_dt.split('+')[0]
        if '-' in clean_dt and clean_dt.count('-') > 2:  # More than just date separators
            clean_dt = clean_dt.rsplit('-', 1)[0]
        
        # Ensure consistent format
        if 'T' in clean_dt:
            date_part, time_part = clean_dt.split('T', 1)
            # Normalize time to HH:MM format for consistency
            time_part = time_part[:5]  # Take only HH:MM
            return f"{date_part}T{time_part}"
        return clean_dt
    except Exception as e:
        logger.warning(f"Failed to normalize datetime '{dt_str}': {e}")
        return dt_str


def normalize_location(location) -> str:
    """
    Normalize location for consistent matching.
    
    CRITICAL: This method MUST be identical across all classes.
    
    Args:
        location: Location string or dict from Microsoft Graph API
        
    Returns:
        Normalized location string
    """
    # Handle dict format from Microsoft Graph API
    if isinstance(location, dict):
        location = location.get('displayName', '')
    
    # Handle empty/None/empty string
    if not location:
        return ""  # Consistent empty value for all empty cases
    
    # Normalize
    return location.lower().replace(' ', '').replace('#', '')
