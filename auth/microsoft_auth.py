"""
Microsoft OAuth - Session-based Authentication (not shared)
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
        # Don't load tokens from environment - each user authenticates
        logger.info("Auth manager initialized (session-based)")
    
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
        """Check if current session is authenticated"""
        return bool(session.get('refresh_token'))
    
    def ensure_valid_token(self):
        """Ensure we have a valid access token for this session"""
        # Get tokens from session
        access_token = session.get('access_token')
        refresh_token = session.get('refresh_token')
        token_expires_at = session.get('token_expires_at')
        
        # If no refresh token, not authenticated
        if not refresh_token:
            logger.warning("No refresh token in session")
            return False
        
        # If no access token, try to get one with refresh token
        if not access_token:
            logger.info("No access token in session, attempting refresh...")
            return self.refresh_access_token()
        
        # Check if token is expired
        if token_expires_at:
            try:
                expires_at = datetime.fromisoformat(token_expires_at)
                now = get_central_time()
                if now >= (expires_at - timedelta(minutes=5)):
                    logger.info(f"Access token expires soon, refreshing...")
                    return self.refresh_access_token()
            except:
                pass
        
        return True
    
    def get_headers(self):
        """Get authorization headers for API calls"""
        if not self.ensure_valid_token():
            logger.error("Cannot get headers - no valid token")
            return None
        
        access_token = session.get('access_token')
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
        """Refresh the access token using refresh token from session"""
        refresh_token = session.get('refresh_token')
        if not refresh_token:
            logger.warning("No refresh token available in session")
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
                
                # Store in session
                session['access_token'] = tokens.get('access_token')
                new_refresh = tokens.get('refresh_token')
                if new_refresh:
                    session['refresh_token'] = new_refresh
                
                expires_in = tokens.get('expires_in', 3600)
                expires_at = get_central_time() + timedelta(seconds=expires_in - 300)
                session['token_expires_at'] = expires_at.isoformat()
                
                logger.info(f"Token refreshed successfully. Expires: {format_central_time(expires_at)}")
                return True
            else:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
                if response.status_code in [400, 401]:
                    logger.error("Refresh token invalid, clearing session")
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
                
                logger.info("Authentication successful for this session")
                return True
            return False
        except Exception as e:
            logger.error(f"Token exchange failed: {e}")
            return False
    
    def clear_tokens(self):
        """Clear all tokens from session"""
        logger.info("Clearing authentication tokens from session")
        session.pop('access_token', None)
        session.pop('refresh_token', None)
        session.pop('token_expires_at', None)
