# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Timezone utilities for converting between UTC and Central Time
"""
from datetime import datetime
import pytz
from typing import Optional


def get_central_time() -> datetime:
    """Get current time in Central timezone"""
    central = pytz.timezone('America/Chicago')
    return datetime.now(central)


def utc_to_central(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to Central Time"""
    if utc_dt is None:
        return None
    
    # If the datetime is naive (no timezone), assume it's UTC
    if utc_dt.tzinfo is None:
        utc_dt = pytz.UTC.localize(utc_dt)
    elif utc_dt.tzinfo != pytz.UTC:
        # Convert to UTC first if it has a different timezone
        utc_dt = utc_dt.astimezone(pytz.UTC)
    
    central = pytz.timezone('America/Chicago')
    return utc_dt.astimezone(central)


def central_to_utc(central_dt: datetime) -> datetime:
    """Convert Central Time datetime to UTC"""
    if central_dt is None:
        return None
    
    # If the datetime is naive, assume it's Central Time
    if central_dt.tzinfo is None:
        central = pytz.timezone('America/Chicago')
        central_dt = central.localize(central_dt)
    
    return central_dt.astimezone(pytz.UTC)


def format_central_time(dt: Optional[datetime], include_timezone: bool = True) -> str:
    """Format datetime in Central Time for display"""
    if dt is None:
        return "Never"
    
    # Convert to Central Time if needed
    if isinstance(dt, datetime):
        central_dt = utc_to_central(dt) if dt.tzinfo == pytz.UTC or dt.tzinfo is None else dt
        
        if include_timezone:
            return central_dt.strftime('%b %d, %Y at %I:%M %p CT')
        else:
            return central_dt.strftime('%b %d, %Y at %I:%M %p')
    
    return str(dt)


def iso_to_central_display(iso_string: str, include_timezone: bool = True) -> str:
    """Convert ISO string to Central Time display format"""
    if not iso_string:
        return "Never"
    
    try:
        # Parse ISO string
        if iso_string.endswith('Z'):
            dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(iso_string)
        
        return format_central_time(dt, include_timezone)
    except:
        return iso_string


def get_timezone_offset() -> str:
    """Get current Central Time offset from UTC"""
    central = pytz.timezone('America/Chicago')
    now = datetime.now(central)
    offset = now.strftime('%z')
    return f"UTC{offset[:3]}:{offset[3:]}"


def parse_graph_datetime(field: dict):
    """Convert Microsoft Graph {"dateTime": str, "timeZone": str} into an aware UTC datetime.

    Args:
        field: dict with keys 'dateTime' and 'timeZone'
    Returns:
        datetime in UTC or None if missing.
    """
    if not field or not field.get("dateTime"):
        return None

    from datetime import datetime
    import pytz

    dt_str = field.get("dateTime")
    tz_label = field.get("timeZone", "UTC") or "UTC"

    try:
        naive_dt = datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback – dateutil offers wider parsing but avoid extra dep; return None on failure
        return None

    # If dt already has tzinfo, just convert to UTC
    if naive_dt.tzinfo is not None:
        return naive_dt.astimezone(pytz.UTC)

    # Map Microsoft "Central Standard Time" et al. to pytz zone
    if "Central" in tz_label:
        tz = pytz.timezone("America/Chicago")
    else:
        try:
            tz = pytz.timezone(tz_label)
        except Exception:
            tz = pytz.UTC

    aware_dt = tz.localize(naive_dt)
    return aware_dt.astimezone(pytz.UTC)
