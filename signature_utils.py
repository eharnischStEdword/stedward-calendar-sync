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
    location = event.get('location', {}).get('displayName', '')
    location_normalized = location.lower().replace(' ', '') if location else ''
    
    # Get normalized start time
    start_raw = event.get('start', {}).get('dateTime', '')
    start_normalized = normalize_datetime(start_raw)
    
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
        # Include seriesMasterId for occurrences to prevent duplicates
        series_master_id = event.get('seriesMasterId', '')
        if series_master_id:
            signature = f"occurrence:{subject}:{series_master_id}:{start_normalized}:{location_normalized}"
            return signature
        else:
            signature = f"occurrence:{subject}:{start_normalized}:{location_normalized}"
            return signature
    
    # For single events - include time to distinguish events on same day
    if 'T' in start_normalized:
        date_part = start_normalized.split('T')[0]
        time_part = start_normalized.split('T')[1] if 'T' in start_normalized else '00:00'
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
        # Remove timezone info and normalize to just date and time
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


def normalize_location(location: str) -> str:
    """
    Normalize location for consistent matching.
    
    CRITICAL: This method MUST be identical across all classes.
    
    Args:
        location: Location string
        
    Returns:
        Normalized location string
    """
    if not location:
        return ""
    return location.lower().replace(' ', '').replace('#', '')
