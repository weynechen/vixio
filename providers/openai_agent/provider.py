"""
OpenAI Agent provider implementation using OpenAI Agents framework and LiteLLM
"""

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional
from . import dump_promt
from providers.agent import AgentProvider, Tool
from providers.registry import register_provider


try:
    from agents import Agent, Runner, FunctionTool, SQLiteSession
    from agents.extensions.models.litellm_model import LitellmModel
    from agents.tool_context import ToolContext
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = False


@register_provider("openai-agent")
class OpenAIAgentProvider(AgentProvider):
    """
    OpenAI Agent provider implementation.
    
    Uses OpenAI Agents framework with LiteLLM for flexible model support.
    Delegates conversation management, memory, and tool execution to the framework.
    """
    
    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service"""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """Agent is stateful (maintains conversation history)"""
        return True
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "agent"
    
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek/deepseek-chat",
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: int = 300,
        top_p: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        session_id: Optional[str] = None,
        session_db_path: Optional[str] = None,
    ):
        """
        Initialize OpenAI Agent provider.
        
        Args:
            api_key: OpenAI API key (or compatible API key)
            model: Model name (e.g., "gpt-4o-mini", "gpt-4")
            base_url: Optional custom base URL (for Azure, OpenAI-compatible APIs, etc.)
            temperature: Response randomness (0.0-2.0)
            max_tokens: Maximum tokens in response
            timeout: Request timeout in seconds
            top_p: Nucleus sampling parameter (optional)
            frequency_penalty: Frequency penalty parameter (optional)
            session_id: Session ID for conversation memory (auto-generated if not provided)
            session_db_path: Path to SQLite database file for session storage (in-memory if not provided)
        """
        if not AGENTS_AVAILABLE:
            raise RuntimeError(
                "OpenAI Agents framework not available. "
                "Please install: pip install agents litellm"
            )
        
        # Use registered name from decorator
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        
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
        
        # Session configuration for conversation memory
        # session_id is expected to be set externally (e.g., by SessionManager via AgentStation)
        # If not provided, session will be created lazily when set_session_id() is called
        self.session_id = session_id  # Can be None initially
        self.session_db_path = session_db_path  # None = in-memory database
        self.session: Optional[SQLiteSession] = None  # Created when session_id is set
        
        # Agent framework objects (created in initialize)
        self.model = None
        self.agent = None
        self.system_prompt = None
        self._current_tools: List[Tool] = []  # Track current tools for add_tools
        
        self.logger.info(
            f"OpenAI Agent provider created: model={model}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "api_key": {
                "type": "string",
                "required": True,
                "description": "API key for OpenAI or compatible service"
            },
            "model": {
                "type": "string",
                "default": "deepseek/deepseek-chat",
                "description": "Model name (e.g., gpt-4, deepseek/deepseek-chat)"
            },
            "base_url": {
                "type": "string",
                "default": None,
                "description": "Optional custom base URL (for Azure, OpenAI-compatible APIs)"
            },
            "temperature": {
                "type": "float",
                "default": 0.7,
                "description": "Response randomness (0.0-2.0)"
            },
            "max_tokens": {
                "type": "int",
                "default": 2000,
                "description": "Maximum tokens in response"
            },
            "timeout": {
                "type": "int",
                "default": 300,
                "description": "Request timeout in seconds"
            },
            "top_p": {
                "type": "float",
                "default": None,
                "description": "Nucleus sampling parameter (optional)"
            },
            "frequency_penalty": {
                "type": "float",
                "default": None,
                "description": "Frequency penalty parameter (optional)"
            },
            "session_id": {
                "type": "string",
                "default": None,
                "description": "Session ID for conversation memory (auto-generated if not provided)"
            },
            "session_db_path": {
                "type": "string",
                "default": None,
                "description": "Path to SQLite database file for session storage (in-memory if not provided)"
            }
        }
    
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
        
        # Create SQLiteSession for conversation memory (if session_id is already set)
        # Otherwise, session will be created when set_session_id() is called
        if self.session_id:
            self._create_session()
        else:
            self.logger.info(
                "Session not created yet - waiting for set_session_id() call"
            )
        
        # Create LiteLLM model
        model_params = {
            "model": self.model_name,
            "api_key": self.api_key,
        }
        
        if self.base_url:
            model_params["base_url"] = self.base_url
        
        self.logger.info(f"Model params: {model_params}")
        self.model = LitellmModel(**model_params)
        
        # Store and convert tools to FunctionTool format
        self._current_tools = list(tools) if tools else []
        function_tools = []
        for tool in self._current_tools:
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
            
        Note:
            This generator can be closed via aclose() to immediately terminate
            the OpenAI streaming connection. This is critical for handling interrupts.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
        
        if not self.session:
            raise RuntimeError(
                "Session not initialized. Ensure set_session_id() is called before chat(). "
                "This typically happens via AgentStation when Pipeline receives session_id."
            )
        
        self.logger.debug(f"User message: {message[:100]}...")
        
        result = None
        try:
            # Import required types for event filtering
            from openai.types.responses import ResponseTextDeltaEvent
            
            # Use Runner.run_streamed for streaming responses with session memory
            result = Runner.run_streamed(self.agent, input=message, session=self.session)
            
            # Stream text deltas
            async for event in result.stream_events():
                if event.type == "raw_response_event":
                    if isinstance(event.data, ResponseTextDeltaEvent):
                        if event.data.delta:
                            yield event.data.delta
            
            self.logger.debug("Agent streaming completed normally")
        
        except asyncio.CancelledError:
            # Stream was cancelled (user interrupted)
            self.logger.info("Agent stream cancelled by interrupt")
            # Don't yield anything, just cleanup and exit
            raise
        
        except GeneratorExit:
            # Generator is being closed (aclose() was called)
            self.logger.info("Agent stream closed via aclose()")
            # Cleanup will happen in finally block
        
        except Exception as e:
            self.logger.error(f"Error during chat: {e}", exc_info=True)
            yield f"[Error: {str(e)}]"
        
        finally:
            # Ensure OpenAI stream is properly closed
            # This terminates the underlying HTTP connection
            if result is not None:
                try:
                    # Runner result cleanup (if available)
                    if hasattr(result, 'close'):
                        await result.close()
                    self.logger.debug("OpenAI stream resources cleaned up")
                except Exception as e:
                    self.logger.warning(f"Error cleaning up OpenAI stream: {e}")
    
    async def reset_conversation(self) -> None:
        """
        Reset conversation by clearing session history.
        
        This clears all conversation history from the SQLiteSession,
        allowing the agent to start a fresh conversation.
        """
        if self.session:
            await self.session.clear_session()
            self.logger.info(f"Conversation reset: session {self.session_id} cleared")
        else:
            self.logger.warning("Cannot reset conversation: session not initialized")
    
    async def add_tools(self, tools: List[Tool]) -> None:
        """
        Add tools to agent's available tools.
        
        Appends new tools to existing tools and recreates the Agent.
        
        Args:
            tools: Tools to add (appended to existing tools)
        """
        if not self._initialized:
            self.logger.warning("Cannot add tools: agent not initialized")
            return
        
        # Append new tools to existing tools
        existing_names = {t.name for t in self._current_tools}
        new_tools = [t for t in tools if t.name not in existing_names]
        
        if not new_tools:
            self.logger.info("No new tools to add (all already exist)")
            return
        
        self._current_tools.extend(new_tools)
        self.logger.info(
            f"Adding {len(new_tools)} tools to existing {len(self._current_tools) - len(new_tools)} tools"
        )
        
        # Rebuild agent with all tools
        await self._rebuild_agent_with_current_tools()
    
    async def update_tools(self, tools: List[Tool]) -> None:
        """
        Replace all agent's tools with new list.
        
        WARNING: This replaces ALL existing tools. Use add_tools() to append.
        
        Args:
            tools: New list of tools (replaces ALL existing tools)
        """
        if not self._initialized:
            self.logger.warning("Cannot update tools: agent not initialized")
            return
        
        self._current_tools = list(tools)
        self.logger.info(f"Replacing all tools with {len(tools)} new tools")
        
        await self._rebuild_agent_with_current_tools()
    
    async def _rebuild_agent_with_current_tools(self) -> None:
        """Rebuild agent with current tool list."""
        # Convert tools to FunctionTool format
        function_tools = []
        for tool in self._current_tools:
            try:
                func_tool = self._convert_to_function_tool(tool)
                function_tools.append(func_tool)
            except Exception as e:
                self.logger.error(f"Failed to convert tool {tool.name}: {e}")
        
        # Recreate agent with all tools
        self.agent = Agent(
            name=self.name,
            model=self.model,
            instructions=self.system_prompt,
            tools=function_tools,
        )
        
        self.logger.info(f"Agent rebuilt with {len(function_tools)} tools")
    
    async def cleanup(self) -> None:
        """
        Cleanup Agent and resources.
        """
        self.logger.info("Cleaning up OpenAI Agent")
        # Agent framework doesn't require explicit cleanup
        # But we can clear references
        self.agent = None
        self.model = None
        self.session = None
    
    async def get_conversation_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get conversation history from session.
        
        Args:
            limit: Maximum number of items to return (None for all)
            
        Returns:
            List of conversation items (messages)
        """
        if not self.session:
            self.logger.warning("Cannot get history: session not initialized")
            return []
        
        items = await self.session.get_items(limit=limit)
        return items
    
    def _create_session(self) -> None:
        """
        Create SQLiteSession with current session_id.
        
        Internal method called during initialization or when session_id is set.
        """
        if not self.session_id:
            self.logger.warning("Cannot create session: session_id is not set")
            return
        
        if self.session_db_path:
            self.session = SQLiteSession(self.session_id, self.session_db_path)
            self.logger.info(
                f"Created persistent SQLiteSession: id={self.session_id[:8]}..., "
                f"db={self.session_db_path}"
            )
        else:
            self.session = SQLiteSession(self.session_id)
            self.logger.info(
                f"Created in-memory SQLiteSession: id={self.session_id[:8]}..."
            )
    
    def set_session_id(self, session_id: str) -> None:
        """
        Set session ID and create/switch session.
        
        This method is typically called by AgentStation when Pipeline receives
        the session_id from SessionManager (connection_id).
        
        Args:
            session_id: Session ID (typically connection_id from SessionManager)
        """
        old_session_id = self.session_id
        self.session_id = session_id
        
        # Create new session
        self._create_session()
        
        if old_session_id:
            self.logger.info(f"Switched session: {old_session_id[:8]}... -> {session_id[:8]}...")
        else:
            self.logger.info(f"Session initialized: {session_id[:8]}...")
    
    def _convert_to_function_tool(self, tool: Tool) -> FunctionTool:
        """
        Convert our Tool to OpenAI Agent's FunctionTool.
        
        This bridges our tool interface with the Agent framework.
        
        Args:
            tool: Our tool definition
            
        Returns:
            FunctionTool instance
        """
        # Capture tool reference for closure
        tool_ref = tool
        
        # Create a wrapper function that calls our executor
        async def on_invoke_tool(ctx: ToolContext, input_json: str) -> str:
            """Tool execution wrapper."""
            import json
            try:
                # Parse input JSON to kwargs
                kwargs = json.loads(input_json) if input_json else {}
                
                # Call our tool executor
                if hasattr(tool_ref.executor, '__call__'):
                    result = await tool_ref.executor(**kwargs)
                else:
                    result = await tool_ref.executor.execute(**kwargs)
                
                # Return result as string
                return str(result) if result else "Success"
            
            except Exception as e:
                self.logger.error(f"Tool {tool_ref.name} execution error: {e}")
                return f"Error: {str(e)}"
        
        # Create FunctionTool with our tool's metadata
        # Note: FunctionTool uses params_json_schema, not parameters
        return FunctionTool(
            name=tool.name,
            description=tool.description,
            params_json_schema=tool.parameters,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=False,
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
            "session_id": self.session_id,
            "session_db_path": self.session_db_path,
        })
        return config

