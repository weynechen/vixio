"""
Loguru configuration for Vixio framework

Provides centralized logger configuration with:
- File logging to logs/ directory
- Console output
- Automatic log rotation
- Default INFO level
"""

import os
import sys
from pathlib import Path
from loguru import logger


# Global flag to ensure configuration only happens once
_configured = False


def configure_logger(
    log_dir: str = "logs",
    level: str = "INFO",
    rotation: str = "100 MB",
    retention: str = "30 days",
    console_level: str = None,
    file_level: str = None,
) -> None:
    """
    Configure loguru logger with file and console outputs.
    
    Args:
        log_dir: Directory for log files (default: "logs")
        level: Default log level (default: "INFO")
        rotation: When to rotate log files (default: "100 MB")
        retention: How long to keep log files (default: "30 days")
        console_level: Console log level (default: same as level)
        file_level: File log level (default: same as level)
    
    Example:
        >>> from utils.logger_config import configure_logger
        >>> configure_logger(level="DEBUG")  # Set DEBUG level
        >>> configure_logger(console_level="INFO", file_level="DEBUG")  # Different levels
    """
    global _configured
    
    # Only configure once (unless explicitly reconfigured)
    if _configured:
        logger.warning("Logger already configured, skipping reconfiguration")
        return
    
    # Remove default handler
    logger.remove()
    
    # Set levels (use default level if specific levels not provided)
    console_level = console_level or level
    file_level = file_level or level
    
    # Add console handler with colors
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
               "<level>{message}</level>",
        level=console_level,
        colorize=True,
    )
    
    # Ensure log directory exists
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Add file handler with rotation
    log_file = log_path / "vixio_{time:YYYY-MM-DD}.log"
    logger.add(
        str(log_file),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=file_level,
        rotation=rotation,
        retention=retention,
        compression="zip",  # Compress rotated logs
        encoding="utf-8",
    )
    
    # Mark as configured
    _configured = True
    
    logger.info(f"Logger configured: console={console_level}, file={file_level}, log_dir={log_dir}")


def reset_logger() -> None:
    """
    Reset logger configuration flag.
    
    This allows reconfiguration by calling configure_logger() again.
    Useful for testing or dynamic reconfiguration.
    """
    global _configured
    _configured = False
    logger.remove()


def get_logger():
    """
    Get configured logger instance.
    
    Returns:
        loguru.Logger: Configured logger instance
    
    Note:
        This function ensures logger is configured before returning.
        If not configured, it will use default configuration.
    """
    if not _configured:
        configure_logger()
    return logger


# Auto-configure with defaults on import (can be overridden by user)
def auto_configure():
    """Auto-configure logger on module import with default settings."""
    # Check if running in pytest (skip auto-config in tests)
    if "pytest" in sys.modules:
        return
    
    # Only auto-configure if not already configured
    if not _configured:
        try:
            configure_logger()
        except Exception as e:
            # Fallback: keep default loguru config if configuration fails
            print(f"Warning: Failed to configure logger: {e}", file=sys.stderr)


# Auto-configure on import
auto_configure()

