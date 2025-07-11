# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Microsoft OAuth - Hybrid Authentication (session + environment fallback)
"""
import os
import requests
import urllib.parse
from datetime import datetime, timedelta
import logging
from flask import session
from utils.timezone import get_central_time, format_central_time

logger = logging.getLogger(__name__)

class MicrosoftAuth:
    def __init__(self):
        # Load tokens from environment as fallback for scheduler
        self.env_access_token = os.environ.get('ACCESS_TOKEN')
        self.env_refresh_token = os.environ.get('REFRESH_TOKEN')
        logger.info("Auth manager initialized (hybrid mode)")
    
    def _is_in_request_context(self):
        """Check if we're in a Flask request context"""
        try:
            # Try to access session - will raise if not in request context
            _ = session.get('test', None)
            return True
        except RuntimeError:
            return False
    
    def get_auth_url(self, state):
        import config
        params = {
            'client_id': config.CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': config.REDIRECT_URI,
            'scope': ' '.join(config.GRAPH_SCOPES),
            'state': state
        }
        return f"https://login.microsoftonline.com/{config.TENANT_ID}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
    
    def is_authenticated(self):
        """Check if authenticated (session or environment)"""
        if self._is_in_request_context():
            # In web request - use session
            return bool(session.get('refresh_token'))
        else:
            # In background thread - use environment
            return bool(self.env_refresh_token)
    
    def ensure_valid_token(self):
        """Ensure we have a valid access token"""
        if self._is_in_request_context():
            # Web request - use session tokens
            access_token = session.get('access_token')
            refresh_token = session.get('refresh_token')
            token_expires_at = session.get('token_expires_at')
        else:
            # Background thread - use environment tokens
            access_token = self.env_access_token
            refresh_token = self.env_refresh_token
            token_expires_at = os.environ.get('TOKEN_EXPIRES_AT')
        
        if not refresh_token:
            logger.warning("No refresh token available")
            return False
        
        # If no access token or expired, refresh
        if not access_token or self._is_token_expired(token_expires_at):
            logger.info("Token expired or missing, refreshing...")
            return self.refresh_access_token()
        
        return True
    
    def _is_token_expired(self, expires_at_str):
        """Check if token is expired"""
        if not expires_at_str:
            return True
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            now = get_central_time()
            return now >= (expires_at - timedelta(minutes=5))
        except:
            return True
    
    def get_headers(self):
        """Get authorization headers for API calls"""
        if not self.ensure_valid_token():
            logger.error("Cannot get headers - no valid token")
            return None
        
        if self._is_in_request_context():
            access_token = session.get('access_token')
        else:
            access_token = self.env_access_token
        
        if access_token:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            logger.debug("Returning valid authorization headers")
            return headers
        
        logger.error("No access token available for headers")
        return None
    
    def refresh_access_token(self):
        """Refresh the access token using refresh token"""
        if self._is_in_request_context():
            refresh_token = session.get('refresh_token')
        else:
            refresh_token = self.env_refresh_token
        
        if not refresh_token:
            logger.warning("No refresh token available")
            return False
        
        logger.info(f"Refreshing access token at {format_central_time(get_central_time())}...")
        import config
        token_url = f"https://login.microsoftonline.com/{config.TENANT_ID}/oauth2/v2.0/token"
        
        data = {
            'client_id': config.CLIENT_ID,
            'client_secret': config.CLIENT_SECRET,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'scope': ' '.join(config.GRAPH_SCOPES)
        }
        
        try:
            response = requests.post(token_url, data=data, timeout=30)
            if response.status_code == 200:
                tokens = response.json()
                
                new_access = tokens.get('access_token')
                new_refresh = tokens.get('refresh_token', refresh_token)
                expires_in = tokens.get('expires_in', 3600)
                expires_at = get_central_time() + timedelta(seconds=expires_in - 300)
                
                if self._is_in_request_context():
                    # Update session
                    session['access_token'] = new_access
                    if tokens.get('refresh_token'):
                        session['refresh_token'] = new_refresh
                    session['token_expires_at'] = expires_at.isoformat()
                else:
                    # Update environment variables for background use
                    self.env_access_token = new_access
                    if tokens.get('refresh_token'):
                        self.env_refresh_token = new_refresh
                    
                    # Log new tokens for manual update
                    logger.info("="*60)
                    logger.info("NEW TOKENS - Update these in Render environment variables:")
                    logger.info(f"ACCESS_TOKEN={new_access}")
                    if tokens.get('refresh_token'):
                        logger.info(f"REFRESH_TOKEN={new_refresh}")
                    logger.info(f"TOKEN_EXPIRES_AT={expires_at.isoformat()}")
                    logger.info("="*60)
                
                logger.info(f"Token refreshed successfully. Expires: {format_central_time(expires_at)}")
                return True
            else:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
                if response.status_code in [400, 401]:
                    logger.error("Refresh token invalid")
                    self.clear_tokens()
                return False
        except Exception as e:
            logger.error(f"Token refresh exception: {str(e)}")
            return False
    
    def exchange_code_for_token(self, auth_code):
        """Exchange authorization code for tokens"""
        import config
        token_url = f"https://login.microsoftonline.com/{config.TENANT_ID}/oauth2/v2.0/token"
        
        data = {
            'client_id': config.CLIENT_ID,
            'client_secret': config.CLIENT_SECRET,
            'code': auth_code,
            'redirect_uri': config.REDIRECT_URI,
            'grant_type': 'authorization_code',
            'scope': ' '.join(config.GRAPH_SCOPES)
        }
        
        try:
            response = requests.post(token_url, data=data, timeout=30)
            if response.status_code == 200:
                tokens = response.json()
                
                # Store in session
                session['access_token'] = tokens.get('access_token')
                session['refresh_token'] = tokens.get('refresh_token')
                expires_in = tokens.get('expires_in', 3600)
                expires_at = get_central_time() + timedelta(seconds=expires_in - 300)
                session['token_expires_at'] = expires_at.isoformat()
                
                # Also update environment variables for scheduler
                self.env_access_token = tokens.get('access_token')
                self.env_refresh_token = tokens.get('refresh_token')
                
                # Log tokens for manual environment update
                logger.info("="*60)
                logger.info("NEW TOKENS - Update these in Render environment variables:")
                logger.info(f"ACCESS_TOKEN={tokens.get('access_token')}")
                logger.info(f"REFRESH_TOKEN={tokens.get('refresh_token')}")
                logger.info(f"TOKEN_EXPIRES_AT={expires_at.isoformat()}")
                logger.info("="*60)
                
                logger.info("Authentication successful")
                return True
            return False
        except Exception as e:
            logger.error(f"Token exchange failed: {e}")
            return False
    
    def clear_tokens(self):
        """Clear all tokens"""
        logger.info("Clearing authentication tokens")
        if self._is_in_request_context():
            session.pop('access_token', None)
            session.pop('refresh_token', None)
            session.pop('token_expires_at', None)
        # Don't clear environment tokens as they're still needed for scheduler
