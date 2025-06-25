"""
Configuration settings for St. Edward Calendar Sync
"""
import os

# Shared Mailbox Configuration
SHARED_MAILBOX = "calendar@stedward.org"
SOURCE_CALENDAR = "Calendar"
TARGET_CALENDAR = "St. Edward Public Calendar"  # Fixed name to match actual calendar

# Azure AD Configuration
CLIENT_ID = "e139467d-fdeb-40bb-be62-718b007c8e0a"
TENANT_ID = "8ccf96b2-b7eb-470b-a715-ec1696d83ebd"
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', '')
REDIRECT_URI = os.environ.get('REDIRECT_URI', "https://stedward-calendar-sync.onrender.com/auth/callback")

# Application Settings
SECRET_KEY = os.environ.get('SECRET_KEY', None)  # Will generate if None
PORT = int(os.environ.get('PORT', 5000))

# Rate Limiting
MAX_SYNC_REQUESTS_PER_HOUR = 20

# Sync Settings
MASTER_CALENDAR_PROTECTION = True  # Never allow operations on source calendar
DRY_RUN_MODE = False  # Set to True to test without making changes
SYNC_CUTOFF_DAYS = 90  # Only sync events from last N days

# Validation Settings - Ignore warnings for events with same names but different times
IGNORE_VALIDATION_WARNINGS = ['no_duplicates', 'event_integrity']

# OAuth Scopes
GRAPH_SCOPES = [
    'https://graph.microsoft.com/Calendars.ReadWrite',
    'https://graph.Microsoft.com/User.Read',
    'https://graph.microsoft.com/Calendars.ReadWrite.Shared',
    'offline_access'
]
