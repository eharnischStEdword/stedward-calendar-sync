#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Main Application (Debug Version)
"""
import os
import logging
import secrets
import threading
import sys
import time
import traceback
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, session, url_for

# Configure logging FIRST
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app EARLY
app = Flask(__name__)

# Basic configuration
try:
    import config
    app.secret_key = getattr(config, 'SECRET_KEY', None) or secrets.token_hex(32)
    logger.info("✅ Config loaded successfully")
except Exception as e:
    logger.error(f"❌ Config import failed: {e}")
    app.secret_key = secrets.token_hex(32)

# Global state tracking
INITIALIZATION_STATUS = {
    'completed': False,
    'error': None,
    'progress': 'Starting...',
    'components': {}
}

# Initialize components with error handling
auth_manager = None
sync_engine = None
scheduler = None
audit_logger = None
APP_VERSION_INFO = {"version": "2025.06.26", "full_version": "Calendar Sync v2025.06.26"}

def safe_import_components():
    """Import components with detailed error reporting"""
    global auth_manager, sync_engine, scheduler, audit_logger, APP_VERSION_INFO, INITIALIZATION_STATUS
    
    try:
        INITIALIZATION_STATUS['progress'] = 'Loading authentication...'
        logger.info("🔄 Loading authentication module...")
        
        from auth.microsoft_auth import MicrosoftAuth
        auth_manager = MicrosoftAuth()
        INITIALIZATION_STATUS['components']['auth'] = True
        logger.info("✅ Auth manager initialized")
        
    except Exception as e:
        error_msg = f"Auth manager failed: {e}"
        logger.error(f"❌ {error_msg}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        INITIALIZATION_STATUS['components']['auth'] = False
        INITIALIZATION_STATUS['error'] = error_msg
        return False
    
    try:
        INITIALIZATION_STATUS['progress'] = 'Loading sync engine...'
        logger.info("🔄 Loading sync engine...")
        
        from sync.engine import SyncEngine
        sync_engine = SyncEngine(auth_manager)
        INITIALIZATION_STATUS['components']['sync'] = True
        logger.info("✅ Sync engine initialized")
        
    except Exception as e:
        error_msg = f"Sync engine failed: {e}"
        logger.error(f"❌ {error_msg}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        INITIALIZATION_STATUS['components']['sync'] = False
        INITIALIZATION_STATUS['error'] = error_msg
        return False
    
    try:
        INITIALIZATION_STATUS['progress'] = 'Loading scheduler...'
        logger.info("🔄 Loading scheduler...")
        
        from sync.scheduler import SyncScheduler
        scheduler = SyncScheduler(sync_engine)
        INITIALIZATION_STATUS['components']['scheduler'] = True
        logger.info("✅ Scheduler initialized")
        
    except Exception as e:
        error_msg = f"Scheduler failed: {e}"
        logger.error(f"❌ {error_msg}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        INITIALIZATION_STATUS['components']['scheduler'] = False
        INITIALIZATION_STATUS['error'] = error_msg
        return False
    
    try:
        INITIALIZATION_STATUS['progress'] = 'Loading utilities...'
        logger.info("🔄 Loading audit logger...")
        
        from utils.audit import AuditLogger
        audit_logger = AuditLogger()
        INITIALIZATION_STATUS['components']['audit'] = True
        logger.info("✅ Audit logger initialized")
        
    except Exception as e:
        logger.warning(f"⚠️ Audit logger failed: {e}")
        INITIALIZATION_STATUS['components']['audit'] = False
        
    try:
        logger.info("🔄 Loading version info...")
        from utils.version import get_version_info
        APP_VERSION_INFO = get_version_info()
        INITIALIZATION_STATUS['components']['version'] = True
        logger.info("✅ Version info loaded")
        
    except Exception as e:
        logger.warning(f"⚠️ Version info failed: {e}")
        INITIALIZATION_STATUS['components']['version'] = False
    
    INITIALIZATION_STATUS['progress'] = 'Completed'
    INITIALIZATION_STATUS['completed'] = True
    return True

# Log startup
logger.info("🚀 Starting St. Edward Calendar Sync")
logger.info(f"🐍 Python version: {sys.version}")
logger.info(f"📦 Environment: {os.environ.get('RENDER', 'local')}")

# Simple decorator for auth (fallback version)
def requires_auth(f):
    """Simple auth decorator with fallback"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not INITIALIZATION_STATUS['completed']:
            return jsonify({"error": "System still initializing"}), 503
        
        if not auth_manager:
            return jsonify({"error": "Auth system not available"}), 503
        
        if not hasattr(auth_manager, 'access_token') or not auth_manager.access_token:
            return jsonify({"error": "Not authenticated", "redirect": "/logout"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

# ==================== BASIC ROUTES ====================

@app.route('/health')
def health_check():
    """Ultra-simple health check"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "calendar-sync",
        "initialization": INITIALIZATION_STATUS['completed']
    }), 200

@app.route('/init-status')
def init_status():
    """Get initialization status"""
    return jsonify(INITIALIZATION_STATUS)

@app.route('/')
def index():
    """Main page with detailed error handling"""
    try:
        if not INITIALIZATION_STATUS['completed']:
            error_info = ""
            if INITIALIZATION_STATUS['error']:
                error_info = f"<p style='color: red; margin-top: 20px;'>Error: {INITIALIZATION_STATUS['error']}</p>"
            
            return f'''
            <html>
            <head>
                <title>St. Edward Calendar Sync - Initializing</title>
                <meta http-equiv="refresh" content="3">
            </head>
            <body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>🔧 System Initializing</h1>
                <p>Progress: {INITIALIZATION_STATUS['progress']}</p>
                <p>Please wait while the system starts up...</p>
                {error_info}
                <div style="margin-top: 30px;">
                    <h3>Component Status:</h3>
                    <ul style="text-align: left; display: inline-block;">
                        <li>Auth: {"✅" if INITIALIZATION_STATUS['components'].get('auth') else "⏳" if INITIALIZATION_STATUS['components'].get('auth') is None else "❌"}</li>
                        <li>Sync: {"✅" if INITIALIZATION_STATUS['components'].get('sync') else "⏳" if INITIALIZATION_STATUS['components'].get('sync') is None else "❌"}</li>
                        <li>Scheduler: {"✅" if INITIALIZATION_STATUS['components'].get('scheduler') else "⏳" if INITIALIZATION_STATUS['components'].get('scheduler') is None else "❌"}</li>
                        <li>Audit: {"✅" if INITIALIZATION_STATUS['components'].get('audit') else "⏳" if INITIALIZATION_STATUS['components'].get('audit') is None else "❌"}</li>
                    </ul>
                </div>
                <p><a href="/init-status">View Raw Status</a></p>
            </body>
            </html>
            '''
        
        if not auth_manager:
            return '<html><body><h1>❌ System Error</h1><p>Authentication system failed to load.</p></body></html>'
        
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
                <p>Sign in with your Microsoft account to access calendar sync.</p>
                <a href="{auth_url}" style="background: #0078d4; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 18px;">
                    Sign in with Microsoft
                </a>
                <p style="margin-top: 30px; font-size: 12px; color: #666;">
                    System fully loaded and ready ✅
                </p>
            </body>
            </html>
            '''
        
        # Try to render the full template
        try:
            status = sync_engine.get_status() if sync_engine else {}
            return render_template('index.html', 
                                 last_sync_time=None,
                                 last_sync_result=status.get('last_sync_result'),
                                 sync_in_progress=status.get('sync_in_progress', False))
        except Exception as e:
            logger.error(f"Template render failed: {e}")
            return f'''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>📅 Calendar Sync Dashboard</h1>
                <p>Welcome! System is running but full dashboard is loading...</p>
                <p><a href="/health">Health Check</a> | <a href="/status">Status</a></p>
                <p style="color: red;">Template error: {e}</p>
            </body></html>
            '''
            
    except Exception as e:
        logger.error(f"Index route failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return f'''
        <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>⚠️ System Error</h1>
            <p>Error: {e}</p>
            <p><a href="/init-status">Check System Status</a></p>
        </body></html>
        '''

@app.route('/auth/callback')
def auth_callback():
    """Handle OAuth callback"""
    try:
        if not INITIALIZATION_STATUS['completed']:
            return "System not ready", 503
            
        if not auth_manager:
            return "Auth system not available", 503
            
        auth_code = request.args.get('code')
        state = request.args.get('state')
        
        if state != session.get('oauth_state'):
            return "Invalid state", 400
        
        if auth_code and auth_manager.exchange_code_for_token(auth_code):
            session.pop('oauth_state', None)
            if scheduler:
                scheduler.start()
            return redirect(url_for('index'))
        else:
            return "Authentication failed", 400
    except Exception as e:
        logger.error(f"Auth callback failed: {e}")
        return f"Authentication error: {e}", 500

@app.route('/status')
def status():
    """Get current status"""
    try:
        if not INITIALIZATION_STATUS['completed']:
            return jsonify({
                "authenticated": False,
                "scheduler_running": False,
                "sync_in_progress": False,
                "system_status": "initializing",
                "initialization": INITIALIZATION_STATUS
            })
        
        if not sync_engine:
            return jsonify({
                "authenticated": False,
                "scheduler_running": False,
                "sync_in_progress": False,
                "system_status": "degraded - sync engine not available"
            })
        
        sync_status = sync_engine.get_status()
        
        return jsonify({
            **sync_status,
            "authenticated": auth_manager.is_authenticated() if auth_manager else False,
            "scheduler_running": scheduler.is_running() if scheduler else False,
            "version": APP_VERSION_INFO.get("version", "unknown"),
            "system_status": "ready",
            "initialization": INITIALIZATION_STATUS
        })
    except Exception as e:
        logger.error(f"Status failed: {e}")
        return jsonify({
            "error": str(e),
            "system_status": "error",
            "initialization": INITIALIZATION_STATUS
        }), 500

@app.route('/sync', methods=['POST'])
@requires_auth
def manual_sync():
    """Trigger manual sync"""
    if not sync_engine:
        return jsonify({"error": "Sync engine not available"}), 503
    
    def sync_thread():
        try:
            result = sync_engine.sync_calendars()
            logger.info(f"Sync result: {result}")
        except Exception as e:
            logger.error(f"Sync failed: {e}")
    
    thread = threading.Thread(target=sync_thread)
    thread.start()
    
    return jsonify({"success": True, "message": "Sync started"})

@app.route('/logout')
def logout():
    """Logout"""
    try:
        if auth_manager:
            auth_manager.clear_tokens()
        if scheduler:
            scheduler.stop()
        session.clear()
    except Exception as e:
        logger.error(f"Logout error: {e}")
    
    return redirect(url_for('index'))

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    return jsonify({"error": "Internal server error", "details": str(error)}), 500

# ==================== INITIALIZATION ====================

def initialize_app():
    """Initialize app components in a thread"""
    logger.info("🔄 Initializing components...")
    
    try:
        success = safe_import_components()
        
        if success:
            logger.info("✅ All components initialized successfully")
            
            # Try to restore auth
            try:
                if auth_manager and auth_manager.is_authenticated():
                    if scheduler:
                        scheduler.start()
                    logger.info("🔐 Authentication restored")
            except Exception as e:
                logger.warning(f"Auth restoration failed: {e}")
        else:
            logger.error("❌ Component initialization failed")
    except Exception as e:
        error_msg = f"Critical initialization error: {e}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        INITIALIZATION_STATUS['error'] = error_msg
        INITIALIZATION_STATUS['progress'] = 'Failed'

# Start initialization in background (don't block app startup)
init_thread = threading.Thread(target=initialize_app, daemon=True)
init_thread.start()

logger.info("🎉 Flask app ready - components initializing in background")

# ==================== MAIN ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
