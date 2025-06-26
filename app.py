#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Production Version
"""
import os
import logging
import secrets
from datetime import datetime
from flask import Flask, jsonify, redirect, session, request

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask IMMEDIATELY
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

@app.route('/health')
def health_check():
    """Health check for Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "st-edward-calendar-sync"
    }), 200

@app.route('/')
def index():
    """Main page - loads auth module only when needed"""
    try:
        # Import config first (fast)
        import config
        logger.info("Config loaded successfully")
        
        # Import auth module (potentially slow, but after HTTP port is open)
        from auth.microsoft_auth import MicrosoftAuth
        logger.info("Auth module loaded successfully")
        
        auth = MicrosoftAuth()
        logger.info("Auth manager created successfully")
        
        if not auth.is_authenticated():
            state = secrets.token_urlsafe(16)
            session['oauth_state'] = state
            auth_url = auth.get_auth_url(state)
            
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
                        St. Edward Church & School
                    </p>
                </div>
            </body>
            </html>
            '''
        else:
            return '''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1 style="color: #005921;">📅 Calendar Sync Dashboard</h1>
                <p>✅ Authentication successful! System is ready.</p>
                <p><em>Full dashboard will load once all components are initialized.</em></p>
            </body></html>
            '''
            
    except Exception as e:
        logger.error(f"Error in index route: {e}")
        return f'''
        <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>⚠️ Initialization Error</h1>
            <p>Error: {str(e)}</p>
            <p>Service is starting up... Please refresh in a moment.</p>
        </body></html>
        ''', 500

@app.route('/auth/callback')
def auth_callback():
    """OAuth callback"""
    try:
        from auth.microsoft_auth import MicrosoftAuth
        
        code = request.args.get('code')
        state = request.args.get('state')
        
        if not code or not state or state != session.get('oauth_state'):
            return "Authentication failed: Invalid request", 400
        
        auth = MicrosoftAuth()
        if auth.exchange_code_for_token(code):
            return redirect('/')
        else:
            return "Authentication failed: Could not exchange token", 400
            
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        return f"Authentication error: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting St. Edward Calendar Sync on port {port}")
    app.run(host='0.0.0.0', port=port)
