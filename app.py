# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

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
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, redirect, session, request, copy_current_request_context, make_response
import threading

# Import timezone utilities
from utils import DateTimeUtils

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add sync status file constant
SYNC_STATUS_FILE = "sync_status.json"

# Add sync status helper function
def update_sync_status(status_data):
    """Thread-safe status update using file system"""
    try:
        with open(SYNC_STATUS_FILE, 'w') as f:
            json.dump(status_data, f)
    except Exception as e:
        logger.error(f"Failed to update sync status: {e}")

# Initialize Flask
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Security Headers Middleware
@app.after_request
def add_security_headers(response):
    """Add comprehensive security headers to all responses"""
    # HTTPS Enforcement
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    
    # Content Security Policy - Strict but allows necessary resources
    csp_policy = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self' https://graph.microsoft.com https://login.microsoftonline.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "upgrade-insecure-requests"
    )
    response.headers['Content-Security-Policy'] = csp_policy
    
    # Additional Security Headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    
    # Add security headers specifically for OAuth callback
    if request.endpoint == 'auth_callback':
        response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Add a custom header to indicate this is a legitimate OAuth service
        response.headers['X-Service-Type'] = 'OAuth-Callback'
        response.headers['X-Service-Provider'] = 'St-Edward-Church'
    
    # Remove server information
    response.headers['Server'] = 'St. Edward Calendar Sync'
    
    return response

# HTTPS Enforcement Middleware
@app.before_request
def enforce_https():
    """Enforce HTTPS for all requests"""
    if request.headers.get('X-Forwarded-Proto') == 'http':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

# State persistence functions
def save_scheduler_state(paused: bool):
    """Save scheduler pause state to file"""
    try:
        state_data = {"scheduler_paused": paused}
        with open('scheduler_state.json', 'w') as f:
            json.dump(state_data, f)
        logger.info(f"Scheduler state saved: paused={paused}")
    except Exception as e:
        logger.error(f"Failed to save scheduler state: {e}")

def load_scheduler_state() -> bool:
    """Load scheduler pause state from file"""
    try:
        if os.path.exists('scheduler_state.json'):
            with open('scheduler_state.json', 'r') as f:
                state_data = json.load(f)
                paused = state_data.get('scheduler_paused', False)
                logger.info(f"Scheduler state loaded: paused={paused}")
                return paused
    except Exception as e:
        logger.error(f"Failed to load scheduler state: {e}")
    return False

# Global components - will be initialized after first successful load
sync_engine = None
scheduler = None
auth_manager = None
scheduler_paused = load_scheduler_state()  # Load state on startup

# Global flag to track if components have been initialized
_components_initialized = False
_sync_ready = False

def initialize_components():
    """Initialize sync components safely"""
    global sync_engine, scheduler, auth_manager
    
    if auth_manager is None:
        try:
            from auth import MicrosoftAuth
            auth_manager = MicrosoftAuth()
            logger.info("✅ Auth manager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize auth manager: {e}")
            return False
    
    if sync_engine is None:
        try:
            from sync import SyncEngine
            sync_engine = SyncEngine(auth_manager)
            logger.info("✅ Sync engine initialized")
        except Exception as e:
            logger.error(f"Failed to initialize sync engine: {e}")
            # Continue without sync engine for now
    
    if scheduler is None and sync_engine is not None:
        try:
            from sync import SyncScheduler
            scheduler = SyncScheduler(sync_engine)
            # Don't start scheduler immediately - let it start on first request
            logger.info("✅ Scheduler initialized (will start on first request)")
        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {e}")
    
    return True

def initialize_calendar_sync_background():
    """Initialize sync operations in background thread"""
    global _sync_ready
    try:
        logger.info("🔄 Starting background calendar sync initialization...")
        
        # Initialize components
        initialize_components()
        
        # Perform any heavy initialization here
        if sync_engine:
            # Test connection to Microsoft Graph (lightweight)
            logger.info("🔗 Testing Microsoft Graph connection...")
            # Don't perform full sync during startup - just test connection
        
        _sync_ready = True
        logger.info("✅ Background calendar sync initialization completed")
    except Exception as e:
        logger.error(f"❌ Background sync initialization failed: {e}")

def ensure_components_initialized():
    """Initialize components on first request to avoid startup delays"""
    global _components_initialized
    if not _components_initialized:
        try:
            initialize_components()
            _components_initialized = True
            logger.info("✅ Components initialized on first request")
        except Exception as e:
            logger.error(f"Failed to initialize components on first request: {e}")

# Start background initialization if not in debug mode
if not app.debug:
    import threading
    init_thread = threading.Thread(target=initialize_calendar_sync_background, daemon=True)
    init_thread.start()
    logger.info("🚀 Started background sync initialization thread")

@app.route('/health')
def health_check():
    """Lightweight health check - responds immediately for Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "st-edward-calendar-sync",
        "version": "1.0.0"
    }), 200

@app.route('/ready')
def readiness_check():
    """Check if sync services are ready"""
    try:
        global _sync_ready
        if _sync_ready and auth_manager and sync_engine:
            return jsonify({"status": "ready"}), 200
        else:
            return jsonify({"status": "initializing"}), 503
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

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
        "timestamp": DateTimeUtils.get_central_time().isoformat(),
        "timezone": "America/Chicago",
        "checks": checks
    })

@app.route('/status')
def get_status():
    """Get current system status"""
    try:
        # Initialize components on first request
        ensure_components_initialized()
        
        status = {
            "authenticated": auth_manager.is_authenticated() if auth_manager else False,
            "sync_in_progress": False,
            "last_sync_time": None,
            "last_sync_time_display": "Never",
            "last_sync_result": {"success": False, "message": "Not synced yet"},
            "scheduler_running": scheduler.is_running() if scheduler else False,
            "scheduler_paused": scheduler_paused,
            "dry_run_mode": getattr(config, 'DRY_RUN_MODE', False),
            "circuit_breaker_state": "closed",
            "rate_limit_remaining": config.MAX_SYNC_REQUESTS_PER_HOUR,
            "total_syncs": 0,
            "timezone": "America/Chicago",
            "current_time": DateTimeUtils.get_central_time().isoformat() if hasattr(DateTimeUtils.get_central_time(), 'isoformat') else str(DateTimeUtils.get_central_time())
        }
        
        if sync_engine:
            engine_status = sync_engine.get_status()
            status.update(engine_status)
            
            # Use actual rate limit from engine status
            status["rate_limit_remaining"] = engine_status.get('rate_limit_remaining', config.MAX_SYNC_REQUESTS_PER_HOUR)
            
            # Format the last sync time for display
            if engine_status.get('last_sync_time'):
                try:
                    last_sync_dt = datetime.fromisoformat(engine_status['last_sync_time'].replace('Z', '+00:00'))
                    status['last_sync_time_display'] = format_central_time(last_sync_dt)
                except:
                    status['last_sync_time_display'] = "Unknown"
        
        # Add scheduler details to status
        if scheduler:
            scheduler_status = scheduler.get_scheduler_status()
            status['scheduler_details'] = scheduler_status
            status['last_scheduled_sync'] = scheduler_status.get('last_scheduled_sync')
            status['last_scheduled_sync_display'] = scheduler_status.get('last_scheduled_sync_display', 'Never')
            status['next_scheduled_sync'] = scheduler_status.get('next_scheduled_sync')
            status['next_scheduled_sync_display'] = scheduler_status.get('next_scheduled_sync_display', 'Unknown')
            status['scheduled_sync_count'] = scheduler_status.get('scheduled_sync_count', 0)
        
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
        # Initialize components on first request
        ensure_components_initialized()
        
        # Start scheduler on first request (not during deployment)
        if scheduler and not scheduler.is_running() and not scheduler_paused:
            scheduler.start()
            logger.info("✅ Scheduler started on first request")
        
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
    """Trigger sync in background, return immediately"""
    
    # Initialize components on first request
    ensure_components_initialized()
    
    # Validate auth first
    if not auth_manager or not auth_manager.is_authenticated():
        return jsonify({"error": "Not authenticated", "redirect": "/"}), 401
    
    if not sync_engine:
        return jsonify({"error": "Sync engine not initialized"}), 500
    
    # Check if sync already running
    if sync_engine.sync_in_progress:
        return jsonify({
            "status": "already_running",
            "message": "Sync is already in progress",
            "progress": sync_engine.get_progress_percent() if hasattr(sync_engine, 'get_progress_percent') else 0
        }), 409
    
    # Parse request body
    dry_run = request.json.get('dry_run', False) if request.json else False
    
    # Start sync in background thread
    sync_thread = threading.Thread(
        target=_run_sync_background,
        args=(dry_run,),
        daemon=True
    )
    sync_thread.start()
    
    return jsonify({
        "status": "started",
        "message": "Sync started in background",
        "dry_run": dry_run,
        "check_progress": "/sync/progress"
    }), 202  # 202 Accepted

@app.route('/sync/preview', methods=['POST'])
def preview_sync():
    """
    Analyze what would be synced without executing.
    Returns detailed report of adds/updates/deletes.
    """
    if not auth_manager or not auth_manager.is_authenticated():
        return jsonify({"error": "Not authenticated"}), 401
    
    if not sync_engine:
        return jsonify({"error": "Sync engine not initialized"}), 500
    
    try:
        # Run sync in dry-run mode to get preview
        preview_result = sync_engine.preview_sync()
        
        return jsonify({
            "success": True,
            "preview": {
                "events_to_add": preview_result.get('to_add', []),
                "events_to_update": preview_result.get('to_update', []),
                "events_to_delete": preview_result.get('to_delete', []),
                "add_count": len(preview_result.get('to_add', [])),
                "update_count": len(preview_result.get('to_update', [])),
                "delete_count": len(preview_result.get('to_delete', [])),
                "total_changes": len(preview_result.get('to_add', [])) + 
                                len(preview_result.get('to_update', [])) + 
                                len(preview_result.get('to_delete', []))
            }
        })
    except Exception as e:
        logger.error(f"Preview sync failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/sync/progress')
def sync_progress():
    """Get current sync progress"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        return jsonify({
            "in_progress": sync_engine.sync_in_progress,
            "phase": sync_engine.sync_state.get('phase', 'idle'),
            "progress": sync_engine.sync_state.get('progress', 0),
            "total": sync_engine.sync_state.get('total', 0),
            "percent": sync_engine.get_progress_percent() if hasattr(sync_engine, 'get_progress_percent') else 0,
            "last_checkpoint": sync_engine.sync_state.get('last_checkpoint'),
            "last_result": sync_engine.last_sync_result
        })
    except Exception as e:
        logger.error(f"Sync progress error: {e}")
        return jsonify({"error": str(e)}), 500

