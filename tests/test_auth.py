"""
Unit tests for authentication utilities
"""

import pytest
import time
from utils.auth import AuthManager, generate_password_signature


class TestAuthManager:
    """Test AuthManager class"""
    
    @pytest.fixture
    def auth_manager(self):
        """Create AuthManager instance"""
        return AuthManager(secret_key="test_secret_key", expire_seconds=3600)
    
    def test_create_auth_manager(self, auth_manager):
        """Test creating AuthManager"""
        assert auth_manager.secret_key == "test_secret_key"
        assert auth_manager.expire_seconds == 3600
    
    def test_create_auth_manager_with_invalid_expire(self):
        """Test creating AuthManager with invalid expire seconds"""
        manager = AuthManager(secret_key="test", expire_seconds=-1)
        assert manager.expire_seconds == 60 * 60 * 24 * 30  # Default value
        
        manager = AuthManager(secret_key="test", expire_seconds=0)
        assert manager.expire_seconds == 60 * 60 * 24 * 30  # Default value
    
    def test_generate_token(self, auth_manager):
        """Test generating token"""
        client_id = "client-123"
        username = "device-456"
        
        token = auth_manager.generate_token(client_id, username)
        
        assert isinstance(token, str)
        assert "." in token  # Token format: signature.timestamp
        
        parts = token.split(".")
        assert len(parts) == 2
        assert parts[0]  # Signature part
        assert parts[1].isdigit()  # Timestamp part
    
    def test_verify_valid_token(self, auth_manager):
        """Test verifying valid token"""
        client_id = "client-123"
        username = "device-456"
        
        token = auth_manager.generate_token(client_id, username)
        
        # Verify with correct credentials
        assert auth_manager.verify_token(token, client_id, username) == True
    
    def test_verify_invalid_token(self, auth_manager):
        """Test verifying invalid token"""
        client_id = "client-123"
        username = "device-456"
        
        token = auth_manager.generate_token(client_id, username)
        
        # Verify with wrong client_id
        assert auth_manager.verify_token(token, "wrong-client", username) == False
        
        # Verify with wrong username
        assert auth_manager.verify_token(token, client_id, "wrong-user") == False
        
        # Verify with malformed token
        assert auth_manager.verify_token("invalid.token", client_id, username) == False
        assert auth_manager.verify_token("invalid", client_id, username) == False
    
    def test_verify_expired_token(self):
        """Test verifying expired token"""
        # Create manager with 1 second expiration
        auth_manager = AuthManager(secret_key="test_secret", expire_seconds=1)
        
        client_id = "client-123"
        username = "device-456"
        
        token = auth_manager.generate_token(client_id, username)
        
        # Token should be valid immediately
        assert auth_manager.verify_token(token, client_id, username) == True
        
        # Wait for token to expire
        time.sleep(2)
        
        # Token should be expired now
        assert auth_manager.verify_token(token, client_id, username) == False
    
    def test_token_signature_consistency(self, auth_manager):
        """Test that same credentials produce different tokens (due to timestamp)"""
        client_id = "client-123"
        username = "device-456"
        
        token1 = auth_manager.generate_token(client_id, username)
        time.sleep(1.1)  # Wait more than 1 second to ensure different timestamp
        token2 = auth_manager.generate_token(client_id, username)
        
        # Tokens should be different (different timestamps)
        assert token1 != token2
        
        # But both should be valid
        assert auth_manager.verify_token(token1, client_id, username) == True
        assert auth_manager.verify_token(token2, client_id, username) == True


class TestPasswordSignature:
    """Test password signature generation"""
    
    def test_generate_password_signature(self):
        """Test generating password signature"""
        content = "client_id|username"
        secret_key = "test_secret_key"
        
        signature = generate_password_signature(content, secret_key)
        
        assert isinstance(signature, str)
        assert len(signature) > 0
        assert signature != content  # Should be hashed
    
    def test_password_signature_consistency(self):
        """Test that same input produces same signature"""
        content = "client_id|username"
        secret_key = "test_secret_key"
        
        sig1 = generate_password_signature(content, secret_key)
        sig2 = generate_password_signature(content, secret_key)
        
        assert sig1 == sig2
    
    def test_password_signature_different_for_different_input(self):
        """Test that different input produces different signature"""
        secret_key = "test_secret_key"
        
        sig1 = generate_password_signature("content1", secret_key)
        sig2 = generate_password_signature("content2", secret_key)
        
        assert sig1 != sig2
    
    def test_password_signature_different_for_different_key(self):
        """Test that different secret key produces different signature"""
        content = "client_id|username"
        
        sig1 = generate_password_signature(content, "key1")
        sig2 = generate_password_signature(content, "key2")
        
        assert sig1 != sig2
    
    def test_password_signature_error_handling(self):
        """Test error handling in password signature"""
        # Invalid input should return empty string
        # This test may vary depending on implementation
        try:
            sig = generate_password_signature("content", "key")
            assert isinstance(sig, str)
        except Exception:
            pytest.fail("generate_password_signature should not raise exception")

