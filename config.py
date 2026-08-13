# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Environment-based configuration for St. Edward Calendar Sync
"""
import logging
import os
import secrets
from datetime import timedelta

logger = logging.getLogger(__name__)

# Environment Detection
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'production')
DEBUG = ENVIRONMENT == 'development'

def _clean_name(value):
    """
    Collapse every run of whitespace to one space and trim the ends.

    Calendar names are pasted into hosting-panel textareas, which can wrap or
    carry a stray newline. Outlook names never contain newlines or doubled
    spaces, so normalizing here makes the lookup immune to how the value was
    entered instead of failing to match a calendar that plainly exists.
    """
    return ' '.join((value or '').split())


# Shared Mailbox Configuration
SHARED_MAILBOX = (os.environ.get('SHARED_MAILBOX', "your_shared_mailbox@yourdomain.org") or '').strip()
SOURCE_CALENDAR = _clean_name(os.environ.get('SOURCE_CALENDAR', "Calendar"))
TARGET_CALENDAR = _clean_name(os.environ.get('TARGET_CALENDAR', "St. Edward Public Calendar"))

# Category the primary pair syncs (events tagged with it land on TARGET_CALENDAR)
SYNC_CATEGORY = _clean_name(os.environ.get('SYNC_CATEGORY', "Public"))

# Additional category -> calendar pairs, beyond the primary one above.
# Format: "Category=Calendar Name", multiple pairs separated by semicolons.
# Example: EXTRA_SYNC_PAIRS="SAS=Sundays At St. Edward"
# Leave unset and the service behaves exactly as it did before this setting existed.
EXTRA_SYNC_PAIRS = os.environ.get('EXTRA_SYNC_PAIRS', '')

# Target calendars that keep the real event description from the source event.
# Anything not listed here gets a body containing only the sync marker, which has
# always been the default: the public calendar is embedded on the website and its
# source events routinely carry gate codes and door-access times.
# Format: semicolon-separated calendar names, matched case-insensitively.
# Example: COPY_BODY_TARGETS="Sundays At St. Edward"
COPY_BODY_TARGETS = os.environ.get('COPY_BODY_TARGETS', '')


def get_copy_body_targets():
    """Return the normalized, lowercased target names that keep descriptions."""
    names = set()
    for chunk in COPY_BODY_TARGETS.split(';'):
        name = _clean_name(chunk)
        if not name:
            continue
        # Hard rail, not a preference. Descriptions on the public calendar is the
        # exact exposure the marker-only body exists to prevent, so a typo or a
        # well-meaning edit to this variable cannot switch it on.
        if name.lower() == TARGET_CALENDAR.lower():
            logger.warning(
                f"Ignoring COPY_BODY_TARGETS entry {name!r}: the public calendar "
                f"never carries source event descriptions"
            )
            continue
        names.add(name.lower())
    return names


def copies_body(target_calendar_name):
    """True if this target calendar should receive the source event's description."""
    return _clean_name(target_calendar_name).lower() in get_copy_body_targets()


def get_sync_pairs():
    """
    Return the list of {category, target} pairs to sync, primary pair first.

    Safety: a pair is dropped if it is malformed, duplicated, or points at the
    source calendar, so a typo in EXTRA_SYNC_PAIRS can never make the sync
    write to the master calendar.
    """
    pairs = []
    seen = set()
    used_targets = set()

    def add(category, target, raw=None):
        category = _clean_name(category)
        target = _clean_name(target)
        if not category or not target:
            logger.warning(f"Ignoring malformed sync pair: {raw!r}")
            return
        if target.lower() == SOURCE_CALENDAR.lower():
            logger.warning(
                f"Ignoring sync pair '{category}={target}': the target is the source calendar"
            )
            return
        # One target may belong to exactly one pair. Two categories writing to
        # the same calendar would make each pair treat the other's events as
        # orphans and delete them, churning the calendar on every cycle.
        if target.lower() in used_targets:
            logger.warning(
                f"Ignoring sync pair '{category}={target}': that calendar is already "
                f"the target of another pair"
            )
            return
        key = (category.lower(), target.lower())
        if key in seen:
            logger.warning(f"Ignoring duplicate sync pair '{category}={target}'")
            return
        seen.add(key)
        used_targets.add(target.lower())
        pairs.append({'category': category, 'target': target})

    add(SYNC_CATEGORY, TARGET_CALENDAR)

    for chunk in EXTRA_SYNC_PAIRS.split(';'):
        if not chunk.strip():
            continue
        if '=' not in chunk:
            # Silently dropping this used to leave a calendar simply not
            # syncing, with nothing anywhere to explain why.
            logger.warning(
                f"Ignoring sync pair {chunk.strip()!r}: expected the form 'Category=Calendar Name'"
            )
            continue
        category, target = chunk.split('=', 1)
        add(category, target, raw=chunk)

    return pairs

