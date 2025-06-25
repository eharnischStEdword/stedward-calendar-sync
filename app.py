#!/usr/bin/env python3
"""
Calendar Sync Web Application for Render.com
BULLETPROOF VERSION with master calendar protection and enhanced debugging
Now with automatic version management!
"""

from flask import Flask, render_template, jsonify, request, redirect, session, url_for
import asyncio
import os
import json
from datetime import datetime, timedelta
import pytz
import threading
import time
import requests
import urllib.parse
import schedule
from azure.identity import AuthorizationCodeCredential, ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.event import Event
import atexit
import logging
from functools import wraps
import secrets
import subprocess

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== VERSION MANAGEMENT ====================

def get_version_info():
    """Get version info 100% automatically - no manual tracking needed"""
    now = datetime.utcnow()
    version_info = {
        "version": now.strftime("%Y.%m.%d"),  # Date-based version like 2025.06.24
        "build_number": 1,
        "commit_hash": "unknown",
        "build_date": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "environment": "unknown"
    }
    
    try:
        # Try to get git commit hash (first 7 characters)
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version_info["commit_hash"] = result.stdout.strip()
        
        # Try to get commit count (for build number)
        result = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            count = int(result.stdout.strip())
            version_info["build_number"] = count
            
            # 100% AUTOMATIC: Use date + build for version
            # Examples: 2025.06.24.47, 2025.06.25.48, etc.
            version_info["version"] = f"{now.strftime('%Y.%m.%d')}.{count}"
        
        # Try to get branch name
        result = subprocess.run(['git', 'branch', '--show-current'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            branch = result.stdout.strip()
            version_info["branch"] = branch
            version_info["environment"] = "production" if branch == "main" else "development"
    
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        # Git not available - use date-based fallback with timestamp
        timestamp = int(now.timestamp())
        version_info["version"] = f"{now.strftime('%Y.%m.%d')}.{timestamp}"
        version_info["build_number"] = timestamp
        logger.info("Git not available, using date-based version")
    
    # Add deployment environment info
    if "RENDER" in os.environ:
        version_info["deployment_platform"] = "Render.com"
        version_info["service_name"] = os.environ.get("RENDER_SERVICE_NAME", "calendar-sync")
        version_info["environment"] = "production"
    else:
        version_info["deployment_platform"] = "Local Development"
        version_info["environment"] = "local"
    
    # Add startup time
    version_info["startup_time"] = datetime.utcnow().isoformat() + "Z"
    
    # Create display strings
    version_info["version_string"] = f"v{version_info['version']}"
    version_info["build_string"] = f"Build #{version_info['build_number']}"
    version_info["full_version"] = f"Calendar Sync v{version_info['version']} (Build #{version_info['build_number']})"
    
    if version_info["commit_hash"] != "unknown":
        version_info["full_version"] += f" [{version_info['commit_hash']}]"
    
    return version_info

# Initialize version info on startup
APP_VERSION_INFO = get_version_info()
logger.info(f"🚀 Starting {APP_VERSION_INFO['full_version']}")
logger.info(f"📦 Platform: {APP_VERSION_INFO['deployment_platform']}")
logger.info(f"🌍 Environment: {APP_VERSION_INFO['environment']}")

# ==================== FLASK APP SETUP ====================

app = Flask(__name__)
# Generate a secure secret key if not provided
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Configuration
SHARED_MAILBOX = "calendar@stedward.org"
SOURCE_CALENDAR = "Calendar"
TARGET_CALENDAR = "St. Edward Public Calendar"

# Azure AD App Registration
CLIENT_ID = "e139467d-fdeb-40bb-be62-718b007c8e0a"
TENANT_ID = "8ccf96b2-b7eb-470b-a715-ec1696d83ebd"
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', '')
REDIRECT_URI = os.environ.get('REDIRECT_URI', "https://stedward-calendar-sync.onrender.com/auth/callback")

# Rate limiting
MAX_SYNC_REQUESTS_PER_HOUR = 20
sync_request_times = []

# Global variables for tracking sync status (thread-safe)
import threading
sync_lock = threading.Lock()
last_sync_time = None
last_sync_result = {"success": False, "message": "Not synced yet"}
sync_in_progress = False

# Token management with thread safety
token_lock = threading.Lock()
access_token = None
refresh_token = None
token_expires_at = None

# Scheduler control
scheduler_lock = threading.Lock()
scheduler_running = False
scheduler_thread = None

# BULLETPROOF SAFEGUARDS
MASTER_CALENDAR_PROTECTION = True  # Never allow operations on source calendar
DRY_RUN_MODE = False  # Set to True to test without making changes

def rate_limit_check():
    """Check if we're within rate limits"""
    global sync_request_times
    current_time = datetime.now()
    # Remove requests older than 1 hour
    sync_request_times = [t for t in sync_request_times if current_time - t < timedelta(hours=1)]
    return len(sync_request_times) < MAX_SYNC_REQUESTS_PER_HOUR

def make_json_serializable(obj):
    """Convert objects to JSON-serializable format"""
    if obj is None:
        return None
    elif isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    elif hasattr(obj, 'isoformat'):  # datetime objects
        return obj.isoformat()
    else:
        return str(obj)

def save_tokens_to_env(access_token, refresh_token, expires_at):
    """Save tokens to environment variables (for next deployment)"""
    logger.info("SAVE THESE TO YOUR RENDER ENVIRONMENT VARIABLES:")
    logger.info(f"REFRESH_TOKEN={refresh_token}")
    logger.info(f"TOKEN_EXPIRES_AT={expires_at.isoformat()}")

def load_tokens_from_env():
    """Load tokens from environment variables on startup"""
    global access_token, refresh_token, token_expires_at
    
    stored_refresh = os.environ.get('REFRESH_TOKEN')
    stored_expires = os.environ.get('TOKEN_EXPIRES_AT')
    
    if stored_refresh and stored_expires:
        try:
            expires_datetime = datetime.fromisoformat(stored_expires)
            
            # If token is still valid for more than 1 hour, try to use it
            if expires_datetime > datetime.utcnow() + timedelta(hours=1):
                with token_lock:
                    refresh_token = stored_refresh
                    token_expires_at = expires_datetime
                
                # Try to refresh the access token
                if refresh_access_token():
                    logger.info("Successfully restored authentication from environment variables")
                    start_scheduler()  # Start scheduler after successful restore
                    return True
                else:
                    logger.warning("Failed to refresh token from stored refresh token")
            else:
                logger.info("Stored token is expired, need fresh authentication")
        except Exception as e:
            logger.error(f"Error loading tokens from environment: {e}")
    
    return False

def get_auth_url():
    """Generate Microsoft OAuth URL with offline_access for refresh tokens"""
    params = {
        'client_id': CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': REDIRECT_URI,
        'scope': 'https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/User.Read https://graph.microsoft.com/Calendars.ReadWrite.Shared offline_access',
        'response_mode': 'query',
        'state': secrets.token_urlsafe(16)  # Add state for CSRF protection
    }
    
    auth_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
    session['oauth_state'] = params['state']  # Store state for verification
    return auth_url

def exchange_code_for_token(auth_code):
    """Exchange authorization code for access token AND refresh token"""
    global access_token, refresh_token, token_expires_at
    
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': auth_code,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code',
        'scope': 'https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/User.Read https://graph.microsoft.com/Calendars.ReadWrite.Shared offline_access'
    }
    
    try:
        response = requests.post(token_url, data=data, timeout=30)
        if response.status_code == 200:
            tokens = response.json()
            
            with token_lock:
                access_token = tokens.get('access_token')
                refresh_token = tokens.get('refresh_token')
                
                # Calculate when token expires (usually 1 hour)
                expires_in = tokens.get('expires_in', 3600)
                token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)  # Refresh 5 minutes early
            
            # Save tokens for persistence
            save_tokens_to_env(access_token, refresh_token, token_expires_at)
            
            logger.info(f"Successfully obtained tokens. Access token expires at: {token_expires_at}")
            
            # Start the scheduler when we get authenticated
            start_scheduler()
            
            return True
        else:
            logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Token exchange exception: {str(e)}")
        return False

