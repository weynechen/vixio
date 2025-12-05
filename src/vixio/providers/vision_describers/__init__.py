"""
Vision describers - implementations for image analysis

Available describers:
- OpenAICompatibleVLM: OpenAI-compatible VLM (GPT-4o, GLM-4V, etc.)
- CompositeDescriber: Combine multiple describers

Each describer implements VisionDescriber interface and handles
image format conversion internally based on provider requirements.

Note: VisionDescriber base class is defined in providers.vision
"""

from vixio.providers.vision import VisionDescriber
from vixio.providers.vision_describers.openai_compatible import OpenAICompatibleVLM, VLMDescriber
from vixio.providers.vision_describers.composite import CompositeDescriber

__all__ = [
    "VisionDescriber",
    "OpenAICompatibleVLM",
    "VLMDescriber",  # Backward compatibility alias
    "CompositeDescriber",
]
