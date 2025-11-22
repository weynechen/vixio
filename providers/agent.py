"""
Agent provider interface
"""

from abc import abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional
from vixio.providers.base import BaseProvider


class Tool:
    """
    Tool definition for Agent.
    
    Represents a tool/function that the Agent can call.
    """
    
    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        executor: Any,
    ):
        """
        Initialize tool.
        
        Args:
            name: Tool name
            description: Tool description
            parameters: JSON schema for parameters
            executor: Executor function or object
        """
        self.name = name
        self.description = description
        self.parameters = parameters
        self.executor = executor
    
    def __str__(self) -> str:
        return f"Tool({self.name})"


class AgentProvider(BaseProvider):
    """
    Agent provider interface.
    
    Provides a unified interface for different Agent frameworks
    (OpenAI Agent, LangChain, LangGraph, etc.)
    
    Key principle: Pure text input/output, completely independent of audio.
    """
    
    def __init__(self, name: str = None, config: Dict[str, Any] = None):
        """
        Initialize Agent provider.
        
        Args:
            name: Provider name
            config: Agent configuration
        """
        super().__init__(name=name)
        self.config = config or {}
        self._initialized = False
    
    @abstractmethod
    async def initialize(
        self,
        tools: Optional[List[Tool]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Initialize Agent with tools and system prompt.
        
        Args:
            tools: List of available tools
            system_prompt: System prompt (with context injected)
            **kwargs: Additional framework-specific parameters
        """
        pass
    
    @abstractmethod
    async def chat(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """
        Send a message to Agent and get streaming response.
        
        Pure text input/output - no audio dependencies.
        
        Args:
            message: User message (pure text)
            context: Optional context (device_id, user_info, etc.)
            
        Yields:
            Response chunks (pure text deltas)
            
        Example:
            async for chunk in agent.chat("Hello"):
                print(chunk, end="", flush=True)
        """
        pass
    
    @abstractmethod
    async def reset_conversation(self) -> None:
        """
        Reset conversation history.
        
        Uses framework's built-in memory management.
        """
        pass
    
    def is_initialized(self) -> bool:
        """
        Check if Agent is initialized.
        
        Returns:
            True if initialized
        """
        return self._initialized
    
    async def shutdown(self) -> None:
        """
        Shutdown Agent and cleanup resources.
        
        Default implementation does nothing.
        Override if needed.
        """
        pass
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get provider configuration.
        
        Returns:
            Configuration dictionary
        """
        config = super().get_config()
        config.update({
            "initialized": self._initialized,
        })
        return config
