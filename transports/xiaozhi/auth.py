"""
Xiaozhi Authentication Module

Provides authentication for Xiaozhi protocol:
- OTA authentication (device registration)
- Vision API authentication (self-contained JWT token)

Design:
- device_id is the core entity (database primary key)
- session_id is temporary per-connection identifier
- token is self-contained credential (contains device_id)
"""

import time
import hmac
import base64
import hashlib
import json
from typing import Tuple, Optional
from datetime import datetime, timedelta
from loguru import logger

# Try to import JWT library
try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False


class XiaozhiAuth:
    """
    Unified authentication manager for Xiaozhi protocol.
    
    Provides two types of tokens:
    1. OTA Token: For WebSocket connection authentication
    2. Vision Token: Self-contained JWT for Vision API
    
    Both tokens are tied to device_id for database association.
    """
    
    def __init__(
        self, 
        secret_key: str, 
        ota_expire_seconds: int = 60 * 60 * 24 * 30,  # 30 days
        vision_expire_seconds: int = 60 * 60 * 24,     # 24 hours
    ):
        """
        Initialize authentication manager.
        
        Args:
            secret_key: Secret key for signing/encryption
            ota_expire_seconds: OTA token expiration (default 30 days)
            vision_expire_seconds: Vision token expiration (default 24 hours)
        """
        self.secret_key = secret_key
        self.ota_expire_seconds = ota_expire_seconds
        self.vision_expire_seconds = vision_expire_seconds
        self.logger = logger.bind(component="XiaozhiAuth")
        
        if not JWT_AVAILABLE:
            self.logger.warning("PyJWT not installed, vision token will use HMAC fallback")
    
    # ============ OTA Token (for WebSocket connection) ============
    
    def generate_ota_token(self, client_id: str, device_id: str) -> str:
        """
        Generate OTA token for WebSocket authentication.
        
        This token is used during OTA to authorize WebSocket connection.
        Format: HMAC signature + timestamp
        
        Args:
            client_id: Client connection identifier
            device_id: Device ID (MAC address)
            
        Returns:
            OTA token string
        """
        ts = int(time.time())
        content = f"{client_id}|{device_id}|{ts}"
        signature = self._hmac_sign(content)
        return f"{signature}.{ts}"
    
    def verify_ota_token(
        self, 
        token: str, 
        client_id: str, 
        device_id: str
    ) -> bool:
        """
        Verify OTA token for WebSocket connection.
        
        Args:
            token: Token from device
            client_id: Expected client_id
            device_id: Expected device_id
            
        Returns:
            True if valid, False otherwise
        """
        try:
            sig_part, ts_str = token.split(".")
            ts = int(ts_str)
            
            # Check expiration
            if int(time.time()) - ts > self.ota_expire_seconds:
                return False
            
            # Verify signature
            expected_sig = self._hmac_sign(f"{client_id}|{device_id}|{ts}")
            return hmac.compare_digest(sig_part, expected_sig)
            
        except Exception as e:
            self.logger.debug(f"OTA token verification failed: {e}")
            return False
    
    # ============ Vision Token (self-contained JWT) ============
    
    def generate_vision_token(self, device_id: str) -> str:
        """
        Generate self-contained token for Vision API.
        
        Token contains device_id, can be verified without additional context.
        Uses JWT if available, falls back to HMAC format.
        
        Args:
            device_id: Device ID (MAC address)
            
        Returns:
            Vision token string
        """
        if JWT_AVAILABLE:
            return self._generate_jwt_token(device_id)
        else:
            return self._generate_hmac_vision_token(device_id)
    
    def verify_vision_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """
        Verify Vision token and extract device_id.
        
        Args:
            token: Token from Authorization header
            
        Returns:
            Tuple of (is_valid, device_id or None)
        """
        if JWT_AVAILABLE:
            return self._verify_jwt_token(token)
        else:
            return self._verify_hmac_vision_token(token)
    
    # ============ JWT Implementation ============
    
    def _generate_jwt_token(self, device_id: str) -> str:
        """Generate JWT token containing device_id."""
        payload = {
            "device_id": device_id,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(seconds=self.vision_expire_seconds),
        }
        return jwt.encode(payload, self.secret_key, algorithm="HS256")
    
    def _verify_jwt_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """Verify JWT token and return device_id."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
            return True, payload.get("device_id")
        except jwt.ExpiredSignatureError:
            self.logger.debug("Vision token expired")
            return False, None
        except jwt.InvalidTokenError as e:
            self.logger.debug(f"Invalid vision token: {e}")
            return False, None
    
    # ============ HMAC Fallback Implementation ============
    
    def _generate_hmac_vision_token(self, device_id: str) -> str:
        """
        Generate HMAC-based vision token (fallback when JWT not available).
        
        Format: base64(device_id).timestamp.signature
        """
        ts = int(time.time())
        device_id_b64 = base64.urlsafe_b64encode(device_id.encode()).decode().rstrip("=")
        content = f"{device_id_b64}|{ts}"
        signature = self._hmac_sign(content)
        return f"{device_id_b64}.{ts}.{signature}"
    
    def _verify_hmac_vision_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """Verify HMAC-based vision token and return device_id."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False, None
            
            device_id_b64, ts_str, signature = parts
            ts = int(ts_str)
            
            # Check expiration
            if int(time.time()) - ts > self.vision_expire_seconds:
                return False, None
            
            # Verify signature
            content = f"{device_id_b64}|{ts}"
            expected_sig = self._hmac_sign(content)
            if not hmac.compare_digest(signature, expected_sig):
                return False, None
            
            # Decode device_id
            # Add padding back for base64 decode
            padding = 4 - len(device_id_b64) % 4
            if padding != 4:
                device_id_b64 += "=" * padding
            device_id = base64.urlsafe_b64decode(device_id_b64).decode()
            
            return True, device_id
            
        except Exception as e:
            self.logger.debug(f"HMAC vision token verification failed: {e}")
            return False, None
    
    # ============ Helper Methods ============
    
    def _hmac_sign(self, content: str) -> str:
        """HMAC-SHA256 signature with Base64 encoding."""
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            content.encode("utf-8"),
            hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def generate_mqtt_password(content: str, secret_key: str) -> str:
    """
    Generate MQTT password signature.
    
    Used for MQTT gateway authentication.
    
    Args:
        content: Signature content (clientId + '|' + username)
        secret_key: Secret key
        
    Returns:
        Base64 encoded HMAC-SHA256 signature
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

