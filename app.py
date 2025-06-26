#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Main Application (Bypass Auth Debug)
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
app.secret_key = secrets.token_hex(32)

# Global state tracking
INITIALIZATION_STATUS = {
    'completed': False,
    'error': None,
    'progress': 'Starting...',
    'components': {},
    'detailed_errors': []
}

# Check environment first
def check_environment():
    """Check required environment variables"""
    required_vars = ['CLIENT_SECRET', 'CLIENT_ID', 'TENANT_ID']
    missing = []
    present = []
    
    for var in required_vars:
        if os.environ.get(var):
            present.append(var)
        else:
            missing.append(var)
    
    INITIALIZATION_STATUS['env_check'] = {
        'present': present,
        'missing': missing,
        'total_env_vars': len(os.environ)
    }
    
    logger.info(f"Environment check - Present: {present}, Missing: {missing}")
    return len(missing) == 0

def test_imports():
    """Test each import individually"""
    results = {}
    
    # Test config import
    try:
        import config
        results['config'] = {'success': True, 'details': 'OK'}
        logger.info("✅ Config import successful")
        
        # Check config attributes
        config_attrs = []
        for attr in ['CLIENT_SECRET', 'CLIENT_ID', 'TENANT_ID', 'SHARED_MAILBOX']:
            if hasattr(config, attr):
                value = getattr(config, attr)
                config_attrs.append(f"{attr}: {'SET' if value else 'EMPTY'}")
            else:
                config_attrs.append(f"{attr}: MISSING")
        
        results['config']['attributes'] = config_attrs
        
    except Exception as e:
        error_msg = f"Config import failed: {e}"
        results['config'] = {'success': False, 'error': error_msg}
        logger.error(f"❌ {error_msg}")
        logger.error(traceback.format_exc())
    
    # Test auth module imports step by step
    try:
        # Test basic Python imports first
        import requests
        import urllib.parse
        from datetime import datetime, timedelta
        results['basic_imports'] = {'success': True}
        logger.info("✅ Basic imports OK")
    except Exception as e:
        results['basic_imports'] = {'success': False, 'error': str(e)}
        logger.error(f"❌ Basic imports failed: {e}")
    
    try:
        # Test Azure imports
        from azure.core.credentials import AccessToken
        results['azure_core'] = {'success': True}
        logger.info("✅ Azure core import OK")
    except Exception as e:
        results['azure_core'] = {'success': False, 'error': str(e)}
        logger.error(f"❌ Azure core import failed: {e}")
    
    try:
        # Test msgraph import
        from msgraph import GraphServiceClient
        results['msgraph'] = {'success': True}
        logger.info("✅ MSGraph import OK")
    except Exception as e:
        results['msgraph'] = {'success': False, 'error': str(e)}
        logger.error(f"❌ MSGraph import failed: {e}")
    
    try:
        # Test threading import
        from threading import Lock
        results['threading'] = {'success': True}
        logger.info("✅ Threading import OK")
    except Exception as e:
        results['threading'] = {'success': False, 'error': str(e)}
        logger.error(f"❌ Threading import failed: {e}")
    
    # Now try the actual auth module
    try:
        from auth.microsoft_auth import MicrosoftAuth
        auth_manager = MicrosoftAuth()
        results['auth_module'] = {'success': True}
        logger.info("✅ Auth module import successful")
    except Exception as e:
        error_msg = f"Auth module import failed: {e}"
        results['auth_module'] = {'success': False, 'error': error_msg}
        logger.error(f"❌ {error_msg}")
        logger.error(traceback.format_exc())
    
    return results

# ==================== ROUTES ====================

