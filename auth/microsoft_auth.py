"""
Microsoft OAuth - Complete Fast-Loading Version with Token Refresh
"""
import os
import requests
import urllib.parse
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class MicrosoftAuth:
    def __init__(self):
        self.access_token = os.environ.get('ACCESS_TOKEN')
        self.refresh_token = os.environ.get('REFRESH_TOKEN') 
        self.token_expires_at = None
        
        # Parse expiry if available
        expires_str = os.environ.get('TOKEN_EXPIRES_AT')
        if expires_str:
            try:
                self.token_expires_at = datetime.fromisoformat(expires_str)
            except:
                pass
        
        logger.info("Auth manager initialized quickly")
        
        # If we have refresh token but no access token, try to refresh
        if self.refresh_token and not self.access_token:
            logger.info("Have refresh token but no access token, attempting refresh...")
            self.refresh_access_token()
    
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
        return bool(self.refresh_token)
    
    def ensure_valid_token(self):
        """Ensure we have a valid access token"""
        # If no access token, try to get one with refresh token
        if not self.access_token and self.refresh_token:
            logger.info("No access token but have refresh token, attempting refresh...")
            return self.refresh_access_token()
        
        # If no access token at all, can't proceed
        if not self.access_token:
            logger.warning("No access token available")
            return False
        
        # If no expiration time set, assume token is valid (more forgiving)
        if not self.token_expires_at:
            logger.debug("No expiration time set, assuming token is valid")
            return True
        
        # Check if token is expired or about to expire (within 5 minutes)
        now = datetime.utcnow()
        if now >= (self.token_expires_at - timedelta(minutes=5)):
            logger.info(f"Access token expires soon ({self.token_expires_at}), refreshing...")
            return self.refresh_access_token()
        else:
            # Token is still valid
            time_until_expiry = self.token_expires_at - now
            logger.debug(f"Token valid for {time_until_expiry}")
            return True
    
    def get_headers(self):
        """Get authorization headers for API calls"""
        if not self.ensure_valid_token():
            logger.error("Cannot get headers - no valid token")
            return None
        
        if self.access_token:
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            logger.debug("Returning valid authorization headers")
            return headers
        
        logger.error("No access token available for headers")
        return None
    
    def refresh_access_token(self):
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            logger.warning("No refresh token available")
            return False
        
        logger.info("Refreshing access token...")
        import config
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
                
                self.access_token = tokens.get('access_token')
                new_refresh = tokens.get('refresh_token')
                if new_refresh:
                    self.refresh_token = new_refresh
                
                expires_in = tokens.get('expires_in', 3600)
                # Add 5 minute buffer before expiration
                self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
                
                logger.info(f"Token refreshed successfully. New expiration: {self.token_expires_at}")
                
                # Log tokens for manual environment variable update
                logger.info("=" * 40)
                logger.info("🔑 UPDATE RENDER ENVIRONMENT VARIABLES:")
                logger.info(f"ACCESS_TOKEN={self.access_token}")
                logger.info(f"REFRESH_TOKEN={self.refresh_token}")
                logger.info(f"TOKEN_EXPIRES_AT={self.token_expires_at.isoformat()}")
                logger.info("=" * 40)
                
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
                self.access_token = tokens.get('access_token')
                self.refresh_token = tokens.get('refresh_token')
                expires_in = tokens.get('expires_in', 3600)
                self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
                
                # Log tokens for manual environment variable update
                logger.info("=" * 50)
                logger.info("🔑 UPDATE RENDER ENVIRONMENT VARIABLES:")
                logger.info(f"ACCESS_TOKEN={self.access_token}")
                logger.info(f"REFRESH_TOKEN={self.refresh_token}")
                logger.info(f"TOKEN_EXPIRES_AT={self.token_expires_at.isoformat()}")
                logger.info("=" * 50)
                
                return True
            return False
        except Exception as e:
            logger.error(f"Token exchange failed: {e}")
            return False
    
    def clear_tokens(self):
        """Clear all tokens"""
        logger.info("Clearing all authentication tokens")
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
