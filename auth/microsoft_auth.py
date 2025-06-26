"""
Microsoft OAuth - Minimal Fast-Loading Version
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
    
    def exchange_code_for_token(self, auth_code):
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
                self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
                
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
    
    def get_headers(self):
        if self.access_token:
            return {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
        return None
