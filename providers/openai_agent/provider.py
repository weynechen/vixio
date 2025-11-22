"""
OpenAI Agent provider implementation
"""

from typing import Any, AsyncIterator, Dict, List, Optional
from vixio.providers.agent import AgentProvider, Tool


try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class OpenAIAgentProvider(AgentProvider):
    """
    OpenAI Agent provider implementation.
    
    Uses OpenAI's Chat Completion API with streaming support.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        name: str = "OpenAIAgent"
    ):
        """
        Initialize OpenAI Agent provider.
        
        Args:
            api_key: OpenAI API key
            model: Model name (e.g., "gpt-4o-mini", "gpt-4")
            base_url: Optional custom base URL (for Azure, etc.)
            temperature: Response randomness (0.0-2.0)
            max_tokens: Maximum tokens in response
            name: Provider name
        """
        if not OPENAI_AVAILABLE:
            raise RuntimeError(
                "OpenAI SDK not available. "
                "Please install: pip install openai"
            )
        
        config = {
            "api_key": api_key,
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        super().__init__(name=name, config=config)
        
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        # Create OpenAI client
        client_params = {"api_key": api_key}
        if base_url:
            client_params["base_url"] = base_url
        
        self.client = AsyncOpenAI(**client_params)
        
        # System prompt and tools
        self.system_prompt = None
        self.tools = []
        self.messages = []  # Conversation history
        
        self.logger.info(
            f"OpenAI Agent initialized: model={model}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )
    
    async def initialize(
        self,
        tools: Optional[List[Tool]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Initialize Agent with tools and system prompt.
        
        Args:
            tools: List of tools (OpenAI function calling)
            system_prompt: System prompt
            **kwargs: Additional parameters
        """
        self.logger.info("Initializing OpenAI Agent...")
        
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        self.tools = tools or []
        
        # Initialize conversation with system message
        self.messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        # Convert tools to OpenAI function format if provided
        if self.tools:
            self.logger.info(f"Registered {len(self.tools)} tools")
            # Note: Tool execution not implemented in this basic version
            # Can be added later with function calling support
        
        self._initialized = True
        self.logger.info("OpenAI Agent initialized successfully")
    
    async def chat(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """
        Chat with Agent (streaming).
        
        Args:
            message: User message (pure text)
            context: Optional context
            
        Yields:
            Response chunks (text deltas)
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
        
        self.logger.debug(f"User message: {message[:100]}...")
        
        # Add user message to history
        self.messages.append({"role": "user", "content": message})
        
        try:
            # Create streaming completion
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
            )
            
            # Collect full response for history
            full_response = ""
            
            # Stream response chunks
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_response += delta.content
                        yield delta.content
            
            # Add assistant response to history
            if full_response:
                self.messages.append({"role": "assistant", "content": full_response})
                self.logger.debug(f"Assistant response: {full_response[:100]}...")
        
        except Exception as e:
            self.logger.error(f"Error during chat: {e}", exc_info=True)
            error_msg = f"[Error: {str(e)}]"
            yield error_msg
            # Add error to history
            self.messages.append({"role": "assistant", "content": error_msg})
    
    async def reset_conversation(self) -> None:
        """
        Reset conversation history.
        """
        self.logger.info("Resetting conversation history")
        
        # Reset to just system message
        self.messages = [
            {"role": "system", "content": self.system_prompt}
        ]
    
    async def shutdown(self) -> None:
        """
        Shutdown Agent and cleanup resources.
        """
        self.logger.info("Shutting down OpenAI Agent")
        await self.client.close()
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "message_count": len(self.messages),
        })
        return config