# Azure AD Configuration
CLIENT_ID = os.environ.get('CLIENT_ID', "your_client_id_here")
TENANT_ID = os.environ.get('TENANT_ID', "your_tenant_id_here")
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', 'your_client_secret_here')
REDIRECT_URI = os.environ.get('REDIRECT_URI', "https://your-app-domain.onrender.com/auth/callback")

# Application Settings
SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))
PORT = int(os.environ.get('PORT', 5000))

# Optional: comma-separated list of emails allowed to use the dashboard (e.g. rcarroll@stedward.org,eharnisch@stedward.org,ckloss@stedward.org).
# If set, only these users can sign in; if empty, any tenant user who can sign in is allowed.
ALLOWED_DASHBOARD_USERS = [
    e.strip().lower() for e in os.environ.get('ALLOWED_DASHBOARD_USERS', '').split(',') if e.strip()
]

# Rate Limiting
MAX_SYNC_REQUESTS_PER_HOUR = int(os.environ.get('MAX_SYNC_REQUESTS_PER_HOUR', 20))

# Sync Settings
MASTER_CALENDAR_PROTECTION = os.environ.get('MASTER_CALENDAR_PROTECTION', 'True').lower() == 'true'
DRY_RUN_MODE = os.environ.get('DRY_RUN_MODE', 'False').lower() == 'true'
SYNC_CUTOFF_DAYS = int(os.environ.get('SYNC_CUTOFF_DAYS', 180))  # 6 months back
SYNC_LOOKAHEAD_DAYS = int(os.environ.get('SYNC_LOOKAHEAD_DAYS', 365))  # 12 months ahead

# Occurrence Exception Settings
SYNC_OCCURRENCE_EXCEPTIONS = os.environ.get('SYNC_OCCURRENCE_EXCEPTIONS', 'True').lower() == 'true'
OCCURRENCE_SYNC_DAYS = int(os.environ.get('OCCURRENCE_SYNC_DAYS', 60))

# Validation Settings
IGNORE_VALIDATION_WARNINGS = ['no_duplicates', 'event_integrity']

# OAuth Scopes
GRAPH_SCOPES = [
    'Calendars.ReadWrite',
    'Calendars.ReadWrite.Shared', 
    'User.Read',
    'offline_access'
]

# Sync Intervals (in minutes)
SYNC_INTERVAL_MIN = int(os.environ.get('SYNC_INTERVAL_MIN', 23))
HEALTH_CHECK_INTERVAL = int(os.environ.get('HEALTH_CHECK_INTERVAL', 5))

# Circuit Breaker Settings
CIRCUIT_BREAKER_FAIL_MAX = int(os.environ.get('CIRCUIT_BREAKER_FAIL_MAX', 5))
CIRCUIT_BREAKER_RESET_TIMEOUT = int(os.environ.get('CIRCUIT_BREAKER_RESET_TIMEOUT', 60))

# Retry Settings
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 3))
BASE_DELAY = float(os.environ.get('BASE_DELAY', 1.0))

# Cache Settings
CACHE_TTL_HOURS = int(os.environ.get('CACHE_TTL_HOURS', 24))

# Logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
STRUCTURED_LOGGING = os.environ.get('STRUCTURED_LOGGING', 'True').lower() == 'true'

# Development Settings
if DEBUG:
    LOG_LEVEL = 'DEBUG'
    DRY_RUN_MODE = True
    SYNC_INTERVAL_MIN = 1  # Faster syncs for development