def refresh_access_token():
    """Automatically refresh the access token using refresh token"""
    global access_token, refresh_token, token_expires_at
    
    with token_lock:
        if not refresh_token:
            logger.warning("No refresh token available - need to re-authenticate")
            return False
    
    logger.info("Refreshing access token...")
    
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
        'scope': 'https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/User.Read https://graph.microsoft.com/Calendars.ReadWrite.Shared offline_access'
    }
    
    try:
        response = requests.post(token_url, data=data, timeout=30)
        if response.status_code == 200:
            tokens = response.json()
            
            with token_lock:
                access_token = tokens.get('access_token')
                
                # Update refresh token if provided (sometimes it changes)
                new_refresh_token = tokens.get('refresh_token')
                if new_refresh_token:
                    refresh_token = new_refresh_token
                
                # Calculate new expiration time
                expires_in = tokens.get('expires_in', 3600)
                token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
            
            logger.info(f"Token refreshed successfully. New expiration: {token_expires_at}")
            return True
        else:
            logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
            # Clear tokens so user needs to re-authenticate
            with token_lock:
                access_token = None
                refresh_token = None
                token_expires_at = None
            return False
    except Exception as e:
        logger.error(f"Token refresh exception: {str(e)}")
        return False

def ensure_valid_token():
    """Ensure we have a valid access token, refresh if needed"""
    with token_lock:
        if not access_token:
            logger.warning("No access token available")
            return False
        
        # Check if token is about to expire (within 5 minutes)
        if token_expires_at and datetime.utcnow() >= token_expires_at:
            logger.info("Token is expiring soon, attempting refresh...")
            return refresh_access_token()
    
    return True

def create_graph_client():
    """Create Graph client with automatic token refresh"""
    if not ensure_valid_token():
        return None
    
    # Simple bearer token authentication
    class BearerTokenCredential:
        def __init__(self, token):
            self.token = token
        
        def get_token(self, *scopes, **kwargs):
            from azure.core.credentials import AccessToken
            return AccessToken(self.token, int(time.time()) + 3600)
    
    with token_lock:
        credential = BearerTokenCredential(access_token)
    
    return GraphServiceClient(credentials=credential)

def requires_auth(f):
    """Decorator to check authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated_function

def normalize_subject(subject):
    """Normalize event subject for consistent matching"""
    if not subject:
        return ""
    # Strip whitespace, replace multiple spaces with single space, convert to lowercase
    return ' '.join(subject.strip().lower().split())

def normalize_datetime(dt_str):
    """Normalize datetime string for consistent matching"""
    if not dt_str:
        return ""
    try:
        # Parse and reformat to ensure consistency
        if 'T' in dt_str:
            date_part, time_part = dt_str.split('T', 1)
            # Remove timezone info for comparison
            time_part = time_part.split('+')[0].split('Z')[0].split('-')[0]
            return f"{date_part}T{time_part}"
        return dt_str
    except:
        return dt_str

def create_event_signature(event):
    """Bulletproof signature using Microsoft's unique IDs"""
    subject = normalize_subject(event.get('subject', ''))
    event_type = event.get('type', 'unknown')
    
    if event_type == 'seriesMaster':
        # For recurring events, use the series master ID
        # This is GUARANTEED unique by Microsoft
        series_id = event.get('id', '')
        
        # Handle the edge case where ID might be missing
        if not series_id:
            # Fallback: use subject + creation time if available
            created = event.get('createdDateTime', 'unknown')
            return f"recurring:{subject}:{created}"
        
        # Use last 12 chars of ID (enough for uniqueness, not too long)
        short_id = series_id[-12:] if len(series_id) > 12 else series_id
        return f"recurring:{subject}:{short_id}"
    
    # For single events, keep the existing logic (it works fine)
    start = normalize_datetime(event.get('start', {}).get('dateTime', ''))
    try:
        if 'T' in start:
            date_part = start.split('T')[0]
            time_part = start.split('T')[1][:5]  # Just HH:MM
            return f"single:{subject}:{date_part}:{time_part}"
    except:
        pass
    
    return f"single:{subject}:{start}"

