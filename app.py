#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Fixed Production Version
"""
import os
import logging
import secrets
import traceback
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

@app.route('/debug')
def debug_info():
    """Debug information"""
    try:
        import config
        debug_data = {
            "timestamp": datetime.now().isoformat(),
            "environment_vars": {
                "CLIENT_ID": bool(os.environ.get('CLIENT_ID')),
                "CLIENT_SECRET": bool(os.environ.get('CLIENT_SECRET')),
                "TENANT_ID": bool(os.environ.get('TENANT_ID')),
            },
            "config_loaded": True,
            "auth_test": "pending"
        }
        
        # Test auth import
        try:
            from auth.microsoft_auth import MicrosoftAuth
            debug_data["auth_import"] = "success"
            
            # Test auth creation
            auth = MicrosoftAuth()
            debug_data["auth_creation"] = "success"
            debug_data["auth_test"] = "success"
            
        except Exception as auth_error:
            debug_data["auth_import"] = f"failed: {str(auth_error)}"
            debug_data["auth_test"] = f"failed: {str(auth_error)}"
        
        return jsonify(debug_data)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/')
def index():
    """Main page with comprehensive error handling"""
    try:
        logger.info("Index route accessed")
        
        # Step 1: Import config (should be fast)
        try:
            import config
            logger.info("Config loaded successfully")
        except Exception as e:
            logger.error(f"Config import failed: {e}")
            return f'''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>⚠️ Configuration Error</h1>
                <p>Failed to load configuration: {str(e)}</p>
                <p><a href="/debug">View Debug Info</a></p>
            </body></html>
            ''', 500
        
        # Step 2: Import auth module
        try:
            from auth.microsoft_auth import MicrosoftAuth
            logger.info("Auth module imported successfully")
        except Exception as e:
            logger.error(f"Auth module import failed: {e}")
            return f'''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>⚠️ Auth Module Error</h1>
                <p>Failed to import auth module: {str(e)}</p>
                <p><a href="/debug">View Debug Info</a></p>
            </body></html>
            ''', 500
        
        # Step 3: Create auth manager
        try:
            auth = MicrosoftAuth()
            logger.info("Auth manager created successfully")
        except Exception as e:
            logger.error(f"Auth manager creation failed: {e}")
            return f'''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>⚠️ Auth Manager Error</h1>
                <p>Failed to create auth manager: {str(e)}</p>
                <p><a href="/debug">View Debug Info</a></p>
            </body></html>
            ''', 500
        
        # Step 4: Check authentication
        try:
            is_authenticated = auth.is_authenticated()
            logger.info(f"Authentication check: {is_authenticated}")
        except Exception as e:
            logger.error(f"Authentication check failed: {e}")
            return f'''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1>⚠️ Authentication Check Error</h1>
                <p>Failed to check authentication: {str(e)}</p>
                <p><a href="/debug">View Debug Info</a></p>
            </body></html>
            ''', 500
        
        # Step 5: Generate auth URL if not authenticated
        if not is_authenticated:
            try:
                state = secrets.token_urlsafe(16)
                session['oauth_state'] = state
                auth_url = auth.get_auth_url(state)
                logger.info("Auth URL generated successfully")
                
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
                        <p style="margin-top: 20px;">
                            <a href="/debug" style="color: #666; font-size: 12px;">Debug Info</a>
                        </p>
                    </div>
                </body>
                </html>
                '''
            except Exception as e:
                logger.error(f"Auth URL generation failed: {e}")
                return f'''
                <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                    <h1>⚠️ Auth URL Error</h1>
                    <p>Failed to generate auth URL: {str(e)}</p>
                    <p><a href="/debug">View Debug Info</a></p>
                </body></html>
                ''', 500
        else:
            # User is authenticated
            return '''
            <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
                <h1 style="color: #005921;">📅 Calendar Sync Dashboard</h1>
                <p>✅ Authentication successful! System is ready.</p>
                <p><em>Full dashboard will load once all components are initialized.</em></p>
                <p><a href="/debug">Debug Info</a></p>
            </body></html>
            '''
            
    except Exception as e:
        logger.error(f"Unexpected error in index route: {e}")
        logger.error(traceback.format_exc())
        return f'''
        <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>⚠️ Unexpected Error</h1>
            <p>Error: {str(e)}</p>
            <p><a href="/debug">View Debug Info</a></p>
            <details style="margin-top: 20px; text-align: left;">
                <summary>Technical Details</summary>
                <pre style="background: #f0f0f0; padding: 10px; font-size: 12px;">{traceback.format_exc()}</pre>
            </details>
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
