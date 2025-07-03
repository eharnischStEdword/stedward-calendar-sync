#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Production Version with Central Time Support
"""
import os
import logging
import secrets
import traceback
import signal
import sys
import config
from datetime import datetime
from flask import Flask, render_template, jsonify, redirect, session, request

# Import timezone utilities
from utils.timezone import get_central_time, format_central_time, utc_to_central

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Global components - will be initialized after first successful load
sync_engine = None
scheduler = None
auth_manager = None

def initialize_components():
    """Initialize sync components safely"""
    global sync_engine, scheduler, auth_manager
    
    if auth_manager is None:
        try:
            from auth.microsoft_auth import MicrosoftAuth
            auth_manager = MicrosoftAuth()
            logger.info("✅ Auth manager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize auth manager: {e}")
            return False
    
    if sync_engine is None:
        try:
            from sync.engine import SyncEngine
            sync_engine = SyncEngine(auth_manager)
            logger.info("✅ Sync engine initialized")
        except Exception as e:
            logger.error(f"Failed to initialize sync engine: {e}")
            # Continue without sync engine for now
    
    if scheduler is None:
        try:
            from sync.scheduler import SyncScheduler
            scheduler = SyncScheduler(sync_engine)
            scheduler.start()
            logger.info("✅ Scheduler started")
        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {e}")
            # Continue without scheduler for now
    
    return True

@app.route('/health')
def health_check():
    """Health check for Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": get_central_time().isoformat(),
        "service": "st-edward-calendar-sync",
        "timezone": "America/Chicago"
    }), 200

@app.route('/health/detailed')
def detailed_health():
    """Detailed health check"""
    checks = {}
    
    # Check authentication
    if auth_manager:
        checks['authenticated'] = auth_manager.is_authenticated()
    else:
        checks['authenticated'] = False
    
    # Check sync engine
    checks['sync_engine_loaded'] = sync_engine is not None
    
    # Check scheduler
    if scheduler:
        checks['scheduler_running'] = scheduler.is_running()
    else:
        checks['scheduler_running'] = False
    
    status = "healthy" if all(checks.values()) else "degraded"
    
    return jsonify({
        "status": status,
        "timestamp": get_central_time().isoformat(),
        "timezone": "America/Chicago",
        "checks": checks
    })

@app.route('/status')
def get_status():
    """Get current system status"""
    try:
        if not auth_manager:
            initialize_components()
        
        status = {
            "authenticated": auth_manager.is_authenticated() if auth_manager else False,
            "sync_in_progress": False,
            "last_sync_time": None,
            "last_sync_time_display": "Never",
            "last_sync_result": {"success": False, "message": "Not synced yet"},
            "scheduler_running": scheduler.is_running() if scheduler else False,
            "dry_run_mode": getattr(config, 'DRY_RUN_MODE', False),
            "circuit_breaker_state": "closed",
            "rate_limit_remaining": 20,
            "total_syncs": 0,
            "timezone": "America/Chicago",
            "current_time": get_central_time().isoformat()
        }
        
        if sync_engine:
            engine_status = sync_engine.get_status()
            status.update(engine_status)
            
            # Format the last sync time for display
            if engine_status.get('last_sync_time'):
                try:
                    last_sync_dt = datetime.fromisoformat(engine_status['last_sync_time'].replace('Z', '+00:00'))
                    status['last_sync_time_display'] = format_central_time(last_sync_dt)
                except:
                    status['last_sync_time_display'] = "Unknown"
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return jsonify({
            "error": str(e),
            "authenticated": False,
            "sync_in_progress": False
        }), 500

