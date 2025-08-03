# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Consolidated authentication and OAuth handlers for St. Edward Calendar Sync
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import requests
from flask import session, redirect, url_for, request, g

import config
from utils import DateTimeUtils, RetryUtils, structured_logger

logger = logging.getLogger(__name__)

class MicrosoftAuth:
    """Microsoft OAuth authentication handler"""
    
    def __init__(self):
        self.client_id = config.CLIENT_ID
        self.client_secret = config.CLIENT_SECRET
        self.tenant_id = config.TENANT_ID
        self.redirect_uri = config.REDIRECT_URI
        self.scopes = config.GRAPH_SCOPES
        
        # Token storage
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        
        # Load tokens from persistent storage
        self._load_tokens_from_disk()
    
    def get_auth_url(self) -> str:
        """Generate Microsoft OAuth authorization URL"""
        auth_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize"
        params = {
            'client_id': self.client_id,
            'response_type': 'code',
            'redirect_uri': self.redirect_uri,
            'scope': ' '.join(self.scopes),
            'response_mode': 'query'
        }
        
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        return f"{auth_url}?{query_string}"
    
    def exchange_code_for_token(self, auth_code: str) -> bool:
        """Exchange authorization code for access token"""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': auth_code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri
        }
        
        try:
            response = requests.post(token_url, data=data, timeout=30)
            
            if response.status_code == 200:
                token_data = response.json()
                
                self.access_token = token_data.get('access_token')
                self.refresh_token = token_data.get('refresh_token')
                
                # Calculate expiration time
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                # Store expiration time in environment for background threads
                os.environ['TOKEN_EXPIRES_AT'] = self.token_expires_at.isoformat()
                
                # Save tokens to persistent storage
                self._save_tokens_to_disk()
                
                logger.info("✅ Tokens acquired and saved to persistent storage")
                structured_logger.log_sync_event('auth_success', {
                    'method': 'code_exchange',
                    'expires_in': expires_in
                })
                
                return True
            else:
                logger.error(f"❌ Token exchange failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Exception during token exchange: {e}")
            return False
    
    def refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            logger.error("❌ No refresh token available")
            return False
        
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token'
        }
        
        try:
            logger.info(f"🔄 Refreshing access token at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}...")
            
            response = requests.post(token_url, data=data, timeout=30)
            
            if response.status_code == 200:
                token_data = response.json()
                
                self.access_token = token_data.get('access_token')
                if 'refresh_token' in token_data:
                    self.refresh_token = token_data.get('refresh_token')
                
                # Calculate new expiration time
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                # Store expiration time in environment for background threads
                os.environ['TOKEN_EXPIRES_AT'] = self.token_expires_at.isoformat()
                
                # Save tokens to persistent storage
                self._save_tokens_to_disk()
                
                logger.info("✅ Tokens saved to persistent storage")
                logger.info("=" * 60)
                logger.info("TOKENS REFRESHED - Saved to persistent storage")
                logger.info(f"ACCESS_TOKEN=<redacted>")
                logger.info(f"REFRESH_TOKEN=<redacted>")
                logger.info(f"TOKEN_EXPIRES_AT={self.token_expires_at}")
                logger.info("=" * 60)
                logger.info(f"Token refreshed successfully. Expires: {DateTimeUtils.format_central_time(self.token_expires_at)}")
                
                structured_logger.log_sync_event('auth_refresh_success', {
                    'expires_in': expires_in
                })
                
                return True
            else:
                logger.error(f"❌ Token refresh failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Exception during token refresh: {e}")
            return False
    
    def is_token_valid(self) -> bool:
        """Check if current access token is valid"""
        if not self.access_token or not self.token_expires_at:
            return False
        
        # Add 5-minute buffer to prevent edge cases
        buffer_time = datetime.now() + timedelta(minutes=5)
        return self.token_expires_at > buffer_time
    
    def get_headers(self) -> Optional[Dict[str, str]]:
        """Get headers for Microsoft Graph API requests"""
        if not self.is_token_valid():
            logger.info("Token expired or missing, refreshing...")
            if not self.refresh_access_token():
                return None
        
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
    
    def is_authenticated(self) -> bool:
        """Check if user is authenticated"""
        return self.is_token_valid()
    
    @RetryUtils.retry_with_backoff(max_retries=3, base_delay=1.0)
    def _save_tokens_to_disk(self):
        """Save tokens to persistent storage"""
        token_data = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None
        }
        
        token_file = '/data/token_cache.json'
        os.makedirs(os.path.dirname(token_file), exist_ok=True)
        
        with open(token_file, 'w') as f:
            json.dump(token_data, f)
        
        logger.info("✅ Tokens saved to persistent storage")
    
    def _load_tokens_from_disk(self):
        """Load tokens from persistent storage"""
        token_file = '/data/token_cache.json'
        
        try:
            if os.path.exists(token_file):
                with open(token_file, 'r') as f:
                    cached = json.load(f)
                
                self.access_token = cached.get('access_token')
                self.refresh_token = cached.get('refresh_token')
                
                # Parse expiration time
                expires_at = cached.get('expires_at')
                if expires_at:
                    self.token_expires_at = datetime.fromisoformat(expires_at)
                    # Also load expiration time to environment
                    os.environ['TOKEN_EXPIRES_AT'] = expires_at
                
                logger.info("✅ Tokens loaded from persistent storage")
                
                # Check if tokens are still valid
                if self.is_token_valid():
                    logger.info("✅ Loaded tokens are still valid")
                else:
                    logger.info("⚠️ Loaded tokens are expired, will refresh on next use")
            else:
                logger.info("ℹ️ No cached tokens found")
                
        except Exception as e:
            logger.error(f"❌ Error loading tokens from disk: {e}")

