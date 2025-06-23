#!/usr/bin/env python3
"""
Calendar Sync Web Application for Render.com
STABLE VERSION with improvements for production deployment and token persistence
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
    # Note: This won't persist immediately, but you can set these manually in Render dashboard
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
                
                # Test 3: Can we read events from the source calendar?
                if source_found:
                    try:
                        source_events = await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(source_found["id"]).events.get()
                        event_count = len(source_events.value) if source_events and source_events.value else 0
                        diagnostics["findings"]["source_events_count"] = event_count
                        logger.info(f"Source calendar has {event_count} total events")
                        
                        # Count public events
                        public_count = 0
                        if source_events and source_events.value:
                            for event in source_events.value:
                                if hasattr(event, 'categories') and event.categories and "Public" in event.categories:
                                    public_count += 1
                        
                        diagnostics["findings"]["public_events_count"] = public_count
                        logger.info(f"Found {public_count} public events in source calendar")
                        
                    except Exception as e:
                        diagnostics["findings"]["source_access_error"] = str(e)
                        logger.error(f"Error accessing source calendar events: {e}")
                
                # Test 4: Can we read events from the target calendar?
                if target_found:
                    try:
                        target_events = await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(target_found["id"]).events.get()
                        target_event_count = len(target_events.value) if target_events and target_events.value else 0
                        diagnostics["findings"]["target_events_count"] = target_event_count
                        logger.info(f"Target calendar has {target_event_count} events")
                        
                    except Exception as e:
                        diagnostics["findings"]["target_access_error"] = str(e)
                        logger.error(f"Error accessing target calendar events: {e}")
                
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

def sync_calendars():
    """Sync function with automatic token refresh and improved error handling"""
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
    
    logger.info("Starting calendar sync")
    
    try:
        # Ensure token is valid before sync
        if not ensure_valid_token():
            return {"error": "Authentication failed - token expired and refresh failed"}
        
        with token_lock:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
        
        # Get calendars
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
            if calendar.get('name') == SOURCE_CALENDAR:
                source_calendar_id = calendar.get('id')
            elif calendar.get('name') == TARGET_CALENDAR:
                target_calendar_id = calendar.get('id')
        
        if not source_calendar_id or not target_calendar_id:
            logger.error("Required calendars not found")
            return {"error": "Required calendars not found"}
        
        # Get source events (all types)
        source_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        source_params = {
            '$top': 200,
        }
        
        source_response = requests.get(source_events_url, headers=headers, params=source_params, timeout=30)
        if source_response.status_code != 200:
            logger.error(f"Failed to get source events: {source_response.status_code}")
            return {"error": f"Failed to get source events: {source_response.status_code}"}
        
        source_events = source_response.json().get('value', [])
        
        # Get target events
        target_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        target_params = {
            '$top': 200,
        }
        
        target_response = requests.get(target_events_url, headers=headers, params=target_params, timeout=30)
        if target_response.status_code != 200:
            logger.error(f"Failed to get target events: {target_response.status_code}")
            return {"error": f"Failed to get target events: {target_response.status_code}"}
        
        target_events = target_response.json().get('value', [])
        
        # Process public events from source - FILTER OUT INSTANCES AND TENTATIVE EVENTS
        public_events = []
        tentative_count = 0
        
        for event in source_events:
            categories = event.get('categories', [])
            if 'Public' in categories:
                # NEW: Check if event is confirmed (busy) and not tentative
                show_as = event.get('showAs', 'busy')  # Default to 'busy' if not specified
                
                # Only sync events that are marked as "busy" (confirmed)
                # Skip events marked as "tentative", "free", or "oof" (out of office)
                if show_as != 'busy':
                    logger.debug(f"Skipping tentative/unconfirmed event: {event.get('subject')} (showAs: {show_as})")
                    if show_as == 'tentative':
                        tentative_count += 1
                    continue
                
                logger.debug(f"Processing public event: {event.get('subject')}")
                
                # Handle ONLY recurring masters AND true single events, skip instances
                event_type = event.get('type', 'unknown')
                if event_type == 'seriesMaster':
                    logger.debug(f"Found recurring master event: {event.get('subject')}")
                    # Clear body content for privacy
                    event_copy = event.copy()
                    if 'body' in event_copy:
                        event_copy['body'] = {'contentType': 'html', 'content': ''}
                    public_events.append(event_copy)
                    
                elif event_type == 'singleInstance' and not event.get('seriesMasterId'):
                    # Only include single events that are NOT part of a recurring series
                    logger.debug(f"Found single event: {event.get('subject')}")
                    # Clear body content for privacy
                    event_copy = event.copy()
                    if 'body' in event_copy:
                        event_copy['body'] = {'contentType': 'html', 'content': ''}
                    public_events.append(event_copy)
                    
                elif event_type == 'occurrence':
                    logger.debug(f"Skipping recurring instance: {event.get('subject')} (will sync via master)")
                    
                else:
                    logger.debug(f"Skipped event type '{event_type}': {event.get('subject')}")
        
        logger.info(f"Sync summary: {len(public_events)} confirmed public events to sync, {tentative_count} tentative events skipped")
        
        # Create source and target dictionaries for comparison
        source_events_dict = {}
        for event in public_events:
            subject = event.get('subject', '')
            event_type = event.get('type', 'unknown')
            
            # Create different key formats for recurring vs single events
            if event_type == 'seriesMaster':
                key = f"recurring:{subject}"
            else:
                # For single events, include start date to make them unique
                start_date = event.get('start', {}).get('dateTime', 'no-date')
                if 'T' in start_date:
                    start_date = start_date.split('T')[0]  # Just the date part
                key = f"single:{subject}:{start_date}"
                
            source_events_dict[key] = event
        
        target_events_dict = {}
        for event in target_events:
            subject = event.get('subject', '')
            event_type = event.get('type', 'unknown')
            
            # Create matching key format
            if event_type == 'seriesMaster':
                key = f"recurring:{subject}"
            else:
                start_date = event.get('start', {}).get('dateTime', 'no-date')
                if 'T' in start_date:
                    start_date = start_date.split('T')[0]
                key = f"single:{subject}:{start_date}"
                
            target_events_dict[key] = event
        
        # Determine what needs to be added, updated, or deleted
        to_add = []
        to_update = []
        to_delete = []
        
        for key, source_event in source_events_dict.items():
            if key in target_events_dict:
                to_update.append((key, source_event, target_events_dict[key]))
            else:
                to_add.append((key, source_event))
        
        for key in target_events_dict:
            if key not in source_events_dict:
                to_delete.append((key, target_events_dict[key]))
        
        logger.info(f"Sync plan: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
        
        # Execute sync operations
        successful_operations = 0
        total_operations = len(to_add) + len(to_update) + len(to_delete)
        
        # Add new events
        for key, source_event in to_add:
            try:
                create_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
                create_data = {
                    'subject': source_event.get('subject'),
                    'start': source_event.get('start'),
                    'end': source_event.get('end'),
                    'categories': source_event.get('categories'),
                    'body': {'contentType': 'html', 'content': ''},  # Always clear body
                    'location': source_event.get('location', {}),
                    'isAllDay': source_event.get('isAllDay', False),
                    'showAs': source_event.get('showAs', 'free'),  # Preserve free/busy status
                    'isReminderOn': False  # NO ALERTS/REMINDERS
                }
                
                # Only add recurrence for recurring events
                if source_event.get('type') == 'seriesMaster' and source_event.get('recurrence'):
                    create_data['recurrence'] = source_event.get('recurrence')
                
                create_response = requests.post(create_url, headers=headers, json=create_data, timeout=30)
                if create_response.status_code in [200, 201]:
                    successful_operations += 1
                    logger.debug(f"Successfully added: {key}")
                else:
                    logger.error(f"Failed to add {key}: {create_response.status_code}")
            except Exception as e:
                logger.error(f"Error adding {key}: {str(e)}")
        
        # Update existing events
        for key, source_event, target_event in to_update:
            try:
                update_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{target_event.get('id')}"
                update_data = {
                    'subject': source_event.get('subject'),
                    'start': source_event.get('start'),
                    'end': source_event.get('end'),
                    'categories': source_event.get('categories'),
                    'body': {'contentType': 'html', 'content': ''},  # Always clear body
                    'location': source_event.get('location', {}),
                    'isAllDay': source_event.get('isAllDay', False),
                    'showAs': source_event.get('showAs', 'free'),  # Preserve free/busy status
                    'isReminderOn': False  # NO ALERTS/REMINDERS
                }
                
                # Only add recurrence for recurring events
                if source_event.get('type') == 'seriesMaster' and source_event.get('recurrence'):
                    update_data['recurrence'] = source_event.get('recurrence')
                
                update_response = requests.patch(update_url, headers=headers, json=update_data, timeout=30)
                if update_response.status_code == 200:
                    successful_operations += 1
                    logger.debug(f"Successfully updated: {key}")
                else:
                    logger.error(f"Failed to update {key}: {update_response.status_code}")
            except Exception as e:
                logger.error(f"Error updating {key}: {str(e)}")
        
        # Delete removed events
        for key, target_event in to_delete:
            try:
                delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{target_event.get('id')}"
                delete_response = requests.delete(delete_url, headers=headers, timeout=30)
                if delete_response.status_code in [200, 204]:
                    successful_operations += 1
                    logger.debug(f"Successfully deleted: {key}")
                else:
                    logger.error(f"Failed to delete {key}: {delete_response.status_code}")
            except Exception as e:
                logger.error(f"Error deleting {key}: {str(e)}")
        
        with sync_lock:
            last_sync_time = datetime.now(pytz.UTC)
            result = {
                'success': True,
                'message': f'Sync complete: {successful_operations}/{total_operations} operations successful',
                'changes': successful_operations,
                'successful_operations': successful_operations,
                'added': len(to_add),
                'updated': len(to_update),
                'deleted': len(to_delete),
                'tentative_skipped': tentative_count
            }
            last_sync_result = result
        
        logger.info(f"Sync completed: {result}")
        return result
        
    except Exception as e:
        import traceback
        error_result = {
            'success': False,
            'message': f'Sync failed: {str(e)}',
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        with sync_lock:
            last_sync_result = error_result
        logger.error(f"Sync error: {error_result}")
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
    logger.info(f"Sync completed: {result}")

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

# Web Routes
@app.route('/')
def index():
    """Main page"""
    if not access_token:
        auth_url = get_auth_url()
        return f'''
        <html>
        <head><title>St. Edward Calendar Sync - Sign In</title></head>
        <body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>🗓️ St. Edward Calendar Sync</h1>
            <p>You need to sign in with your Microsoft account to access the calendar sync.</p>
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
    """Get current sync status"""
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
        "rate_limit_remaining": MAX_SYNC_REQUESTS_PER_HOUR - len(sync_request_times)
    })

@app.route('/health')
def health():
    """Health check endpoint for monitoring"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "authenticated": access_token is not None,
        "scheduler_running": scheduler_running
    }
    
    # Check if token needs refresh soon
    with token_lock:
        if token_expires_at:
            minutes_until_expiry = (token_expires_at - datetime.utcnow()).total_seconds() / 60
            if minutes_until_expiry < 10:
                health_status["warning"] = "Token expiring soon"
    
    return jsonify(health_status)

@app.route('/scheduler-status')
def scheduler_status():
    """Check if scheduler thread is actually running"""
    global scheduler_thread
    
    with scheduler_lock:
        thread_alive = scheduler_thread is not None and scheduler_thread.is_alive()
        running = scheduler_running
    
    return jsonify({
        "scheduler_running_flag": running,
        "scheduler_thread_exists": scheduler_thread is not None,
        "scheduler_thread_alive": thread_alive,
        "needs_restart": not thread_alive or not running
    })

@app.route('/start-scheduler')  
@requires_auth
def start_scheduler_endpoint():
    """Manually start the automatic scheduler"""
    start_scheduler()
    return jsonify({"message": "Automatic sync scheduler started", "scheduler_running": scheduler_running})

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

if __name__ == '__main__':
    # Validate required environment variables
    if not CLIENT_SECRET:
        logger.error("CLIENT_SECRET environment variable is not set!")
        exit(1)
    
    # Try to restore authentication on startup
    load_tokens_from_env()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
