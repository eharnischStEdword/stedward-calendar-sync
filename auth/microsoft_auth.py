"""
Microsoft OAuth and Token Management - IMPROVED
"""
import os
import time
import logging
import requests
import urllib.parse
from datetime import datetime, timedelta
from threading import Lock
from azure.core.credentials import AccessToken
from msgraph import GraphServiceClient

import config

logger = logging.getLogger(__name__)


class MicrosoftAuth:
    """Handles Microsoft OAuth authentication and token management"""
    
    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.token_lock = Lock()
        
        # Try to restore tokens from environment on startup
        self.load_tokens_from_env()
    
    def get_auth_url(self, state):
        """Generate Microsoft OAuth URL"""
        params = {
            'client_id': config.CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': config.REDIRECT_URI,
            'scope': ' '.join(config.GRAPH_SCOPES),
            'response_mode': 'query',
            'state': state
        }
        
        auth_url = f"https://login.microsoftonline.com/{config.TENANT_ID}/oauth2/v2.0/authorize?"
        return auth_url + urllib.parse.urlencode(params)
    
    def exchange_code_for_token(self, auth_code):
        """Exchange authorization code for tokens"""
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
                
                with self.token_lock:
                    self.access_token = tokens.get('access_token')
                    self.refresh_token = tokens.get('refresh_token')
                    expires_in = tokens.get('expires_in', 3600)
                    # Add 5 minute buffer before expiration
                    self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
                
                self._save_tokens_to_env()
                logger.info(f"Successfully obtained tokens. Expires at: {self.token_expires_at}")
                return True
            else:
                logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Token exchange exception: {str(e)}")
            return False
    
    def refresh_access_token(self):
        """Refresh the access token using refresh token"""
        with self.token_lock:
            if not self.refresh_token:
                logger.warning("No refresh token available")
                return False
        
        logger.info("Refreshing access token...")
        token_url = f"https://login.microsoftonline.com/{config.TENANT_ID}/oauth2/v2.0/token"
        
        with self.token_lock:
            current_refresh_token = self.refresh_token
        
        data = {
            'client_id': config.CLIENT_ID,
            'client_secret': config.CLIENT_SECRET,
            'refresh_token': current_refresh_token,
            'grant_type': 'refresh_token',
            'scope': ' '.join(config.GRAPH_SCOPES)
        }
        
        try:
            response = requests.post(token_url, data=data, timeout=30)
            if response.status_code == 200:
                tokens = response.json()
                
                with self.token_lock:
                    self.access_token = tokens.get('access_token')
                    new_refresh = tokens.get('refresh_token')
                    if new_refresh:
                        self.refresh_token = new_refresh
                    
                    expires_in = tokens.get('expires_in', 3600)
                    # Add 5 minute buffer before expiration
                    self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
                
                logger.info(f"Token refreshed successfully. New expiration: {self.token_expires_at}")
                return True
            else:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
                # Only clear tokens if it's a permanent failure
                if response.status_code in [400, 401]:
                    logger.error("Refresh token invalid, clearing all tokens")
                    self.clear_tokens()
                return False
        except Exception as e:
            logger.error(f"Token refresh exception: {str(e)}")
            return False
    
    def ensure_valid_token(self):
        """Ensure we have a valid access token - IMPROVED"""
        with self.token_lock:
            # If no access token, can't proceed
            if not self.access_token:
                logger.warning("No access token available")
                return False
            
            # If no expiration time set, assume token is valid
            if not self.token_expires_at:
                logger.debug("No expiration time set, assuming token is valid")
                return True
            
            # Check if token is expired or about to expire (within 5 minutes)
            now = datetime.utcnow()
            if now >= self.token_expires_at:
                logger.info(f"Access token expired at {self.token_expires_at}, refreshing...")
                if not self.refresh_access_token():
                    logger.error("Failed to refresh token")
                    return False
                logger.info("Token refresh successful")
                return True
            else:
                # Token is still valid
                time_until_expiry = self.token_expires_at - now
                logger.debug(f"Token valid for {time_until_expiry}")
                return True
    
    def get_graph_client(self):
        """Create Microsoft Graph client"""
        if not self.ensure_valid_token():
            logger.error("Cannot create Graph client - no valid token")
            return None
        
        class BearerTokenCredential:
            def __init__(self, token):
                self.token = token
            
            def get_token(self, *scopes, **kwargs):
                return AccessToken(self.token, int(time.time()) + 3600)
        
        with self.token_lock:
            credential = BearerTokenCredential(self.access_token)
        
        return GraphServiceClient(credentials=credential)
    
    def get_headers(self):
        """Get authorization headers for direct API calls"""
        if not self.ensure_valid_token():
            logger.error("Cannot get headers - no valid token")
            return None
        
        with self.token_lock:
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            logger.debug("Returning valid authorization headers")
            return headers
    
    def is_authenticated(self):
        """Check if user is authenticated - IMPROVED"""
        with self.token_lock:
            has_tokens = self.access_token is not None and self.refresh_token is not None
            
            if not has_tokens:
                logger.debug("No tokens available")
                return False
            
            # If we have tokens, check if we can ensure they're valid
            if not self.token_expires_at:
                logger.debug("No expiration time, assuming authenticated")
                return True
            
            # Check if refresh token would work (don't actually refresh, just check if we have one)
            now = datetime.utcnow()
            if now >= self.token_expires_at:
                # Token is expired, but we have a refresh token
                if self.refresh_token:
                    logger.debug("Access token expired but refresh token available")
                    return True
                else:
                    logger.warning("Access token expired and no refresh token")
                    return False
            else:
                logger.debug("Access token still valid")
                return True
    
    def clear_tokens(self):
        """Clear all tokens"""
        with self.token_lock:
            logger.info("Clearing all authentication tokens")
            self.access_token = None
            self.refresh_token = None
            self.token_expires_at = None
    
    def load_tokens_from_env(self):
        """Load tokens from environment variables"""
        stored_refresh = os.environ.get('REFRESH_TOKEN')
        stored_expires = os.environ.get('TOKEN_EXPIRES_AT')
        
        if stored_refresh and stored_expires:
            try:
                expires_datetime = datetime.fromisoformat(stored_expires)
                
                # If the stored expiration is more than 1 hour in the future, restore tokens
                if expires_datetime > datetime.utcnow() + timedelta(hours=1):
                    with self.token_lock:
                        self.refresh_token = stored_refresh
                        self.token_expires_at = expires_datetime
                    
                    if self.refresh_access_token():
                        logger.info("Successfully restored authentication from environment")
                        return True
                else:
                    logger.info("Stored tokens too old, requiring fresh authentication")
            except Exception as e:
                logger.error(f"Error loading tokens: {e}")
        
        return False
    
    def _save_tokens_to_env(self):
        """Log tokens for manual environment variable update"""
        logger.info("SAVE THESE TO YOUR RENDER ENVIRONMENT VARIABLES:")
        logger.info(f"REFRESH_TOKEN={self.refresh_token}")
        logger.info(f"TOKEN_EXPIRES_AT={self.token_expires_at.isoformat()}")
    
    def get_token_status(self):
        """Get current token status"""
        with self.token_lock:
            if not self.access_token:
                return "missing"
            elif self.token_expires_at and datetime.utcnow() >= self.token_expires_at:
                if self.refresh_token:
                    return "expired_but_refreshable"
                else:
                    return "expired"
            else:
                return "valid"
