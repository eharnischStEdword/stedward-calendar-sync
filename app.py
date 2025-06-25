#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Main Application with Enhancements
"""
import os
import logging
import secrets
import threading
import signal
import sys
import time
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, session, url_for

import config
from auth.microsoft_auth import MicrosoftAuth
from sync.engine import SyncEngine
from sync.scheduler import SyncScheduler
from utils.version import get_version_info
from utils.audit import AuditLogger

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
audit_logger = AuditLogger()

# Get version info
APP_VERSION_INFO = get_version_info()

# Log startup
logger.info("=" * 60)
logger.info(f"🚀 {APP_VERSION_INFO['full_version']}")
logger.info(f"📦 Platform: {APP_VERSION_INFO['deployment_platform']}")
logger.info(f"🌍 Environment: {APP_VERSION_INFO['environment']}")
logger.info("=" * 60)


def signal_handler(sig, frame):
    """Handle graceful shutdown"""
    logger.info('Graceful shutdown initiated...')
    scheduler.stop()
    
    # Wait for any in-progress syncs
    timeout = 30
    start = time.time()
    while sync_engine.sync_in_progress and time.time() - start < timeout:
        time.sleep(1)
    
    logger.info('Shutdown complete')
    sys.exit(0)


# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


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


# ==================== HEALTH CHECK HELPERS ====================

def check_microsoft_api():
    """Check if Microsoft API is accessible"""
    try:
        headers = auth_manager.get_headers()
        if not headers:
            return False
        response = requests.get(
            'https://graph.microsoft.com/v1.0/me',
            headers=headers,
            timeout=5
        )
        return response.status_code == 200
    except:
        return False


def check_calendar_access():
    """Check if calendars are accessible"""
    try:
        return sync_engine.reader.get_calendars() is not None
    except:
        return False


def get_last_sync_health():
    """Check if last sync was recent and successful"""
    status = sync_engine.get_status()
    if not status.get('last_sync_time'):
        return False
    
    last_sync = status['last_sync_time']
    if isinstance(last_sync, str):
        last_sync = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
    
    # Check if last sync was within 2 hours
    time_since_sync = datetime.now() - last_sync
    return time_since_sync.total_seconds() < 7200  # 2 hours


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
        audit_logger.log_sync_operation('auth_callback', 'unknown', {
            'error': 'Invalid OAuth state',
            'ip': request.remote_addr
        })
        return "Invalid state parameter", 400
    
    if auth_code and auth_manager.exchange_code_for_token(auth_code):
        session.pop('oauth_state', None)
        # Start scheduler after successful auth
        scheduler.start()
        
        audit_logger.log_sync_operation('auth_success', 'user', {
            'ip': request.remote_addr
        })
        
        return redirect(url_for('index'))
    else:
        audit_logger.log_sync_operation('auth_failed', 'user', {
            'ip': request.remote_addr
        })
        return "Authentication failed", 400


@app.route('/sync', methods=['POST'])
@requires_auth
def manual_sync():
    """Trigger manual sync"""
    audit_logger.log_sync_operation('manual_sync_triggered', 'user', {
        'ip': request.remote_addr
    })
    
    def sync_thread():
        result = sync_engine.sync_calendars()
        audit_logger.log_sync_operation('manual_sync_completed', 'user', {
            'result': result.get('success', False),
            'operations': {
                'added': result.get('added', 0),
                'updated': result.get('updated', 0),
                'deleted': result.get('deleted', 0)
            }
        })
    
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
    """Basic health check endpoint"""
    return jsonify({
        "status": "healthy",
        "version": APP_VERSION_INFO["version_string"],
        "authenticated": auth_manager.is_authenticated(),
        "scheduler_running": scheduler.is_running()
    })


@app.route('/health/detailed')
@requires_auth
def detailed_health():
    """Comprehensive health check"""
    checks = {
        'authentication': auth_manager.is_authenticated(),
        'microsoft_api': check_microsoft_api(),
        'calendar_access': check_calendar_access(),
        'scheduler': scheduler.is_running(),
        'last_sync_healthy': get_last_sync_health(),
        'circuit_breaker': sync_engine.circuit_breaker.state == 'closed'
    }
    
    overall_health = all(checks.values())
    status_code = 200 if overall_health else 503
    
    # Add more details
    details = {
        'last_sync_time': None,
        'sync_count_24h': 0,
        'error_count_24h': 0,
        'average_sync_duration': 0
    }
    
    # Get metrics summary
    metrics_summary = sync_engine.metrics.get_metrics_summary()
    if metrics_summary:
        details.update(metrics_summary)
    
    return jsonify({
        'status': 'healthy' if overall_health else 'unhealthy',
        'checks': checks,
        'details': details,
        'timestamp': datetime.now().isoformat()
    }), status_code


@app.route('/logout')
def logout():
    """Logout and clear tokens"""
    audit_logger.log_sync_operation('logout', 'user', {
        'ip': request.remote_addr
    })
    
    auth_manager.clear_tokens()
    scheduler.stop()
    session.clear()
    logger.info("User logged out")
    return redirect(url_for('index'))


@app.route('/diagnostics', methods=['POST'])
@requires_auth
def run_diagnostics():
    """Run diagnostics"""
    audit_logger.log_sync_operation('diagnostics_run', 'user', {
        'ip': request.remote_addr
    })
    
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


@app.route('/metrics')
@requires_auth
def get_metrics():
    """Get sync metrics"""
    return jsonify(sync_engine.metrics.get_metrics_summary())


@app.route('/history')
@requires_auth
def get_sync_history():
    """Get sync history"""
    return jsonify({
        'history': [make_json_serializable(entry) for entry in sync_engine.history.history],
        'statistics': sync_engine.history.get_statistics()
    })


@app.route('/enable-dry-run')
@requires_auth
def enable_dry_run():
    """Enable dry run mode"""
    config.DRY_RUN_MODE = True
    audit_logger.log_sync_operation('dry_run_enabled', 'user', {
        'ip': request.remote_addr
    })
    return jsonify({"message": "DRY RUN mode enabled", "dry_run_mode": True})


@app.route('/disable-dry-run')
@requires_auth
def disable_dry_run():
    """Disable dry run mode"""
    config.DRY_RUN_MODE = False
    audit_logger.log_sync_operation('dry_run_disabled', 'user', {
        'ip': request.remote_addr
    })
    return jsonify({"message": "DRY RUN mode disabled", "dry_run_mode": False})


@app.route('/debug/events/<calendar_name>')
@requires_auth
def debug_events(calendar_name):
    """Debug endpoint to inspect calendar events"""
    if config.APP_VERSION_INFO.get('environment') != 'development':
        return jsonify({"error": "Debug endpoint only available in development"}), 403
    
    calendar_id = sync_engine.reader.find_calendar_id(calendar_name)
    if not calendar_id:
        return jsonify({"error": f"Calendar '{calendar_name}' not found"}), 404
    
    events = sync_engine.reader.get_calendar_events(calendar_id)
    if not events:
        return jsonify({"error": "Failed to retrieve events"}), 500
    
    # Sanitize events for display
    sanitized_events = []
    for event in events[:10]:  # First 10 only
        sanitized_events.append({
            'id': event.get('id', '')[:8] + '...',
            'subject': event.get('subject'),
            'start': event.get('start'),
            'categories': event.get('categories'),
            'type': event.get('type')
        })
    
    return jsonify({
        'calendar': calendar_name,
        'calendar_id': calendar_id,
        'event_count': len(events),
        'sample_events': sanitized_events
    })


@app.route('/validate-sync', methods=['POST'])
@requires_auth
def validate_sync():
    """Manually validate sync status"""
    source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
    target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
    
    if not source_id or not target_id:
        return jsonify({"error": "Cannot find calendars"}), 400
    
    source_events = sync_engine.reader.get_public_events(source_id)
    target_events = sync_engine.reader.get_calendar_events(target_id)
    
    if source_events is None or target_events is None:
        return jsonify({"error": "Failed to retrieve events"}), 500
    
    is_valid, validations = sync_engine.validator.validate_sync_result(
        source_events, target_events
    )
    
    return jsonify({
        'is_valid': is_valid,
        'validations': validations,
        'source_count': len(source_events),
        'target_count': len(target_events)
    })

# Add this route to your app.py file

@app.route('/debug-validation', methods=['GET'])
@requires_auth
def debug_validation():
    """Debug validation issues"""
    source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
    target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
    
    if not source_id or not target_id:
        return jsonify({"error": "Cannot find calendars"}), 400
    
    source_events = sync_engine.reader.get_public_events(source_id)
    target_events = sync_engine.reader.get_calendar_events(target_id)
    
    if source_events is None or target_events is None:
        return jsonify({"error": "Failed to retrieve events"}), 500
    
    # Find events with "Adoration & Confession" in the name
    adoration_events = []
    for event in target_events:
        if "adoration" in event.get('subject', '').lower():
            adoration_events.append({
                'subject': event.get('subject'),
                'type': event.get('type'),
                'start': event.get('start'),
                'end': event.get('end'),
                'id': event.get('id')[:8] + '...',
                'seriesMasterId': event.get('seriesMasterId', 'N/A')[:8] + '...' if event.get('seriesMasterId') else 'N/A',
                'signature': sync_engine._create_event_signature(event),
                'recurrence': 'Yes' if event.get('recurrence') else 'No'
            })
    
    # Group by signature to find duplicates
    signature_groups = {}
    for event in target_events:
        sig = sync_engine._create_event_signature(event)
        if not sig.startswith('skip:'):
            if sig not in signature_groups:
                signature_groups[sig] = []
            signature_groups[sig].append({
                'subject': event.get('subject'),
                'type': event.get('type'),
                'start': event.get('start', {}).get('dateTime', 'N/A')
            })
    
    # Find actual duplicates
    duplicates = {}
    for sig, events in signature_groups.items():
        if len(events) > 1:
            duplicates[sig] = events
    
    return jsonify({
        'adoration_events': adoration_events,
        'duplicate_signatures': duplicates,
        'total_source_events': len(source_events),
        'total_target_events': len(target_events),
        'signature_count': len(signature_groups),
        'target_event_types': {
            'singleInstance': sum(1 for e in target_events if e.get('type') == 'singleInstance'),
            'seriesMaster': sum(1 for e in target_events if e.get('type') == 'seriesMaster'),
            'occurrence': sum(1 for e in target_events if e.get('type') == 'occurrence'),
            'exception': sum(1 for e in target_events if e.get('type') == 'exception')
        }
    })


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ==================== MAIN ====================

if __name__ == '__main__':
    # Validate configuration
    if not config.CLIENT_SECRET:
        logger.error("CLIENT_SECRET environment variable is not set!")
        exit(1)
    
    # Try to restore auth on startup
    if auth_manager.is_authenticated():
        scheduler.start()
        logger.info("Authentication restored, scheduler started")
    
    # Run Flask app
    port = config.PORT
    logger.info(f"Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port)
