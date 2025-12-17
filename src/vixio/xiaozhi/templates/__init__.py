"""
Xiaozhi project templates for vixio init command.

Available templates:
- xiaozhi-server: Full-featured voice conversation server with:
  - Configurable providers (ASR, TTS, LLM)
  - Customizable prompts
  - Environment variable management
  - Ready-to-run server script

Template structure:
    server/
    ├── .env.example      - Environment variables template
    ├── config.yaml       - Provider configuration
    ├── prompt.txt        - Default system prompt
    ├── prompts/          - Additional prompt examples
    ├── run.py            - Server startup script
    └── README.md         - Usage documentation
"""

from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent

# Map user-facing names to actual directory names
TEMPLATE_MAP = {
    "xiaozhi-server": "server",
}

AVAILABLE_TEMPLATES = list(TEMPLATE_MAP.keys())


def get_template_path(template_name: str) -> Path:
    """
    Get template directory path.
    
    Args:
        template_name: Name of the template (xiaozhi-server)
        
    Returns:
        Path to the template directory
        
    Raises:
        ValueError: If template_name is not valid
        FileNotFoundError: If template directory doesn't exist
    """
    if template_name not in AVAILABLE_TEMPLATES:
        raise ValueError(
            f"Unknown template: {template_name}. "
            f"Available templates: {', '.join(AVAILABLE_TEMPLATES)}"
        )
    
    # Map to actual directory name
    actual_name = TEMPLATE_MAP[template_name]
    template_path = TEMPLATE_DIR / actual_name
    
    if not template_path.exists():
        raise FileNotFoundError(f"Template directory not found: {template_path}")
    
    return template_path


def list_templates() -> list[str]:
    """
    List all available templates.
    
    Returns:
        List of template names
    """
    return AVAILABLE_TEMPLATES.copy()
