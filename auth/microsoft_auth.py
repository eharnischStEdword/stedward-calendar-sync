"""
Microsoft OAuth and Token Management
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
        
        data = {
            'client_id': config.CLIENT_ID,
            'client_secret': config.CLIENT_SECRET,
            'refresh_token': self.refresh_token,
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
                    self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
                
                logger.info(f"Token refreshed. New expiration: {self.token_expires_at}")
                return True
            else:
                logger.error(f"Token refresh failed: {response.status_code}")
                self.clear_tokens()
                return False
        except Exception as e:
            logger.error(f"Token refresh exception: {str(e)}")
            return False
    
    def ensure_valid_token(self):
        """Ensure we have a valid access token"""
        with self.token_lock:
            if not self.access_token:
                return False
            
            if self.token_expires_at and datetime.utcnow() >= self.token_expires_at:
                return self.refresh_access_token()
        
        return True
    
    def get_graph_client(self):
        """Create Microsoft Graph client"""
        if not self.ensure_valid_token():
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
            return None
        
        with self.token_lock:
            return {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
    
    def is_authenticated(self):
        """Check if user is authenticated"""
        return self.access_token is not None
    
    def clear_tokens(self):
        """Clear all tokens"""
        with self.token_lock:
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
                
                if expires_datetime > datetime.utcnow() + timedelta(hours=1):
                    with self.token_lock:
                        self.refresh_token = stored_refresh
                        self.token_expires_at = expires_datetime
                    
                    if self.refresh_access_token():
                        logger.info("Successfully restored authentication from environment")
                        return True
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
                return "expired"
            else:
                return "valid"
