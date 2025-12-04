"""
Vision describers - implementations for image-to-text conversion

Available describers:
- VLMDescriber: Use VLM (GLM-4V, GPT-4o-mini) for description
- CompositeDescriber: Combine multiple describers
"""

from providers.vision_describers.base import VisionDescriber
from providers.vision_describers.vlm import VLMDescriber
from providers.vision_describers.composite import CompositeDescriber

__all__ = [
    "VisionDescriber",
    "VLMDescriber", 
    "CompositeDescriber",
]

