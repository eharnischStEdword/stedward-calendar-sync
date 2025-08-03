# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Consolidated authentication and OAuth handlers for St. Edward Calendar Sync
"""
import hmac
import hashlib
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any
import requests
from flask import session, redirect, url_for, request, g

import config
from utils import DateTimeUtils, RetryUtils, structured_logger

logger = logging.getLogger(__name__)

class MicrosoftAuth:
    """Microsoft OAuth authentication handler"""
    
    def __init__(self):
        # Set up persistent token storage on Render disk
        self.token_file = '/data/token_cache.json'
        
        # Try to load tokens from persistent storage first
        self._load_tokens_from_disk()
        
        # Fall back to environment variables if disk storage is empty
        if not self.env_refresh_token:
            self.env_access_token = os.environ.get('ACCESS_TOKEN')
            self.env_refresh_token = os.environ.get('REFRESH_TOKEN')
            logger.info("Auth manager initialized (hybrid mode - using environment fallback)")
        else:
            logger.info("Auth manager initialized (hybrid mode - using persistent storage)")
    
    def _load_tokens_from_disk(self):
        """Load tokens from persistent disk storage"""
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, 'r') as f:
                    cached = json.load(f)
                self.env_access_token = cached.get('access_token')
                self.env_refresh_token = cached.get('refresh_token')
                
                # Also load expiration time to environment
                expires_at = cached.get('expires_at')
                if expires_at:
                    os.environ['TOKEN_EXPIRES_AT'] = expires_at
                
                logger.info("✅ Loaded tokens from persistent storage")
            else:
                self.env_access_token = None
                self.env_refresh_token = None
                logger.info("No persistent token cache found")
        except Exception as e:
            logger.warning(f"Failed to load tokens from disk: {e}")
            self.env_access_token = None
            self.env_refresh_token = None
    
    def _save_tokens_to_disk(self, access_token, refresh_token, expires_at):
        """Save tokens to persistent disk storage"""
        try:
            token_data = {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': expires_at.isoformat()
            }
            with open(self.token_file, 'w') as f:
                json.dump(token_data, f)
            logger.info("✅ Tokens saved to persistent storage")
        except Exception as e:
            logger.error(f"Failed to save tokens to disk: {e}")
    
    def _is_in_request_context(self):
        """Check if we're in a Flask request context"""
        try:
            # Try to access session - will raise if not in request context
            _ = session.get('test', None)
            return True
        except RuntimeError:
            return False
    
    def get_auth_url(self, state=None):
        """Generate Microsoft OAuth authorization URL"""
        params = {
            'client_id': config.CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': config.REDIRECT_URI,
            'scope': ' '.join(config.GRAPH_SCOPES),
        }
        if state:
            params['state'] = state
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
            access_token = session.get('access_token')
            refresh_token = session.get('refresh_token')
            token_expires_at = session.get('token_expires_at')
        else:
            access_token = self.env_access_token
            refresh_token = self.env_refresh_token
            token_expires_at = os.environ.get('TOKEN_EXPIRES_AT')
        
        if not refresh_token:
            logger.warning("No refresh token available")
            return False
        
        # Refresh if no access token OR within 10 minutes of expiry (was 5)
        if not access_token or self._is_token_expired(token_expires_at, buffer_minutes=10):
            logger.info("Token expired or missing, refreshing...")
            return self.refresh_access_token()
        
        return True

    def _is_token_expired(self, expires_at_str, buffer_minutes=10):
        """Check if token is expired with larger buffer"""
        if not expires_at_str:
            return True
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            now = DateTimeUtils.get_central_time()
            return now >= (expires_at - timedelta(minutes=buffer_minutes))
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
        
        logger.info(f"Refreshing access token at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}...")
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
                expires_at = DateTimeUtils.get_central_time() + timedelta(seconds=expires_in - 300)
                
                if self._is_in_request_context():
                    # Update session
                    session['access_token'] = new_access
                    if tokens.get('refresh_token'):
                        session['refresh_token'] = new_refresh
                    session['token_expires_at'] = expires_at.isoformat()
                else:
                    # Update environment variables for scheduler
                    self.env_access_token = new_access
                    self.env_refresh_token = new_refresh
                    
                    # Store expiration time in environment for background threads
                    os.environ['TOKEN_EXPIRES_AT'] = expires_at.isoformat()
                    
                    # Save to persistent storage instead of logging full tokens
                    self._save_tokens_to_disk(new_access, new_refresh, expires_at)
                    
                    # Log a truncated version for debugging
                    logger.info("="*60)
                    logger.info("TOKENS REFRESHED - Saved to persistent storage")
                    logger.info(f"ACCESS_TOKEN=<redacted>")
                    if tokens.get('refresh_token'):
                        logger.info(f"REFRESH_TOKEN=<redacted>")
                    logger.info(f"TOKEN_EXPIRES_AT={expires_at.isoformat()}")
                    logger.info("="*60)
                
                logger.info(f"Token refreshed successfully. Expires: {DateTimeUtils.format_central_time(expires_at)}")
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
                expires_at = DateTimeUtils.get_central_time() + timedelta(seconds=expires_in - 300)
                session['token_expires_at'] = expires_at.isoformat()
                
                # Also update environment variables for scheduler
                self.env_access_token = tokens.get('access_token')
                self.env_refresh_token = tokens.get('refresh_token')
                
                # Store expiration time in environment for background threads
                os.environ['TOKEN_EXPIRES_AT'] = expires_at.isoformat()
                
                # Save to persistent storage instead of logging full tokens
                self._save_tokens_to_disk(tokens.get('access_token'), tokens.get('refresh_token'), expires_at)
                
                # Log a truncated version for debugging
                logger.info("="*60)
                logger.info("AUTHENTICATION SUCCESSFUL - Tokens saved to persistent storage")
                logger.info(f"ACCESS_TOKEN=<redacted>")
                logger.info(f"REFRESH_TOKEN=<redacted>")
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
        
        # Clear persistent storage
        try:
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
                logger.info("Cleared persistent token cache")
        except Exception as e:
            logger.warning(f"Failed to clear persistent token cache: {e}")
        
        # Don't clear environment tokens as they're still needed for scheduler

