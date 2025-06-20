#!/usr/bin/env python3
"""
Calendar Sync Web Application for Render.com
Simple web interface for rcarroll + automatic hourly sync + diagnostics
FRESH VERSION with all debug functions included
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

@app.route('/debug-problem-events')
def debug_problem_events():
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
                    "old_events": len([e for e in problematic_events if "Old event" in str(e["potential_issues"])]),
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

@app.route('/force-delete-event/<event_id>')
def force_delete_event(event_id):
    """Try multiple methods to delete a specific event"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        results = []
        
        # Method 1: Standard DELETE
        try:
            delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}"
            delete_response = requests.delete(delete_url, headers=headers)
            results.append({
                "method": "Standard DELETE",
                "status_code": delete_response.status_code,
                "success": delete_response.status_code in [200, 204],
                "response": delete_response.text[:200] if delete_response.text else "No response body"
            })
        except Exception as e:
            results.append({
                "method": "Standard DELETE",
                "success": False,
                "error": str(e)
            })
        
        # Method 2: Try to get event details first
        try:
            get_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}"
            get_response = requests.get(get_url, headers=headers)
            if get_response.status_code == 200:
                event_data = get_response.json()
                results.append({
                    "method": "Get Event Details",
                    "success": True,
                    "event_type": event_data.get('type'),
                    "organizer": event_data.get('organizer', {}).get('emailAddress', {}).get('address'),
                    "isOrganizer": event_data.get('isOrganizer'),
                    "seriesMasterId": event_data.get('seriesMasterId')
                })
                
                # If it's a recurring instance, try to delete the master
                if event_data.get('seriesMasterId'):
                    master_delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_data.get('seriesMasterId')}"
                    master_delete_response = requests.delete(master_delete_url, headers=headers)
                    results.append({
                        "method": "Delete Series Master",
                        "status_code": master_delete_response.status_code,
                        "success": master_delete_response.status_code in [200, 204],
                        "response": master_delete_response.text[:200] if master_delete_response.text else "No response body"
                    })
            else:
                results.append({
                    "method": "Get Event Details",
                    "success": False,
                    "status_code": get_response.status_code,
                    "error": get_response.text[:200]
                })
        except Exception as e:
            results.append({
                "method": "Get Event Details",
                "success": False,
                "error": str(e)
            })
        
        # Method 3: Try canceling instead of deleting
        try:
            cancel_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}/cancel"
            cancel_response = requests.post(cancel_url, headers=headers)
            results.append({
                "method": "Cancel Event",
                "status_code": cancel_response.status_code,
                "success": cancel_response.status_code in [200, 202],
                "response": cancel_response.text[:200] if cancel_response.text else "No response body"
            })
        except Exception as e:
            results.append({
                "method": "Cancel Event",
                "success": False,
                "error": str(e)
            })
        
        return jsonify({
            "event_id": event_id,
            "attempted_methods": results,
            "overall_success": any(r.get("success", False) for r in results)
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Force delete failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/clean-duplicates')
def clean_duplicates():
    """Find and remove duplicate events from public calendar"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Get ALL events from public calendar
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {'$top': 500}
        
        events_response = requests.get(events_url, headers=headers, params=params)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get events: {events_response.status_code}"})
        
        all_events = events_response.json().get('value', [])
        
        # Group events by subject and start date to find duplicates
        event_groups = {}
        for event in all_events:
            subject = event.get('subject', 'No Subject')
            start_time = event.get('start', {}).get('dateTime', 'No Start')
            event_type = event.get('type', 'unknown')
            
            # Create a key for grouping
            # For recurring events, use just subject
            # For single events, use subject + start date
            if event_type == 'seriesMaster':
                key = f"recurring:{subject}"
            else:
                # Extract just the date part for single events
                start_date = start_time.split('T')[0] if 'T' in start_time else start_time
                key = f"single:{subject}:{start_date}"
            
            if key not in event_groups:
                event_groups[key] = []
            
            event_groups[key].append({
                'id': event.get('id'),
                'subject': subject,
                'start': start_time,
                'type': event_type,
                'created': event.get('createdDateTime'),
                'modified': event.get('lastModifiedDateTime')
            })
        
        # Find groups with duplicates
        duplicates_found = []
        events_to_delete = []
        
        for key, events in event_groups.items():
            if len(events) > 1:
                # Sort by creation date - keep the oldest, delete the rest
                events.sort(key=lambda x: x.get('created', ''))
                
                keep_event = events[0]  # Keep the first (oldest) one
                delete_events = events[1:]  # Delete the rest
                
                duplicates_found.append({
                    'group_key': key,
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
            "warning": "This will permanently delete the duplicate events. Make sure you review the list above first!"
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Duplicate analysis failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/execute-duplicate-cleanup')
def execute_duplicate_cleanup():
    """Actually delete the duplicate events (DANGEROUS - USE WITH CAUTION)"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        # First run the analysis again to get current duplicates
        analysis_result = clean_duplicates()
        if 'error' in analysis_result.get_json():
            return analysis_result
        
        analysis = analysis_result.get_json()
        events_to_delete = analysis.get('deletion_preview', [])
        
        if not events_to_delete:
            return jsonify({"message": "No duplicates found to delete", "deleted_count": 0})
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID again
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Delete the duplicate events
        deletion_results = []
        successful_deletions = 0
        
        for event_id in events_to_delete:
            try:
                delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}"
                delete_response = requests.delete(delete_url, headers=headers)
                
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
            "recommendation": "Run another sync to ensure calendar is clean, then re-enable automatic sync"
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Cleanup execution failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/stop-scheduler')
def stop_scheduler_endpoint():
    """Manually stop the automatic scheduler"""
    stop_scheduler()
    return jsonify({"message": "Automatic sync scheduler stopped", "scheduler_running": False})

@app.route('/start-scheduler')  
def start_scheduler_endpoint():
    """Manually start the automatic scheduler"""
    start_scheduler()
    return jsonify({"message": "Automatic sync scheduler started", "scheduler_running": True})

@app.route('/logout')
def logout():
    """Clear authentication"""
    global access_token
    access_token = None
    stop_scheduler()
    return redirect(url_for('index'))

# Add these endpoints to your app.py file (paste them before the "if __name__ == '__main__':" line)

@app.route('/list-all-events')
def list_all_events():
    """List all events in public calendar with full details for manual inspection"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Get ALL events with full details
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {'$top': 500}
        
        events_response = requests.get(events_url, headers=headers, params=params)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get events: {events_response.status_code}"})
        
        all_events = events_response.json().get('value', [])
        
        # Format events for easy reading
        formatted_events = []
        for i, event in enumerate(all_events, 1):
            formatted_event = {
                "number": i,
                "id": event.get('id'),
                "subject": event.get('subject'),
                "start": event.get('start', {}).get('dateTime', 'No start time'),
                "type": event.get('type'),
                "categories": event.get('categories', []),
                "organizer_email": event.get('organizer', {}).get('emailAddress', {}).get('address', 'Unknown'),
                "isOrganizer": event.get('isOrganizer', False),
                "created": event.get('createdDateTime', 'Unknown'),
                "modified": event.get('lastModifiedDateTime', 'Unknown'),
                "recurrence": "Yes" if event.get('recurrence') else "No",
                "seriesMasterId": event.get('seriesMasterId', 'None'),
                "webLink": event.get('webLink', 'No link'),
                "delete_test_url": f"/force-delete-event/{event.get('id')}"
            }
            formatted_events.append(formatted_event)
        
        # Sort by start date
        formatted_events.sort(key=lambda x: x['start'] if x['start'] != 'No start time' else '9999')
        
        return jsonify({
            "total_events": len(formatted_events),
            "calendar_name": "St. Edward Public Calendar",
            "events": formatted_events,
            "instructions": [
                "Look through this list to identify events you're having trouble deleting",
                "Copy the event ID of any problematic event",
                "Visit /force-delete-event/EVENT_ID to attempt deletion",
                "Pay attention to 'type' field - 'occurrence' events need master deleted instead"
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Failed to list events: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/find-visual-duplicates')
def find_visual_duplicates():
    """Find events that LOOK like duplicates to users (more aggressive matching)"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Get ALL events from public calendar
        events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        params = {'$top': 500}
        
        events_response = requests.get(events_url, headers=headers, params=params)
        if events_response.status_code != 200:
            return jsonify({"error": f"Failed to get events: {events_response.status_code}"})
        
        all_events = events_response.json().get('value', [])
        
        # Group by SUBJECT ONLY (most aggressive duplicate detection)
        subject_groups = {}
        for event in all_events:
            subject = (event.get('subject', 'No Subject') or 'No Subject').strip()
            
            if subject not in subject_groups:
                subject_groups[subject] = []
            
            subject_groups[subject].append({
                'id': event.get('id'),
                'subject': subject,
                'start': event.get('start', {}).get('dateTime', 'No Start'),
                'type': event.get('type', 'unknown'),
                'created': event.get('createdDateTime', 'Unknown'),
                'categories': event.get('categories', []),
                'seriesMasterId': event.get('seriesMasterId')
            })
        
        # Find ANY subject that appears more than once
        visual_duplicates = []
        all_duplicates_to_delete = []
        
        for subject, events in subject_groups.items():
            if len(events) > 1:
                print(f"Found {len(events)} events with subject: '{subject}'")
                
                # Sort by type preference: seriesMaster > singleInstance > occurrence
                # Then by creation date (oldest first)
                def sort_key(event):
                    type_priority = {
                        'seriesMaster': 1,
                        'singleInstance': 2, 
                        'occurrence': 3,
                        'unknown': 4
                    }
                    return (type_priority.get(event['type'], 5), event['created'])
                
                events.sort(key=sort_key)
                
                keep_event = events[0]  # Keep the "best" one
                delete_events = events[1:]  # Delete the rest
                
                visual_duplicates.append({
                    'subject': subject,
                    'total_count': len(events),
                    'keeping': {
                        'id': keep_event['id'],
                        'type': keep_event['type'],
                        'start': keep_event['start'],
                        'created': keep_event['created']
                    },
                    'deleting': [{
                        'id': e['id'],
                        'type': e['type'], 
                        'start': e['start'],
                        'created': e['created']
                    } for e in delete_events],
                    'delete_count': len(delete_events)
                })
                
                all_duplicates_to_delete.extend([e['id'] for e in delete_events])
        
        # Also show all events for manual review
        all_events_summary = []
        for event in all_events:
            all_events_summary.append({
                'subject': event.get('subject'),
                'type': event.get('type'),
                'start': event.get('start', {}).get('dateTime', '').split('T')[0] if event.get('start', {}).get('dateTime') else 'No date',
                'time': event.get('start', {}).get('dateTime', '').split('T')[1][:5] if 'T' in event.get('start', {}).get('dateTime', '') else 'No time',
                'id_short': event.get('id', '')[:8] + '...'
            })
        
        # Sort by subject for easy reading
        all_events_summary.sort(key=lambda x: x['subject'] or 'zzz')
        
        return jsonify({
            "analysis_type": "Visual duplicates (same subject)",
            "total_events_in_calendar": len(all_events),
            "subjects_with_duplicates": len(visual_duplicates),
            "total_events_to_delete": len(all_duplicates_to_delete),
            "visual_duplicates": visual_duplicates,
            "all_events_list": all_events_summary,
            "events_to_delete_ids": all_duplicates_to_delete,
            "next_step": "Review the duplicates above, then visit /execute-visual-duplicate-cleanup"
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Visual duplicate analysis failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/execute-visual-duplicate-cleanup')
def execute_visual_duplicate_cleanup():
    """Delete the visual duplicates found by the enhanced finder"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        # First run the analysis to get current duplicates
        analysis_result = find_visual_duplicates()
        if 'error' in analysis_result.get_json():
            return analysis_result
        
        analysis = analysis_result.get_json()
        events_to_delete = analysis.get('events_to_delete_ids', [])
        
        if not events_to_delete:
            return jsonify({"message": "No visual duplicates found to delete", "deleted_count": 0})
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Delete the duplicate events
        deletion_results = []
        successful_deletions = 0
        
        for event_id in events_to_delete:
            try:
                delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}"
                delete_response = requests.delete(delete_url, headers=headers)
                
                success = delete_response.status_code in [200, 204]
                if success:
                    successful_deletions += 1
                
                deletion_results.append({
                    "event_id": event_id[:8] + "...",
                    "status_code": delete_response.status_code,
                    "success": success,
                    "error": None if success else delete_response.text[:100]
                })
                
                print(f"{'✅' if success else '❌'} Delete {event_id[:8]}... - Status: {delete_response.status_code}")
                
            except Exception as e:
                deletion_results.append({
                    "event_id": event_id[:8] + "...",
                    "success": False,
                    "error": str(e)
                })
                print(f"❌ Error deleting {event_id[:8]}...: {str(e)}")
        
        return jsonify({
            "cleanup_completed": True,
            "total_attempted": len(events_to_delete),
            "successful_deletions": successful_deletions,
            "failed_deletions": len(events_to_delete) - successful_deletions,
            "deletion_details": deletion_results,
            "next_steps": [
                "Check your public calendar to see if duplicates are gone",
                "Wait a few minutes for changes to propagate",
                "Run a manual sync to repopulate any missing events",
                "Re-enable automatic sync if everything looks good"
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Visual cleanup execution failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/find-orphaned-events')
def find_orphaned_events():
    """Find events that exist in public calendar but not in source calendar"""
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
        target_calendar_id = None
        
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'Calendar':
                source_calendar_id = calendar.get('id')
            elif calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
        
        if not source_calendar_id or not target_calendar_id:
            return jsonify({"error": "Required calendars not found"})
        
        # Get source events
        source_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{source_calendar_id}/events"
        source_params = {'$top': 200}
        source_response = requests.get(source_events_url, headers=headers, params=source_params)
        source_events = source_response.json().get('value', [])
        
        # Get target events
        target_events_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events"
        target_params = {'$top': 200}
        target_response = requests.get(target_events_url, headers=headers, params=target_params)
        target_events = target_response.json().get('value', [])
        
        # Build list of public events that SHOULD exist (from source)
        should_exist = set()
        source_event_details = []
        
        for event in source_events:
            categories = event.get('categories', [])
            if 'Public' in categories:
                event_type = event.get('type', 'unknown')
                subject = event.get('subject', '')
                
                # Only include masters and true single events
                if event_type == 'seriesMaster':
                    should_exist.add(f"recurring:{subject}")
                elif event_type == 'singleInstance' and not event.get('seriesMasterId'):
                    start_date = event.get('start', {}).get('dateTime', 'no-date')
                    if 'T' in start_date:
                        start_date = start_date.split('T')[0]
                    should_exist.add(f"single:{subject}:{start_date}")
                
                source_event_details.append({
                    'subject': subject,
                    'type': event_type,
                    'should_sync': True
                })
        
        # Check what actually exists in target
        actually_exists = []
        orphaned_events = []
        
        for event in target_events:
            subject = event.get('subject', '')
            event_type = event.get('type', 'unknown')
            event_id = event.get('id')
            
            # Create the same key format
            if event_type == 'seriesMaster':
                key = f"recurring:{subject}"
            else:
                start_date = event.get('start', {}).get('dateTime', 'no-date')
                if 'T' in start_date:
                    start_date = start_date.split('T')[0]
                key = f"single:{subject}:{start_date}"
            
            actually_exists.append({
                'key': key,
                'subject': subject,
                'type': event_type,
                'id': event_id,
                'should_exist': key in should_exist
            })
            
            # If this event is NOT in the should_exist list, it's orphaned
            if key not in should_exist:
                orphaned_events.append({
                    'subject': subject,
                    'type': event_type,
                    'id': event_id,
                    'key': key,
                    'start': event.get('start', {}).get('dateTime', 'No start'),
                    'created': event.get('createdDateTime', 'Unknown'),
                    'delete_url': f"/force-delete-event/{event_id}"
                })
        
        return jsonify({
            "analysis": "Orphaned events (exist in public but not in source)",
            "source_public_events": len([e for e in source_event_details if e['should_sync']]),
            "target_events_total": len(target_events),
            "should_exist_count": len(should_exist),
            "orphaned_events_found": len(orphaned_events),
            "orphaned_events": orphaned_events,
            "source_events_that_should_sync": source_event_details,
            "all_target_events": actually_exists,
            "next_step": "Review orphaned events above, then visit /cleanup-orphaned-events to delete them"
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Orphan analysis failed: {str(e)}", "traceback": traceback.format_exc()}), 500

@app.route('/cleanup-orphaned-events')
def cleanup_orphaned_events():
    """Delete orphaned events that exist in public but not in source"""
    try:
        if not access_token:
            return jsonify({"error": "Not authenticated"}), 401
        
        # First get the orphaned events
        orphan_analysis = find_orphaned_events()
        if 'error' in orphan_analysis.get_json():
            return orphan_analysis
        
        analysis = orphan_analysis.get_json()
        orphaned_events = analysis.get('orphaned_events', [])
        
        if not orphaned_events:
            return jsonify({"message": "No orphaned events found to delete", "deleted_count": 0})
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Get target calendar ID
        calendars_url = "https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars"
        calendars_response = requests.get(calendars_url, headers=headers)
        calendars_data = calendars_response.json()
        
        target_calendar_id = None
        for calendar in calendars_data.get('value', []):
            if calendar.get('name') == 'St. Edward Public Calendar':
                target_calendar_id = calendar.get('id')
                break
        
        if not target_calendar_id:
            return jsonify({"error": "Target calendar not found"})
        
        # Delete the orphaned events
        deletion_results = []
        successful_deletions = 0
        
        for orphan in orphaned_events:
            event_id = orphan['id']
            subject = orphan['subject']
            
            try:
                delete_url = f"https://graph.microsoft.com/v1.0/users/calendar@stedward.org/calendars/{target_calendar_id}/events/{event_id}"
                delete_response = requests.delete(delete_url, headers=headers)
                
                success = delete_response.status_code in [200, 204]
                if success:
                    successful_deletions += 1
                
                deletion_results.append({
                    "subject": subject,
                    "event_id": event_id[:8] + "...",
                    "status_code": delete_response.status_code,
                    "success": success,
                    "error": None if success else delete_response.text[:100]
                })
                
                print(f"{'✅' if success else '❌'} Delete orphaned '{subject}' - Status: {delete_response.status_code}")
                
            except Exception as e:
                deletion_results.append({
                    "subject": subject,
                    "event_id": event_id[:8] + "...",
                    "success": False,
                    "error": str(e)
                })
                print(f"❌ Error deleting orphaned '{subject}': {str(e)}")
        
        return jsonify({
            "cleanup_completed": True,
            "total_orphaned_events": len(orphaned_events),
            "successful_deletions": successful_deletions,
            "failed_deletions": len(orphaned_events) - successful_deletions,
            "deletion_details": deletion_results,
            "recommendation": "Run a manual sync to ensure everything is properly synchronized",
            "next_steps": [
                "Check your public calendar to verify orphaned events are gone",
                "Run a manual sync to repopulate any legitimate events",
                "The automatic hourly sync will keep things clean going forward"
            ]
        })
        
    except Exception as e:
        import traceback
        return jsonify({"error": f"Orphan cleanup failed: {str(e)}", "traceback": traceback.format_exc()}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
