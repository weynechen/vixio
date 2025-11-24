"""Authentication utilities."""

import hmac
import base64
import hashlib
import time


class AuthenticationError(Exception):
    """Authentication exception."""
    pass


class AuthManager:
    """
    Unified authentication manager.
    Generate and verify client_id, device_id, token (HMAC-SHA256) authentication triple.
    Token contains only signature + timestamp, not plaintext client_id/device_id.
    """

    def __init__(self, secret_key: str, expire_seconds: int = 60 * 60 * 24 * 30):
        """
        Initialize auth manager.
        
        Args:
            secret_key: Secret key for signing
            expire_seconds: Token expiration time in seconds
        """
        if not expire_seconds or expire_seconds < 0:
            self.expire_seconds = 60 * 60 * 24 * 30
        else:
            self.expire_seconds = expire_seconds
        self.secret_key = secret_key

    def _sign(self, content: str) -> str:
        """HMAC-SHA256 signature and Base64 encode."""
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            content.encode("utf-8"),
            hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")

    def generate_token(self, client_id: str, username: str) -> str:
        """
        Generate token.
        
        Args:
            client_id: Device connection ID
            username: Device username (usually deviceId)
            
        Returns:
            str: Token string
        """
        ts = int(time.time())
        content = f"{client_id}|{username}|{ts}"
        signature = self._sign(content)
        # Token only contains signature and timestamp, not plaintext info
        token = f"{signature}.{ts}"
        return token

    def verify_token(self, token: str, client_id: str, username: str) -> bool:
        """
        Verify token validity.
        
        Args:
            token: Token from client
            client_id: Connection client_id
            username: Connection username
            
        Returns:
            bool: True if valid, False otherwise
        """
        try:
            sig_part, ts_str = token.split(".")
            ts = int(ts_str)
            if int(time.time()) - ts > self.expire_seconds:
                return False  # Expired

            expected_sig = self._sign(f"{client_id}|{username}|{ts}")
            if not hmac.compare_digest(sig_part, expected_sig):
                return False

            return True
        except Exception:
            return False


def generate_password_signature(content: str, secret_key: str) -> str:
    """
    Generate MQTT password signature.
    
    Args:
        content: Signature content (clientId + '|' + username)
        secret_key: Secret key
        
    Returns:
        str: Base64 encoded HMAC-SHA256 signature
    """
    try:
        hmac_obj = hmac.new(
            secret_key.encode("utf-8"),
            content.encode("utf-8"),
            hashlib.sha256
        )
        signature = hmac_obj.digest()
        return base64.b64encode(signature).decode("utf-8")
    except Exception:
        return ""