class AuthManager:
    """Main authentication manager"""
    
    def __init__(self):
        self.microsoft_auth = MicrosoftAuth()
    
    def is_authenticated(self) -> bool:
        """Check if user is authenticated"""
        return self.microsoft_auth.is_authenticated()
    
    def get_auth_url(self) -> str:
        """Get authentication URL"""
        return self.microsoft_auth.get_auth_url()
    
    def handle_callback(self, auth_code: str) -> bool:
        """Handle OAuth callback"""
        return self.microsoft_auth.exchange_code_for_token(auth_code)
    
    def get_headers(self) -> Optional[Dict[str, str]]:
        """Get authenticated headers for API requests"""
        return self.microsoft_auth.get_headers()
    
    def refresh_if_needed(self) -> bool:
        """Refresh tokens if needed"""
        if not self.microsoft_auth.is_token_valid():
            return self.microsoft_auth.refresh_access_token()
        return True

# =============================================================================
# FLASK AUTHENTICATION INTEGRATION
# =============================================================================

def init_auth(app):
    """Initialize authentication for Flask app"""
    auth_manager = AuthManager()
    
    @app.before_request
    def before_request():
        """Set up authentication context"""
        g.auth_manager = auth_manager
    
    @app.route('/auth/login')
    def login():
        """Redirect to Microsoft OAuth"""
        return redirect(auth_manager.get_auth_url())
    
    @app.route('/auth/callback')
    def auth_callback():
        """Handle OAuth callback"""
        auth_code = request.args.get('code')
        error = request.args.get('error')
        
        if error:
            logger.error(f"OAuth error: {error}")
            return redirect(url_for('index'))
        
        if not auth_code:
            logger.error("No authorization code received")
            return redirect(url_for('index'))
        
        if auth_manager.handle_callback(auth_code):
            session['authenticated'] = True
            logger.info("✅ Authentication successful")
            return redirect(url_for('index'))
        else:
            logger.error("❌ Authentication failed")
            return redirect(url_for('index'))
    
    @app.route('/auth/logout')
    def logout():
        """Clear authentication session"""
        session.pop('authenticated', None)
        return redirect(url_for('index'))

# =============================================================================
# DECORATORS
# =============================================================================

def require_auth(f):
    """Decorator to require authentication"""
    from functools import wraps
    from flask import g, jsonify, redirect, url_for
    
    @wraps(f)
    def decorated(*args, **kwargs):
        if not g.auth_manager or not g.auth_manager.is_authenticated():
            return jsonify({'error': 'Not authenticated', 'redirect': '/auth/login'}), 401
        return f(*args, **kwargs)
    return decorated

# =============================================================================
# INITIALIZATION
# =============================================================================

# Create global auth manager instance
auth_manager = AuthManager() 