@app.route('/health')
def health_check():
    """Ultra-simple health check"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "calendar-sync"
    }), 200

@app.route('/debug')
def debug_info():
    """Get detailed debug information"""
    env_ok = check_environment()
    import_results = test_imports()
    
    return jsonify({
        'timestamp': datetime.now().isoformat(),
        'environment_check': INITIALIZATION_STATUS.get('env_check', {}),
        'environment_ok': env_ok,
        'import_tests': import_results,
        'python_version': sys.version,
        'working_directory': os.getcwd(),
        'python_path': sys.path[:3]  # First 3 entries
    })

@app.route('/init-status')
def init_status():
    """Get initialization status"""
    return jsonify(INITIALIZATION_STATUS)

@app.route('/')
def index():
    """Main page with detailed diagnostics"""
    try:
        # Check environment first
        env_ok = check_environment()
        
        if not env_ok:
            missing_vars = INITIALIZATION_STATUS['env_check']['missing']
            return f'''
            <html>
            <head>
                <title>St. Edward Calendar Sync - Configuration Error</title>
                <meta http-equiv="refresh" content="10">
            </head>
            <body style="font-family: Arial; text-align: center; margin-top: 50px;">
                <h1>⚠️ Configuration Error</h1>
                <p>Missing required environment variables:</p>
                <ul style="text-align: left; display: inline-block; color: red;">
                    {"".join([f"<li>{var}</li>" for var in missing_vars])}
                </ul>
                <p><strong>Action Required:</strong></p>
                <ol style="text-align: left; display: inline-block;">
                    <li>Go to your Render dashboard</li>
                    <li>Navigate to Environment Variables</li>
                    <li>Add the missing variables listed above</li>
                    <li>Redeploy the service</li>
                </ol>
                <p><a href="/debug">View Full Debug Info</a></p>
            </body>
            </html>
            '''
        
        # Test imports
        import_results = test_imports()
        failed_imports = [name for name, result in import_results.items() if not result['success']]
        
        if failed_imports:
            return f'''
            <html>
            <head>
                <title>St. Edward Calendar Sync - Import Error</title>
                <meta http-equiv="refresh" content="10">
            </head>
            <body style="font-family: Arial; text-align: center; margin-top: 50px;">
                <h1>⚠️ Import Error</h1>
                <p>Failed to import required modules:</p>
                <div style="text-align: left; display: inline-block; background: #f0f0f0; padding: 20px; border-radius: 5px;">
                    {"".join([f"<p><strong>{name}:</strong> {import_results[name].get('error', 'Unknown error')}</p>" for name in failed_imports])}
                </div>
                <p><a href="/debug">View Full Debug Info</a></p>
                <p style="font-size: 12px; margin-top: 30px;">
                    This usually indicates a dependency issue or missing files.
                </p>
            </body>
            </html>
            '''
        
        # If we get here, everything should work - try to load the auth module
        try:
            from auth.microsoft_auth import MicrosoftAuth
            auth_manager = MicrosoftAuth()
            
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
                    <p>System loaded successfully! Sign in to continue.</p>
                    <a href="{auth_url}" style="background: #0078d4; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 18px;">
                        Sign in with Microsoft
                    </a>
                    <p style="margin-top: 30px; font-size: 12px; color: #666;">
                        All systems ready ✅
                    </p>
                </body>
                </html>
                '''
            else:
                return '''
                <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                    <h1>📅 Calendar Sync Dashboard</h1>
                    <p>Authentication successful! Dashboard loading...</p>
                    <p><em>Full dashboard will be available once all components are loaded.</em></p>
                </body></html>
                '''
                
        except Exception as e:
            return f'''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>⚠️ Runtime Error</h1>
                <p>Error creating auth manager: {e}</p>
                <p><a href="/debug">View Debug Info</a></p>
            </body></html>
            '''
            
    except Exception as e:
        logger.error(f"Index route failed: {e}")
        logger.error(traceback.format_exc())
        return f'''
        <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>⚠️ Critical Error</h1>
            <p>Error: {e}</p>
            <p><a href="/debug">View Debug Info</a></p>
        </body></html>
        '''

@app.route('/test-auth')
def test_auth():
    """Test authentication setup without full initialization"""
    try:
        # Check if we can create the auth manager
        from auth.microsoft_auth import MicrosoftAuth
        auth = MicrosoftAuth()
        
        return jsonify({
            'success': True,
            'message': 'Auth manager created successfully',
            'has_tokens': hasattr(auth, 'access_token') and auth.access_token is not None
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({
        "error": "Internal server error", 
        "details": str(error),
        "debug_url": "/debug"
    }), 500

# ==================== STARTUP ====================

logger.info("🚀 Starting St. Edward Calendar Sync (Debug Mode)")
logger.info(f"🐍 Python version: {sys.version}")
logger.info(f"📂 Working directory: {os.getcwd()}")
logger.info(f"📦 Environment: {os.environ.get('RENDER', 'local')}")

# Log some basic environment info
logger.info(f"🔧 Total environment variables: {len(os.environ)}")
logger.info(f"🔧 PORT: {os.environ.get('PORT', 'not set')}")
logger.info(f"🔧 CLIENT_SECRET present: {'Yes' if os.environ.get('CLIENT_SECRET') else 'No'}")

logger.info("🎉 Flask app ready - visit /debug for detailed diagnostics")

# ==================== MAIN ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
