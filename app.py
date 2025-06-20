#!/usr/bin/env python3
"""
Calendar Sync Web Application for Render.com
Simple web interface for rcarroll + automatic hourly sync + diagnostics
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
from azure.identity import AuthorizationCodeCredential
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
                        "id": cal.id,
                        "name": cal.name,
                        "owner": getattr(cal, 'owner', 'Unknown'),
                        "can_edit": getattr(cal, 'can_edit', 'Unknown')
                    })
                
                diagnostics["findings"]["available_calendars"] = available_calendars
                diagnostics["findings"]["calendar_count"] = len(available_calendars)
                
                print(f"📋 Found {len(available_calendars)} calendars:")
                for cal in available_calendars:
                    print(f"   - '{cal['name']}' (ID: {cal['id'][:20]}...)")
                
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

def parse_date(event):
    """Extract date from event"""
    try:
        if hasattr(event, 'start'):
            if hasattr(event.start, 'date') and event.start.date:
                return datetime.strptime(event.start.date, '%Y-%m-%d')
            elif hasattr(event.start, 'date_time') and event.start.date_time:
                dt_str = event.start.date_time
                if '.' in dt_str:
                    dt_str = dt_str.split('.')[0]
                return datetime.strptime(dt_str, '%Y-%m-%dT%H:%M:%S')
    except Exception as e:
        print(f"Date parse error: {e}")
    return None

def create_event_signature(event):
    """Create a detailed signature for event comparison"""
    sig_parts = []
    
    # Subject
    sig_parts.append(f"subject:{event.subject}")
    
    # For recurring events, use the series master ID or recurrence pattern
    if hasattr(event, 'recurrence') and event.recurrence:
        sig_parts.append(f"recurring:true")
        # Add recurrence pattern to signature
        if hasattr(event.recurrence, 'pattern'):
            sig_parts.append(f"pattern:{event.recurrence.pattern}")
    else:
        # Start time for non-recurring events
        if hasattr(event, 'start'):
            if hasattr(event.start, 'date'):
                sig_parts.append(f"start_date:{event.start.date}")
            elif hasattr(event.start, 'date_time'):
                sig_parts.append(f"start_datetime:{event.start.date_time}")
        
        # End time for non-recurring events
        if hasattr(event, 'end'):
            if hasattr(event.end, 'date'):
                sig_parts.append(f"end_date:{event.end.date}")
            elif hasattr(event.end, 'date_time'):
                sig_parts.append(f"end_datetime:{event.end.date_time}")
    
    # Location
    if hasattr(event, 'location') and event.location:
        if hasattr(event.location, 'display_name'):
            sig_parts.append(f"location:{event.location.display_name}")
    
    # Note: Excluding body/meeting details for privacy - not included in signature
    
    return "|".join(sig_parts)

async def get_shared_calendars(client):
    """Get calendars directly from shared mailbox"""
    try:
        calendars = await client.users.by_user_id(SHARED_MAILBOX).calendars.get()
        return calendars.value
    except Exception as e:
        print(f"Error accessing shared mailbox: {e}")
        return []

async def get_events_from_shared(client, calendar_id):
    """Get events from a shared mailbox calendar"""
    try:
        result = await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(calendar_id).events.get()
        return result.value if result else []
    except Exception as e:
        print(f"Error getting events: {e}")
        return []

async def create_event_in_shared(client, calendar_id, event):
    """Create event in shared mailbox calendar"""
    try:
        new_event = Event(
            subject=event.subject,
            # Don't set body field to avoid copying meeting details
            start=event.start,
            end=event.end,
            location=event.location,
            categories=["Public"],
            is_all_day=event.is_all_day if hasattr(event, 'is_all_day') else False,
            is_reminder_on=event.is_reminder_on if hasattr(event, 'is_reminder_on') else False,
            importance=event.importance if hasattr(event, 'importance') else None,
            sensitivity=event.sensitivity if hasattr(event, 'sensitivity') else None,
            show_as=event.show_as if hasattr(event, 'show_as') else None,
            # Copy recurrence pattern if it exists
            recurrence=event.recurrence if hasattr(event, 'recurrence') and event.recurrence else None
        )
        
        created = await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(calendar_id).events.post(new_event)
        return created
    except Exception as e:
        print(f"Error creating event: {e}")
        return None

async def update_event_in_shared(client, calendar_id, event_id, source_event):
    """Update an existing event in shared mailbox calendar"""
    try:
        updated_event = Event(
            subject=source_event.subject,
            start=source_event.start,
            end=source_event.end,
            location=source_event.location,
            categories=["Public"],
            is_all_day=source_event.is_all_day if hasattr(source_event, 'is_all_day') else False,
            is_reminder_on=source_event.is_reminder_on if hasattr(source_event, 'is_reminder_on') else False,
            importance=source_event.importance if hasattr(source_event, 'importance') else None,
            sensitivity=source_event.sensitivity if hasattr(source_event, 'sensitivity') else None,
            show_as=source_event.show_as if hasattr(source_event, 'show_as') else None,
            # Copy recurrence pattern if it exists
            recurrence=source_event.recurrence if hasattr(source_event, 'recurrence') and source_event.recurrence else None
        )
        
        # Don't set body at all - let it default to empty
        
        await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(calendar_id).events.by_event_id(event_id).patch(updated_event)
        return True
    except Exception as e:
        print(f"Error updating event: {e}")
        return False

async def delete_event_from_shared(client, calendar_id, event_id):
    """Delete event from shared mailbox calendar"""
    try:
        await client.users.by_user_id(SHARED_MAILBOX).calendars.by_calendar_id(calendar_id).events.by_event_id(event_id).delete()
        return True
    except Exception as e:
        print(f"Error deleting event: {e}")
        return False

async def sync_calendars():
    """Main sync function with full synchronization"""
    global last_sync_time, last_sync_result, sync_in_progress
    
    if sync_in_progress:
        return {"success": False, "message": "Sync already in progress"}
    
    if not access_token:
        return {"success": False, "message": "Not authenticated - please sign in first"}
    
    sync_in_progress = True
    print(f"Starting calendar sync at {datetime.now()}")
    
    try:
        # Create Graph client
        client = create_graph_client()
        if not client:
            result = {"success": False, "message": "Authentication failed"}
            last_sync_result = result
            return result
        
        # Get shared mailbox calendars
        calendars = await get_shared_calendars(client)
        if not calendars:
            result = {"success": False, "message": "Could not access calendars"}
            last_sync_result = result
            return result
        
        # Find source and target
        source_cal = None
        target_cal = None
        
        for cal in calendars:
            if cal.name == SOURCE_CALENDAR:
                source_cal = cal
            elif cal.name == TARGET_CALENDAR:
                target_cal = cal
        
        if not source_cal or not target_cal:
            result = {"success": False, "message": "Could not find required calendars"}
            last_sync_result = result
            return result
        
        # Get all events from both calendars
        source_events = await get_events_from_shared(client, source_cal.id)
        target_events = await get_events_from_shared(client, target_cal.id)
        
        # Filter source events for public ones in date range
        public_events = []
        recurring_events = []  # Track master recurring events
        now = datetime.now()
        future_limit = now + timedelta(days=30)
        
        for event in source_events:
            if hasattr(event, 'categories') and event.categories and "Public" in event.categories:
                # Check if this is a recurring event master or instance
                if hasattr(event, 'recurrence') and event.recurrence:
                    # This is a master recurring event
                    recurring_events.append(event)
                    print(f"Found recurring master event: {event.subject}")
                elif hasattr(event, 'type') and event.type == 'occurrence':
                    # This is an instance of a recurring event - skip it, we'll handle the master
                    print(f"Skipping recurring instance: {event.subject}")
                    continue
                else:
                    # Regular single event
                    event_date = parse_date(event)
                    if event_date and now.date() <= event_date.date() <= future_limit.date():
                        public_events.append(event)
        
        # Add all recurring events (they manage their own date ranges)
        public_events.extend(recurring_events)
        
        # Create lookup dictionaries
        source_lookup = {}
        for event in public_events:
            if hasattr(event, 'recurrence') and event.recurrence:
                # For recurring events, use subject as key
                key = f"recurring:{event.subject}"
            else:
                # For single events, use subject+date as key
                event_date = parse_date(event)
                if event_date:
                    key = f"single:{event.subject}|{event_date.date()}"
                else:
                    key = f"single:{event.subject}|no_date"
            source_lookup[key] = event
        
        target_lookup = {}
        for event in target_events:
            if hasattr(event, 'recurrence') and event.recurrence:
                # For recurring events, use subject as key
                key = f"recurring:{event.subject}"
            else:
                # For single events, use subject+date as key
                event_date = parse_date(event)
                if event_date:
                    key = f"single:{event.subject}|{event_date.date()}"
                else:
                    key = f"single:{event.subject}|no_date"
            target_lookup[key] = event
        
        # Determine actions needed
        events_to_add = []
        events_to_update = []
        events_to_delete = []
        
        # Check for events to add or update
        for key, source_event in source_lookup.items():
            if key in target_lookup:
                # Event exists - check if it needs updating
                target_event = target_lookup[key]
                source_sig = create_event_signature(source_event)
                target_sig = create_event_signature(target_event)
                
                # Always update if target event has body content (to clear private details)
                has_body_content = (hasattr(target_event, 'body') and 
                                  target_event.body and 
                                  hasattr(target_event.body, 'content') and 
                                  target_event.body.content)
                
                if source_sig != target_sig or has_body_content:
                    events_to_update.append((target_event.id, source_event, target_event.subject))
                    if has_body_content:
                        print(f"Clearing body content from: {target_event.subject}")
            else:
                # Event doesn't exist - add it
                events_to_add.append(source_event)
        
        # Check for events to delete (in target but not in source)
        for key, target_event in target_lookup.items():
            if key not in source_lookup:
                events_to_delete.append((target_event.id, target_event.subject))
        
        total_changes = len(events_to_add) + len(events_to_update) + len(events_to_delete)
        
        if total_changes == 0:
            result = {"success": True, "message": "Calendars already in sync", "changes": 0}
            # Convert to Central Time for display
            central_tz = pytz.timezone('US/Central')
            last_sync_result = result
            last_sync_time = datetime.now(central_tz)
            return result
        
        # Perform sync operations
        success_count = 0
        
        # Add new events
        for event in events_to_add:
            result = await create_event_in_shared(client, target_cal.id, event)
            if result:
                success_count += 1
        
        # Update existing events
        for event_id, source_event, subject in events_to_update:
            if await update_event_in_shared(client, target_cal.id, event_id, source_event):
                success_count += 1
        
        # Delete removed events
        for event_id, subject in events_to_delete:
            if await delete_event_from_shared(client, target_cal.id, event_id):
                success_count += 1
        
        result = {
            "success": True,
            "message": f"Sync complete: {success_count}/{total_changes} operations successful",
            "changes": total_changes,
            "successful_operations": success_count,
            "added": len(events_to_add),
            "updated": len(events_to_update),
            "deleted": len(events_to_delete)
        }
        
        # Convert to Central Time for display
        central_tz = pytz.timezone('US/Central')
        last_sync_result = result
        last_sync_time = datetime.now(central_tz)
        return result
        
    except Exception as e:
        result = {"success": False, "message": f"Sync failed: {str(e)}"}
        # Convert to Central Time for display
        central_tz = pytz.timezone('US/Central')
        last_sync_result = result
        last_sync_time = datetime.now(central_tz)
        return result
    finally:
        sync_in_progress = False

def run_sync():
    """Run sync in asyncio event loop"""
    if not access_token:
        print("Cannot run sync - not authenticated")
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
        result = loop.run_until_complete(sync_calendars())
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
        run_sync()
    
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
        # Store result in global variable so we can display it
        last_sync_result = result
    
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
        "last_sync_result": last_sync_result,
        "sync_in_progress": sync_in_progress,
        "authenticated": access_token is not None,
        "scheduler_running": scheduler_running
    })

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
