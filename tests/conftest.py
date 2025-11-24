"""
Pytest fixtures for testing
"""

import pytest


@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "websocket_path": "/xiaozhi/v1/",
            "auth": {
                "enabled": True,
                "allowed_devices": [],
                "expire_seconds": 3600,
            },
            "auth_key": "test_secret_key",
            "timezone_offset": 8,
        }
    }


@pytest.fixture
def sample_audio_data():
    """Sample audio data for testing"""
    # Simulated Opus-encoded audio (just random bytes for testing)
    return b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09'

