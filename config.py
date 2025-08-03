# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Environment-based configuration for St. Edward Calendar Sync
"""
import os
import secrets
from datetime import timedelta

# Environment Detection
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'production')
DEBUG = ENVIRONMENT == 'development'

# Shared Mailbox Configuration
SHARED_MAILBOX = os.environ.get('SHARED_MAILBOX', "calendar@stedward.org")
SOURCE_CALENDAR = os.environ.get('SOURCE_CALENDAR', "Calendar")
TARGET_CALENDAR = os.environ.get('TARGET_CALENDAR', "St. Edward Public Calendar")

# Azure AD Configuration
CLIENT_ID = os.environ.get('CLIENT_ID', "e139467d-fdeb-40bb-be62-718b007c8e0a")
TENANT_ID = os.environ.get('TENANT_ID', "8c0196b2-b7eb-470b-a715-ec1696d83eba")
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', '')
REDIRECT_URI = os.environ.get('REDIRECT_URI', "https://stedward-calendar-sync.onrender.com/auth/callback")

# Application Settings
SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))
PORT = int(os.environ.get('PORT', 5000))

# Rate Limiting
MAX_SYNC_REQUESTS_PER_HOUR = int(os.environ.get('MAX_SYNC_REQUESTS_PER_HOUR', 20))

# Sync Settings
MASTER_CALENDAR_PROTECTION = os.environ.get('MASTER_CALENDAR_PROTECTION', 'True').lower() == 'true'
DRY_RUN_MODE = os.environ.get('DRY_RUN_MODE', 'False').lower() == 'true'
SYNC_CUTOFF_DAYS = int(os.environ.get('SYNC_CUTOFF_DAYS', 1825))  # 5 years

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