class RequestSigner:
    """
    Sign and verify requests using HMAC for webhook security and API authentication
    """
    
    def __init__(self, secret_key: str):
        """
        Initialize with a secret key
        
        Args:
            secret_key: Secret key for signing (should be at least 32 characters)
        """
        if len(secret_key) < 32:
            raise ValueError("Secret key should be at least 32 characters for security")
        
        self.secret_key = secret_key.encode('utf-8')
    
    def sign_request(
        self,
        payload: Dict[str, Any],
        timestamp: Optional[int] = None,
        include_timestamp: bool = True
    ) -> Dict[str, str]:
        """
        Sign a request payload
        
        Args:
            payload: Dictionary payload to sign
            timestamp: Unix timestamp (defaults to current time)
            include_timestamp: Whether to include timestamp in signature
            
        Returns:
            Dictionary with signature and optional timestamp
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        # Create canonical string representation
        if include_timestamp:
            # Include timestamp in the signed data
            signed_data = {
                'timestamp': timestamp,
                'payload': payload
            }
        else:
            signed_data = payload
        
        # Create stable JSON string (sorted keys for consistency)
        message = json.dumps(signed_data, sort_keys=True, separators=(',', ':'))
        
        # Calculate HMAC-SHA256 signature
        signature = hmac.new(
            self.secret_key,
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        result = {'signature': signature}
        if include_timestamp:
            result['timestamp'] = str(timestamp)
        
        return result
    
    def verify_request(
        self,
        payload: Dict[str, Any],
        signature: str,
        timestamp: Optional[str] = None,
        max_age_seconds: int = 300
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify a request signature
        
        Args:
            payload: The payload that was signed
            signature: The signature to verify
            timestamp: The timestamp of the signature (if used)
            max_age_seconds: Maximum age of signature in seconds
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check timestamp if provided
        if timestamp is not None:
            try:
                sig_timestamp = int(timestamp)
                current_time = int(time.time())
                
                # Check if signature is too old
                if current_time - sig_timestamp > max_age_seconds:
                    return False, f"Signature expired (age: {current_time - sig_timestamp}s)"
                
                # Check if signature is from the future (clock skew)
                if sig_timestamp > current_time + 60:  # Allow 60s clock skew
                    return False, "Signature timestamp is in the future"
                    
            except ValueError:
                return False, "Invalid timestamp format"
        
        # Calculate expected signature
        expected_sig_data = self.sign_request(
            payload,
            int(timestamp) if timestamp else None,
            include_timestamp=timestamp is not None
        )
        expected_signature = expected_sig_data['signature']
        
        # Constant-time comparison to prevent timing attacks
        is_valid = hmac.compare_digest(signature, expected_signature)
        
        if not is_valid:
            return False, "Invalid signature"
        
        return True, None
    
    def sign_webhook_request(
        self,
        url: str,
        payload: Dict[str, Any],
        method: str = 'POST'
    ) -> Dict[str, str]:
        """
        Sign a webhook request with additional metadata
        
        Args:
            url: The webhook URL
            payload: The webhook payload
            method: HTTP method
            
        Returns:
            Headers to include with the webhook request
        """
        timestamp = int(time.time())
        
        # Create signature over URL + method + payload
        signed_data = {
            'url': url,
            'method': method.upper(),
            'timestamp': timestamp,
            'payload': payload
        }
        
        message = json.dumps(signed_data, sort_keys=True, separators=(',', ':'))
        signature = hmac.new(
            self.secret_key,
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Return headers to include with request
        return {
            'X-Webhook-Signature': signature,
            'X-Webhook-Timestamp': str(timestamp),
            'X-Webhook-Version': 'v1'
        }
    
    def verify_webhook_request(
        self,
        url: str,
        payload: Dict[str, Any],
        method: str,
        headers: Dict[str, str],
        max_age_seconds: int = 300
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify an incoming webhook request
        
        Args:
            url: The webhook URL
            payload: The webhook payload
            method: HTTP method
            headers: Request headers
            max_age_seconds: Maximum age of signature
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Extract signature and timestamp from headers
        signature = headers.get('X-Webhook-Signature')
        timestamp = headers.get('X-Webhook-Timestamp')
        version = headers.get('X-Webhook-Version', 'v1')
        
        if not signature:
            return False, "Missing signature header"
        
        if not timestamp:
            return False, "Missing timestamp header"
        
        if version != 'v1':
            return False, f"Unsupported signature version: {version}"
        
        # Verify timestamp
        try:
            sig_timestamp = int(timestamp)
            current_time = int(time.time())
            
            if current_time - sig_timestamp > max_age_seconds:
                return False, f"Webhook too old (age: {current_time - sig_timestamp}s)"
                
        except ValueError:
            return False, "Invalid timestamp format"
        
        # Recreate signed data
        signed_data = {
            'url': url,
            'method': method.upper(),
            'timestamp': sig_timestamp,
            'payload': payload
        }
        
        message = json.dumps(signed_data, sort_keys=True, separators=(',', ':'))
        expected_signature = hmac.new(
            self.secret_key,
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Constant-time comparison
        if not hmac.compare_digest(signature, expected_signature):
            return False, "Invalid webhook signature"
        
        return True, None
    
    @staticmethod
    def generate_api_key(length: int = 32) -> str:
        """Generate a secure random API key"""
        import secrets
        return secrets.token_urlsafe(length)
    
    def create_signed_token(
        self,
        user_id: str,
        expires_in_seconds: int = 3600,
        additional_claims: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create a simple signed token (not JWT, but similar concept)
        
        Args:
            user_id: User identifier
            expires_in_seconds: Token lifetime
            additional_claims: Additional data to include
            
        Returns:
            Signed token string
        """
        import base64
        
        expires_at = int(time.time()) + expires_in_seconds
        
        token_data = {
            'user_id': user_id,
            'expires_at': expires_at,
            'issued_at': int(time.time())
        }
        
        if additional_claims:
            token_data.update(additional_claims)
        
        # Create token
        token_json = json.dumps(token_data, sort_keys=True)
        token_bytes = token_json.encode('utf-8')
        
        # Sign token
        signature = hmac.new(
            self.secret_key,
            token_bytes,
            hashlib.sha256
        ).digest()
        
        # Combine token and signature
        signed_token = base64.urlsafe_b64encode(
            token_bytes + b'.' + signature
        ).decode('utf-8').rstrip('=')
        
        return signed_token
    
    def verify_signed_token(self, token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Verify and decode a signed token
        
        Args:
            token: The signed token to verify
            
        Returns:
            Tuple of (token_data, error_message)
        """
        import base64
        
        try:
            # Decode token
            padded_token = token + '=' * (4 - len(token) % 4)
            decoded = base64.urlsafe_b64decode(padded_token)
            
            # Split token and signature
            parts = decoded.split(b'.')
            if len(parts) != 2:
                return None, "Invalid token format"
            
            token_bytes, signature = parts
            
            # Verify signature
            expected_signature = hmac.new(
                self.secret_key,
                token_bytes,
                hashlib.sha256
            ).digest()
            
            if not hmac.compare_digest(signature, expected_signature):
                return None, "Invalid token signature"
            
            # Decode token data
            token_data = json.loads(token_bytes.decode('utf-8'))
            
            # Check expiration
            if token_data.get('expires_at', 0) < int(time.time()):
                return None, "Token expired"
            
            return token_data, None
            
        except Exception as e:
            return None, f"Token verification failed: {str(e)}"


# Convenience function for simple request signing
def sign_request(payload: Dict[str, Any], secret: str) -> str:
    """
    Simple function to sign a request payload
    
    Args:
        payload: Dictionary payload to sign
        secret: Secret key for signing
        
    Returns:
        Signature string
    """
    message = json.dumps(payload, sort_keys=True)
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature

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
        if not self.microsoft_auth.ensure_valid_token():
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