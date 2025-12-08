"""
OpenAI-compatible VLM vision describer

Uses OpenAI-compatible API format for vision language models:
- GPT-4o, GPT-4o-mini
- GLM-4V (Zhipu AI)
- Qwen-VL (via OpenAI-compatible endpoint)
- Other OpenAI-compatible VLM providers

Image format: base64 data URL (data:image/jpeg;base64,...)
API format: /chat/completions with image_url content type
"""

from typing import Optional, Dict, Any
from vixio.providers.vision import ImageContent, VisionDescriber
from vixio.providers.registry import register_provider

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


@register_provider("openai-vlm-remote")
class OpenAICompatibleVLM(VisionDescriber):
    """
    OpenAI-compatible VLM vision describer.
    
    Uses OpenAI chat/completions API format with image_url content type.
    Image is converted to base64 data URL format.
    
    Suitable for:
    - OpenAI GPT-4o, GPT-4o-mini
    - Zhipu GLM-4V (glm-4v-flash, glm-4v-plus)
    - Any OpenAI-compatible VLM endpoint
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
        name: Optional[str] = None,
    ):
        """
        Initialize OpenAI-compatible VLM describer.
        
        Args:
            model: Model name (e.g., "glm-4v-flash", "gpt-4o-mini")
            api_key: API key
            base_url: API base URL (default: https://api.openai.com/v1)
            base_prompt: Custom base prompt (optional)
            max_tokens: Maximum tokens for response
            timeout: Request timeout in seconds
            name: Provider name for logging
        """
        super().__init__(name=name or "OpenAICompatibleVLM")
        
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for OpenAICompatibleVLM. Install with: uv add httpx")
        
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.base_prompt = base_prompt or self.DEFAULT_PROMPT
        self.max_tokens = max_tokens
        self.timeout = timeout
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema for this provider."""
        return {
            "model": {
                "type": "string",
                "required": True,
                "description": "Model name (e.g., 'glm-4v-flash', 'gpt-4o-mini')"
            },
            "api_key": {
                "type": "string",
                "required": True,
                "description": "API key for authentication"
            },
            "base_url": {
                "type": "string",
                "required": False,
                "default": "https://api.openai.com/v1",
                "description": "API base URL"
            },
            "base_prompt": {
                "type": "string",
                "required": False,
                "description": "Custom base prompt for image description"
            },
            "max_tokens": {
                "type": "int",
                "required": False,
                "default": 500,
                "description": "Maximum tokens for response"
            },
            "timeout": {
                "type": "float",
                "required": False,
                "default": 30.0,
                "description": "Request timeout in seconds"
            },
        }
    
    async def describe(
        self, 
        image: ImageContent, 
        query: str
    ) -> str:
        """
        Describe image using OpenAI-compatible VLM API.
        
        Converts image to base64 data URL and sends to /chat/completions.
        
        Args:
            image: Image content (raw bytes or base64)
            query: User's question
            
        Returns:
            Targeted image description / answer
        """
        prompt = f"{self.base_prompt}\n\nUser's question: {query}\n\nPlease describe the relevant content in the image."
        
        # Convert image to data URL (provider handles format conversion)
        image_url = image.to_data_url()
        
        # Build message with image (OpenAI format)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url}
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
                self.logger.debug(f"VLM response: {description[:100]}...")
                return description
                
        except Exception as e:
            self.logger.error(f"OpenAI-compatible VLM request failed: {e}")
            return f"[Image analysis failed: {str(e)}]"


# Backward compatibility alias
VLMDescriber = OpenAICompatibleVLM
