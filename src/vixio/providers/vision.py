"""
Vision module - data structures and strategies for vision processing

This module provides:
- ImageContent: Image data container
- MultimodalMessage: Unified message format for Agent
- VisionStrategy: Abstract base for vision processing strategies
- DescribeStrategy: Single-modal strategy (image -> description -> LLM)
- PassthroughStrategy: Multi-modal strategy (image + text -> VLM)
- VisionDescriber: Base class for vision describers (inherits BaseProvider)
"""

from abc import abstractmethod, ABC
from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict
import time
import base64

from vixio.providers.base import BaseProvider


@dataclass
class ImageContent:
    """
    Image content - carrier for visual context.
    
    Design principle: Framework-agnostic representation.
    
    Attributes:
        image: Image data (base64 string, URL, or raw bytes)
        mime_type: MIME type of the image
        source: Image source ("camera", "screenshot", "upload")
        trigger: Capture trigger ("vad_start", "heartbeat", "request")
        capture_time: Timestamp when image was captured
        width: Image width in pixels
        height: Image height in pixels
    """
    image: bytes | str  # base64 string, URL, or raw bytes
    mime_type: str = "image/jpeg"
    
    # Metadata
    source: str = ""            # "camera", "screenshot", "upload"
    trigger: str = ""           # "vad_start", "heartbeat", "request"
    capture_time: Optional[float] = field(default_factory=time.time)
    width: int = 0
    height: int = 0
    
    def to_base64(self) -> str:
        """
        Convert image to base64 string.
        
        Returns:
            Base64 encoded image string
        """
        if isinstance(self.image, str):
            # Already a string (base64 or URL)
            return self.image
        elif isinstance(self.image, bytes):
            return base64.b64encode(self.image).decode("utf-8")
        else:
            raise ValueError(f"Unknown image type: {type(self.image)}")
    
    def to_data_url(self) -> str:
        """
        Convert image to data URL format.
        
        Returns:
            Data URL string (data:image/jpeg;base64,...)
        """
        if isinstance(self.image, str):
            if self.image.startswith(("http://", "https://", "data:")):
                return self.image
            else:
                # Assume it's base64
                return f"data:{self.mime_type};base64,{self.image}"
        elif isinstance(self.image, bytes):
            b64 = base64.b64encode(self.image).decode("utf-8")
            return f"data:{self.mime_type};base64,{b64}"
        else:
            raise ValueError(f"Unknown image type: {type(self.image)}")
    
    def __str__(self) -> str:
        size_info = f"{self.width}x{self.height}" if self.width and self.height else "unknown"
        data_size = len(self.image) if isinstance(self.image, (bytes, str)) else 0
        return f"ImageContent({size_info}, {self.mime_type}, {data_size} bytes, trigger={self.trigger})"


@dataclass  
class MultimodalMessage:
    """
    Multimodal message - unified input format for Agent.
    
    Regardless of which strategy is used, Agent receives this format.
    
    Attributes:
        text: Text content (may include image descriptions for single-modal)
        images: Original images (only used for multi-modal strategy)
    """
    text: str
    images: Optional[List[ImageContent]] = None
    
    @property
    def has_images(self) -> bool:
        """Check if message contains images."""
        return self.images is not None and len(self.images) > 0
    
    def __str__(self) -> str:
        text_preview = self.text[:50] + "..." if len(self.text) > 50 else self.text
        image_info = f", {len(self.images)} images" if self.has_images else ""
        return f"MultimodalMessage('{text_preview}'{image_info})"


class VisionStrategy(ABC):
    """
    Vision processing strategy base class.
    
    Framework-level component that converts (text, images) to Agent-processable format.
    """
    
    @abstractmethod
    async def process(
        self, 
        text: str, 
        images: Optional[List[ImageContent]]
    ) -> MultimodalMessage:
        """
        Process vision input.
        
        Args:
            text: User text input (from ASR)
            images: List of images (may be empty)
            
        Returns:
            MultimodalMessage: Unified format for Agent
        """
        pass


class PassthroughStrategy(VisionStrategy):
    """
    Multi-modal strategy: Pass images directly to Agent.
    
    Suitable for VLM (GPT-4o, GLM-4V, Gemini, etc.)
    """
    
    async def process(
        self, 
        text: str, 
        images: Optional[List[ImageContent]]
    ) -> MultimodalMessage:
        """
        Pass through text and images directly.
        
        Args:
            text: User text input
            images: List of images
            
        Returns:
            MultimodalMessage with original text and images
        """
        return MultimodalMessage(text=text, images=images)


class DescribeStrategy(VisionStrategy):
    """
    Single-modal strategy: Convert images to text descriptions.
    
    Suitable for regular LLM, using VLM/YOLO/OCR for preprocessing.
    
    Key design: Describer needs user's query to provide targeted descriptions.
    """
    
    def __init__(self, describer: "VisionDescriber"):
        """
        Initialize with a vision describer.
        
        Args:
            describer: VisionDescriber instance for image-to-text conversion
        """
        self.describer = describer
    
    async def process(
        self, 
        text: str, 
        images: Optional[List[ImageContent]]
    ) -> MultimodalMessage:
        """
        Convert images to descriptions and inject into text.
        
        Args:
            text: User text input (used as query for targeted description)
            images: List of images
            
        Returns:
            MultimodalMessage with enhanced text (no images)
        """
        if not images:
            return MultimodalMessage(text=text)
        
        # Get targeted descriptions based on user's query
        descriptions = []
        for img in images:
            desc = await self.describer.describe(image=img, query=text)
            descriptions.append(desc)
        
        # Build enhanced text
        description_text = "\n".join([
            f"[Image {i+1}: {desc}]" 
            for i, desc in enumerate(descriptions)
        ])
        
        enhanced_text = f"User question: {text}\n\nVisual context:\n{description_text}"
        
        return MultimodalMessage(text=enhanced_text, images=None)


class VisionDescriber(BaseProvider):
    """
    Vision describer base class.
    
    Inherits from BaseProvider to integrate with ProviderFactory.
    Converts images to text descriptions / answers.
    
    Key design: describe() needs user's query to provide relevant descriptions.
    
    Default BaseProvider properties:
    - is_local: False (typically cloud API)
    - is_stateful: False (stateless)
    - category: "vlm"
    """
    
    # ============ BaseProvider implementations ============
    
    @property
    def is_local(self) -> bool:
        """VLM providers are typically remote (cloud API)."""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """VLM providers are stateless."""
        return False
    
    @property
    def category(self) -> str:
        """Provider category."""
        return "vlm"
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """
        Return configuration schema.
        
        Subclasses should override this to provide their specific schema.
        """
        return {}
    
    async def initialize(self) -> None:
        """
        Initialize the provider.
        
        Default implementation does nothing.
        Subclasses can override if needed (e.g., for connection pooling).
        """
        pass
    
    async def cleanup(self) -> None:
        """
        Cleanup provider resources.
        
        Default implementation does nothing.
        Subclasses can override if needed.
        """
        pass
    
    # ============ VisionDescriber specific ============
    
    @abstractmethod
    async def describe(
        self, 
        image: ImageContent, 
        query: str
    ) -> str:
        """
        Describe image based on user's query.
        
        Args:
            image: Image content
            query: User's question (guides description focus)
            
        Returns:
            Targeted image description / answer
        """
        pass
