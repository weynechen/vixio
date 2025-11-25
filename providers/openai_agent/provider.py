"""
OpenAI Agent provider implementation using OpenAI Agents framework and LiteLLM
"""

from typing import Any, AsyncIterator, Dict, List, Optional
from providers.agent import AgentProvider, Tool


try:
    from agents import Agent, Runner, FunctionTool
    from agents.extensions.models.litellm_model import LitellmModel
    from agents.tool_context import ToolContext
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = False


class OpenAIAgentProvider(AgentProvider):
    """
    OpenAI Agent provider implementation.
    
    Uses OpenAI Agents framework with LiteLLM for flexible model support.
    Delegates conversation management, memory, and tool execution to the framework.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek/deepseek-chat",
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        name: str = "OpenAIAgent",
        timeout: int = 300,
        top_p: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
    ):
        """
        Initialize OpenAI Agent provider.
        
        Args:
            api_key: OpenAI API key (or compatible API key)
            model: Model name (e.g., "gpt-4o-mini", "gpt-4")
            base_url: Optional custom base URL (for Azure, OpenAI-compatible APIs, etc.)
            temperature: Response randomness (0.0-2.0)
            max_tokens: Maximum tokens in response
            name: Provider name
            timeout: Request timeout in seconds
            top_p: Nucleus sampling parameter (optional)
            frequency_penalty: Frequency penalty parameter (optional)
        """
        if not AGENTS_AVAILABLE:
            raise RuntimeError(
                "OpenAI Agents framework not available. "
                "Please install: pip install agents litellm"
            )
        
        config = {
            "api_key": api_key,
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
        }
        super().__init__(name=name, config=config)
        
        self.api_key = api_key
        self.model_name = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.top_p = top_p
        self.frequency_penalty = frequency_penalty
        
        # Agent framework objects (created in initialize)
        self.model = None
        self.agent = None
        self.system_prompt = None
        
        self.logger.info(
            f"OpenAI Agent provider created: model={model}, "
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
            tools: List of tools (converted to FunctionTool)
            system_prompt: System prompt (instructions for the agent)
            **kwargs: Additional Agent framework parameters
        """
        self.logger.info("Initializing OpenAI Agent framework...")
        
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        
        # Create LiteLLM model
        model_params = {
            "model": self.model_name,
            "api_key": self.api_key,
        }
        
        if self.base_url:
            model_params["base_url"] = self.base_url
        
        self.logger.info(f"Model params: {model_params}")
        self.model = LitellmModel(**model_params)
        
        # Convert tools to FunctionTool format
        function_tools = []
        if tools:
            for tool in tools:
                try:
                    func_tool = self._convert_to_function_tool(tool)
                    function_tools.append(func_tool)
                except Exception as e:
                    self.logger.error(f"Failed to convert tool {tool.name}: {e}")
        
        self.logger.info(f"Registered {len(function_tools)} tools")
        
        # Create Agent (framework manages conversation and memory)
        self.agent = Agent(
            name=self.name,
            model=self.model,
            instructions=self.system_prompt,
            tools=function_tools,
            **kwargs,
        )
        
        self._initialized = True
        self.logger.info(
            f"OpenAI Agent initialized successfully: "
            f"agent={self.name}, model={self.model_name}"
        )
    
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
            Response chunks (pure text deltas)
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
        
        self.logger.debug(f"User message: {message[:100]}...")
        
        try:
            # Import required types for event filtering
            from openai.types.responses import ResponseTextDeltaEvent
            
            # Use Runner.run_streamed for streaming responses
            result = Runner.run_streamed(self.agent, input=message)
            
            # Stream text deltas
            async for event in result.stream_events():
                if event.type == "raw_response_event":
                    if isinstance(event.data, ResponseTextDeltaEvent):
                        if event.data.delta:
                            yield event.data.delta
        
        except Exception as e:
            self.logger.error(f"Error during chat: {e}", exc_info=True)
            yield f"[Error: {str(e)}]"
    
    async def reset_conversation(self) -> None:
        """
        Reset conversation.
        
        Note: OpenAI Agents SDK manages conversation state via previous_response_id.
        Each new run without previous_response_id starts a fresh conversation.
        """
        # No explicit reset needed - each Runner.run() call without
        # previous_response_id starts a fresh conversation
        self.logger.info("Conversation reset (next run will start fresh)")
    
    async def shutdown(self) -> None:
        """
        Shutdown Agent and cleanup resources.
        """
        self.logger.info("Shutting down OpenAI Agent")
        # Agent framework doesn't require explicit cleanup
        # But we can clear references
        self.agent = None
        self.model = None
    
    def _convert_to_function_tool(self, tool: Tool) -> FunctionTool:
        """
        Convert our Tool to OpenAI Agent's FunctionTool.
        
        This bridges our tool interface with the Agent framework.
        
        Args:
            tool: Our tool definition
            
        Returns:
            FunctionTool instance
        """
        # Create a wrapper function that calls our executor
        async def tool_function(**kwargs) -> str:
            """Tool execution wrapper."""
            try:
                # Call our tool executor
                if hasattr(tool.executor, '__call__'):
                    result = await tool.executor(**kwargs)
                else:
                    result = await tool.executor.execute(**kwargs)
                
                # Return result as string
                return str(result) if result else "Success"
            
            except Exception as e:
                self.logger.error(f"Tool {tool.name} execution error: {e}")
                return f"Error: {str(e)}"
        
        # Create FunctionTool with our tool's metadata
        return FunctionTool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            function=tool_function,
        )
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "model": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "base_url": self.base_url,
            "timeout": self.timeout,
        })
        return config

