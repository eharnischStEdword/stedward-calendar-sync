#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Main Application
Clean, modular architecture version
"""
import os
import logging
import secrets
import threading
from flask import Flask, render_template, jsonify, request, redirect, session, url_for

import config
from auth.microsoft_auth import MicrosoftAuth
from sync.engine import SyncEngine
from sync.scheduler import SyncScheduler
from utils.version import get_version_info

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = config.SECRET_KEY or secrets.token_hex(32)

# Initialize components
auth_manager = MicrosoftAuth()
sync_engine = SyncEngine(auth_manager)
scheduler = SyncScheduler(sync_engine)

# Get version info
APP_VERSION_INFO = get_version_info()

# Log startup
logger.info("=" * 60)
logger.info(f"🚀 {APP_VERSION_INFO['full_version']}")
logger.info(f"📦 Platform: {APP_VERSION_INFO['deployment_platform']}")
logger.info(f"🌍 Environment: {APP_VERSION_INFO['environment']}")
logger.info("=" * 60)


def requires_auth(f):
    """Decorator to check authentication"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated_function


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


# ==================== ROUTES ====================

@app.route('/')
def index():
    """Main page"""
    if not auth_manager.is_authenticated():
        state = secrets.token_urlsafe(16)
        session['oauth_state'] = state
        auth_url = auth_manager.get_auth_url(state)
        
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
    
    # Get sync status for display
    status = sync_engine.get_status()
    
    # Convert last sync time to Central Time for display
    import pytz
    central_tz = pytz.timezone('US/Central')
    display_sync_time = None
    
    if status.get('last_sync_time'):
        last_sync_time = status['last_sync_time']
        if last_sync_time.tzinfo is None:
            # If no timezone info, assume UTC and convert
            utc_time = pytz.utc.localize(last_sync_time)
            display_sync_time = utc_time.astimezone(central_tz)
        else:
            # Already has timezone info, convert to Central
            display_sync_time = last_sync_time.astimezone(central_tz)
    
    return render_template('index.html', 
                         last_sync_time=display_sync_time,
                         last_sync_result=status.get('last_sync_result'),
                         sync_in_progress=status.get('sync_in_progress'))


@app.route('/auth/callback')
def auth_callback():
    """Handle OAuth callback"""
    auth_code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state
    if state != session.get('oauth_state'):
        logger.warning("Invalid OAuth state - possible CSRF attempt")
        return "Invalid state parameter", 400
    
    if auth_code and auth_manager.exchange_code_for_token(auth_code):
        session.pop('oauth_state', None)
        # Start scheduler after successful auth
        scheduler.start()
        return redirect(url_for('index'))
    else:
        return "Authentication failed", 400


@app.route('/sync', methods=['POST'])
@requires_auth
def manual_sync():
    """Trigger manual sync"""
    def sync_thread():
        result = sync_engine.sync_calendars()
        # Store result in engine's state (it's already done internally)
    
    # Run sync in background
    thread = threading.Thread(target=sync_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Sync started"})


@app.route('/status')
def status():
    """Get current status"""
    sync_status = sync_engine.get_status()
    
    # Convert datetime objects to strings
    if sync_status.get('last_sync_time'):
        import pytz
        central_tz = pytz.timezone('US/Central')
        if sync_status['last_sync_time'].tzinfo is None:
            utc_time = pytz.utc.localize(sync_status['last_sync_time'])
            sync_status['last_sync_time'] = utc_time.astimezone(central_tz).isoformat()
        else:
            sync_status['last_sync_time'] = sync_status['last_sync_time'].astimezone(central_tz).isoformat()
    
    # Make the result JSON serializable
    sync_status['last_sync_result'] = make_json_serializable(sync_status.get('last_sync_result'))
    
    return jsonify({
        **sync_status,
        "authenticated": auth_manager.is_authenticated(),
        "scheduler_running": scheduler.is_running(),
        "token_status": auth_manager.get_token_status(),
        "version": APP_VERSION_INFO["version"],
        "build_number": APP_VERSION_INFO["build_number"],
        "environment": APP_VERSION_INFO["environment"],
        "app_info": APP_VERSION_INFO["full_version"],
        "master_calendar_protection": config.MASTER_CALENDAR_PROTECTION,
        "dry_run_mode": config.DRY_RUN_MODE
    })


@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "version": APP_VERSION_INFO["version_string"],
        "authenticated": auth_manager.is_authenticated(),
        "scheduler_running": scheduler.is_running()
    })


@app.route('/logout')
def logout():
    """Logout and clear tokens"""
    auth_manager.clear_tokens()
    scheduler.stop()
    session.clear()
    logger.info("User logged out")
    return redirect(url_for('index'))


@app.route('/diagnostics', methods=['POST'])
@requires_auth
def run_diagnostics():
    """Run diagnostics"""
    # Run in thread for non-blocking
    def diag_thread():
        from cal_ops.reader import CalendarReader
        reader = CalendarReader(auth_manager)
        
        calendars = reader.get_calendars()
        if calendars:
            logger.info(f"Found {len(calendars)} calendars")
            for cal in calendars:
                logger.info(f"Calendar: {cal.get('name')} (ID: {cal.get('id')})")
    
    thread = threading.Thread(target=diag_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Diagnostics started - check logs"})


@app.route('/version')
def version():
    """Get detailed version information"""
    return jsonify(APP_VERSION_INFO)


@app.route('/enable-dry-run')
@requires_auth
def enable_dry_run():
    """Enable dry run mode"""
    config.DRY_RUN_MODE = True
    return jsonify({"message": "DRY RUN mode enabled", "dry_run_mode": True})


@app.route('/disable-dry-run')
@requires_auth
def disable_dry_run():
    """Disable dry run mode"""
    config.DRY_RUN_MODE = False
    return jsonify({"message": "DRY RUN mode disabled", "dry_run_mode": False})


# ==================== MAIN ====================

if __name__ == '__main__':
    # Validate configuration
    if not config.CLIENT_SECRET:
        logger.error("CLIENT_SECRET environment variable is not set!")
        exit(1)
    
    # Try to restore auth on startup
    if auth_manager.is_authenticated():
        scheduler.start()
    
    # Run Flask app
    port = config.PORT
    app.run(host='0.0.0.0', port=port)
