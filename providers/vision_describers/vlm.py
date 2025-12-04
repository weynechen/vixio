"""
VLM-based vision describer

Uses Vision Language Models (GLM-4V, GPT-4o-mini, etc.) to describe images.
"""

from typing import Optional
from loguru import logger
from providers.vision import ImageContent
from providers.vision_describers.base import VisionDescriber

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class VLMDescriber(VisionDescriber):
    """
    VLM-based vision describer.
    
    Uses Vision Language Models to generate targeted image descriptions
    based on user's query.
    """
    
    DEFAULT_PROMPT = (
        "The user is looking at this image and asking a question. "
        "Please describe the image content relevant to the user's question. "
        "Only describe what's relevant, don't include unnecessary details."
    )
    
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        base_prompt: Optional[str] = None,
        max_tokens: int = 500,
        timeout: float = 30.0,
    ):
        """
        Initialize VLM describer.
        
        Args:
            model: Model name (e.g., "glm-4v-flash", "gpt-4o-mini")
            api_key: API key
            base_url: API base URL (optional, uses OpenAI-compatible format)
            base_prompt: Custom base prompt (optional)
            max_tokens: Maximum tokens for response
            timeout: Request timeout in seconds
        """
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for VLMDescriber. Install with: uv add httpx")
        
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.base_prompt = base_prompt or self.DEFAULT_PROMPT
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.logger = logger.bind(component="VLMDescriber")
    
    async def describe(
        self, 
        image: ImageContent, 
        query: str
    ) -> str:
        """
        Describe image using VLM based on user's query.
        
        Args:
            image: Image content
            query: User's question
            
        Returns:
            Targeted image description
        """
        prompt = f"{self.base_prompt}\n\nUser's question: {query}\n\nPlease describe the relevant content in the image."
        
        # Build message with image
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image.to_data_url()}
                    }
                ]
            }
        ]
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": self.max_tokens,
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                description = result["choices"][0]["message"]["content"]
                self.logger.debug(f"VLM description: {description[:100]}...")
                return description
                
        except Exception as e:
            self.logger.error(f"VLM describe failed: {e}")
            return f"[Image description failed: {str(e)}]"

