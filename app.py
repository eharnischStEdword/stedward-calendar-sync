#!/usr/bin/env python3
"""
Calendar Sync Web Application for Render.com
Simple web interface for rcarroll + automatic hourly sync
"""

from flask import Flask, render_template, jsonify, request, redirect, session, url_for
import asyncio
import os
import json
from datetime import datetime, timedelta
from azure.identity import AuthorizationCodeCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.event import Event
import threading
import time
import requests
import urllib.parse

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
    
    # Start time
    if hasattr(event, 'start'):
        if hasattr(event.start, 'date'):
            sig_parts.append(f"start_date:{event.start.date}")
        elif hasattr(event.start, 'date_time'):
            sig_parts.append(f"start_datetime:{event.start.date_time}")
    
    # End time
    if hasattr(event, 'end'):
        if hasattr(event.end, 'date'):
            sig_parts.append(f"end_date:{event.end.date}")
        elif hasattr(event.end, 'date_time'):
            sig_parts.append(f"end_datetime:{event.end.date_time}")
    
    # Location
    if hasattr(event, 'location') and event.location:
        if hasattr(event.location, 'display_name'):
            sig_parts.append(f"location:{event.location.display_name}")
    
    # Body (first 100 chars)
    if hasattr(event, 'body') and event.body and hasattr(event.body, 'content'):
        content = event.body.content[:100] if event.body.content else ""
        sig_parts.append(f"body:{content}")
    
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
            body=event.body,
            start=event.start,
            end=event.end,
            location=event.location,
            categories=["Public"],
            is_all_day=event.is_all_day if hasattr(event, 'is_all_day') else False,
            is_reminder_on=event.is_reminder_on if hasattr(event, 'is_reminder_on') else False,
            importance=event.importance if hasattr(event, 'importance') else None,
            sensitivity=event.sensitivity if hasattr(event, 'sensitivity') else None,
            show_as=event.show_as if hasattr(event, 'show_as') else None
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
            body=source_event.body,
            start=source_event.start,
            end=source_event.end,
            location=source_event.location,
            categories=["Public"],
            is_all_day=source_event.is_all_day if hasattr(source_event, 'is_all_day') else False,
            is_reminder_on=source_event.is_reminder_on if hasattr(source_event, 'is_reminder_on') else False,
            importance=source_event.importance if hasattr(source_event, 'importance') else None,
            sensitivity=source_event.sensitivity if hasattr(source_event, 'sensitivity') else None,
            show_as=source_event.show_as if hasattr(source_event, 'show_as') else None
        )
        
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
        now = datetime.now()
        future_limit = now + timedelta(days=30)
        
        for event in source_events:
            if hasattr(event, 'categories') and event.categories and "Public" in event.categories:
                event_date = parse_date(event)
                if event_date and now.date() <= event_date.date() <= future_limit.date():
                    public_events.append(event)
        
        # Create lookup dictionaries
        source_lookup = {}
        for event in public_events:
            event_date = parse_date(event)
            if event_date:
                key = f"{event.subject}|{event_date.date()}"
                source_lookup[key] = event
        
        target_lookup = {}
        for event in target_events:
            event_date = parse_date(event)
            if event_date:
                key = f"{event.subject}|{event_date.date()}"
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
                
                if source_sig != target_sig:
                    events_to_update.append((target_event.id, source_event, target_event.subject))
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
            last_sync_result = result
            last_sync_time = datetime.now()
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
        
        last_sync_result = result
        last_sync_time = datetime.now()
        return result
        
    except Exception as e:
        result = {"success": False, "message": f"Sync failed: {str(e)}"}
        last_sync_result = result
        return result
    finally:
        sync_in_progress = False

def run_sync():
    """Run sync in asyncio event loop"""
    if not access_token:
        print("Cannot run sync - not authenticated")
        return {"success": False, "message": "Not authenticated"}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(sync_calendars())
        print(f"Sync result: {result}")
        return result
    except Exception as e:
        print(f"Sync error: {e}")
        return {"success": False, "message": str(e)}
    finally:
        loop.close()

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
    
    return render_template('index.html', 
                         last_sync_time=last_sync_time,
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

@app.route('/status')
def status():
    """Get current sync status"""
    return jsonify({
        "last_sync_time": last_sync_time.isoformat() if last_sync_time else None,
        "last_sync_result": last_sync_result,
        "sync_in_progress": sync_in_progress,
        "authenticated": access_token is not None
    })

@app.route('/logout')
def logout():
    """Clear authentication"""
    global access_token
    access_token = None
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
