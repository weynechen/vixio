"""
Model downloader utility for automatic model downloading from HuggingFace.

Supports automatic downloading of models on first use:
- ASR: csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09
- TTS: hexgrad/Kokoro-82M-v1.1-zh (handled by kokoro library)
- VAD: onnx-community/silero-vad (handled by silero_vad library)
"""

import os
from pathlib import Path
from typing import Optional
from loguru import logger


# Default model configurations
MODEL_CONFIGS = {
    "sherpa-onnx-asr": {
        "repo_id": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        "local_dir": "models/sherpa-onnx-sense-voice",
        "description": "Sherpa-ONNX SenseVoice ASR model (int8)",
    },
}


def get_project_root() -> Path:
    """
    Get the project root directory.
    
    Searches upward from cwd for a directory containing pyproject.toml or .git.
    
    Returns:
        Path to project root, or cwd if not found.
    """
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    return cwd


def ensure_model_downloaded(
    repo_id: str,
    local_dir: Optional[str] = None,
    models_dir: Optional[str] = None,
) -> str:
    """
    Ensure a model is downloaded from HuggingFace.
    
    If the model already exists locally, returns the local path.
    Otherwise downloads from HuggingFace Hub.
    
    Args:
        repo_id: HuggingFace repository ID (e.g., "csukuangfj/sherpa-onnx-...")
        local_dir: Optional local directory name (relative to models_dir).
                   If None, uses the repo name as directory.
        models_dir: Optional base models directory path.
                    If None, uses "models/" in project root.
    
    Returns:
        Absolute path to the downloaded model directory.
        
    Raises:
        ImportError: If huggingface_hub is not installed.
        Exception: If download fails.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required for automatic model download. "
            "Install with: pip install huggingface_hub"
        )
    
    # Determine models directory
    if models_dir:
        base_models_dir = Path(models_dir)
    else:
        project_root = get_project_root()
        base_models_dir = project_root / "models"
    
    # Ensure models directory exists
    base_models_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine local directory name
    if local_dir:
        model_dir = base_models_dir / local_dir
    else:
        # Use repo name as directory (e.g., "sherpa-onnx-sense-voice-...")
        repo_name = repo_id.split("/")[-1]
        model_dir = base_models_dir / repo_name
    
    # Check if model already exists
    if model_dir.exists() and any(model_dir.iterdir()):
        logger.info(f"Model already exists at: {model_dir}")
        return str(model_dir)
    
    # Download from HuggingFace
    logger.info(f"Downloading model from HuggingFace: {repo_id}")
    logger.info(f"Target directory: {model_dir}")
    
    try:
        # Download to target directory
        downloaded_path = snapshot_download(
            repo_id=repo_id,
            local_dir=str(model_dir),
            local_dir_use_symlinks=False,  # Use actual files, not symlinks
        )
        
        logger.info(f"Model downloaded successfully to: {downloaded_path}")
        return downloaded_path
        
    except Exception as e:
        logger.error(f"Failed to download model {repo_id}: {e}")
        raise


def download_asr_model(
    repo_id: str = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
    local_dir: str = "sherpa-onnx-sense-voice",
    models_dir: Optional[str] = None,
) -> str:
    """
    Download Sherpa-ONNX ASR model.
    
    Args:
        repo_id: HuggingFace repository ID for the ASR model.
        local_dir: Local directory name under models/.
        models_dir: Optional base models directory path.
    
    Returns:
        Path to the downloaded model directory.
    """
    return ensure_model_downloaded(
        repo_id=repo_id,
        local_dir=local_dir,
        models_dir=models_dir,
    )