@app.route('/')
def index():
    """Main dashboard"""
    try:
        # Initialize components if needed
        initialize_components()
        
        if not auth_manager or not auth_manager.is_authenticated():
            # Show auth page
            state = secrets.token_urlsafe(16)
            session['oauth_state'] = state
            auth_url = auth_manager.get_auth_url(state) if auth_manager else "#"
            
            return f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>St. Edward Calendar Sync</title>
                <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📅</text></svg>">
            </head>
            <body style="font-family: Arial; text-align: center; margin-top: 100px; background: #f5f5f5;">
                <div style="background: white; max-width: 500px; margin: 0 auto; padding: 40px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h1 style="color: #005921;">🗓️ St. Edward Calendar Sync</h1>
                    <p style="margin: 20px 0; color: #666;">Automated synchronization between internal and public calendars</p>
                    <a href="{auth_url}" style="background: #0078d4; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 18px; display: inline-block;">
                        Sign in with Microsoft
                    </a>
                    <p style="margin-top: 30px; font-size: 12px; color: #999;">
                        St. Edward Church & School • Nashville, TN
                    </p>
                </div>
            </body>
            </html>
            '''
        
        # User is authenticated - show dashboard
        last_sync_time = None
        last_sync_time_display = "Never"
        
        if sync_engine:
            status = sync_engine.get_status()
            last_sync_time_str = status.get('last_sync_time')
            
            # Convert ISO string back to datetime object and format for display
            if last_sync_time_str:
                try:
                    last_sync_dt = datetime.fromisoformat(last_sync_time_str.replace('Z', '+00:00'))
                    last_sync_time = last_sync_dt  # Keep original for template
                    last_sync_time_display = format_central_time(last_sync_dt)
                except:
                    last_sync_time = None
                    last_sync_time_display = "Never"
        
        return render_template('index.html', 
                             last_sync_time=last_sync_time,
                             last_sync_time_display=last_sync_time_display,
                             authenticated=True)
        
    except Exception as e:
        logger.error(f"Index route error: {e}")
        return f'''
        <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>⚠️ Error</h1>
            <p>System error: {str(e)}</p>
            <p>The service is starting up. Please refresh in a moment.</p>
        </body></html>
        ''', 500

@app.route('/sync', methods=['POST'])
def trigger_sync():
    """Trigger manual sync"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated", "redirect": "/"}), 401
        
        result = sync_engine.sync_calendars()
        
        # Add formatted time to result
        if result.get('success') and sync_engine.last_sync_time:
            result['last_sync_time_display'] = format_central_time(sync_engine.last_sync_time)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Sync trigger error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/auth/callback')
def auth_callback():
    """OAuth callback"""
    try:
        if not auth_manager:
            initialize_components()
        
        code = request.args.get('code')
        state = request.args.get('state')
        
        if not code or not state or state != session.get('oauth_state'):
            return "Authentication failed: Invalid request", 400
        
        if auth_manager and auth_manager.exchange_code_for_token(code):
            return redirect('/')
        else:
            return "Authentication failed: Could not exchange token", 400
            
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        return f"Authentication error: {e}", 500

@app.route('/logout')
def logout():
    """Clear session and tokens"""
    try:
        session.clear()
        if auth_manager:
            auth_manager.clear_tokens()
        return redirect('/')
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return redirect('/')

@app.route('/debug')
def debug_info():
    """Basic debug information"""
    try:
        debug_data = {
            "timestamp": get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "current_time_display": format_central_time(get_central_time()),
            "environment_vars": {
                "CLIENT_ID": bool(os.environ.get('CLIENT_ID')),
                "CLIENT_SECRET": bool(os.environ.get('CLIENT_SECRET')),
                "TENANT_ID": bool(os.environ.get('TENANT_ID')),
                "ACCESS_TOKEN": bool(os.environ.get('ACCESS_TOKEN')),
                "REFRESH_TOKEN": bool(os.environ.get('REFRESH_TOKEN')),
            },
            "config_loaded": True,
            "components": {
                "auth_manager": auth_manager is not None,
                "sync_engine": sync_engine is not None,
                "scheduler": scheduler is not None,
                "scheduler_running": scheduler.is_running() if scheduler else False
            }
        }
        
        if auth_manager:
            debug_data["authenticated"] = auth_manager.is_authenticated()
        
        return jsonify(debug_data)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/metrics')
def get_metrics():
    """Get system metrics"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        metrics = sync_engine.metrics.get_metrics_summary()
        
        # Add timezone info
        metrics['timezone'] = 'America/Chicago'
        metrics['report_time'] = format_central_time(get_central_time())
        
        return jsonify(metrics)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/history')
def get_history():
    """Get sync history"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        stats = sync_engine.history.get_statistics()
        
        # Format times in history
        if stats.get('last_sync'):
            try:
                dt = datetime.fromisoformat(stats['last_sync'])
                stats['last_sync_display'] = format_central_time(dt)
            except:
                pass
        
        if stats.get('last_successful_sync'):
            try:
                dt = datetime.fromisoformat(stats['last_successful_sync'])
                stats['last_successful_sync_display'] = format_central_time(dt)
            except:
                pass
        
        return jsonify(stats)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/validate-sync', methods=['POST'])
def validate_sync():
    """Validate current sync state"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        # Get calendar IDs
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        
        if not source_id or not target_id:
            return jsonify({"error": "Required calendars not found"}), 404
        
        # Get events
        source_events = sync_engine.reader.get_public_events(source_id)
        target_events = sync_engine.reader.get_calendar_events(target_id)
        
        if not source_events or not target_events:
            return jsonify({"error": "Could not retrieve calendar events"}), 500
        
        # Validate
        validation_report = sync_engine.validator.generate_validation_report(
            source_events, target_events, {"success": True}
        )
        
        # Add formatted timestamp
        validation_report['report_time_display'] = format_central_time(get_central_time())
        
        return jsonify(validation_report)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/dry-run/enable')
def enable_dry_run():
    """Enable dry run mode"""
    config.DRY_RUN_MODE = True
    return jsonify({"message": "Dry run mode enabled", "dry_run": True})

@app.route('/dry-run/disable') 
def disable_dry_run():
    """Disable dry run mode"""
    config.DRY_RUN_MODE = False
    return jsonify({"message": "Dry run mode disabled", "dry_run": False})

@app.route('/restart-scheduler')
def restart_scheduler():
    """Restart the scheduler"""
    global scheduler
    try:
        if scheduler:
            scheduler.stop()
        
        if sync_engine:
            from sync.scheduler import SyncScheduler
            scheduler = SyncScheduler(sync_engine)
            scheduler.start()
            return jsonify({"message": "Scheduler restarted", "running": scheduler.is_running()})
        else:
            return jsonify({"error": "Sync engine not available"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/calendars')
def debug_calendars():
    """Debug: List all available calendars"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get all calendars
        all_calendars = sync_engine.reader.get_calendars()
        
        if not all_calendars:
            return jsonify({"error": "Could not retrieve calendars"}), 500
        
        # Format calendar info
        calendar_info = []
        for cal in all_calendars:
            calendar_info.append({
                "name": cal.get('name'),
                "id": cal.get('id'),
                "owner": cal.get('owner', {}).get('name', 'Unknown'),
                "canEdit": cal.get('canEdit', False),
                "canShare": cal.get('canShare', False)
            })
        
        # Also show which ones we're configured to use
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        
        return jsonify({
            "all_calendars": calendar_info,
            "configuration": {
                "source_calendar_name": config.SOURCE_CALENDAR,
                "target_calendar_name": config.TARGET_CALENDAR,
                "source_calendar_id": source_id,
                "target_calendar_id": target_id,
                "shared_mailbox": config.SHARED_MAILBOX
            },
            "timezone": "America/Chicago",
            "current_time": format_central_time(get_central_time()),
            "web_calendar_url": "https://outlook.office365.com/owa/calendar/fbd704b50f2540068cb048469a830830@stedward.org/e399b41349f3441ca9b092248fc807f56673636587944979855/calendar.html"
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    logger.info("Received shutdown signal, cleaning up...")
    if scheduler:
        scheduler.stop()
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Initialize on startup
logger.info("🚀 Starting St. Edward Calendar Sync")
logger.info(f"🕐 Server timezone: America/Chicago")
logger.info(f"🕐 Current time: {format_central_time(get_central_time())}")
initialize_components()

@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/apple-touch-icon-120x120.png')
@app.route('/apple-touch-icon-120x120-precomposed.png')
@app.route('/apple-touch-icon-152x152.png')
@app.route('/apple-touch-icon-167x167.png')
@app.route('/apple-touch-icon-180x180.png')
def apple_touch_icon():
    """Serve apple touch icon"""
    import base64
    
    # Calendar emoji SVG as apple touch icon
    svg_content = '''<svg width="180" height="180" xmlns="http://www.w3.org/2000/svg">
      <rect width="180" height="180" fill="#f8f8f8" rx="36"/>
      <text x="50%" y="55%" font-family="Apple Color Emoji,Segoe UI Emoji,Noto Color Emoji" font-size="120" text-anchor="middle">📅</text>
    </svg>'''
    
    # Return SVG with proper headers
    return svg_content, 200, {
        'Content-Type': 'image/svg+xml',
        'Cache-Control': 'public, max-age=31536000'
    }

@app.route('/favicon.ico')
def favicon():
    """Serve favicon"""
    return redirect("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E📅%3C/text%3E%3C/svg%3E")

# Add this route to your app.py file (add it before the if __name__ == '__main__': line)

@app.route('/bulletin-events')
def bulletin_events():
    """Get events for the weekly bulletin with week selection"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return redirect('/')
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get the week parameter
        week_param = request.args.get('week', 'upcoming')
        
        # Get the public calendar ID
        public_calendar_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not public_calendar_id:
            return jsonify({"error": "Public calendar not found"}), 404
        
        # Calculate date range based on week parameter
        from datetime import timedelta
        import pytz
        import re
        import requests
        
        central_tz = pytz.timezone('America/Chicago')
        today = get_central_time().date()
        
        if week_param == 'current':
            # Current week (Sunday to Saturday)
            days_since_sunday = today.weekday() + 1 if today.weekday() != 6 else 0
            start_date = today - timedelta(days=days_since_sunday)
            end_date = start_date + timedelta(days=6)
            week_label = "Current Week"
        
        elif week_param == 'upcoming':
            # Upcoming Saturday to following Sunday (9 days)
            days_until_saturday = (5 - today.weekday()) % 7
            if days_until_saturday == 0:  # Today is Saturday
                start_date = today
            else:
                start_date = today + timedelta(days=days_until_saturday)
            end_date = start_date + timedelta(days=8)
            week_label = "Upcoming Week"
        
        elif week_param == 'following':
            # Week after upcoming (starts 9 days after upcoming Saturday)
            days_until_saturday = (5 - today.weekday()) % 7
            if days_until_saturday == 0:  # Today is Saturday
                start_date = today + timedelta(days=7)
            else:
                start_date = today + timedelta(days=days_until_saturday + 7)
            end_date = start_date + timedelta(days=8)
            week_label = "Following Week"
        
        else:
            # Default to upcoming
            days_until_saturday = (5 - today.weekday()) % 7
            if days_until_saturday == 0:
                start_date = today
            else:
                start_date = today + timedelta(days=days_until_saturday)
            end_date = start_date + timedelta(days=8)
            week_label = "Upcoming Week"
        
        # Create datetime objects at midnight in Central Time
        start_datetime = central_tz.localize(datetime.combine(start_date, datetime.min.time()))
        end_datetime = central_tz.localize(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
        
        # Get auth headers
        headers = sync_engine.auth.get_headers()
        if not headers:
            return jsonify({"error": "No auth headers"}), 401
        
        # Use Microsoft Graph calendarView API
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{public_calendar_id}/calendarView"
        
        # Format dates for API (must be in UTC)
        start_str = start_datetime.astimezone(pytz.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        end_str = end_datetime.astimezone(pytz.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        params = {
            'startDateTime': start_str,
            'endDateTime': end_str,
            '$select': 'id,subject,start,end,categories,location,isAllDay,showAs,type,body',
            '$orderby': 'start/dateTime',
            '$top': 250
        }
        
        all_events = []
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 401:
                # Try refreshing token
                if sync_engine.auth.refresh_access_token():
                    headers = sync_engine.auth.get_headers()
                    response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                all_events = data.get('value', [])
                
                # Handle pagination if needed
                next_link = data.get('@odata.nextLink')
                while next_link:
                    next_response = requests.get(next_link, headers=headers, timeout=30)
                    if next_response.status_code == 200:
                        next_data = next_response.json()
                        all_events.extend(next_data.get('value', []))
                        next_link = next_data.get('@odata.nextLink')
                    else:
                        break
            else:
                logger.error(f"Failed to get calendar view: {response.status_code} - {response.text}")
                all_events = []
                
        except Exception as e:
            logger.error(f"Error fetching calendar view: {e}")
            all_events = []
        
        # Process events for bulletin
        bulletin_events = []
        for event in all_events:
            # Get event start time
            event_start_str = event.get('start', {}).get('dateTime', '')
            if not event_start_str:
                continue
            
            try:
                event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                event_start_central = utc_to_central(event_start)
                
                # Get event details
                event_data = {
                    'subject': event.get('subject', 'No Title'),
                    'start': event_start_central,
                    'end': None,
                    'location': '',
                    'is_all_day': event.get('isAllDay', False)
                }
                
                # Get end time
                event_end_str = event.get('end', {}).get('dateTime', '')
                if event_end_str:
                    event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                    event_data['end'] = utc_to_central(event_end)
                
                # Extract location from body
                body_content = event.get('body', {}).get('content', '')
                if body_content and 'Location:' in body_content:
                    location_match = re.search(r'<strong>Location:</strong>\s*([^<]+)', body_content)
                    if location_match:
                        event_data['location'] = location_match.group(1).strip()
                
                bulletin_events.append(event_data)
                
            except Exception as e:
                logger.warning(f"Error processing event: {e}")
                continue
        
        # Sort events by start time
        bulletin_events.sort(key=lambda x: x['start'])
        
        # Group events by day
        events_by_day = {}
        for event in bulletin_events:
            day_key = event['start'].date()
            if day_key not in events_by_day:
                events_by_day[day_key] = []
            events_by_day[day_key].append(event)
        
        # Format for display
        formatted_days = []
        current_date = start_date
        
        while current_date <= end_date:
            day_events = events_by_day.get(current_date, [])
            
            formatted_days.append({
                'date': current_date,
                'day_name': current_date.strftime('%A'),
                'date_str': current_date.strftime('%B %d'),
                'events': day_events
            })
            
            current_date += timedelta(days=1)
        
        # Render the bulletin template
        return render_template('bulletin_events.html',
                             start_date=start_date.strftime('%B %d'),
                             end_date=end_date.strftime('%B %d, %Y'),
                             days=formatted_days,
                             week_label=week_label,
                             week_param=week_param,
                             generated_time=format_central_time(get_central_time()))
        
    except Exception as e:
        logger.error(f"Bulletin events error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/event-list/<preset>')
def event_list_preset(preset):
    """Get events for various preset time ranges with optional search"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return redirect('/')
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get search term from query parameters
        search_term = request.args.get('search', '').lower().strip()
        
        # Get the public calendar ID
        public_calendar_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not public_calendar_id:
            return jsonify({"error": "Public calendar not found"}), 404
        
        # Calculate date range based on preset
        from datetime import timedelta
        import pytz
        import requests
        
        central_tz = pytz.timezone('America/Chicago')
        today = get_central_time().date()
        start_date = today
        
        # Determine end date based on preset
        if preset == 'next30':
            end_date = today + timedelta(days=30)
            title = "Next 30 Days"
        elif preset == 'endofmonth':
            # Last day of current month
            if today.month == 12:
                end_date = datetime(today.year + 1, 1, 1).date() - timedelta(days=1)
            else:
                end_date = datetime(today.year, today.month + 1, 1).date() - timedelta(days=1)
            title = f"Until End of {today.strftime('%B')}"
        elif preset == 'june30':
            # June 30 of current or next year
            june30 = datetime(today.year, 6, 30).date()
            if today > june30:
                june30 = datetime(today.year + 1, 6, 30).date()
            end_date = june30
            title = f"Until June 30, {june30.year}"
        elif preset == 'next60':
            end_date = today + timedelta(days=60)
            title = "Next 60 Days"
        else:
            # Default to next 30 days
            end_date = today + timedelta(days=30)
            title = "Next 30 Days"
        
        # Add search term to title if provided
        if search_term:
            title += f" - '{search_term}'"
        
        # Create datetime objects
        start_datetime = central_tz.localize(datetime.combine(start_date, datetime.min.time()))
        end_datetime = central_tz.localize(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
        
        # Get events from API
        headers = sync_engine.auth.get_headers()
        if not headers:
            return jsonify({"error": "No auth headers"}), 401
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{public_calendar_id}/calendarView"
        
        params = {
            'startDateTime': start_datetime.astimezone(pytz.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'endDateTime': end_datetime.astimezone(pytz.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
            '$select': 'id,subject,start,end,categories,location,isAllDay,showAs,type,body',
            '$orderby': 'start/dateTime',
            '$top': 500
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        events = []
        if response.status_code == 200:
            events = response.json().get('value', [])
        
        # Process events for display
        event_list = []
        for event in events:
            event_subject = event.get('subject', '').lower()
            
            # Filter by search term if provided
            if search_term and search_term not in event_subject:
                continue
            
            event_start_str = event.get('start', {}).get('dateTime', '')
            if event_start_str:
                event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                event_start_central = utc_to_central(event_start)
                
                event_list.append({
                    'date': event_start_central.strftime('%A, %B %d, %Y'),
                    'time': event_start_central.strftime('%-I:%M %p') if not event.get('isAllDay') else 'All Day',
                    'subject': event.get('subject', 'No Title'),
                    'datetime_obj': event_start_central
                })
        
        # Sort by datetime
        event_list.sort(key=lambda x: x['datetime_obj'])
        
        # Generate simple HTML response
        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>{title} - Event List</title>
            <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📅</text></svg>">
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    margin: 20px; 
                    background: #f5f5f5; 
                    line-height: 1.6;
                }}
                .container {{ 
                    max-width: 800px; 
                    margin: 0 auto; 
                    background: white; 
                    padding: 30px; 
                    border-radius: 10px; 
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                }}
                h1 {{ 
                    color: #005921; 
                    border-bottom: 2px solid #005921;
                    padding-bottom: 10px;
                }}
                .search-box {{
                    background: #f8f9fa;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 20px 0;
                }}
                .search-input {{
                    width: 70%;
                    padding: 8px 12px;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    font-size: 16px;
                }}
                .search-btn {{
                    padding: 8px 20px;
                    background: #005921;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 16px;
                    margin-left: 10px;
                }}
                .search-btn:hover {{
                    background: #004a1e;
                }}
                .summary {{
                    background: #f0f7f4;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 20px 0;
                }}
                .event {{ 
                    margin: 20px 0; 
                    padding: 15px; 
                    background: #f8f9fa; 
                    border-left: 4px solid #005921;
                    border-radius: 4px;
                }}
                .date {{ 
                    font-weight: bold; 
                    color: #005921;
                    font-size: 1.1em;
                }}
                .time {{
                    color: #666;
                    font-weight: bold;
                }}
                .subject {{
                    margin-top: 5px;
                    font-size: 1.05em;
                }}
                .back-link {{ 
                    display: inline-block; 
                    margin-top: 20px; 
                    padding: 10px 20px;
                    background: #005921;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                }}
                .back-link:hover {{ 
                    background: #004a1e;
                }}
                .copy-btn {{
                    float: right;
                    padding: 10px 20px;
                    background: #6c757d;
                    color: white;
                    border: none;
                    border-radius: 5px;
                    cursor: pointer;
                }}
                .copy-btn:hover {{
                    background: #5a6268;
                }}
                @media print {{
                    .back-link, .copy-btn, .search-box {{ display: none; }}
                    body {{ background: white; }}
                    .container {{ box-shadow: none; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📅 {title}</h1>
                <button class="copy-btn" onclick="copyList()">📋 Copy List</button>
                
                <div class="search-box">
                    <form action="/event-list/{preset}" method="get">
                        <input type="text" 
                               name="search" 
                               class="search-input" 
                               placeholder="Search events (e.g., boy scouts, council, PTO)..." 
                               value="{search_term}">
                        <button type="submit" class="search-btn">🔍 Search</button>
                    </form>
                </div>
                
                <div class="summary">
                    <strong>{len(event_list)} events found</strong> from {start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}
                    {f'<br>Filtered by: "<strong>{search_term}</strong>"' if search_term else ''}
                </div>
                <div id="event-list">
        '''
        
        if event_list:
            for event in event_list:
                html += f'''
                    <div class="event">
                        <div class="date">{event['date']}</div>
                        <div><span class="time">{event['time']}</span> - <span class="subject">{event['subject']}</span></div>
                    </div>
                '''
        else:
            if search_term:
                html += f'<p>No events found containing "{search_term}" in this time range.</p>'
            else:
                html += '<p>No events found in this time range.</p>'
        
        html += f'''
                </div>
                <a href="/" class="back-link">← Back to Dashboard</a>
            </div>
            
            <textarea id="plaintext" style="position: absolute; left: -9999px;">'''
        
        # Add plain text version for copying
        if search_term:
            html += f"Events containing '{search_term}':\n\n"
        for event in event_list:
            html += f"{event['date']}\n{event['time']} - {event['subject']}\n\n"
        
        html += '''</textarea>
            
            <script>
                function copyList() {
                    const textarea = document.getElementById('plaintext');
                    textarea.select();
                    document.execCommand('copy');
                    
                    // Show feedback
                    const btn = document.querySelector('.copy-btn');
                    const originalText = btn.textContent;
                    btn.textContent = '✅ Copied!';
                    setTimeout(() => {
                        btn.textContent = originalText;
                    }, 2000);
                }
            </script>
        </body>
        </html>
        '''
        
        return html
        
    except Exception as e:
        logger.error(f"Event list error: {e}")
        return f"Error: {str(e)}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting calendar sync service on port {port}")
    app.run(host='0.0.0.0', port=port)