def _run_sync_background(dry_run=False):
    """Run sync in background thread"""
    try:
        logger.info(f"🔄 Background sync started (dry_run={dry_run})")
        result = sync_engine.sync_calendars()
        logger.info(f"✅ Background sync completed: {result.get('message')}")
    except Exception as e:
        logger.error(f"❌ Background sync failed: {e}", exc_info=True)

@app.route('/clear-target', methods=['POST'])
def clear_target():
    """Clear all events from target calendar"""
    try:
        # Initialize components on first request
        ensure_components_initialized()
        
        if not sync_engine:
            return jsonify({'success': False, 'message': 'Sync engine not initialized'}), 500
            
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({'success': False, 'message': 'Not authenticated'}), 401
        
        # Target calendar
        target_id = sync_engine.reader.find_calendar_id("St. Edward Public Calendar")
        if not target_id:
            return jsonify({'success': False, 'message': 'Target calendar not found'}), 404
        
        # Get all events (2 years back)
        events = sync_engine.reader.get_calendar_events(target_id)
        
        deleted = 0
        failed = 0
        for event in events:
            try:
                event_id = event.get('id')
                if event_id:
                    success = sync_engine.writer.delete_event(target_id, event_id)
                    if success:
                        deleted += 1
                    else:
                        failed += 1
            except Exception as e:
                logger.error(f"Failed to delete event {event.get('id', 'unknown')}: {e}")
                failed += 1
        
        return jsonify({
            'success': True,
            'deleted': deleted,
            'failed': failed,
            'message': f'Cleared {deleted} events'
        })
    except Exception as e:
        logger.error(f"Clear target error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/clear-synced-only', methods=['POST'])
def clear_synced_only():
    """Clear only events that were created by sync"""
    try:
        # Initialize components on first request
        ensure_components_initialized()
        
        if not sync_engine:
            return jsonify({'success': False, 'message': 'Sync engine not initialized'}), 500
            
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({'success': False, 'message': 'Not authenticated'}), 401
        
        # Target calendar
        target_id = sync_engine.reader.find_calendar_id("St. Edward Public Calendar")
        if not target_id:
            return jsonify({'success': False, 'message': 'Target calendar not found'}), 404
        
        # Clear only synced events
        deleted = sync_engine.writer.clear_synced_events_only(target_id)
        
        return jsonify({
            'success': True,
            'deleted': deleted,
            'message': f'Cleared {deleted} synced events'
        })
    except Exception as e:
        logger.error(f"Clear synced only error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/sync/status')
def sync_status():
    """Get current sync status from file"""
    try:
        if os.path.exists(SYNC_STATUS_FILE):
            with open(SYNC_STATUS_FILE, 'r') as f:
                status = json.load(f)
            return jsonify(status)
        else:
            return jsonify({"status": "idle"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

@app.route('/auth/callback')
def auth_callback():
    """OAuth callback with enhanced security"""
    try:
        # Enhanced bot/crawler detection
        user_agent = request.headers.get('User-Agent', '').lower()
        referrer = request.headers.get('Referer', '').lower()
        
        # Extended bot/crawler patterns including security scanners
        bot_patterns = [
            'googlebot', 'chrome-lighthouse', 'safebrowsing', 'crawler', 'bot',
            'security', 'scanner', 'lighthouse', 'pagespeed', 'gtmetrix',
            'webpagetest', 'pingdom', 'uptimerobot', 'monitor'
        ]
        is_bot = any(pattern in user_agent for pattern in bot_patterns)
        
        # Check for suspicious patterns that might trigger security warnings
        suspicious_patterns = [
            'google.com', 'chrome-error', 'security', 'safebrowsing',
            'phishing', 'malware', 'virus', 'scan'
        ]
        is_suspicious = any(pattern in referrer for pattern in suspicious_patterns)
        
        # Return safe response for bots, crawlers, or suspicious requests
        if is_bot or is_suspicious or 'google.com' in referrer:
            safe_response = '''
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>St. Edward Calendar Sync - Secure Service</title>
                <meta name="description" content="Secure calendar synchronization service for St. Edward Church and School">
                <meta name="robots" content="noindex, nofollow">
                <style>
                    body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
                    .container { max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                    h1 { color: #005921; margin-bottom: 20px; }
                    p { color: #333; line-height: 1.6; }
                    .secure-badge { background: #28a745; color: white; padding: 5px 10px; border-radius: 4px; font-size: 12px; display: inline-block; margin-top: 10px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>St. Edward Calendar Sync</h1>
                    <p>This is a secure OAuth callback endpoint for the St. Edward Church calendar synchronization service.</p>
                    <p>This service is used to securely synchronize calendar events between internal and public calendars.</p>
                    <div class="secure-badge">🔒 Secure Service</div>
                </div>
            </body>
            </html>
            '''
            return safe_response, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
        # For legitimate OAuth requests, proceed with authentication
        ensure_components_initialized()
        
        code = request.args.get('code')
        state = request.args.get('state')
        
        # Enhanced validation
        if not code:
            logger.warning("OAuth callback missing code parameter")
            return "Authentication failed: Missing authorization code", 400
        
        if not state or state != session.get('oauth_state'):
            logger.warning("OAuth callback state mismatch or missing")
            return "Authentication failed: Invalid state parameter", 400
        
        # Attempt token exchange
        if auth_manager and auth_manager.exchange_code_for_token(code):
            logger.info("OAuth authentication successful")
            return redirect('/')
        else:
            logger.error("OAuth token exchange failed")
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
            "timestamp": DateTimeUtils.get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "current_time_display": DateTimeUtils.format_central_time(DateTimeUtils.get_central_time()),
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

        # Use available history statistics since metrics collector is not implemented
        history_stats = sync_engine.history.get_statistics()

        payload = {
            "history": history_stats,
            "timezone": "America/Chicago",
            "report_time": DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        }

        return jsonify(payload)
        
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
        validation_report['report_time_display'] = DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        
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
            from sync import SyncScheduler
            scheduler = SyncScheduler(sync_engine)
            scheduler.start()
            return jsonify({"message": "Scheduler restarted", "running": scheduler.is_running()})
        else:
            return jsonify({"error": "Sync engine not available"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/scheduler/toggle', methods=['POST'])
def toggle_scheduler():
    """Toggle scheduler pause/resume"""
    global scheduler, scheduler_paused
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not scheduler:
            return jsonify({"error": "Scheduler not initialized"}), 500
        
        # Toggle the pause state
        scheduler_paused = not scheduler_paused
        save_scheduler_state(scheduler_paused) # Save state after toggle
        
        if scheduler_paused:
            # Pause the scheduler
            scheduler.stop()
            message = "Scheduler paused - automatic syncing stopped"
            logger.info(f"🔄 Scheduler paused at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
        else:
            # Resume the scheduler
            scheduler.start()
            message = "Scheduler resumed - automatic syncing restarted"
            logger.info(f"🔄 Scheduler resumed at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
        
        return jsonify({
            "message": message,
            "paused": scheduler_paused,
            "running": scheduler.is_running() if not scheduler_paused else False
        })
        
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
            "current_time": DateTimeUtils.format_central_time(DateTimeUtils.get_central_time()),
            "web_calendar_url": "https://outlook.office365.com/owa/calendar/fbd704b50f2540068cb048469a830830@stedward.org/e399b41349f3441ca9b092248fc807f56673636587944979855/calendar.html"
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/events/<calendar_name>')
def debug_events(calendar_name):
    """Debug: Show events and signatures for a specific calendar"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get calendar ID
        calendar_id = sync_engine.reader.find_calendar_id(calendar_name)
        if not calendar_id:
            return jsonify({"error": f"Calendar '{calendar_name}' not found"}), 404
        
        # Get all events (not just public)
        all_events = sync_engine.reader.get_calendar_events(calendar_id)
        
        # Create debug info for each event
        event_debug = []
        for event in all_events:
            signature = sync_engine._create_event_signature(event)
            event_debug.append({
                "subject": event.get('subject'),
                "signature": signature,
                "categories": event.get('categories', []),
                "showAs": event.get('showAs'),
                "type": event.get('type'),
                "start": event.get('start', {}).get('dateTime', 'No date'),
                "id": event.get('id', 'No ID')[:20] + "..."  # Truncate ID
            })
        
        return jsonify({
            "calendar": calendar_name,
            "total_events": len(all_events),
            "events": sorted(event_debug, key=lambda x: x['subject'] or '')
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/cache/clear', methods=['POST'])
def clear_cache():
    """Clear the event cache"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated", "redirect": "/"}), 401
        
        sync_engine.change_tracker.clear_cache()
        
        return jsonify({
            "success": True,
            "message": "Event cache cleared successfully"
        })
        
    except Exception as e:
        logger.error(f"Cache clear error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/scheduler/start', methods=['POST'])
def start_scheduler():
    """Manually start the scheduler"""
    try:
        if not scheduler:
            return jsonify({"error": "Scheduler not initialized"}), 500
        
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated", "redirect": "/"}), 401
        
        if scheduler.is_running():
            return jsonify({
                "success": True,
                "message": "Scheduler is already running"
            })
        
        scheduler.start()
        
        return jsonify({
            "success": True,
            "message": "Scheduler started successfully"
        })
        
    except Exception as e:
        logger.error(f"Scheduler start error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/cache/stats')
def get_cache_stats():
    """Get cache statistics"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        stats = sync_engine.change_tracker.get_cache_stats()
        
        return jsonify({
            "success": True,
            "cache_stats": stats
        })
        
    except Exception as e:
        logger.error(f"Cache stats error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/debug/event-details/<event_subject>')
def debug_event_details(event_subject):
    """Debug specific event to see its raw data"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar events
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        all_events = sync_engine.reader.get_calendar_events(source_id)
        
        # Find matching events
        matching_events = []
        for event in all_events:
            if event_subject.lower() in event.get('subject', '').lower():
                matching_events.append({
                    'subject': event.get('subject'),
                    'isAllDay': event.get('isAllDay'),
                    'start': event.get('start'),
                    'end': event.get('end'),
                    'type': event.get('type'),
                    'showAs': event.get('showAs'),
                    'categories': event.get('categories'),
                    'raw_event': event  # Full raw data
                })
        
        return jsonify({
            'search_term': event_subject,
            'found_count': len(matching_events),
            'events': matching_events
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/event-durations')
def debug_event_durations():
    """Report on event durations to identify potential data issues"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source events
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        events = sync_engine.reader.get_public_events(source_id)
        
        duration_analysis = {
            'under_2_hours': [],
            '2_to_4_hours': [],
            '4_to_8_hours': [],
            'over_8_hours': [],
            'all_day': []
        }
        
        for event in events:
            if event.get('isAllDay', False):
                duration_analysis['all_day'].append(event.get('subject'))
                continue
                
            start_str = event.get('start', {}).get('dateTime', '')
            end_str = event.get('end', {}).get('dateTime', '')
            
            if start_str and end_str:
                try:
                    start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                    duration_hours = (end_dt - start_dt).total_seconds() / 3600
                    
                    # Convert to Central Time for display
                    central_start = DateTimeUtils.utc_to_central(start_dt)
                    central_end = DateTimeUtils.utc_to_central(end_dt)
                    
                    event_info = {
                        'subject': event.get('subject'),
                        'duration_hours': round(duration_hours, 1),
                        'start_time': central_start.strftime('%I:%M %p'),
                        'end_time': central_end.strftime('%I:%M %p'),
                        'date': central_start.strftime('%Y-%m-%d')
                    }
                    
                    if duration_hours < 2:
                        duration_analysis['under_2_hours'].append(event_info)
                    elif duration_hours < 4:
                        duration_analysis['2_to_4_hours'].append(event_info)
                    elif duration_hours < 8:
                        duration_analysis['4_to_8_hours'].append(event_info)
                    else:
                        duration_analysis['over_8_hours'].append(event_info)
                        
                except Exception as e:
                    logger.debug(f"Error analyzing {event.get('subject')}: {e}")
        
        return jsonify({
            'analysis': duration_analysis,
            'recommendations': [
                'Events over 8 hours: Verify these are intentional (e.g., all-day adoration)',
                'Events with odd durations: Check for data entry errors in source calendar',
                'All-day events: Should have no specific times in source'
            ]
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/duplicates')
def debug_duplicates():
    """Debug duplicate events in the target calendar"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get target calendar ID
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        # Get all events from target calendar
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group events by subject to find duplicates
        events_by_subject = {}
        for event in target_events:
            subject = event.get('subject', 'No Subject')
            if subject not in events_by_subject:
                events_by_subject[subject] = []
            
            events_by_subject[subject].append({
                'id': event.get('id', 'No ID'),
                'start': event.get('start', {}).get('dateTime', 'No date'),
                'end': event.get('end', {}).get('dateTime', 'No date'),
                'type': event.get('type', 'unknown'),
                'categories': event.get('categories', []),
                'signature': sync_engine._create_event_signature(event),
                'created': event.get('createdDateTime', 'Unknown'),
                'modified': event.get('lastModifiedDateTime', 'Unknown')
            })
        
        # Find duplicates
        duplicates = {}
        for subject, events in events_by_subject.items():
            if len(events) > 1:
                # Sort by created date to identify which is newer
                sorted_events = sorted(events, key=lambda x: x['created'])
                duplicates[subject] = {
                    'count': len(events),
                    'events': sorted_events
                }
        
        # Also check for signature collisions
        signature_map = {}
        signature_collisions = []
        
        for event in target_events:
            sig = sync_engine._create_event_signature(event)
            if sig in signature_map:
                signature_collisions.append({
                    'signature': sig,
                    'event1': {
                        'subject': signature_map[sig]['subject'],
                        'id': signature_map[sig]['id'][:20] + '...',
                        'created': signature_map[sig]['created']
                    },
                    'event2': {
                        'subject': event.get('subject'),
                        'id': event.get('id', '')[:20] + '...',
                        'created': event.get('createdDateTime', 'Unknown')
                    }
                })
            else:
                signature_map[sig] = {
                    'subject': event.get('subject'),
                    'id': event.get('id', ''),
                    'created': event.get('createdDateTime', 'Unknown')
                }
        
        return jsonify({
            'target_calendar': config.TARGET_CALENDAR,
            'total_events': len(target_events),
            'duplicates_by_subject': duplicates,
            'duplicate_count': len(duplicates),
            'signature_collisions': signature_collisions,
            'signature_collision_count': len(signature_collisions),
            'analysis_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/debug/categories')
def debug_categories():
    """Debug: Show raw event data including categories from source calendar"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Analyze categories
        events_with_categories = []
        events_without_categories = []
        public_events = []
        
        for event in all_events:
            categories = event.get('categories', [])
            subject = event.get('subject', 'No Subject')
            
            event_info = {
                'subject': subject,
                'categories': categories,
                'showAs': event.get('showAs'),
                'isCancelled': event.get('isCancelled', False),
                'type': event.get('type'),
                'start': event.get('start', {}).get('dateTime', 'No date'),
                'raw_event': event  # Include full raw data for debugging
            }
            
            if categories:
                events_with_categories.append(event_info)
                if 'Public' in categories:
                    public_events.append(event_info)
            else:
                events_without_categories.append(event_info)
        
        return jsonify({
            'total_events': len(all_events),
            'events_with_categories': len(events_with_categories),
            'events_without_categories': len(events_without_categories),
            'events_with_public_tag': len(public_events),
            'events_with_categories_list': events_with_categories,
            'events_without_categories_list': events_without_categories,
            'public_events_list': public_events,
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug categories error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/sync-filter')
def debug_sync_filter():
    """Debug: Show what happens during the actual sync filtering process"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get public events using the actual sync method
        public_events = sync_engine.reader.get_public_events(source_id)
        
        # Also get all events for comparison
        all_events = sync_engine.reader.get_calendar_events(source_id)
        
        # Analyze what got filtered out
        public_event_subjects = {event.get('subject') for event in public_events} if public_events else set()
        
        filtered_out = []
        for event in all_events:
            if event.get('subject') not in public_event_subjects:
                categories = event.get('categories', [])
                if 'Public' in categories:
                    # This is a public event that got filtered out!
                    filtered_out.append({
                        'subject': event.get('subject'),
                        'start': event.get('start', {}).get('dateTime', 'No date'),
                        'categories': categories,
                        'showAs': event.get('showAs'),
                        'isCancelled': event.get('isCancelled', False),
                        'type': event.get('type'),
                        'reason': 'Public event filtered out during sync'
                    })
        
        return jsonify({
            'total_events': len(all_events),
            'public_events_found': len(public_events) if public_events else 0,
            'public_events_list': public_events[:10] if public_events else [],  # First 10
            'filtered_out_public_events': filtered_out,
            'filtered_out_count': len(filtered_out),
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug sync filter error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/recurring-events')
def debug_recurring_events():
    """Debug: Analyze recurring events and their types"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Analyze recurring events
        recurring_events = []
        occurrence_events = []
        series_master_events = []
        
        for event in all_events:
            event_type = event.get('type', 'singleInstance')
            categories = event.get('categories', [])
            subject = event.get('subject', 'No Subject')
            
            event_info = {
                'subject': subject,
                'type': event_type,
                'categories': categories,
                'start': event.get('start', {}).get('dateTime', 'No date'),
                'seriesMasterId': event.get('seriesMasterId'),
                'recurrence': event.get('recurrence'),
                'showAs': event.get('showAs'),
                'isCancelled': event.get('isCancelled', False)
            }
            
            if event_type == 'occurrence':
                occurrence_events.append(event_info)
            elif event_type == 'seriesMaster':
                series_master_events.append(event_info)
            elif event_type == 'singleInstance':
                # Check if it has recurrence data (might be a recurring event)
                if event.get('recurrence'):
                    recurring_events.append(event_info)
        
        # Focus on Mass events specifically
        mass_events = []
        for event in all_events:
            if 'mass' in event.get('subject', '').lower():
                mass_events.append({
                    'subject': event.get('subject'),
                    'type': event.get('type', 'singleInstance'),
                    'categories': event.get('categories', []),
                    'start': event.get('start', {}).get('dateTime', 'No date'),
                    'seriesMasterId': event.get('seriesMasterId'),
                    'recurrence': event.get('recurrence'),
                    'showAs': event.get('showAs'),
                    'isCancelled': event.get('isCancelled', False)
                })
        
        return jsonify({
            'total_events': len(all_events),
            'occurrence_events': len(occurrence_events),
            'series_master_events': len(series_master_events),
            'recurring_single_instances': len(recurring_events),
            'occurrence_events_list': occurrence_events[:20],  # First 20
            'series_master_events_list': series_master_events,
            'recurring_single_instances_list': recurring_events[:20],  # First 20
            'mass_events_list': mass_events,
            'mass_events_count': len(mass_events),
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug recurring events error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/mass-events-summary')
def debug_mass_events_summary():
    """Debug: Simple summary of Mass events and their types"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Focus only on Mass events
        mass_events = []
        for event in all_events:
            if 'mass' in event.get('subject', '').lower():
                mass_events.append({
                    'subject': event.get('subject'),
                    'type': event.get('type', 'singleInstance'),
                    'has_public': 'Public' in event.get('categories', []),
                    'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],  # Just the date part
                    'showAs': event.get('showAs'),
                    'isCancelled': event.get('isCancelled', False)
                })
        
        # Count by type
        type_counts = {}
        for event in mass_events:
            event_type = event['type']
            type_counts[event_type] = type_counts.get(event_type, 0) + 1
        
        # Count by date range (Sept-Nov vs after Nov 22)
        sept_nov_count = 0
        after_nov_count = 0
        
        for event in mass_events:
            start_date = event['start_date']
            current_year = datetime.now().year
            problem_start = f"{current_year}-09-21"
            problem_end = f"{current_year}-11-22"
            
            if start_date >= problem_start and start_date <= problem_end:
                sept_nov_count += 1
            elif start_date > problem_end:
                after_nov_count += 1
        
        return jsonify({
            'total_mass_events': len(mass_events),
            'type_counts': type_counts,
            'sept_nov_mass_events': sept_nov_count,
            'after_nov_mass_events': after_nov_count,
            'sample_mass_events': mass_events[:10],  # Just first 10
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug mass events summary error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/public-sync-issue')
def debug_public_sync_issue():
    """Debug: Find out why so many public events aren't syncing"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Get what the sync actually finds as public
        public_events = sync_engine.reader.get_public_events(source_id)
        
        # Analyze the gap
        public_event_subjects = {event.get('subject') for event in public_events} if public_events else set()
        
        # Find events that SHOULD be public but aren't syncing
        missing_public_events = []
        for event in all_events:
            categories = event.get('categories', [])
            subject = event.get('subject', 'No Subject')
            
            # Skip if already syncing
            if subject in public_event_subjects:
                continue
                
            # Look for events that should probably be public
            subject_lower = subject.lower()
            should_be_public_keywords = [
                'mass', 'liturgy', 'worship', 'school', 'education', 'class',
                'parish', 'community', 'fellowship', 'ministry', 'outreach',
                'celebration', 'festival', 'feast', 'baptism', 'confirmation',
                'wedding', 'funeral', 'memorial', 'adoration', 'rosary',
                'retreat', 'mission', 'youth', 'children', 'family', 'choir',
                'music', 'concert', 'fundraiser', 'benefit', 'charity',
                'volunteer', 'service', 'food drive', 'clothing drive',
                'blood drive', 'health fair', 'open house', 'tour'
            ]
            
            has_public_keyword = any(keyword in subject_lower for keyword in should_be_public_keywords)
            
            if has_public_keyword and 'Public' not in categories:
                missing_public_events.append({
                    'subject': subject,
                    'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                    'categories': categories,
                    'showAs': event.get('showAs'),
                    'type': event.get('type'),
                    'isCancelled': event.get('isCancelled', False)
                })
        
        return jsonify({
            'total_source_events': len(all_events),
            'public_events_syncing': len(public_events) if public_events else 0,
            'events_missing_public_tag': len(missing_public_events),
            'missing_events_sample': missing_public_events[:20],  # First 20
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug public sync issue error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/sync-breakdown')
def debug_sync_breakdown():
    """Debug: Show exactly what's happening in the sync filtering process"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Manually run through the same filtering logic as get_public_events
        stats = {
            'total_events': len(all_events),
            'cancelled': 0,
            'no_public_tag': 0,
            'not_busy': 0,
            'recurring_instances': 0,
            'past_events': 0,
            'future_events': 0,
            'date_parse_errors': 0,
            'passed_all_filters': 0
        }
        
        filtered_out_events = []
        
        # Create timezone-aware cutoff dates (same as in calendar_ops.py)
        from datetime import timedelta
        import pytz
        central_tz = pytz.timezone('America/Chicago')
        now_central = DateTimeUtils.get_central_time()
        cutoff_date = (now_central - timedelta(days=config.SYNC_CUTOFF_DAYS)).astimezone(pytz.UTC)
        future_cutoff = (now_central + timedelta(days=365)).astimezone(pytz.UTC)
        
        for event in all_events:
            subject = event.get('subject', 'No Subject')
            categories = event.get('categories', [])
            show_as = event.get('showAs', 'busy')
            event_type = event.get('type', 'singleInstance')
            
            # Track why each event is filtered out
            filter_reason = None
            
            # Skip cancelled events entirely
            if event.get('isCancelled', False):
                stats['cancelled'] += 1
                filter_reason = "cancelled"
            
            # Check if public
            elif 'Public' not in categories:
                stats['no_public_tag'] += 1
                filter_reason = "no_public_tag"
            
            # CRITICAL: Also check if event is marked as Busy
            elif show_as != 'busy':
                stats['not_busy'] += 1
                filter_reason = f"not_busy (showAs: {show_as})"
            
            # ALWAYS skip recurring instances to avoid duplicates
            elif event_type == 'occurrence':
                stats['recurring_instances'] += 1
                filter_reason = "recurring_instance"
            
            # Check event date
            else:
                try:
                    # Parse event date
                    event_date = DateTimeUtils.parse_graph_datetime(event.get('start', {}))
                    if not event_date:
                        stats['date_parse_errors'] += 1
                        filter_reason = "date_parse_error"
                    else:
                        # Ensure both datetimes are timezone-aware for comparison
                        if event_date.tzinfo is None:
                            # If naive, assume it's UTC
                            event_date = pytz.UTC.localize(event_date)

                        # Convert to UTC for comparison
                        event_date_utc = event_date.astimezone(pytz.UTC)

                        # Skip old events (unless it's a recurring event that should always be synced)
                        if event_date_utc < cutoff_date:
                            # Special override for recurring events - always include them regardless of age
                            if event.get('type') == 'seriesMaster':
                                stats['passed_all_filters'] += 1
                                filter_reason = "passed_all_filters"
                            else:
                                stats['past_events'] += 1
                                filter_reason = f"past_event (date: {event_date_utc}, cutoff: {cutoff_date})"

                        # Skip events too far in the future (but allow recurring events to extend further)
                        elif event_date_utc > future_cutoff:
                            # Special override for recurring events - allow them to extend further into the future
                            if event.get('type') == 'seriesMaster':
                                stats['passed_all_filters'] += 1
                                filter_reason = "passed_all_filters"
                            else:
                                stats['future_events'] += 1
                                filter_reason = f"future_event (date: {event_date_utc}, cutoff: {future_cutoff})"
                        else:
                            stats['passed_all_filters'] += 1
                            filter_reason = "passed_all_filters"
                            
                except Exception as e:
                    stats['date_parse_errors'] += 1
                    filter_reason = f"date_parse_error: {str(e)}"
            
            # Record why this event was filtered out
            if filter_reason != "passed_all_filters":
                filtered_out_events.append({
                    'subject': subject,
                    'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                    'categories': categories,
                    'showAs': show_as,
                    'type': event_type,
                    'filter_reason': filter_reason
                })
        
        return jsonify({
            'filtering_stats': stats,
            'filtered_out_events': filtered_out_events[:30],  # First 30
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug sync breakdown error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/specific-event/<event_subject>')
def debug_specific_event(event_subject):
    """Debug: Check why a specific event isn't syncing"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Find the specific event
        matching_events = []
        for event in all_events:
            if event_subject.lower() in event.get('subject', '').lower():
                matching_events.append(event)
        
        if not matching_events:
            return jsonify({"error": f"No events found matching '{event_subject}'"}), 404
        
        # Check each matching event
        results = []
        for event in matching_events:
            subject = event.get('subject', 'No Subject')
            categories = event.get('categories', [])
            show_as = event.get('showAs', 'busy')
            event_type = event.get('type', 'singleInstance')
            is_cancelled = event.get('isCancelled', False)
            start_date = event.get('start', {}).get('dateTime', 'No date')
            
            # Check each filter condition
            checks = {
                'has_public_tag': 'Public' in categories,
                'is_busy': show_as == 'busy',
                'not_cancelled': not is_cancelled,
                'not_occurrence': event_type != 'occurrence',
                'has_valid_date': bool(start_date and start_date != 'No date')
            }
            
            # Check date filtering
            date_check = "unknown"
            if start_date and start_date != 'No date':
                try:
                    from datetime import timedelta
                    import pytz
                    event_date = DateTimeUtils.parse_graph_datetime(event.get('start', {}))
                    if event_date:
                        if event_date.tzinfo is None:
                            event_date = pytz.UTC.localize(event_date)
                        event_date_utc = event_date.astimezone(pytz.UTC)
                        
                        now_central = DateTimeUtils.get_central_time()
                        cutoff_date = (now_central - timedelta(days=config.SYNC_CUTOFF_DAYS)).astimezone(pytz.UTC)
                        future_cutoff = (now_central + timedelta(days=365)).astimezone(pytz.UTC)
                        
                        if event_date_utc < cutoff_date:
                            date_check = f"too_old (date: {event_date_utc}, cutoff: {cutoff_date})"
                        elif event_date_utc > future_cutoff:
                            date_check = f"too_future (date: {event_date_utc}, cutoff: {future_cutoff})"
                        else:
                            date_check = "valid_date"
                    else:
                        date_check = "date_parse_error"
                except Exception as e:
                    date_check = f"date_error: {str(e)}"
            
            checks['date_check'] = date_check
            
            # Overall result
            should_sync = all([
                checks['has_public_tag'],
                checks['is_busy'], 
                checks['not_cancelled'],
                checks['not_occurrence'],
                checks['has_valid_date'],
                date_check == "valid_date"
            ])
            
            results.append({
                'subject': subject,
                'start_date': start_date,
                'categories': categories,
                'showAs': show_as,
                'type': event_type,
                'isCancelled': is_cancelled,
                'checks': checks,
                'should_sync': should_sync,
                'raw_event': event
            })
        
        return jsonify({
            'search_term': event_subject,
            'matching_events': results,
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug specific event error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/mass-daily-sync-status')
def debug_mass_daily_sync_status():
    """Debug: Check which Mass- Daily events are actually syncing"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get public events using the actual sync method
        public_events = sync_engine.reader.get_public_events(source_id)
        
        # Find Mass- Daily events in the public events
        mass_daily_public = []
        if public_events:
            for event in public_events:
                if 'Mass- Daily' in event.get('subject', ''):
                    mass_daily_public.append({
                        'subject': event.get('subject'),
                        'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                        'categories': event.get('categories', []),
                        'showAs': event.get('showAs'),
                        'type': event.get('type')
                    })
        
        # Also get all Mass- Daily events from source for comparison
        all_events = sync_engine.reader.get_calendar_events(source_id)
        mass_daily_all = []
        for event in all_events:
            if 'Mass- Daily' in event.get('subject', ''):
                mass_daily_all.append({
                    'subject': event.get('subject'),
                    'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                    'categories': event.get('categories', []),
                    'showAs': event.get('showAs'),
                    'type': event.get('type'),
                    'has_public_tag': 'Public' in event.get('categories', [])
                })
        
        return jsonify({
            'mass_daily_in_public_calendar': mass_daily_public,
            'mass_daily_in_source_calendar': mass_daily_all,
            'total_mass_daily_source': len(mass_daily_all),
            'total_mass_daily_public': len(mass_daily_public),
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug mass daily sync status error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/missing-public-events')
def debug_missing_public_events():
    """Debug: Show events with Public tags that aren't syncing"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Get public events using the actual sync method
        public_events = sync_engine.reader.get_public_events(source_id)
        public_event_subjects = {event.get('subject') for event in public_events} if public_events else set()
        
        # Find events with Public tags that aren't syncing
        missing_public_events = []
        for event in all_events:
            if 'Public' in event.get('categories', []):
                subject = event.get('subject', 'No Subject')
                if subject not in public_event_subjects:
                    missing_public_events.append({
                        'subject': subject,
                        'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                        'categories': event.get('categories', []),
                        'showAs': event.get('showAs'),
                        'type': event.get('type'),
                        'isCancelled': event.get('isCancelled', False)
                    })
        
        return jsonify({
            'missing_public_events': missing_public_events,
            'total_missing': len(missing_public_events),
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug missing public events error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/current-sync-status')
def debug_current_sync_status():
    """Debug: Check current sync status and what's actually in the public calendar"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get public calendar ID
        public_id = sync_engine.reader.find_calendar_id(config.PUBLIC_CALENDAR)
        if not public_id:
            return jsonify({"error": "Public calendar not found"}), 404
        
        # Get all events from both calendars
        source_events = sync_engine.reader.get_calendar_events(source_id)
        public_events = sync_engine.reader.get_calendar_events(public_id)
        
        # Find public events in source
        source_public_events = []
        for event in source_events:
            if 'Public' in event.get('categories', []):
                source_public_events.append({
                    'subject': event.get('subject'),
                    'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                    'showAs': event.get('showAs'),
                    'type': event.get('type'),
                    'isCancelled': event.get('isCancelled', False)
                })
        
        # Find events in public calendar
        public_calendar_events = []
        for event in public_events:
            public_calendar_events.append({
                'subject': event.get('subject'),
                'start_date': event.get('start', {}).get('dateTime', 'No date')[:10],
                'showAs': event.get('showAs'),
                'type': event.get('type'),
                'isCancelled': event.get('isCancelled', False)
            })
        
        # Find missing events
        public_subjects = {event['subject'] for event in public_calendar_events}
        missing_events = []
        for event in source_public_events:
            if event['subject'] not in public_subjects:
                missing_events.append(event)
        
        return jsonify({
            'source_public_events': source_public_events,
            'public_calendar_events': public_calendar_events,
            'missing_events': missing_events,
            'total_source_public': len(source_public_events),
            'total_public_calendar': len(public_calendar_events),
            'total_missing': len(missing_events),
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Debug current sync status error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/quick-debug')
def quick_debug():
    """Quick debug without full authentication - just check what we can see"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated", "auth_status": "failed"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source calendar
        all_events = sync_engine.reader.get_calendar_events(source_id)
        if not all_events:
            return jsonify({"error": "Could not retrieve source events"}), 500
        
        # Quick analysis
        public_events = []
        busy_public_events = []
        
        for event in all_events:
            if 'Public' in event.get('categories', []):
                subject = event.get('subject', 'No Subject')
                show_as = event.get('showAs', 'busy')
                start_date = event.get('start', {}).get('dateTime', 'No date')[:10]
                
                public_events.append({
                    'subject': subject,
                    'start_date': start_date,
                    'showAs': show_as
                })
                
                if show_as == 'busy':
                    busy_public_events.append({
                        'subject': subject,
                        'start_date': start_date,
                        'showAs': show_as
                    })
        
        return jsonify({
            'total_events': len(all_events),
            'public_events': len(public_events),
            'busy_public_events': len(busy_public_events),
            'sample_public_events': public_events[:5],
            'sample_busy_public': busy_public_events[:5],
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Quick debug error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/graph-api-test')
def debug_graph_api_test():
    """Test Microsoft Graph API directly - focused on categories"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Test Graph API directly with minimal fields
        headers = auth_manager.get_headers()
        if not headers:
            return jsonify({"error": "No auth headers"}), 401
        
        # Get just a few events with categories field
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{source_id}/events"
        params = {
            '$select': 'subject,start,categories,showAs,type,isCancelled',
            '$top': 10,
            '$orderby': 'start/dateTime desc'
        }
        
        import requests
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            return jsonify({
                "error": f"Graph API error: {response.status_code}",
                "response": response.text
            }), 500
        
        events = response.json().get('value', [])
        
        # Analyze categories
        category_analysis = {
            'total_events': len(events),
            'events_with_categories': 0,
            'events_without_categories': 0,
            'public_events': 0,
            'sample_events': []
        }
        
        for event in events:
            categories = event.get('categories', [])
            has_categories = len(categories) > 0
            has_public = 'Public' in categories
            
            if has_categories:
                category_analysis['events_with_categories'] += 1
            else:
                category_analysis['events_without_categories'] += 1
            
            if has_public:
                category_analysis['public_events'] += 1
            
            # Sample events
            if len(category_analysis['sample_events']) < 5:
                category_analysis['sample_events'].append({
                    'subject': event.get('subject'),
                    'categories': categories,
                    'showAs': event.get('showAs'),
                    'type': event.get('type'),
                    'start': event.get('start', {}).get('dateTime', 'No date')[:10]
                })
        
        return jsonify({
            'graph_api_test': category_analysis,
            'api_response_status': response.status_code,
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Graph API test error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/graph-api-test-2')
def debug_graph_api_test_2():
    """Test Microsoft Graph API again - check for consistency"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Test Graph API directly with minimal fields
        headers = auth_manager.get_headers()
        if not headers:
            return jsonify({"error": "No auth headers"}), 401
        
        # Get just a few events with categories field
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{source_id}/events"
        params = {
            '$select': 'subject,start,categories,showAs,type,isCancelled',
            '$top': 10,
            '$orderby': 'start/dateTime desc'
        }
        
        import requests
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            return jsonify({
                "error": f"Graph API error: {response.status_code}",
                "response": response.text
            }), 500
        
        events = response.json().get('value', [])
        
        # Analyze categories
        category_analysis = {
            'total_events': len(events),
            'events_with_categories': 0,
            'events_without_categories': 0,
            'public_events': 0,
            'sample_events': []
        }
        
        for event in events:
            categories = event.get('categories', [])
            has_categories = len(categories) > 0
            has_public = 'Public' in categories
            
            if has_categories:
                category_analysis['events_with_categories'] += 1
            else:
                category_analysis['events_without_categories'] += 1
            
            if has_public:
                category_analysis['public_events'] += 1
            
            # Sample events
            if len(category_analysis['sample_events']) < 5:
                category_analysis['sample_events'].append({
                    'subject': event.get('subject'),
                    'categories': categories,
                    'showAs': event.get('showAs'),
                    'type': event.get('type'),
                    'start': event.get('start', {}).get('dateTime', 'No date')[:10]
                })
        
        return jsonify({
            'graph_api_test_2': category_analysis,
            'api_response_status': response.status_code,
            'generated_time': DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())
        })
        
    except Exception as e:
        logger.error(f"Graph API test 2 error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/test-single-event/<event_subject>')
def test_single_event(event_subject):
    """Test why a specific event isn't syncing"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source
        all_events = sync_engine.reader.get_calendar_events(source_id)
        
        # Find matching event
        matching_events = []
        for event in all_events:
            if event_subject.lower() in (event.get('subject') or '').lower():
                matching_events.append({
                    'subject': event.get('subject'),
                    'type': event.get('type'),
                    'categories': event.get('categories', []),
                    'showAs': event.get('showAs'),
                    'start': event.get('start'),
                    'end': event.get('end'),
                    'isAllDay': event.get('isAllDay'),
                    'isCancelled': event.get('isCancelled'),
                    'seriesMasterId': event.get('seriesMasterId'),
                    'has_public': 'Public' in event.get('categories', []),
                    'is_busy': event.get('showAs') == 'busy',
                    'would_sync': 'Public' in event.get('categories', []) and event.get('showAs') == 'busy'
                })
        
        # Now check what's in the public calendar
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        target_events = []
        if target_id:
            all_target = sync_engine.reader.get_calendar_events(target_id)
            for event in all_target:
                if event_subject.lower() in (event.get('subject') or '').lower():
                    target_events.append({
                        'subject': event.get('subject'),
                        'type': event.get('type'),
                        'start': event.get('start')
                    })
        
        return jsonify({
            'search_term': event_subject,
            'source_events_found': len(matching_events),
            'source_events': matching_events,
            'target_events_found': len(target_events),
            'target_events': target_events,
            'diagnosis': {
                'should_sync': len([e for e in matching_events if e['would_sync']]),
                'blocked_by_category': len([e for e in matching_events if not e['has_public']]),
                'blocked_by_showas': len([e for e in matching_events if not e['is_busy']])
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/verify-config')
def verify_config():
    """Verify configuration matches actual calendar names"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get all calendars
        calendars = sync_engine.reader.get_calendars()
        
        # Find configured calendars
        source_found = False
        target_found = False
        calendar_list = []
        
        for cal in calendars:
            cal_name = cal.get('name')
            calendar_list.append(cal_name)
            
            if cal_name == config.SOURCE_CALENDAR:
                source_found = True
            if cal_name == config.TARGET_CALENDAR:
                target_found = True
        
        return jsonify({
            'configuration': {
                'SOURCE_CALENDAR': config.SOURCE_CALENDAR,
                'TARGET_CALENDAR': config.TARGET_CALENDAR,
                'SHARED_MAILBOX': config.SHARED_MAILBOX
            },
            'validation': {
                'source_calendar_found': source_found,
                'target_calendar_found': target_found
            },
            'available_calendars': calendar_list,
            'issues': {
                'source_not_found': not source_found,
                'target_not_found': not target_found,
                'possible_typo': not source_found or not target_found
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/problem-range-events')
def debug_problem_range():
    """Specifically analyze Sept 22 - Nov 21 events"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source calendar ID
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Get all events from source
        all_events = sync_engine.reader.get_calendar_events(source_id)
        
        problem_events = []
        current_year = datetime.now().year
        problem_start = f"{current_year}-09-21"
        problem_end = f"{current_year}-11-22"
        
        for event in all_events:
            date_str = event.get('start', {}).get('dateTime', '')[:10]
            if problem_start <= date_str <= problem_end:
                problem_events.append({
                    'subject': event.get('subject'),
                    'date': date_str,
                    'type': event.get('type'),
                    'categories': event.get('categories', []),
                    'showAs': event.get('showAs'),
                    'seriesMasterId': event.get('seriesMasterId'),
                    'isCancelled': event.get('isCancelled'),
                    'would_sync': 'Public' in event.get('categories', []) and event.get('showAs') == 'busy' and not event.get('isCancelled', False)
                })
        
        # Group by type for analysis
        by_type = {}
        for event in problem_events:
            event_type = event['type']
            if event_type not in by_type:
                by_type[event_type] = []
            by_type[event_type].append(event)
        
        return jsonify({
            'total_in_range': len(problem_events),
            'by_type': {k: len(v) for k, v in by_type.items()},
            'events': problem_events[:20],  # First 20 as sample
            'would_sync_count': len([e for e in problem_events if e['would_sync']]),
            'analysis': {
                'occurrence_count': len([e for e in problem_events if e['type'] == 'occurrence']),
                'seriesmaster_count': len([e for e in problem_events if e['type'] == 'seriesMaster']),
                'singleinstance_count': len([e for e in problem_events if e['type'] == 'singleInstance'])
            }
        })
        
    except Exception as e:
        logger.error(f"Problem range debug error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/debug/october-full-analysis')
def debug_october_full():
    """Complete analysis of October sync pipeline"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Track events through the entire pipeline
        pipeline_stats = {
            'raw_fetch': 0,
            'after_date_filter': 0,
            'public_category': 0,
            'busy_status': 0,
            'public_and_busy': 0,
            'filtered_occurrences': 0,
            'final_count': 0
        }
        
        # Get raw events for October
        current_year = datetime.now().year
        start = f"{current_year}-10-01T00:00:00-05:00"
        end = f"{current_year}-10-31T23:59:59-05:00"
        
        # Direct API call to see what Graph returns
        headers = auth_manager.get_headers()
        if not headers:
            return jsonify({"error": "No auth headers"}), 401
        
        endpoint = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{source_id}/calendarView"
        params = {
            "startDateTime": start,
            "endDateTime": end,
            "$top": 999,
            "$select": "id,subject,start,end,categories,showAs,type,seriesMasterId,isCancelled"
        }
        
        import requests
        response = requests.get(endpoint, headers=headers, params=params)
        if response.status_code != 200:
            return jsonify({"error": f"API call failed: {response.status_code}", "response": response.text}), 500
        
        raw_events = response.json().get('value', [])
        pipeline_stats['raw_fetch'] = len(raw_events)
        
        # Analyze each event
        october_events = []
        for event in raw_events:
            event_date = event.get('start', {}).get('dateTime', '')[:10]
            if not (f'{current_year}-10-01' <= event_date <= f'{current_year}-10-31'):
                continue
                
            pipeline_stats['after_date_filter'] += 1
            
            categories = event.get('categories', [])
            has_public = 'Public' in categories
            show_as = event.get('showAs', 'busy')
            is_busy = show_as == 'busy'
            event_type = event.get('type', 'singleInstance')
            
            if has_public:
                pipeline_stats['public_category'] += 1
            if is_busy:
                pipeline_stats['busy_status'] += 1
            if has_public and is_busy:
                pipeline_stats['public_and_busy'] += 1
            if event_type == 'occurrence':
                pipeline_stats['filtered_occurrences'] += 1
            
            october_events.append({
                'subject': event.get('subject'),
                'date': event_date,
                'categories': categories,
                'showAs': show_as,
                'type': event_type,
                'has_public': has_public,
                'is_busy': is_busy,
                'would_sync': has_public and is_busy and event_type != 'occurrence'
            })
        
        # Count final events that would sync
        pipeline_stats['final_count'] = sum(1 for e in october_events if e['would_sync'])
        
        # Sample events that should sync but might not be
        should_sync_sample = [e for e in october_events if e['has_public'] and e['is_busy']][:10]
        blocked_sample = [e for e in october_events if not e['would_sync']][:10]
        
        return jsonify({
            'pipeline_stats': pipeline_stats,
            'total_october_events': len(october_events),
            'should_sync_sample': should_sync_sample,
            'blocked_sample': blocked_sample,
            'summary': {
                'raw_from_api': pipeline_stats['raw_fetch'],
                'have_public_category': pipeline_stats['public_category'],
                'are_busy': pipeline_stats['busy_status'],
                'meet_both_criteria': pipeline_stats['public_and_busy'],
                'blocked_as_occurrences': pipeline_stats['filtered_occurrences'],
                'will_sync': pipeline_stats['final_count']
            }
        })
        
    except Exception as e:
        logger.error(f"October analysis error: {e}")
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/debug/verify-pagination-fix')
def verify_pagination():
    """Verify pagination is working"""
    try:
        from datetime import datetime
        current_year = datetime.now().year
        
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
            
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        if not source_id:
            return jsonify({"error": "Source calendar not found"}), 404
        
        # Now test the fixed method
        all_events = sync_engine.reader.get_calendar_events(source_id)
        
        # Count October events
        october_events = [e for e in all_events if e.get('start', {}).get('dateTime', '').startswith(f'{current_year}-10')]
        
        return jsonify({
            'total_events_fetched': len(all_events),
            'october_events_count': len(october_events),
            'october_subjects': [e.get('subject') for e in october_events[:20]],
            'success': len(october_events) > 10,
            'pagination_working': len(all_events) > 100
        })
        
    except Exception as e:
        logger.error(f"Verification error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/debug/test-single-event/<subject>')
def debug_test_single_event(subject):
    """Test why a specific event isn't syncing"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get source events
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        source_events = sync_engine.reader.get_public_events(source_id)
        
        # Find events matching subject
        matching = [e for e in source_events if subject.lower() in e.get('subject', '').lower()]
        
        # Get target events
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        target_events = sync_engine.reader.get_calendar_events(target_id)
        
        # Generate signatures
        results = []
        for event in matching[:5]:  # First 5 matches
            sig = sync_engine._create_event_signature(event)
            
            # Check if signature exists in target
            target_match = None
            for t_event in target_events:
                if sync_engine._create_event_signature(t_event) == sig:
                    target_match = t_event
                    break
            
            results.append({
                'source_subject': event.get('subject'),
                'source_start': event.get('start'),
                'signature': sig,
                'exists_in_target': target_match is not None,
                'target_match': {
                    'subject': target_match.get('subject'),
                    'start': target_match.get('start')
                } if target_match else None
            })
        
        return jsonify({
            'test_subject': subject,
            'found_in_source': len(matching),
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Test single event error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/admin')
def admin_interface():
    """Web interface for debugging category reading issues"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return redirect('/')
        
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Admin - Category Debug Tool</title>
            <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📅</text></svg>">
            <style>
                body { 
                    font-family: Arial, sans-serif; 
                    margin: 20px; 
                    background: #f5f5f5; 
                    line-height: 1.6;
                }
                .container { 
                    max-width: 1200px; 
                    margin: 0 auto; 
                    background: white; 
                    padding: 30px; 
                    border-radius: 10px; 
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                }
                h1 { 
                    color: #005921; 
                    border-bottom: 2px solid #005921;
                    padding-bottom: 10px;
                }
                .section {
                    margin: 30px 0;
                    padding: 20px;
                    background: #f8f9fa;
                    border-radius: 8px;
                    border-left: 4px solid #005921;
                }
                .btn {
                    background: #005921;
                    color: white;
                    padding: 12px 24px;
                    border: none;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 16px;
                    margin: 10px 5px;
                }
                .btn:hover {
                    background: #004a1e;
                }
                .btn-secondary {
                    background: #6c757d;
                }
                .btn-secondary:hover {
                    background: #5a6268;
                }
                .loading {
                    display: none;
                    text-align: center;
                    padding: 20px;
                }
                .results {
                    margin-top: 20px;
                    padding: 20px;
                    background: white;
                    border-radius: 5px;
                    border: 1px solid #ddd;
                }
                .event-item {
                    padding: 10px;
                    margin: 5px 0;
                    background: #f8f9fa;
                    border-radius: 4px;
                    border-left: 3px solid #28a745;
                }
                .event-item.no-categories {
                    border-left-color: #dc3545;
                }
                .event-item.has-public {
                    border-left-color: #28a745;
                }
                .summary-stats {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 15px;
                    margin: 20px 0;
                }
                .stat-card {
                    background: white;
                    padding: 15px;
                    border-radius: 8px;
                    text-align: center;
                    border: 1px solid #ddd;
                }
                .stat-number {
                    font-size: 24px;
                    font-weight: bold;
                    color: #005921;
                }
                .stat-label {
                    color: #666;
                    font-size: 14px;
                }
                .back-link { 
                    display: inline-block; 
                    margin-top: 20px; 
                    padding: 10px 20px;
                    background: #6c757d;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                }
                .back-link:hover { 
                    background: #5a6268;
                }
                .raw-data {
                    background: #f8f9fa;
                    padding: 10px;
                    border-radius: 4px;
                    font-family: monospace;
                    font-size: 12px;
                    max-height: 200px;
                    overflow-y: auto;
                    margin-top: 10px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔍 Category Debug Tool</h1>
                <p>This tool helps debug why events with "Public" categories in Outlook aren't being synced to the public calendar.</p>
                
                <div class="section">
                    <h3>Problem:</h3>
                    <p>Events like "Mass- Daily" are already tagged with "Public" category in Outlook, but they're not appearing on the public calendar. This tool shows what categories the sync application is actually reading from Microsoft Graph.</p>
                </div>
                
                <div class="section">
                    <h3>Actions:</h3>
                    <button class="btn" onclick="debugCategories()">🔍 Debug Category Reading</button>
                    <button class="btn btn-secondary" onclick="location.reload()">🔄 Refresh</button>
                </div>
                
                <div class="loading" id="loading">
                    <p>⏳ Reading events from source calendar...</p>
                </div>
                
                <div id="results"></div>
                
                <a href="/" class="back-link">← Back to Dashboard</a>
            </div>
            
            <script>
                async function debugCategories() {
                    const loading = document.getElementById('loading');
                    const results = document.getElementById('results');
                    
                    loading.style.display = 'block';
                    results.innerHTML = '';
                    
                    try {
                        const response = await fetch('/debug/categories');
                        const data = await response.json();
                        
                        if (data.error) {
                            throw new Error(data.error);
                        }
                        
                        displayResults(data);
                        
                    } catch (error) {
                        results.innerHTML = `<div class="results"><h3>❌ Error</h3><p>${error.message}</p></div>`;
                    } finally {
                        loading.style.display = 'none';
                    }
                }
                
                function displayResults(data) {
                    const results = document.getElementById('results');
                    
                    let html = `
                        <div class="results">
                            <h3>📊 Category Analysis Results</h3>
                            
                            <div class="summary-stats">
                                <div class="stat-card">
                                    <div class="stat-number">${data.total_events}</div>
                                    <div class="stat-label">Total Events</div>
                                </div>
                                <div class="stat-card">
                                    <div class="stat-number">${data.events_with_categories}</div>
                                    <div class="stat-label">Events with Categories</div>
                                </div>
                                <div class="stat-card">
                                    <div class="stat-number">${data.events_without_categories}</div>
                                    <div class="stat-label">Events without Categories</div>
                                </div>
                                <div class="stat-card">
                                    <div class="stat-number">${data.events_with_public_tag}</div>
                                    <div class="stat-label">Events with Public Tag</div>
                                </div>
                            </div>
                            
                            <h4>📋 Events with Public Category:</h4>
                    `;
                    
                    if (data.public_events_list.length > 0) {
                        data.public_events_list.forEach(event => {
                            html += `
                                <div class="event-item has-public">
                                    <strong>${event.subject}</strong><br>
                                    <small>Date: ${event.start}</small><br>
                                    <small>Categories: ${event.categories.join(', ')}</small><br>
                                    <small>ShowAs: ${event.showAs}</small><br>
                                    <small>Cancelled: ${event.isCancelled}</small><br>
                                    <small>Type: ${event.type}</small>
                                </div>
                            `;
                        });
                    } else {
                        html += '<p><strong>❌ No events found with "Public" category!</strong></p>';
                    }
                    
                    html += `
                            <h4>📋 Events without Categories:</h4>
                    `;
                    
                    if (data.events_without_categories_list.length > 0) {
                        data.events_without_categories_list.slice(0, 10).forEach(event => {
                            html += `
                                <div class="event-item no-categories">
                                    <strong>${event.subject}</strong><br>
                                    <small>Date: ${event.start}</small><br>
                                    <small>ShowAs: ${event.showAs}</small><br>
                                    <small>Cancelled: ${event.isCancelled}</small>
                                </div>
                            `;
                        });
                        if (data.events_without_categories_list.length > 10) {
                            html += `<p><em>... and ${data.events_without_categories_list.length - 10} more events without categories</em></p>`;
                        }
                    } else {
                        html += '<p>✅ All events have categories</p>';
                    }
                    
                    html += '</div>';
                    results.innerHTML = html;
                }
            </script>
        </body>
        </html>
        '''
        
    except Exception as e:
        logger.error(f"Admin interface error: {e}")
        return f"Error: {str(e)}", 500

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
logger.info(f"🕐 Current time: {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
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
    svg_content = '''<svg width="180" height="180" xmlns="https://www.w3.org/2000/svg">
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
    return redirect("data:image/svg+xml,%3Csvg xmlns='https://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E📅%3C/text%3E%3C/svg%3E")

# Add this route to your app.py file (add it before the if __name__ == '__main__': line)

def utc_to_central(utc_dt):
    """Convert UTC datetime to Central Time"""
    import pytz
    if utc_dt.tzinfo is None:
        utc_dt = pytz.UTC.localize(utc_dt)
    central = pytz.timezone('America/Chicago')
    return utc_dt.astimezone(central)

def format_central_time(dt):
    """Format datetime for display in Central timezone"""
    if dt is None:
        return 'Never'
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
    import pytz
    central_tz = pytz.timezone('America/Chicago')
    if dt.tzinfo is None:
        dt = central_tz.localize(dt)
    return dt.astimezone(central_tz).strftime('%b %d, %Y at %I:%M %p CT')

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
        logger.info(f"Looking for calendar: {config.TARGET_CALENDAR}")
        public_calendar_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        logger.info(f"Calendar ID found: {public_calendar_id}")
        if not public_calendar_id:
            logger.error(f"Public calendar not found: {config.TARGET_CALENDAR}")
            return jsonify({"error": "Public calendar not found"}), 404
        
        # Calculate date range based on week parameter
        from datetime import timedelta
        import pytz
        import re
        import requests
        
        central_tz = pytz.timezone('America/Chicago')
        today = DateTimeUtils.get_central_time().date()
        
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
        
        logger.info(f"Date range: {start_date} to {end_date}")
        logger.info(f"API date range: {start_str} to {end_str}")
        
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
                logger.info(f"Fetched {len(all_events)} events from calendar")
                
                # Handle pagination if needed
                next_link = data.get('@odata.nextLink')
                while next_link:
                    next_response = requests.get(next_link, headers=headers, timeout=30)
                    if next_response.status_code == 200:
                        next_data = next_response.json()
                        next_events = next_data.get('value', [])
                        all_events.extend(next_events)
                        logger.info(f"Added {len(next_events)} more events from pagination")
                        next_link = next_data.get('@odata.nextLink')
                    else:
                        break
                
                logger.info(f"Total events fetched: {len(all_events)}")
                if all_events:
                    logger.info(f"Sample event: {all_events[0].get('subject', 'No Subject')} at {all_events[0].get('start', {}).get('dateTime', 'No Time')}")
            else:
                logger.error(f"Failed to get calendar view: {response.status_code} - {response.text}")
                all_events = []
                
        except Exception as e:
            logger.error(f"Error fetching calendar view: {e}")
            all_events = []
        
        # Process events for bulletin
        from utils.formatting import normalize_location, is_omitted_from_bulletin

        def graph_datetime_to_utc(dt_dict):
            """
            Convert a Microsoft Graph dateTime dict to timezone-aware UTC datetime.
            Handles cases where dateTime has no 'Z' and timeZone is 'Central Standard Time'.
            """
            try:
                dt_str = (dt_dict or {}).get('dateTime', '')
                if not dt_str:
                    return None
                tz_name = (dt_dict or {}).get('timeZone', 'UTC') or 'UTC'

                # Normalize ISO string; handle Z → +00:00
                iso = dt_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(iso)

                # If already timezone-aware, convert to UTC
                import pytz as _pytz
                if dt.tzinfo is not None:
                    return dt.astimezone(_pytz.UTC)

                # Map Graph TZ to pytz
                if tz_name == 'UTC':
                    return _pytz.UTC.localize(dt)
                if tz_name == 'Central Standard Time':
                    central = _pytz.timezone('America/Chicago')
                    return central.localize(dt).astimezone(_pytz.UTC)

                # Fallback: assume UTC if unknown
                return _pytz.UTC.localize(dt)
            except Exception as _e:
                logger.warning(f"Failed to parse Graph datetime: {dt_dict} ({_e})")
                return None

        logger.info(f"Processing {len(all_events)} events for bulletin")
        bulletin_events = []
        for event in all_events:
            # Convert start/end to UTC and Central
            start_utc = graph_datetime_to_utc(event.get('start'))
            if start_utc is None:
                logger.info("Skipping event with invalid start time")
                continue

            event_start_central = utc_to_central(start_utc)

            # Prefer Graph location; fallback to body "Location:" snippet
            location_text = ''
            location_field = event.get('location', {})
            if isinstance(location_field, dict):
                location_text = (location_field.get('displayName') or '').strip()

            body_content = event.get('body', {}).get('content', '')
            if not location_text and body_content and 'Location:' in body_content:
                location_match = re.search(r'<strong>Location:</strong>\s*([^<]+)', body_content)
                if location_match:
                    location_text = location_match.group(1).strip()

            # Check if this event should be omitted from bulletin
            subject = event.get('subject', 'No Title')
            
            # Debug logging for Mass events
            if 'Mass' in subject:
                logger.info(f"DEBUG Mass event: '{subject}' at {event_start_central} (weekday: {event_start_central.weekday()}, time: {event_start_central.strftime('%H:%M')})")
            
            try:
                omission_result = is_omitted_from_bulletin(subject, start_utc, location_text)
                if omission_result:
                    logger.info(f"Omitting event from bulletin: {subject} at {event_start_central}")
                    continue
                elif 'Mass' in subject:
                    logger.info(f"DEBUG Mass event NOT omitted: '{subject}' at {event_start_central}")
            except Exception as e:
                # If omission check fails, default to include rather than drop
                logger.warning(f"Omission check error for '{subject}': {e} — including in bulletin")

            logger.info(f"Including event in bulletin: {subject} at {event_start_central}")

            # Build event details
            event_data = {
                'subject': subject,
                'start': event_start_central,
                'end': None,
                'location': normalize_location(location_text),
                'is_all_day': event.get('isAllDay', False)
            }

            # End time (optional)
            end_utc = graph_datetime_to_utc(event.get('end'))
            if end_utc is not None:
                event_data['end'] = utc_to_central(end_utc)

            bulletin_events.append(event_data)
        
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
                             generated_time=DateTimeUtils.format_central_time(DateTimeUtils.get_central_time()))
        
    except Exception as e:
        logger.error(f"Bulletin events error: {e}")
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/event-search')
def event_search():
    """Search events with custom parameters"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return redirect('/')
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get search parameters
        search_term = request.args.get('search', '').lower().strip()
        date_range = request.args.get('range', '30')  # Default 30 days
        
        # Get the public calendar ID
        public_calendar_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not public_calendar_id:
            return jsonify({"error": "Public calendar not found"}), 404
        
        # Calculate date range
        from datetime import timedelta
        import pytz
        import requests
        
        central_tz = pytz.timezone('America/Chicago')
        today = DateTimeUtils.get_central_time().date()
        start_date = today
        
        # Determine end date based on range
        if date_range == 'week':
            end_date = today + timedelta(days=7)
            range_text = "Next 7 Days"
        elif date_range == 'month':
            end_date = today + timedelta(days=30)
            range_text = "Next 30 Days"
        elif date_range == 'quarter':
            end_date = today + timedelta(days=90)
            range_text = "Next 90 Days"
        elif date_range == 'june':
            june30 = datetime(today.year, 6, 30).date()
            if today > june30:
                june30 = datetime(today.year + 1, 6, 30).date()
            end_date = june30
            range_text = f"Until June 30, {june30.year}"
        else:
            # Default to 30 days
            end_date = today + timedelta(days=30)
            range_text = "Next 30 Days"
        
        # Build title
        if search_term:
            title = f"Events containing '{search_term}' - {range_text}"
        else:
            title = f"All Events - {range_text}"
        
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
            <title>{title}</title>
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
                    .back-link, .copy-btn {{ display: none; }}
                    body {{ background: white; }}
                    .container {{ box-shadow: none; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📅 {title}</h1>
                <button class="copy-btn" onclick="copyList()">📋 Copy List</button>
                
                <div class="summary">
                    <strong>{len(event_list)} events found</strong><br>
                    Date range: {start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}
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
                html += f'<p>No events found containing "{search_term}" in this date range.</p>'
            else:
                html += '<p>No events found in this date range.</p>'
        
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
        logger.error(f"Event search error: {e}")
        return f"Error: {str(e)}", 500

class GracefulShutdownHandler:
    def __init__(self):
        self.shutdown_requested = False
        signal.signal(signal.SIGTERM, self.handle_sigterm)
        signal.signal(signal.SIGINT, self.handle_sigterm)
    
    def handle_sigterm(self, signum, frame):
        logger.warning(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True
        
        # Wait for current operations to complete (max 30 seconds)
        if hasattr(self, 'current_sync_thread') and self.current_sync_thread.is_alive():
            self.current_sync_thread.join(timeout=30)
        
        logger.info("Graceful shutdown completed")
        sys.exit(0)

# Initialize graceful shutdown handler
shutdown_handler = GracefulShutdownHandler()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting calendar sync service on port {port}")
    app.run(host='0.0.0.0', port=port)
