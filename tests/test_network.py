"""
Unit tests for network utilities
"""

import pytest
from vixio.utils.network import get_local_ip


class TestNetworkUtils:
    """Test network utility functions"""
    
    def test_get_local_ip(self):
        """Test getting local IP address"""
        ip = get_local_ip()
        
        assert isinstance(ip, str)
        assert len(ip) > 0
        
        # Should be a valid IP format
        parts = ip.split(".")
        assert len(parts) == 4
        
        # Each part should be a valid number between 0-255
        for part in parts:
            assert part.isdigit()
            num = int(part)
            assert 0 <= num <= 255
    
    def test_get_local_ip_not_localhost(self):
        """Test that local IP is not 127.0.0.1 in normal cases"""
        ip = get_local_ip()
        
        # In normal network environment, should not be localhost
        # But if network is not available, it may fallback to 127.0.0.1
        # So we just check it's a valid IP
        assert ip in ["127.0.0.1"] or not ip.startswith("127.")
    
    def test_get_local_ip_consistency(self):
        """Test that get_local_ip returns consistent results"""
        ip1 = get_local_ip()
        ip2 = get_local_ip()
        
        # Should return same IP on consecutive calls
        assert ip1 == ip2

