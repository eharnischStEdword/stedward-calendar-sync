#!/usr/bin/env python3
"""
Calendar Sync Web Application for Render.com
Simple web interface for rcarroll + automatic hourly sync + diagnostics
FIXED: JSON serialization issue in /status endpoint
FIXED: sync_calendars function to use events endpoint like master events search
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

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')

# Configuration
SHARED_MAILBOX = "calendar@stedward.org"
SOURCE_CALENDAR = "Calendar"
TARGET_CALENDAR = "St. Edward Public Calendar"

# Azure AD App Registration
CLIENT_ID = "e139467d-fdeb-40bb-be62-718b007c8e0a"
TENANT_ID = "8ccf96b2-b7eb-470b-a715-ec1696d83ebd"
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', '')
REDIRECT_URI = "https://stedward-calendar-sync.onrender.com/auth/callback"

# Global variables for tracking sync status
last_sync_time = None
last_sync_result = {"success": False, "message": "Not synced yet"}
sync_in_progress = False
access_token = None

# Scheduler control
scheduler_running = False
scheduler_thread = None

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
        # Convert any other object to string
        return str(obj)

def get_auth_url():
    """Generate Microsoft OAuth URL"""
    params = {
        'client_id': CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': REDIRECT_URI,
        'scope': 'https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/User.Read https://graph.microsoft.com/Calendars.ReadWrite.Shared offline_access',
        'response_mode': 'query'
    }
    
    auth_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
    return auth_url

def exchange_code_for_token(auth_code):
    """Exchange authorization code for access token"""
    global access_token
    
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': auth_code,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code',
        'scope': 'https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/User.Read https://graph.microsoft.com/Calendars.ReadWrite.Shared offline_access'
    }
    
    response = requests.post(token_url, data=data)
    if response.status_code == 200:
        tokens = response.json()
        access_token = tokens.get('access_token')
        
        # Start the scheduler when we get authenticated
        start_scheduler()
        
        return True
    return False

def create_graph_client():
    """Create Graph client with stored access token"""
    if not access_token:
        return None
    
    # Simple bearer token authentication
    class BearerTokenCredential:
        def __init__(self, token):
            self.token = token
        
        def get_token(self, *scopes, **kwargs):
            from azure.core.credentials import AccessToken
            return AccessToken(self.token, int(time.time()) + 3600)
    
    credential = BearerTokenCredential(access_token)
    return GraphServiceClient(credentials=credential)

async def run_diagnostics():
    """Run comprehensive diagnostics to check what's available"""
    if not access_token:
        return {"success": False, "message": "Not authenticated - please sign in first"}
    
    print(f"🔍 Starting diagnostics at {datetime.now()}")
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
        
        print("✅ Graph client created successfully")
        
        # Test 1: Can we access the shared mailbox?
        try:
            calendars = await client.users.by_user_id(SHARED_MAILBOX).calendars.get()
            print(f"✅ Successfully accessed shared mailbox: {SHARED_MAILBOX}")
            
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
                
                print(f"📋 Found {len(available_calendars)} calendars:")
                for cal in available_calendars:
                    print(f"   - '{cal['name']}'")
                
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
                    print(f"✅ Source calendar '{SOURCE_CALENDAR}' found")
                    diagnostics["findings"]["source_calendar_details"] = source_found
                else:
                    print(f"❌ Source calendar '{SOURCE_CALENDAR}' NOT found")
                
                if target_found:
                    print(f"✅ Target calendar '{TARGET_CALENDAR}' found")
                    diagnostics["findings"]["target_calendar_details"] = target_found
                else:
                    print(f"❌ Target calendar '{TARGET_CALENDAR}' NOT found")
                
                # Test 3: Can we read events from the source calendar?
                if source_found:
                    try:
                        source_events = await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(source_found["id"]).events.get()
                        event_count = len(source_events.value) if source_events and source_events.value else 0
                        diagnostics["findings"]["source_events_count"] = event_count
                        print(f"📅 Source calendar has {event_count} total events")
                        
                        # Count public events
                        public_count = 0
                        if source_events and source_events.value:
                            for event in source_events.value:
                                if hasattr(event, 'categories') and event.categories and "Public" in event.categories:
                                    public_count += 1
                        
                        diagnostics["findings"]["public_events_count"] = public_count
                        print(f"🔓 Found {public_count} public events in source calendar")
                        
                    except Exception as e:
                        diagnostics["findings"]["source_access_error"] = str(e)
                        print(f"❌ Error accessing source calendar events: {e}")
                
                # Test 4: Can we read events from the target calendar?
                if target_found:
                    try:
                        target_events = await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(target_found["id"]).events.get()
                        target_event_count = len(target_events.value) if target_events and target_events.value else 0
                        diagnostics["findings"]["target_events_count"] = target_event_count
                        print(f"🎯 Target calendar has {target_event_count} events")
                        
                    except Exception as e:
                        diagnostics["findings"]["target_access_error"] = str(e)
                        print(f"❌ Error accessing target calendar events: {e}")
                
                diagnostics["success"] = True
                diagnostics["message"] = "Diagnostics completed successfully"
                
            else:
                diagnostics["message"] = "No calendars found in shared mailbox"
                print("❌ No calendars found in shared mailbox")
                
        except Exception as e:
            diagnostics["message"] = f"Error accessing shared mailbox: {str(e)}"
            diagnostics["findings"]["mailbox_access_error"] = str(e)
            print(f"❌ Error accessing shared mailbox: {e}")
        
    except Exception as e:
        diagnostics["message"] = f"Diagnostics failed: {str(e)}"
        print(f"💥 Diagnostics failed: {e}")
    
    print(f"🏁 Diagnostics completed")
    return diagnostics

