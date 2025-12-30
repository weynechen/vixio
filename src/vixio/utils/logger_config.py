"""
Loguru configuration for Vixio framework

Provides centralized logger configuration with:
- File logging to logs/ directory (when VIXIO_LOG_MODE=file)
- Console output
- Automatic log rotation
- Default INFO level

Log Mode Control:
    Set environment variable VIXIO_LOG_MODE to control file logging:
    - VIXIO_LOG_MODE=file: Enable file logging (for development/debugging)
    - VIXIO_LOG_MODE=none or not set: Console only (default, for quick start)
"""

import os
import sys
from pathlib import Path
from typing import Optional, List
from loguru import logger


# Environment variable to control log mode
LOG_MODE_ENV = "VIXIO_LOG_MODE"
LOG_MODE_FILE = "file"
LOG_MODE_NONE = "none"

# Global flag to ensure configuration only happens once
_configured = False

# Store last configuration for reconfiguration (e.g., after third-party library resets logger)
_last_config = {}


def is_file_logging_enabled() -> bool:
    """
    Check if file logging is enabled via environment variable.
    
    Returns:
        True if VIXIO_LOG_MODE=file, False otherwise
    """
    return os.environ.get(LOG_MODE_ENV, "").lower() == LOG_MODE_FILE


def configure_logger(
    log_dir: str = "logs",
    level: str = "INFO",
    rotation: str = "100 MB",
    retention: str = "30 days",
    console_level: Optional[str] = None,
    file_level: Optional[str] = None,
    debug_components: Optional[List[str]] = None,
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
        debug_components: List of component names to enable DEBUG output for
                         (e.g., ["LatencyMonitor", "InputValidator"])
    
    Example:
        >>> from vixio.utils.logger_config import configure_logger
        >>> configure_logger(level="DEBUG")  # Set DEBUG level globally
        >>> configure_logger(console_level="INFO", file_level="DEBUG")  # Different levels
        >>> configure_logger(level="INFO", debug_components=["LatencyMonitor"])  # DEBUG for specific component
    """
    global _configured, _last_config
    
    # Only configure once (unless explicitly reconfigured)
    if _configured:
        logger.warning("Logger already configured, skipping reconfiguration")
        return
    
    # Save configuration for potential reconfiguration
    _last_config = {
        "log_dir": log_dir,
        "level": level,
        "rotation": rotation,
        "retention": retention,
        "console_level": console_level,
        "file_level": file_level,
        "debug_components": debug_components,
    }
    
    # Remove default handler
    logger.remove()
    
    # Configure default extra (session_id will be set later per-station)
    logger.configure(extra={"session_id": "--------"})
    
    # Set levels (use default level if specific levels not provided)
    console_level = console_level or level
    file_level = file_level or level
    debug_components = debug_components or []
    
    # Create filter function for component-specific DEBUG
    def make_component_filter(min_level_name: str):
        """
        Create a filter function for component-specific DEBUG.
        
        Args:
            min_level_name: Minimum level for non-debug components (e.g., "INFO")
        
        Returns:
            Filter function for loguru handler
        """
        def component_filter(record):
            """
            Allow DEBUG level for specific components, enforce min_level for others.
            
            Components are identified by 'component' field in record extra.
            All logger.bind() calls should use component=<name> for consistency.
            
            Uses prefix matching: "OutputStation" matches "OutputStation-12345678".
            """
            # Check if this is a debug-enabled component (using prefix match)
            component_name = record["extra"].get("component") or ""
            is_debug_component = any(
                component_name.startswith(prefix) for prefix in debug_components
            )
            
            if is_debug_component:
                # Allow DEBUG and above for this component
                return True
            
            # For other components, filter based on the minimum level
            if debug_components:
                # When debug_components is set, handler level is DEBUG
                # So we need to filter non-debug components at the global level
                from loguru import logger as _logger
                min_level_no = _logger.level(min_level_name).no
                return record["level"].no >= min_level_no
            
            return True
        
        return component_filter
    
    # Add console handler with colors and filter
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
               "<level>{level: <8}</level> | "
               "<yellow>[{extra[session_id]}]</yellow> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
               "<level>{message}</level>",
        level="DEBUG" if debug_components else console_level,  # Allow DEBUG if any components need it
        filter=make_component_filter(console_level),
        colorize=True,
    )
    
    # Only add file handler if file logging is enabled
    file_logging_enabled = is_file_logging_enabled()
    if file_logging_enabled:
        # Ensure log directory exists
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Add file handler with rotation and filter
        log_file = log_path / "vixio_{time:YYYY-MM-DD}.log"
        logger.add(
            str(log_file),
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | [{extra[session_id]}] | {name}:{function}:{line} | {message}",
            level="DEBUG" if debug_components else file_level,  # Allow DEBUG if any components need it
            filter=make_component_filter(file_level),
            rotation=rotation,
            retention=retention,
            compression="zip",  # Compress rotated logs
            encoding="utf-8",
        )
    
    # Mark as configured
    _configured = True
    
    debug_info = f" (DEBUG components: {', '.join(debug_components)})" if debug_components else ""
    file_info = f", file={file_level}, log_dir={log_dir}" if file_logging_enabled else " (file logging disabled)"
    logger.info(f"Logger configured: console={console_level}{file_info}{debug_info}")


def reset_logger() -> None:
    """
    Reset logger configuration flag.
    
    This allows reconfiguration by calling configure_logger() again.
    Useful for testing or dynamic reconfiguration.
    """
    global _configured
    _configured = False
    logger.remove()


def reconfigure_logger() -> None:
    """
    Reconfigure logger using the last saved configuration.
    
    This is useful when a third-party library (e.g., kokoro) calls logger.remove()
    and removes all vixio's handlers. Call this function to restore the logger
    with the same configuration that was used before.
    
    If no previous configuration exists, uses default settings.
    """
    global _configured, _last_config
    
    # Reset the configured flag to allow reconfiguration
    _configured = False
    logger.remove()
    
    # Reconfigure with saved settings (or defaults if no previous config)
    if _last_config:
        configure_logger(**_last_config)
    else:
        configure_logger()


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