def sync_calendars():
    """BULLETPROOF sync function with master calendar protection"""
    global last_sync_time, last_sync_result, sync_in_progress, sync_request_times
    
    # Check rate limiting
    if not rate_limit_check():
        logger.warning("Rate limit exceeded for sync requests")
        return {"error": "Rate limit exceeded. Please wait before syncing again."}
    
    with sync_lock:
        if sync_in_progress:
            return {"error": "Sync already in progress"}
        sync_in_progress = True
    
    # Record this sync request
    sync_request_times.append(datetime.now())
    
    logger.info("🚀 Starting BULLETPROOF calendar sync with master calendar protection")
    
    try:
        # Ensure token is valid before sync
        if not ensure_valid_token():
            return {"error": "Authentication failed - token expired and refresh failed"}
        
        with token_lock:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
        
        # Get calendars with EXTRA VERIFICATION
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers, timeout=30)
        
        if calendars_response.status_code == 401:
            # Try refreshing token once more
            if refresh_access_token():
                with token_lock:
                    headers['Authorization'] = f'Bearer {access_token}'
                calendars_response = requests.get(calendars_url, headers=headers, timeout=30)
            else:
                return {"error": "Authentication failed - please sign in again"}
        
        if calendars_response.status_code != 200:
            logger.error(f"Failed to get calendars: {calendars_response.status_code}")
            return {"error": f"Failed to get calendars: {calendars_response.status_code}"}
        
        calendars_data = calendars_response.json()
        source_calendar_id = None
        target_calendar_id = None
        
        for calendar in calendars_data.get('value', []):
            calendar_name = calendar.get('name', '')
            if calendar_name == SOURCE_CALENDAR:
                source_calendar_id = calendar.get('id')
                logger.info(f"✅ Found SOURCE calendar: {calendar_name} (ID: {source_calendar_id})")
            elif calendar_name == TARGET_CALENDAR:
                target_calendar_id = calendar.get('id')
                logger.info(f"✅ Found TARGET calendar: {calendar_name} (ID: {target_calendar_id})")
        
        if not source_calendar_id or not target_calendar_id:
            logger.error("Required calendars not found")
            return {"error": "Required calendars not found"}
        
        # SAFEGUARD: Verify we have the right calendar IDs
        if source_calendar_id == target_calendar_id:
            logger.error("🚨 CRITICAL ERROR: Source and target calendar IDs are the same!")
            return {"error": "SAFETY ABORT: Source and target calendars are identical"}
        
        logger.info(f"🔍 Source calendar ID: {source_calendar_id}")
        logger.info(f"🎯 Target calendar ID: {target_calendar_id}")
        
        # Get ALL source events (READ ONLY - never modify source!)
        source_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        source_params = {
            '$top': 500,
            '$select': 'id,subject,start,end,categories,location,isAllDay,showAs,type,recurrence,seriesMasterId,body,createdDateTime'
        }
        
        logger.info(f"📖 Reading from SOURCE calendar (READ ONLY): {SOURCE_CALENDAR}")
        source_response = requests.get(source_events_url, headers=headers, params=source_params, timeout=30)
        if source_response.status_code != 200:
            logger.error(f"Failed to get source events: {source_response.status_code}")
            return {"error": f"Failed to get source events: {source_response.status_code}"}
        
        source_events = source_response.json().get('value', [])
        logger.info(f"📚 Retrieved {len(source_events)} total events from SOURCE calendar")
        
        # Get ALL target events
        target_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        target_params = {
            '$top': 500,
            '$select': 'id,subject,start,end,categories,location,isAllDay,showAs,type,recurrence,seriesMasterId,body,createdDateTime'
        }
        
        logger.info(f"📖 Reading from TARGET calendar: {TARGET_CALENDAR}")
        target_response = requests.get(target_events_url, headers=headers, params=target_params, timeout=30)
        if target_response.status_code != 200:
            logger.error(f"Failed to get target events: {target_response.status_code}")
            return {"error": f"Failed to get target events: {target_response.status_code}"}
        
        target_events = target_response.json().get('value', [])
        logger.info(f"📚 Retrieved {len(target_events)} total events from TARGET calendar")
        
        # Build a map of target events by signature
        target_map = {}
        for event in target_events:
            signature = create_event_signature(event)
            if signature in target_map:
                logger.warning(f"⚠️ Duplicate signature in target: {signature}")
                # Keep the newer one
                existing_created = target_map[signature].get('createdDateTime', '')
                new_created = event.get('createdDateTime', '')
                if new_created > existing_created:
                    target_map[signature] = event
            else:
                target_map[signature] = event
        
        logger.info(f"🗂️ Built target map with {len(target_map)} unique events")
        
        # Process public events from source (NEVER MODIFY SOURCE!)
        public_events = []
        skipped_events = {
            'non_public': 0,
            'tentative': 0,
            'recurring_instances': 0,
            'past_events': 0
        }
        
        cutoff_date = datetime.now() - timedelta(days=90)  # Only sync recent/future events
        
        for event in source_events:
            categories = event.get('categories', [])
            subject = event.get('subject', '')
            
            # Skip if not public
            if 'Public' not in categories:
                skipped_events['non_public'] += 1
                continue
            
            # Skip tentative events
            show_as = event.get('showAs', 'busy')
            if show_as != 'busy':
                skipped_events['tentative'] += 1
                logger.debug(f"⏭️ Skipping tentative event: {subject}")
                continue
            
            # Skip recurring instances (only sync masters)
            event_type = event.get('type', 'unknown')
            if event_type == 'occurrence':
                skipped_events['recurring_instances'] += 1
                logger.debug(f"⏭️ Skipping recurring instance: {subject}")
                continue
            
            # Skip very old events
            try:
                event_start = event.get('start', {}).get('dateTime', '')
                if event_start:
                    event_date = datetime.fromisoformat(event_start.replace('Z', ''))
                    if event_date < cutoff_date:
                        skipped_events['past_events'] += 1
                        logger.debug(f"⏭️ Skipping old event: {subject}")
                        continue
            except:
                pass  # If we can't parse date, include the event
            
            # This is a valid public event to sync
            public_events.append(event)
        
        logger.info(f"✅ Found {len(public_events)} public events to sync")
        logger.info(f"⏭️ Skipped: {skipped_events}")
        
        # Determine what needs to be added, updated, or deleted IN TARGET ONLY
        to_add = []
        to_update = []
        to_delete = []
        
        # Check each public event from source
        for event in public_events:
            signature = create_event_signature(event)
            logger.debug(f"🔍 Checking event with signature: {signature}")
            
            if signature in target_map:
                # Event exists in target - check if it needs updating
                target_event = target_map[signature]
                
                # Compare key fields to see if update is needed
                needs_update = False
                
                # Check basic fields
                source_subject = event.get('subject', '')
                target_subject = target_event.get('subject', '')
                if source_subject != target_subject:
                    needs_update = True
                    logger.debug(f"📝 Subject changed: '{target_subject}' -> '{source_subject}'")
                
                if event.get('start') != target_event.get('start'):
                    needs_update = True
                    logger.debug(f"🕐 Start time changed")
                
                if event.get('end') != target_event.get('end'):
                    needs_update = True
                    logger.debug(f"🕐 End time changed")
                
                if event.get('isAllDay') != target_event.get('isAllDay'):
                    needs_update = True
                    logger.debug(f"📅 All-day status changed")
                
                if event.get('recurrence') != target_event.get('recurrence'):
                    needs_update = True
                    logger.debug(f"🔄 Recurrence changed")
                
                if needs_update:
                    to_update.append((event, target_event))
                    logger.debug(f"✏️ Event needs update: {source_subject}")
                
                # Mark this signature as processed
                del target_map[signature]
            else:
                # Event doesn't exist in target - add it
                to_add.append(event)
                logger.debug(f"➕ Event needs to be added: {event.get('subject')}")
        
        # Any remaining events in target_map should be deleted (they're no longer in source as public)
        for signature, target_event in target_map.items():
            to_delete.append(target_event)
            logger.debug(f"🗑️ Event needs to be deleted: {target_event.get('subject')}")
        
        logger.info(f"📋 SYNC PLAN: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
        
        if DRY_RUN_MODE:
            logger.info("🧪 DRY RUN MODE - No changes will be made")
            return {
                'success': True,
                'message': f'DRY RUN: Would add {len(to_add)}, update {len(to_update)}, delete {len(to_delete)}',
                'dry_run': True,
                'changes': 0,
                'added': len(to_add),
                'updated': len(to_update),
                'deleted': len(to_delete)
            }
        
        # Execute sync operations ON TARGET CALENDAR ONLY
        successful_operations = 0
        failed_operations = 0
        total_operations = len(to_add) + len(to_update) + len(to_delete)
        
        # SAFEGUARD: Double-check we're only operating on target calendar
        target_base_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        
        # Add new events to TARGET ONLY
        for event in to_add:
            try:
                # Extract location information
                source_location = event.get('location', {})
                location_text = ""
                if source_location:
                    if isinstance(source_location, dict):
                        location_text = source_location.get('displayName', '')
                    else:
                        location_text = str(source_location)
                
                # Create body content with location info
                body_content = ""
                if location_text:
                    body_content = f"<p><strong>Location:</strong> {location_text}</p>"
                
                create_url = target_base_url
                create_data = {
                    'subject': event.get('subject'),
                    'start': event.get('start'),
                    'end': event.get('end'),
                    'categories': event.get('categories'),
                    'body': {'contentType': 'html', 'content': body_content},
                    'location': {},  # Clear location for privacy
                    'isAllDay': event.get('isAllDay', False),
                    'showAs': 'busy',  # Always set to busy for public calendar
                    'isReminderOn': False  # No reminders on public calendar
                }
                
                # Add recurrence for recurring events
                if event.get('type') == 'seriesMaster' and event.get('recurrence'):
                    create_data['recurrence'] = event.get('recurrence')
                
                create_response = requests.post(create_url, headers=headers, json=create_data, timeout=30)
                if create_response.status_code in [200, 201]:
                    successful_operations += 1
                    logger.info(f"✅ Added to TARGET: {event.get('subject')}")
                else:
                    failed_operations += 1
                    logger.error(f"❌ Failed to add to TARGET {event.get('subject')}: {create_response.status_code}")
            except Exception as e:
                failed_operations += 1
                logger.error(f"❌ Error adding to TARGET {event.get('subject')}: {str(e)}")
        
        # Update existing events in TARGET ONLY
        for source_event, target_event in to_update:
            try:
                # Extract location information
                source_location = source_event.get('location', {})
                location_text = ""
                if source_location:
                    if isinstance(source_location, dict):
                        location_text = source_location.get('displayName', '')
                    else:
                        location_text = str(source_location)
                
                # Create body content with location info
                body_content = ""
                if location_text:
                    body_content = f"<p><strong>Location:</strong> {location_text}</p>"
                
                update_url = f"{target_base_url}/{target_event.get('id')}"
                update_data = {
                    'subject': source_event.get('subject'),
                    'start': source_event.get('start'),
                    'end': source_event.get('end'),
                    'categories': source_event.get('categories'),
                    'body': {'contentType': 'html', 'content': body_content},
                    'location': {},  # Clear location for privacy
                    'isAllDay': source_event.get('isAllDay', False),
                    'showAs': 'busy',
                    'isReminderOn': False
                }
                
                # Update recurrence for recurring events
                if source_event.get('type') == 'seriesMaster' and source_event.get('recurrence'):
                    update_data['recurrence'] = source_event.get('recurrence')
                
                update_response = requests.patch(update_url, headers=headers, json=update_data, timeout=30)
                if update_response.status_code == 200:
                    successful_operations += 1
                    logger.info(f"✅ Updated in TARGET: {source_event.get('subject')}")
                else:
                    failed_operations += 1
                    logger.error(f"❌ Failed to update in TARGET {source_event.get('subject')}: {update_response.status_code}")
            except Exception as e:
                failed_operations += 1
                logger.error(f"❌ Error updating in TARGET {source_event.get('subject')}: {str(e)}")
        
        # Delete removed events from TARGET ONLY
        for event in to_delete:
            try:
                delete_url = f"{target_base_url}/{event.get('id')}"
                # Add header to suppress notifications
                delete_headers = {
                    **headers,
                    'Prefer': 'outlook.timezone="UTC", outlook.send-notifications="false"'
                }
                delete_response = requests.delete(delete_url, headers=delete_headers, timeout=30)
                if delete_response.status_code in [200, 204]:
                    successful_operations += 1
                    logger.info(f"✅ Deleted from TARGET: {event.get('subject')}")
                else:
                    failed_operations += 1
                    logger.error(f"❌ Failed to delete from TARGET {event.get('subject')}: {delete_response.status_code}")
            except Exception as e:
                failed_operations += 1
                logger.error(f"❌ Error deleting from TARGET {event.get('subject')}: {str(e)}")
        
        with sync_lock:
            last_sync_time = datetime.now(pytz.UTC)
            result = {
                'success': True,
                'message': f'BULLETPROOF sync complete: {successful_operations}/{total_operations} operations successful',
                'changes': successful_operations,
                'successful_operations': successful_operations,
                'failed_operations': failed_operations,
                'added': len(to_add),
                'updated': len(to_update),
                'deleted': len(to_delete),
                'skipped': skipped_events,
                'safeguards_active': MASTER_CALENDAR_PROTECTION,
                'target_calendar_id': target_calendar_id,
                'source_calendar_id': source_calendar_id
            }
            last_sync_result = result
        
        logger.info(f"🎉 BULLETPROOF sync completed: {result}")
        return result
        
    except Exception as e:
        import traceback
        error_result = {
            'success': False,
            'message': f'BULLETPROOF sync failed: {str(e)}',
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        with sync_lock:
            last_sync_result = error_result
        logger.error(f"💥 BULLETPROOF sync error: {error_result}")
        return error_result
    
    finally:
        with sync_lock:
            sync_in_progress = False

def run_sync():
    """Run sync with automatic token refresh"""
    if not ensure_valid_token():
        logger.warning("Cannot run sync - authentication failed")
        return {"success": False, "message": "Authentication failed - please sign in again"}
    
    try:
        result = sync_calendars()
        return result
    except Exception as e:
        logger.error(f"Sync error: {e}")
        return {"success": False, "message": str(e)}

async def run_diagnostics():
    """Run comprehensive diagnostics to check what's available"""
    if not ensure_valid_token():
        return {"success": False, "message": "Not authenticated or token refresh failed - please sign in"}
    
    logger.info("Starting diagnostics")
    diagnostics = {
        "success": False,
        "timestamp": datetime.now().isoformat(),
        "shared_mailbox": SHARED_MAILBOX,
        "expected_source": SOURCE_CALENDAR,
        "expected_target": TARGET_CALENDAR,
        "findings": {}
    }
    
    try:
        # Create Graph client
        client = create_graph_client()
        if not client:
            diagnostics["message"] = "Could not create Graph client"
            return diagnostics
        
        logger.info("Graph client created successfully")
        
        # Test 1: Can we access the shared mailbox?
        try:
            calendars = await client.users.by_user_id(SHARED_MAILBOX).calendars.get()
            logger.info(f"Successfully accessed shared mailbox: {SHARED_MAILBOX}")
            
            if calendars and calendars.value:
                available_calendars = []
                for cal in calendars.value:
                    available_calendars.append({
                        "id": str(cal.id) if cal.id else "Unknown",
                        "name": str(cal.name) if cal.name else "Unknown",
                        "owner": str(getattr(cal, 'owner', 'Unknown')),
                        "can_edit": str(getattr(cal, 'can_edit', 'Unknown'))
                    })
                
                diagnostics["findings"]["available_calendars"] = available_calendars
                diagnostics["findings"]["calendar_count"] = len(available_calendars)
                
                logger.info(f"Found {len(available_calendars)} calendars")
                
                # Test 2: Do our expected calendars exist?
                source_found = None
                target_found = None
                
                for cal in available_calendars:
                    if cal["name"] == SOURCE_CALENDAR:
                        source_found = cal
                    elif cal["name"] == TARGET_CALENDAR:
                        target_found = cal
                
                diagnostics["findings"]["source_calendar_found"] = source_found is not None
                diagnostics["findings"]["target_calendar_found"] = target_found is not None
                
                if source_found:
                    logger.info(f"Source calendar '{SOURCE_CALENDAR}' found")
                    diagnostics["findings"]["source_calendar_details"] = source_found
                else:
                    logger.warning(f"Source calendar '{SOURCE_CALENDAR}' NOT found")
                
                if target_found:
                    logger.info(f"Target calendar '{TARGET_CALENDAR}' found")
                    diagnostics["findings"]["target_calendar_details"] = target_found
                else:
                    logger.warning(f"Target calendar '{TARGET_CALENDAR}' NOT found")
                
                diagnostics["success"] = True
                diagnostics["message"] = "Diagnostics completed successfully"
                
            else:
                diagnostics["message"] = "No calendars found in shared mailbox"
                logger.warning("No calendars found in shared mailbox")
                
        except Exception as e:
            diagnostics["message"] = f"Error accessing shared mailbox: {str(e)}"
            diagnostics["findings"]["mailbox_access_error"] = str(e)
            logger.error(f"Error accessing shared mailbox: {e}")
        
    except Exception as e:
        diagnostics["message"] = f"Diagnostics failed: {str(e)}"
        logger.error(f"Diagnostics failed: {e}")
    
    logger.info("Diagnostics completed")
    return diagnostics

def run_diagnostics_sync():
    """Run diagnostics in asyncio event loop"""
    if not ensure_valid_token():
        logger.warning("Cannot run diagnostics - authentication failed")
        return {"success": False, "message": "Authentication failed - please sign in again"}
    
    try:
        # Try to get the current event loop
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
    except RuntimeError:
        # Create a new event loop if none exists or it's closed
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        result = loop.run_until_complete(run_diagnostics())
        return result
    except Exception as e:
        logger.error(f"Diagnostics error: {e}")
        return {"success": False, "message": str(e)}

def scheduled_sync():
    """Function to be called by the scheduler"""
    logger.info("Running scheduled sync")
    result = run_sync()
    logger.info(f"Scheduled sync completed: {result}")

def run_scheduler():
    """Run the scheduler in a background thread"""
    global scheduler_running
    
    with scheduler_lock:
        scheduler_running = True
    
    # Schedule the sync to run every hour
    schedule.every().hour.do(scheduled_sync)
    
    logger.info("Scheduler started - sync will run every hour")
    
    while True:
        with scheduler_lock:
            if not scheduler_running:
                break
        schedule.run_pending()
        time.sleep(60)  # Check every minute
    
    logger.info("Scheduler stopped")

def start_scheduler():
    """Start the scheduler thread if not already running"""
    global scheduler_thread
    
    with scheduler_lock:
        if scheduler_thread is None or not scheduler_thread.is_alive():
            logger.info("Starting scheduler thread...")
            scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
            scheduler_thread.start()
        else:
            logger.info("Scheduler already running")

def stop_scheduler():
    """Stop the scheduler"""
    global scheduler_running
    
    with scheduler_lock:
        scheduler_running = False
    
    logger.info("Stopping scheduler...")

# Register cleanup function
atexit.register(stop_scheduler)

# ==================== WEB ROUTES ====================

@app.route('/')
def index():
    """Main page"""
    if not access_token:
        auth_url = get_auth_url()
        return f'''
        <html>
        <head>
            <title>St. Edward Calendar Sync - Sign In</title>
            <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📅</text></svg>">
        </head>
        <body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>🗓️ St. Edward Calendar Sync</h1>
            <p>You need to sign in with your Microsoft account to access the calendar sync.</p>
            <p style="margin: 1rem 0; opacity: 0.7; font-size: 0.9rem;">{APP_VERSION_INFO['full_version']}</p>
            <a href="{auth_url}" style="background: #0078d4; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 18px;">
                Sign in with Microsoft
            </a>
        </body>
        </html>
        '''
    
    # Convert last sync time to Central Time for display
    central_tz = pytz.timezone('US/Central')
    display_sync_time = None
    with sync_lock:
        if last_sync_time:
            if last_sync_time.tzinfo is None:
                # If no timezone info, assume UTC and convert
                utc_time = pytz.utc.localize(last_sync_time)
                display_sync_time = utc_time.astimezone(central_tz)
            else:
                # Already has timezone info, convert to Central
                display_sync_time = last_sync_time.astimezone(central_tz)
    
    return render_template('index.html', 
                         last_sync_time=display_sync_time,
                         last_sync_result=last_sync_result,
                         sync_in_progress=sync_in_progress)

@app.route('/auth/callback')
def auth_callback():
    """Handle OAuth callback with CSRF protection"""
    auth_code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state for CSRF protection
    if state != session.get('oauth_state'):
        logger.warning("Invalid OAuth state - possible CSRF attempt")
        return "Invalid state parameter", 400
    
    if auth_code and exchange_code_for_token(auth_code):
        session.pop('oauth_state', None)  # Clear state after use
        return redirect(url_for('index'))
    else:
        return "Authentication failed", 400

@app.route('/sync', methods=['POST'])
@requires_auth
def manual_sync():
    """Manual sync trigger"""
    def sync_thread():
        global last_sync_result
        result = run_sync()
        # Ensure result is JSON serializable
        with sync_lock:
            last_sync_result = make_json_serializable(result)
    
    # Run sync in background thread
    thread = threading.Thread(target=sync_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Sync started"})

@app.route('/diagnostics', methods=['POST'])
@requires_auth
def run_diagnostics_endpoint():
    """Run diagnostics to check calendar configuration"""
    def diagnostics_thread():
        global last_sync_result
        result = run_diagnostics_sync()
        # Ensure result is JSON serializable before storing
        with sync_lock:
            last_sync_result = make_json_serializable(result)
    
    # Run diagnostics in background thread
    thread = threading.Thread(target=diagnostics_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Diagnostics started"})

@app.route('/status')
def status():
    """Get current sync status with version info"""
    # Convert last sync time to Central Time for display
    central_tz = pytz.timezone('US/Central')
    display_sync_time = None
    
    with sync_lock:
        if last_sync_time:
            if last_sync_time.tzinfo is None:
                # If no timezone info, assume UTC and convert
                utc_time = pytz.utc.localize(last_sync_time)
                display_sync_time = utc_time.astimezone(central_tz)
            else:
                # Already has timezone info, convert to Central
                display_sync_time = last_sync_time.astimezone(central_tz)
    
    # Include token status information
    with token_lock:
        token_status = "valid"
        if not access_token:
            token_status = "missing"
        elif token_expires_at and datetime.utcnow() >= token_expires_at:
            token_status = "expired"
        
        has_refresh = refresh_token is not None
        expires_at = token_expires_at.isoformat() if token_expires_at else None
    
    with scheduler_lock:
        scheduler_active = scheduler_running
    
    return jsonify({
        "last_sync_time": display_sync_time.isoformat() if display_sync_time else None,
        "last_sync_result": make_json_serializable(last_sync_result),
        "sync_in_progress": sync_in_progress,
        "authenticated": access_token is not None,
        "scheduler_running": scheduler_active,
        "token_status": token_status,
        "token_expires_at": expires_at,
        "has_refresh_token": has_refresh,
        "rate_limit_remaining": MAX_SYNC_REQUESTS_PER_HOUR - len(sync_request_times),
        "master_calendar_protection": MASTER_CALENDAR_PROTECTION,
        "dry_run_mode": DRY_RUN_MODE,
        # VERSION INFORMATION
        "version": APP_VERSION_INFO["version"],
        "build_number": APP_VERSION_INFO["build_number"],
        "commit_hash": APP_VERSION_INFO["commit_hash"],
        "build_date": APP_VERSION_INFO["build_date"],
        "environment": APP_VERSION_INFO["environment"],
        "deployment_platform": APP_VERSION_INFO["deployment_platform"],
        "app_info": APP_VERSION_INFO["full_version"],
        "version_string": APP_VERSION_INFO["version_string"],
        "build_string": APP_VERSION_INFO["build_string"]
    })

@app.route('/version')
def version():
    """Get detailed version information"""
    return jsonify(APP_VERSION_INFO)

@app.route('/debug-master-calendar')
@requires_auth
def debug_master_calendar():
    """EMERGENCY: Debug what's happening to the master calendar"""
    try:
        if not ensure_valid_token():
            return jsonify({"error": "Authentication failed"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers, timeout=30)
        calendars_data = calendars_response.json()
        
        source_calendar_id = None
        
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == SOURCE_CALENDAR:
                source_calendar_id = calendar.get('id')
                break
        
        if not source_calendar_id:
            return jsonify({"error": "Source calendar not found"})
        
        # Get recent events from master calendar with detailed info
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        params = {
            '$top': 100,
            '$orderby': 'lastModifiedDateTime desc',
            '$select': 'id,subject,start,end,categories,lastModifiedDateTime,createdDateTime,organizer,isOrganizer,responseStatus,type,seriesMasterId'
        }
        
        events_response = requests.get(events_url, headers=headers, params=params, timeout=30)
        events = events_response.json().get('value', [])
        
        # Analyze events
        analysis = {
            'total_events': len(events),
            'public_events': 0,
            'recent_changes': [],
            'suspicious_activity': [],
            'organizer_breakdown': {},
            'calendar_id': source_calendar_id,
            'calendar_name': SOURCE_CALENDAR
        }
        
        for event in events:
            # Count public events
            if 'Public' in event.get('categories', []):
                analysis['public_events'] += 1
            
            # Check for recent modifications
            last_modified = event.get('lastModifiedDateTime', '')
            if last_modified:
                try:
                    mod_date = datetime.fromisoformat(last_modified.replace('Z', ''))
                    if mod_date > datetime.utcnow() - timedelta(hours=24):
                        analysis['recent_changes'].append({
                            'subject': event.get('subject'),
                            'modified': last_modified,
                            'id': event.get('id'),
                            'categories': event.get('categories', [])
                        })
                except:
                    pass
            
            # Check organizer
            organizer = event.get('organizer', {}).get('emailAddress', {}).get('address', 'Unknown')
            is_organizer = event.get('isOrganizer', False)
            
            if organizer not in analysis['organizer_breakdown']:
                analysis['organizer_breakdown'][organizer] = {'count': 0, 'is_organizer': 0}
            
            analysis['organizer_breakdown'][organizer]['count'] += 1
            if is_organizer:
                analysis['organizer_breakdown'][organizer]['is_organizer'] += 1
            
            # Flag suspicious activity
            if not is_organizer and organizer != 'calendar@stedward.org':
                analysis['suspicious_activity'].append({
                    'subject': event.get('subject'),
                    'organizer': organizer,
                    'reason': 'External organizer'
                })
        
        return jsonify({
            'timestamp': datetime.now().isoformat(),
            'warning': '🚨 This is your MASTER calendar - it should NEVER be modified by sync!',
            'analysis': analysis,
            'recommendations': [
                'Check if anyone else has access to modify this calendar',
                'Verify no other sync processes are running',
                'Check calendar permissions and sharing settings',
                'Review recent activity logs in Microsoft 365 admin center'
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Debug failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/enable-dry-run')
@requires_auth
def enable_dry_run():
    """Enable dry run mode for safe testing"""
    global DRY_RUN_MODE
    DRY_RUN_MODE = True
    return jsonify({"message": "DRY RUN mode enabled - no changes will be made during sync", "dry_run_mode": True})

@app.route('/disable-dry-run')
@requires_auth
def disable_dry_run():
    """Disable dry run mode"""
    global DRY_RUN_MODE
    DRY_RUN_MODE = False
    return jsonify({"message": "DRY RUN mode disabled - sync will make real changes", "dry_run_mode": False})

@app.route('/health')
def health_check():
    """Health check endpoint with version info"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "authenticated": access_token is not None,
        "scheduler_running": scheduler_running,
        "master_calendar_protection": MASTER_CALENDAR_PROTECTION,
        "dry_run_mode": DRY_RUN_MODE,
        "version": APP_VERSION_INFO["version_string"],
        "build": APP_VERSION_INFO["build_string"],
        "environment": APP_VERSION_INFO["environment"]
    }
    
    # Check if token needs refresh soon
    with token_lock:
        if token_expires_at:
            minutes_until_expiry = (token_expires_at - datetime.utcnow()).total_seconds() / 60
            if minutes_until_expiry < 10:
                health_status["warning"] = "Token expiring soon"
    
    return jsonify(health_status)

@app.route('/logout')
def logout():
    """Clear authentication and all tokens"""
    global access_token, refresh_token, token_expires_at
    
    with token_lock:
        access_token = None
        refresh_token = None
        token_expires_at = None
    
    stop_scheduler()
    session.clear()  # Clear all session data
    logger.info("User logged out - all tokens cleared")
    return redirect(url_for('index'))

@app.route('/clean-duplicates')
@requires_auth
def clean_duplicates():
    """Find and analyze duplicate events in public calendar"""
    try:
        if not ensure_valid_token():
            return jsonify({"error": "Authentication failed"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers, timeout=30)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == TARGET_CALENDAR:
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Get ALL events from public calendar
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {'$top': 500}
        
        events_response = requests.get(events_url, headers=headers, params=params, timeout=30)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get events: {events_response.status_code}"})
        
        all_events = events_response.json().get('value', [])
        
        # Group events by signature to find duplicates
        event_groups = {}
        for event in all_events:
            signature = create_event_signature(event)
            
            if signature not in event_groups:
                event_groups[signature] = []
            
            event_groups[signature].append({
                'id': event.get('id'),
                'subject': event.get('subject'),
                'start': event.get('start', {}).get('dateTime', 'No Start'),
                'type': event.get('type', 'unknown'),
                'created': event.get('createdDateTime'),
                'modified': event.get('lastModifiedDateTime')
            })
        
        # Find groups with duplicates
        duplicates_found = []
        events_to_delete = []
        
        for signature, events in event_groups.items():
            if len(events) > 1:
                # Sort by creation date - keep the oldest, delete the rest
                events.sort(key=lambda x: x.get('created', ''))
                
                keep_event = events[0]  # Keep the first (oldest) one
                delete_events = events[1:]  # Delete the rest
                
                duplicates_found.append({
                    'signature': signature,
                    'total_count': len(events),
                    'keeping': f"{keep_event['subject']} (ID: {keep_event['id'][:8]}...)",
                    'deleting_count': len(delete_events),
                    'deleting': [f"{e['subject']} (ID: {e['id'][:8]}...)" for e in delete_events]
                })
                
                # Add to deletion list
                events_to_delete.extend([e['id'] for e in delete_events])
        
        return jsonify({
            "total_events_in_calendar": len(all_events),
            "duplicate_groups_found": len(duplicates_found),
            "events_to_delete": len(events_to_delete),
            "duplicates_analysis": duplicates_found,
            "deletion_preview": events_to_delete,
            "next_step": "Visit /execute-duplicate-cleanup to actually delete the duplicates",
            "warning": "⚠️ This will permanently delete duplicate events. Review the list above first!",
            "dry_run_note": f"DRY RUN MODE: {'ENABLED' if DRY_RUN_MODE else 'DISABLED'}"
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Duplicate analysis failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/execute-duplicate-cleanup')
@requires_auth
def execute_duplicate_cleanup():
    """Actually delete the duplicate events found by clean-duplicates"""
    try:
        if not ensure_valid_token():
            return jsonify({"error": "Authentication failed"}), 401
        
        # First run the analysis again to get current duplicates
        analysis_result = clean_duplicates()
        analysis_data = analysis_result.get_json()
        
        if 'error' in analysis_data:
            return jsonify(analysis_data), 500
        
        events_to_delete = analysis_data.get('deletion_preview', [])
        
        if not events_to_delete:
            return jsonify({"message": "No duplicates found to delete", "deleted_count": 0})
        
        # Check dry run mode
        if DRY_RUN_MODE:
            return jsonify({
                "dry_run": True,
                "message": f"DRY RUN: Would delete {len(events_to_delete)} duplicate events",
                "events_to_delete": len(events_to_delete),
                "note": "Visit /disable-dry-run to make real changes"
            })
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID again
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers, timeout=30)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == TARGET_CALENDAR:
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Delete the duplicate events
        deletion_results = []
        successful_deletions = 0
        
        # Add header to suppress notifications
        delete_headers = {
            **headers,
            'Prefer': 'outlook.timezone="UTC", outlook.send-notifications="false"'
        }
        
        for event_id in events_to_delete:
            try:
                delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}"
                delete_response = requests.delete(delete_url, headers=delete_headers, timeout=30)
                
                success = delete_response.status_code in [200, 204]
                if success:
                    successful_deletions += 1
                
                deletion_results.append({
                    "event_id": event_id[:8] + "...",
                    "status_code": delete_response.status_code,
                    "success": success,
                    "error": None if success else delete_response.text[:100]
                })
                
            except Exception as e:
                deletion_results.append({
                    "event_id": event_id[:8] + "...",
                    "success": False,
                    "error": str(e)
                })
        
        return jsonify({
            "cleanup_completed": True,
            "total_attempted": len(events_to_delete),
            "successful_deletions": successful_deletions,
            "failed_deletions": len(events_to_delete) - successful_deletions,
            "deletion_details": deletion_results,
            "recommendation": "Now you can run a sync to ensure calendar is properly populated"
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Cleanup execution failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/list-all-events')
@requires_auth
def list_all_events():
    """List all events in public calendar with full details"""
    try:
        if not ensure_valid_token():
            return jsonify({"error": "Authentication failed"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers, timeout=30)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == TARGET_CALENDAR:
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Get ALL events with full details
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {'$top': 500}
        
        events_response = requests.get(events_url, headers=headers, params=params, timeout=30)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get events: {events_response.status_code}"})
        
        all_events = events_response.json().get('value', [])
        
        # Format events for easy reading
        formatted_events = []
        for i, event in enumerate(all_events, 1):
            signature = create_event_signature(event)
            formatted_event = {
                "number": i,
                "id": event.get('id'),
                "subject": event.get('subject'),
                "start": event.get('start', {}).get('dateTime', 'No start time'),
                "type": event.get('type'),
                "signature": signature,
                "created": event.get('createdDateTime', 'Unknown'),
                "modified": event.get('lastModifiedDateTime', 'Unknown')
            }
            formatted_events.append(formatted_event)
        
        # Sort by signature to group similar events together
        formatted_events.sort(key=lambda x: x['signature'])
        
        return jsonify({
            "total_events": len(formatted_events),
            "calendar_name": TARGET_CALENDAR,
            "events": formatted_events,
            "instructions": [
                "Events are sorted by signature to group duplicates together",
                "Look for events with identical signatures",
                "Use /clean-duplicates to analyze and /execute-duplicate-cleanup to remove duplicates"
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Failed to list events: {str(e)}", "traceback": traceback.format_exc()}), 500

if __name__ == '__main__':
    # Validate required environment variables
    if not CLIENT_SECRET:
        logger.error("CLIENT_SECRET environment variable is not set!")
        exit(1)
    
    # Log startup information
    logger.info("=" * 60)
    logger.info(f"🚀 {APP_VERSION_INFO['full_version']}")
    logger.info(f"📦 Platform: {APP_VERSION_INFO['deployment_platform']}")
    logger.info(f"🌍 Environment: {APP_VERSION_INFO['environment']}")
    logger.info(f"🔨 Build Date: {APP_VERSION_INFO['build_date']}")
    if APP_VERSION_INFO['commit_hash'] != 'unknown':
        logger.info(f"🔗 Commit: {APP_VERSION_INFO['commit_hash']}")
    logger.info("=" * 60)
    
    # Try to restore authentication on startup
    load_tokens_from_env()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
