"""
Request Signing - Sign and verify requests for additional security
"""
import hmac
import hashlib
import json
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta


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