def sync_calendars():
    """FIXED: Sync function handles BOTH recurring master events AND single events"""
    global last_sync_time, last_sync_result, sync_in_progress
    
    if sync_in_progress:
        return {"error": "Sync already in progress"}
    
    sync_in_progress = True
    print(f"Starting calendar sync at {datetime.now()}")
    
    try:
        if not access_token:
            return {"error": "Not authenticated"}
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        
        if calendars_response.status_code != 200:
            return {"error": f"Failed to get calendars: {calendars_response.status_code}"}
        
        calendars_data = calendars_response.json()
        source_calendar_id = None
        target_calendar_id = None
        
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
            elif calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
        
        if not source_calendar_id or not target_calendar_id:
            return {"error": "Required calendars not found"}
        
        # FIXED: Get BOTH recurring master events AND single events
        source_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        source_params = {
            '$top': 200,
            # REMOVED the filter so we get ALL event types, not just seriesMaster
        }
        
        source_response = requests.get(source_events_url, headers=headers, params=source_params)
        if source_response.status_code != 200:
            return {"error": f"Failed to get source events: {source_response.status_code}"}
        
        source_events = source_response.json().get('value', [])
        
        # Get target events using the same approach  
        target_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        target_params = {
            '$top': 200,
            # REMOVED the filter here too
        }
        
        target_response = requests.get(target_events_url, headers=headers, params=target_params)
        if target_response.status_code != 200:
            return {"error": f"Failed to get target events: {target_response.status_code}"}
        
        target_events = target_response.json().get('value', [])
        
        # Process public events from source - FILTER OUT INSTANCES
        public_events = []
        for event in source_events:
            categories = event.get('categories', [])
            if 'Public' in categories:
                print(f"🔍 Processing public event: {event.get('subject')}")
                
                # Handle ONLY recurring masters AND true single events, skip instances
                event_type = event.get('type', 'unknown')
                if event_type == 'seriesMaster':
                    print(f"✅ Found recurring master event: {event.get('subject')}")
                    # Clear body content for privacy
                    event_copy = event.copy()
                    if 'body' in event_copy:
                        event_copy['body'] = {'contentType': 'html', 'content': ''}
                    print(f"Clearing body content from: {event.get('subject')}")
                    public_events.append(event_copy)
                    
                elif event_type == 'singleInstance' and not event.get('seriesMasterId'):
                    # Only include single events that are NOT part of a recurring series
                    print(f"✅ Found single event: {event.get('subject')}")
                    # Clear body content for privacy
                    event_copy = event.copy()
                    if 'body' in event_copy:
                        event_copy['body'] = {'contentType': 'html', 'content': ''}
                    print(f"Clearing body content from: {event.get('subject')}")
                    public_events.append(event_copy)
                    
                elif event_type == 'occurrence':
                    print(f"⏩ Skipping recurring instance: {event.get('subject')} (will sync via master)")
                    
                else:
                    print(f"⚠️ Skipped event type '{event_type}': {event.get('subject')}")
            else:
                print(f"⚠️ Skipped non-public event: {event.get('subject')}")
        
        print(f"📊 Sync summary: {len(public_events)} total public events to sync")
        
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
            print(f"🔑 Source key: {key}")
        
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
            print(f"🎯 Target key: {key}")
        
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
        
        print(f"📋 Sync plan: {len(to_add)} to add, {len(to_update)} to update, {len(to_delete)} to delete")
        
        for key, _ in to_add:
            event_name = key.split(':', 1)[1] if ':' in key else key
            print(f"  ➕ Will add: {event_name}")
        for key, _, _ in to_update:
            event_name = key.split(':', 1)[1] if ':' in key else key  
            print(f"  ✏️ Will update: {event_name}")
        for key, _ in to_delete:
            event_name = key.split(':', 1)[1] if ':' in key else key
            print(f"  🗑️ Will delete: {event_name}")
        
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
                
                create_response = requests.post(create_url, headers=headers, json=create_data)
                if create_response.status_code in [200, 201]:
                    successful_operations += 1
                    print(f"✅ Successfully added: {key}")
                else:
                    print(f"❌ Failed to add {key}: {create_response.status_code} - {create_response.text}")
            except Exception as e:
                print(f"❌ Error adding {key}: {str(e)}")
        
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
                
                update_response = requests.patch(update_url, headers=headers, json=update_data)
                if update_response.status_code == 200:
                    successful_operations += 1
                    print(f"✅ Successfully updated: {key}")
                else:
                    print(f"❌ Failed to update {key}: {update_response.status_code} - {update_response.text}")
            except Exception as e:
                print(f"❌ Error updating {key}: {str(e)}")
        
        # Delete removed events
        for key, target_event in to_delete:
            try:
                delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{target_event.get('id')}"
                delete_response = requests.delete(delete_url, headers=headers)
                if delete_response.status_code in [200, 204]:
                    successful_operations += 1
                    print(f"✅ Successfully deleted: {key}")
                else:
                    print(f"❌ Failed to delete {key}: {delete_response.status_code}")
            except Exception as e:
                print(f"❌ Error deleting {key}: {str(e)}")
        
        last_sync_time = datetime.now(pytz.UTC)
        result = {
            'success': True,
            'message': f'Sync complete: {successful_operations}/{total_operations} operations successful',
            'changes': successful_operations,
            'successful_operations': successful_operations,
            'added': len(to_add),
            'updated': len(to_update),
            'deleted': len(to_delete)
        }
        
        last_sync_result = result
        print(f"Sync result: {result}")
        return result
        
    except Exception as e:
        import traceback
        error_result = {
            'success': False,
            'message': f'Sync failed: {str(e)}',
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        last_sync_result = error_result
        print(f"Sync error: {error_result}")
        return error_result
    
    finally:
        sync_in_progress = False

def run_sync():
    """Run sync - NO LONGER ASYNC"""
    if not access_token:
        print("Cannot run sync - not authenticated")
        return {"success": False, "message": "Not authenticated"}
    
    try:
        result = sync_calendars()
        print(f"Sync result: {result}")
        return result
    except Exception as e:
        print(f"Sync error: {e}")
        return {"success": False, "message": str(e)}

def run_diagnostics_sync():
    """Run diagnostics in asyncio event loop"""
    if not access_token:
        print("Cannot run diagnostics - not authenticated")
        return {"success": False, "message": "Not authenticated"}
    
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
        print(f"Diagnostics result: {result}")
        return result
    except Exception as e:
        print(f"Diagnostics error: {e}")
        return {"success": False, "message": str(e)}

def scheduled_sync():
    """Function to be called by the scheduler"""
    print(f"Running scheduled sync at {datetime.now()}")
    run_sync()

def run_scheduler():
    """Run the scheduler in a background thread"""
    global scheduler_running
    scheduler_running = True
    
    # Schedule the sync to run every hour
    schedule.every().hour.do(scheduled_sync)
    
    print("Scheduler started - sync will run every hour")
    
    while scheduler_running:
        schedule.run_pending()
        time.sleep(60)  # Check every minute
    
    print("Scheduler stopped")

def start_scheduler():
    """Start the scheduler thread if not already running"""
    global scheduler_thread, scheduler_running
    
    if scheduler_thread is None or not scheduler_thread.is_alive():
        print("Starting scheduler thread...")
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
    else:
        print("Scheduler already running")

def stop_scheduler():
    """Stop the scheduler"""
    global scheduler_running
    scheduler_running = False
    print("Stopping scheduler...")

# Register cleanup function
atexit.register(stop_scheduler)

# Web Routes
@app.route('/')
def index():
    """Main page for rcarroll"""
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
    """Handle OAuth callback"""
    auth_code = request.args.get('code')
    if auth_code and exchange_code_for_token(auth_code):
        return redirect(url_for('index'))
    else:
        return "Authentication failed", 400

@app.route('/sync', methods=['POST'])
def manual_sync():
    """Manual sync trigger"""
    if not access_token:
        return jsonify({"success": False, "message": "Not authenticated"})
    
    def sync_thread():
        global last_sync_result
        result = run_sync()
        # Ensure result is JSON serializable
        last_sync_result = make_json_serializable(result)
    
    # Run sync in background thread
    thread = threading.Thread(target=sync_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Sync started"})

@app.route('/diagnostics', methods=['POST'])
def run_diagnostics_endpoint():
    """Run diagnostics to check calendar configuration"""
    if not access_token:
        return jsonify({"success": False, "message": "Not authenticated"})
    
    def diagnostics_thread():
        global last_sync_result
        result = run_diagnostics_sync()
        # FIXED: Ensure result is JSON serializable before storing
        last_sync_result = make_json_serializable(result)
    
    # Run diagnostics in background thread
    thread = threading.Thread(target=diagnostics_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Diagnostics started"})

@app.route('/debug-all-events')
def debug_all_events():
    """Debug: Show ALL events in source calendar regardless of public/private status"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get all calendars first
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        
        if calendars_response.status_code != 200:
            return jsonify({"error": f"Failed to get calendars: {calendars_response.status_code}"})
        
        calendars_data = calendars_response.json()
        source_calendar_id = None
        
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
                break
        
        if not source_calendar_id:
            return jsonify({"error": "Source calendar 'Calendar' not found"})
        
        # Get ALL events from source calendar with extended date range
        start_time = datetime.now(pytz.UTC)
        end_time = start_time + timedelta(days=365)  # Look ahead 1 year
        
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/calendarView"
        params = {
            'startDateTime': start_time.isoformat(),
            'endDateTime': end_time.isoformat(),
            '$top': 100
        }
        
        events_response = requests.get(events_url, headers=headers, params=params)
        
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get events: {events_response.status_code}"})
        
        events_data = events_response.json()
        all_events = events_data.get('value', [])
        
        # Process all events
        debug_info = {
            "total_events": len(all_events),
            "events": []
        }
        
        for event in all_events:
            event_info = {
                "subject": event.get('subject', 'No subject'),
                "start": event.get('start', {}).get('dateTime', 'No start time'),
                "type": event.get('type', 'unknown'),
                "categories": event.get('categories', []),
                "is_public": "Public" in event.get('categories', []),
                "sensitivity": event.get('sensitivity', 'normal'),
                "is_recurring": event.get('type') == 'seriesMaster'
            }
            debug_info["events"].append(event_info)
        
        # Sort by subject for easier reading
        debug_info["events"].sort(key=lambda x: x["subject"] or "")
        
        return jsonify(debug_info)
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Debug failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/status')
def status():
    """Get current sync status - FIXED: JSON serialization issue"""
    # Convert last sync time to Central Time for display
    central_tz = pytz.timezone('US/Central')
    display_sync_time = None
    if last_sync_time:
        if last_sync_time.tzinfo is None:
            # If no timezone info, assume UTC and convert
            utc_time = pytz.utc.localize(last_sync_time)
            display_sync_time = utc_time.astimezone(central_tz)
        else:
            # Already has timezone info, convert to Central
            display_sync_time = last_sync_time.astimezone(central_tz)
    
    return jsonify({
        "last_sync_time": display_sync_time.isoformat() if display_sync_time else None,
        "last_sync_result": make_json_serializable(last_sync_result),
        "sync_in_progress": sync_in_progress,
        "authenticated": access_token is not None,
        "scheduler_running": scheduler_running
    })

@app.route('/search-8am')
def search_8am():
    """Search specifically for any events containing '8:00' or '8am'"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        source_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
                break
        
        if not source_calendar_id:
            return jsonify({"error": "Source calendar not found"})
        
        # Search with a much larger range and higher limit
        start_time = datetime.now(pytz.UTC) - timedelta(days=30)  # Look back 30 days too
        end_time = start_time + timedelta(days=400)  # Look ahead 400 days
        
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/calendarView"
        params = {
            'startDateTime': start_time.isoformat(),
            'endDateTime': end_time.isoformat(),
            '$top': 500  # Much higher limit
        }
        
        events_response = requests.get(events_url, headers=headers, params=params)
        events_data = events_response.json()
        all_events = events_data.get('value', [])
        
        # Search for any event containing "8:00", "8am", or "Mass- 8"
        search_terms = ["8:00", "8am", "Mass- 8", "8 am"]
        found_events = []
        
        for event in all_events:
            subject = event.get('subject', '').lower()
            for term in search_terms:
                if term.lower() in subject:
                    found_events.append({
                        "subject": event.get('subject'),
                        "start": event.get('start', {}).get('dateTime'),
                        "type": event.get('type'),
                        "categories": event.get('categories', []),
                        "is_public": "Public" in event.get('categories', [])
                    })
                    break
        
        return jsonify({
            "total_events_searched": len(all_events),
            "search_terms": search_terms,
            "found_events": found_events,
            "found_count": len(found_events)
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Search failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/search-mass-events')
def search_mass_events():
    """Search for all events containing 'Mass' to see what actually exists"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        source_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
                break
        
        if not source_calendar_id:
            return jsonify({"error": "Source calendar not found"})
        
        # Get events with extended range
        start_time = datetime.now(pytz.UTC) - timedelta(days=30)
        end_time = start_time + timedelta(days=400)
        
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/calendarView"
        params = {
            'startDateTime': start_time.isoformat(),
            'endDateTime': end_time.isoformat(),
            '$top': 500
        }
        
        events_response = requests.get(events_url, headers=headers, params=params)
        events_data = events_response.json()
        all_events = events_data.get('value', [])
        
        # Find all Mass events and get unique subjects
        mass_events = []
        unique_subjects = set()
        
        for event in all_events:
            subject = event.get('subject', '')
            if 'Mass' in subject and subject not in unique_subjects:
                unique_subjects.add(subject)
                mass_events.append({
                    "subject": subject,
                    "start": event.get('start', {}).get('dateTime'),
                    "type": event.get('type'),
                    "categories": event.get('categories', []),
                    "is_public": "Public" in event.get('categories', [])
                })
        
        # Sort by subject
        mass_events.sort(key=lambda x: x['subject'])
        
        return jsonify({
            "total_events_searched": len(all_events),
            "unique_mass_subjects": sorted(list(unique_subjects)),
            "mass_events_details": mass_events,
            "unique_mass_count": len(unique_subjects)
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Search failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/search-master-events')
def search_master_events():
    """Search for master recurring events (not instances)"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        source_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
                break
        
        if not source_calendar_id:
            return jsonify({"error": "Source calendar not found"})
        
        # Search for EVENTS (not calendarView) to get master recurring events
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        params = {
            '$top': 200,
            '$filter': "type eq 'seriesMaster'"  # Only get recurring master events
        }
        
        events_response = requests.get(events_url, headers=headers, params=params)
        events_data = events_response.json()
        master_events = events_data.get('value', [])
        
        # Process master events
        all_masters = []
        mass_masters = []
        
        for event in master_events:
            subject = event.get('subject', '')
            event_info = {
                "subject": subject,
                "type": event.get('type'),
                "categories": event.get('categories', []),
                "is_public": "Public" in event.get('categories', []),
                "start": event.get('start', {}).get('dateTime', 'No start time'),
                "recurrence": event.get('recurrence', {})
            }
            
            all_masters.append(event_info)
            
            if 'Mass' in subject:
                mass_masters.append(event_info)
        
        return jsonify({
            "total_master_events": len(all_masters),
            "all_master_subjects": [e['subject'] for e in all_masters],
            "mass_master_events": mass_masters,
            "mass_master_count": len(mass_masters)
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Search failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/debug-sync-events')
def debug_sync_events():
    """Debug: Show exactly what events the sync function will process"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        source_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
                break
        
        if not source_calendar_id:
            return jsonify({"error": "Source calendar not found"})
        
        # Get ALL events from source (same as sync function)
        source_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        source_params = {'$top': 200}
        
        source_response = requests.get(source_events_url, headers=headers, params=source_params)
        source_events = source_response.json().get('value', [])
        
        # Process events exactly like the sync function does
        all_events = []
        public_events_will_sync = []
        
        for event in source_events:
            event_info = {
                "subject": event.get('subject'),
                "type": event.get('type'),
                "categories": event.get('categories', []),
                "is_public": "Public" in event.get('categories', []),
                "start": event.get('start', {}).get('dateTime', 'No start'),
                "seriesMasterId": event.get('seriesMasterId'),
                "will_sync": False,
                "sync_reason": ""
            }
            
            # Apply the same logic as sync function
            categories = event.get('categories', [])
            if 'Public' in categories:
                event_type = event.get('type', 'unknown')
                if event_type == 'seriesMaster':
                    event_info["will_sync"] = True
                    event_info["sync_reason"] = "Recurring master event"
                    public_events_will_sync.append(event_info)
                elif event_type == 'singleInstance' and not event.get('seriesMasterId'):
                    event_info["will_sync"] = True
                    event_info["sync_reason"] = "True single event (no series master)"
                    public_events_will_sync.append(event_info)
                elif event_type == 'occurrence':
                    event_info["sync_reason"] = "Skipped - recurring instance"
                elif event_type == 'singleInstance' and event.get('seriesMasterId'):
                    event_info["sync_reason"] = "Skipped - part of recurring series"
                else:
                    event_info["sync_reason"] = f"Skipped - unknown type '{event_type}'"
            else:
                event_info["sync_reason"] = "Not public"
            
            all_events.append(event_info)
        
        # Look for duplicates
        subjects_count = {}
        for event in public_events_will_sync:
            subject = event["subject"]
            if subject in subjects_count:
                subjects_count[subject] += 1
            else:
                subjects_count[subject] = 1
        
        duplicates = {k: v for k, v in subjects_count.items() if v > 1}
        
        return jsonify({
            "total_events_in_source": len(all_events),
            "will_sync_count": len(public_events_will_sync),
            "events_that_will_sync": public_events_will_sync,
            "all_events_debug": all_events,
            "duplicate_subjects": duplicates,
            "analysis": {
                "mass_8am_events": [e for e in all_events if "8:00" in e["subject"] or "8am" in e["subject"].lower()],
                "totus_tuus_events": [e for e in all_events if "totus" in e["subject"].lower()]
            }
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Debug failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/debug-undeletable-events')
def debug_undeletable_events():
    """Debug: Analyze events in public calendar that might be causing delete issues"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar 'St. Edward Public Calendar' not found"})
        
        # Get ALL events from public calendar (not just calendarView)
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {
            '$top': 500,  # Get lots of events
            '$select': 'id,subject,start,end,type,organizer,createdBy,lastModifiedBy,isOrganizer,responseStatus,webLink,categories,recurrence,seriesMasterId'
        }
        
        events_response = requests.get(events_url, headers=headers, params=params)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get target events: {events_response.status_code} - {events_response.text}"})
        
        target_events = events_response.json().get('value', [])
        
        # Analyze each event for potential delete issues
        problematic_events = []
        normal_events = []
        
        for event in target_events:
            event_analysis = {
                "id": event.get('id'),
                "subject": event.get('subject'),
                "type": event.get('type'),
                "start": event.get('start', {}).get('dateTime', 'No start'),
                "organizer": event.get('organizer', {}).get('emailAddress', {}).get('address', 'Unknown'),
                "isOrganizer": event.get('isOrganizer', False),
                "createdBy": event.get('createdBy', {}).get('emailAddress', {}).get('address', 'Unknown'),
                "lastModifiedBy": event.get('lastModifiedBy', {}).get('emailAddress', {}).get('address', 'Unknown'),
                "categories": event.get('categories', []),
                "seriesMasterId": event.get('seriesMasterId'),
                "recurrence": event.get('recurrence') is not None,
                "potential_issues": []
            }
            
            # Check for potential delete issues
            
            # Issue 1: Not the organizer
            if not event_analysis["isOrganizer"]:
                event_analysis["potential_issues"].append("Not the organizer of this event")
            
            # Issue 2: Different creator
            creator = event_analysis["createdBy"]
            if creator != "calendar@stedward.org" and creator != "Unknown":
                event_analysis["potential_issues"].append(f"Created by different user: {creator}")
            
            # Issue 3: Recurring event instance (not master)
            if event_analysis["type"] == "occurrence":
                event_analysis["potential_issues"].append("This is a recurring event instance - delete the master instead")
            
            # Issue 4: External organizer
            organizer = event_analysis["organizer"]
            if organizer != "calendar@stedward.org" and organizer != "Unknown":
                event_analysis["potential_issues"].append(f"External organizer: {organizer}")
            
            # Issue 5: System-generated events
            subject = event_analysis["subject"] or ""
            if any(keyword in subject.lower() for keyword in ["cancelled:", "updated:", "fw:", "re:"]):
                event_analysis["potential_issues"].append("Appears to be system-generated or forwarded event")
            
            if event_analysis["potential_issues"]:
                problematic_events.append(event_analysis)
            else:
                normal_events.append(event_analysis)
        
        # Test deletion on a few problematic events (just test, don't actually delete)
        deletion_test_results = []
        for event in problematic_events[:3]:  # Test first 3 problematic events
            test_result = {
                "event_id": event["id"],
                "subject": event["subject"],
                "can_delete": False,
                "error_message": None
            }
            
            # Try to get detailed permissions on this specific event
            try:
                event_detail_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event['id']}"
                detail_response = requests.get(event_detail_url, headers=headers)
                
                if detail_response.status_code == 200:
                    test_result["can_delete"] = True
                    test_result["error_message"] = "Event is accessible - deletion should work"
                else:
                    test_result["error_message"] = f"Cannot access event: {detail_response.status_code}"
                    
            except Exception as e:
                test_result["error_message"] = f"Access test failed: {str(e)}"
            
            deletion_test_results.append(test_result)
        
        return jsonify({
            "total_events_in_public_calendar": len(target_events),
            "problematic_events_count": len(problematic_events),
            "normal_events_count": len(normal_events),
            "problematic_events": problematic_events,
            "deletion_test_results": deletion_test_results,
            "summary": {
                "most_common_issues": {
                    "not_organizer": len([e for e in problematic_events if "Not the organizer" in str(e["potential_issues"])]),
                    "external_creator": len([e for e in problematic_events if "Created by different user" in str(e["potential_issues"])]),
                    "recurring_instances": len([e for e in problematic_events if "recurring event instance" in str(e["potential_issues"])]),
                    "external_organizer": len([e for e in problematic_events if "External organizer" in str(e["potential_issues"])])
                }
            },
            "recommendations": [
                "If events have external organizers, you may need to decline/remove them instead of deleting",
                "For recurring event instances, delete the series master instead",
                "Events created by other users may require different permissions",
                "Some events might be read-only due to calendar sharing settings"
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Debug failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/debug-undeletable-events')
def debug_undeletable_events():
    """Debug: Analyze events in public calendar that might be causing delete issues"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get calendars
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar 'St. Edward Public Calendar' not found"})
        
        # Get ALL events from public calendar (not just calendarView)
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {
            '$top': 500,  # Get lots of events
            '$select': 'id,subject,start,end,type,organizer,isOrganizer,responseStatus,webLink,categories,recurrence,seriesMasterId,createdDateTime,lastModifiedDateTime'
        }
        
        events_response = requests.get(events_url, headers=headers, params=params)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get target events: {events_response.status_code} - {events_response.text}"})
        
        target_events = events_response.json().get('value', [])
        
        # Analyze each event for potential delete issues
        problematic_events = []
        normal_events = []
        
        for event in target_events:
            event_analysis = {
                "id": event.get('id'),
                "subject": event.get('subject'),
                "type": event.get('type'),
                "start": event.get('start', {}).get('dateTime', 'No start'),
                "organizer": event.get('organizer', {}).get('emailAddress', {}).get('address', 'Unknown'),
                "isOrganizer": event.get('isOrganizer', False),
                "createdDateTime": event.get('createdDateTime', 'Unknown'),
                "lastModifiedDateTime": event.get('lastModifiedDateTime', 'Unknown'),
                "categories": event.get('categories', []),
                "seriesMasterId": event.get('seriesMasterId'),
                "recurrence": event.get('recurrence') is not None,
                "potential_issues": []
            }
            
            # Check for potential delete issues
            
            # Issue 1: Not the organizer
            if not event_analysis["isOrganizer"]:
                event_analysis["potential_issues"].append("Not the organizer of this event")
            
            # Issue 2: Old events (might have different permissions)
            if event_analysis["createdDateTime"] != "Unknown":
                try:
                    from datetime import datetime, timedelta
                    created_date = datetime.fromisoformat(event_analysis["createdDateTime"].replace('Z', '+00:00'))
                    if created_date < datetime.now().replace(tzinfo=created_date.tzinfo) - timedelta(days=90):
                        event_analysis["potential_issues"].append(f"Old event (created {created_date.strftime('%Y-%m-%d')})")
                except:
                    pass
            
            # Issue 3: Recurring event instance (not master)
            if event_analysis["type"] == "occurrence":
                event_analysis["potential_issues"].append("This is a recurring event instance - delete the master instead")
            
            # Issue 4: External organizer
            organizer = event_analysis["organizer"]
            if organizer != "calendar@stedward.org" and organizer != "Unknown":
                event_analysis["potential_issues"].append(f"External organizer: {organizer}")
            
            # Issue 5: System-generated events
            subject = event_analysis["subject"] or ""
            if any(keyword in subject.lower() for keyword in ["cancelled:", "updated:", "fw:", "re:"]):
                event_analysis["potential_issues"].append("Appears to be system-generated or forwarded event")
            
            if event_analysis["potential_issues"]:
                problematic_events.append(event_analysis)
            else:
                normal_events.append(event_analysis)
        
        # Test deletion on a few problematic events (just test, don't actually delete)
        deletion_test_results = []
        for event in problematic_events[:3]:  # Test first 3 problematic events
            test_result = {
                "event_id": event["id"],
                "subject": event["subject"],
                "can_delete": False,
                "error_message": None
            }
            
            # Try to get detailed permissions on this specific event
            try:
                event_detail_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event['id']}"
                detail_response = requests.get(event_detail_url, headers=headers)
                
                if detail_response.status_code == 200:
                    test_result["can_delete"] = True
                    test_result["error_message"] = "Event is accessible - deletion should work"
                else:
                    test_result["error_message"] = f"Cannot access event: {detail_response.status_code}"
                    
            except Exception as e:
                test_result["error_message"] = f"Access test failed: {str(e)}"
            
            deletion_test_results.append(test_result)
        
        return jsonify({
            "total_events_in_public_calendar": len(target_events),
            "problematic_events_count": len(problematic_events),
            "normal_events_count": len(normal_events),
            "problematic_events": problematic_events,
            "deletion_test_results": deletion_test_results,
            "summary": {
                "most_common_issues": {
                    "not_organizer": len([e for e in problematic_events if "Not the organizer" in str(e["potential_issues"])]),
                    "external_creator": len([e for e in problematic_events if "Created by different user" in str(e["potential_issues"])]),
                    "recurring_instances": len([e for e in problematic_events if "recurring event instance" in str(e["potential_issues"])]),
                    "external_organizer": len([e for e in problematic_events if "External organizer" in str(e["potential_issues"])])
                }
            },
            "recommendations": [
                "If events have external organizers, you may need to decline/remove them instead of deleting",
                "For recurring event instances, delete the series master instead",
                "Events created by other users may require different permissions",
                "Some events might be read-only due to calendar sharing settings"
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Debug failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/logout')
def logout():
    """Clear authentication"""
    global access_token
    access_token = None
    stop_scheduler()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
