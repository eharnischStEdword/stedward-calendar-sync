# auth/request_signing.py
import hmac
import hashlib

def sign_request(payload, secret):
    """Sign requests for additional security"""
    message = json.dumps(payload, sort_keys=True)
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature
