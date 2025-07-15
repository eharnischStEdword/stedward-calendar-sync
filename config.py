# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Configuration settings for St. Edward Calendar Sync
"""
import os
import secrets

# Shared Mailbox Configuration
SHARED_MAILBOX = "calendar@stedward.org"
SOURCE_CALENDAR = "Calendar"
TARGET_CALENDAR = "St. Edward Public Calendar"  # Points to your public calendar

# Azure AD Configuration
CLIENT_ID = "e139467d-fdeb-40bb-be62-718b007c8e0a"
TENANT_ID = "8ccf96b2-b7eb-470b-a715-ec1696d83ebd"
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', '')
REDIRECT_URI = os.environ.get('REDIRECT_URI', "https://stedward-calendar-sync.onrender.com/auth/callback")

# Application Settings
SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))  # Flask session key
PORT = int(os.environ.get('PORT', 5000))

# Rate Limiting
MAX_SYNC_REQUESTS_PER_HOUR = 20

# Sync Settings
MASTER_CALENDAR_PROTECTION = True  # Never allow operations on source calendar
DRY_RUN_MODE = False  # Set to True to test without making changes
SYNC_CUTOFF_DAYS = 90  # Only sync events from last N days

# Occurrence Exception Settings
SYNC_OCCURRENCE_EXCEPTIONS = False  # Disable occurrence sync to prevent duplicates
OCCURRENCE_SYNC_DAYS = 60  # How many days ahead to sync instances

# Validation Settings - No longer needed with improved duplicate detection
# IGNORE_VALIDATION_WARNINGS = ['no_duplicates', 'event_integrity']

# OAuth Scopes
GRAPH_SCOPES = [
    'Calendars.ReadWrite',
    'Calendars.ReadWrite.Shared', 
    'User.Read',
    'offline_access'
]
