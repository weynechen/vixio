"""
Test script for logger configuration

Run this to verify logger is working correctly:
    python test_logger.py
"""

from loguru import logger
from vixio.utils import configure_logger

# Optional: Reconfigure with custom settings
# configure_logger(level="DEBUG", log_dir="logs")

logger.info("This is an INFO message - should appear in console and file")
logger.debug("This is a DEBUG message - will only appear if level is DEBUG")
logger.warning("This is a WARNING message")
logger.error("This is an ERROR message")

# Test with context binding
session_logger = logger.bind(session="test-123")
session_logger.info("This message has session context")

station_logger = logger.bind(station="TestStation")
station_logger.info("This message has station context")

print("\n✅ Logger test complete! Check logs/ directory for log files.")

