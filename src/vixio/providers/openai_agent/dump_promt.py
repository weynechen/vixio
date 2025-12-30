# Setup LiteLLM callback to capture prompts
#
# Log Mode Control:
#     Set environment variable VIXIO_LOG_MODE=file to enable prompt capture.
#     When not set or set to 'none', prompt capture is disabled.
import litellm
from datetime import datetime as dt
import json
from pathlib import Path
import inspect
import threading
from typing import Optional
import os


def _is_file_logging_enabled() -> bool:
    """Check if file logging is enabled via environment variable."""
    return os.environ.get("VIXIO_LOG_MODE", "").lower() == "file"


# Global state for prompt capture
_prompt_capture_state = {
    "output_dir": Path("logs/prompt_logs"),
    "agent_name": "agent",
    "session_id": None,
    "session_start_time": None,
    "session_calls": [],  # List of calls for current session
    "lock": threading.Lock(),
    "enabled": _is_file_logging_enabled(),  # Check at module load time
}

# Only create output directory if file logging is enabled
if _prompt_capture_state["enabled"]:
    _prompt_capture_state["output_dir"].mkdir(parents=True, exist_ok=True)


def configure_session(session_id: str, agent_name: str = "agent"):
    """Configure the current session for prompt capture.
    
    Args:
        session_id: Unique identifier for the session
        agent_name: Name of the agent (used in filename)
    """
    with _prompt_capture_state["lock"]:
        # If switching to a new session, save the previous one first
        if (_prompt_capture_state["session_id"] is not None and 
            _prompt_capture_state["session_id"] != session_id and
            _prompt_capture_state["session_calls"]):
            _save_session_sync()
        
        _prompt_capture_state["session_id"] = session_id
        _prompt_capture_state["agent_name"] = agent_name
        _prompt_capture_state["session_start_time"] = dt.now().strftime("%Y%m%d_%H%M%S")
        _prompt_capture_state["session_calls"] = []
        print(f"[INFO] Prompt capture session configured: agent={agent_name}, session_id={session_id}")


def set_agent_name(agent_name: str):
    """Set the agent name for prompt filenames.
    
    Args:
        agent_name: Name of the agent
    """
    _prompt_capture_state["agent_name"] = agent_name
    print(f"[INFO] Prompt agent name set to: {agent_name}")


def set_session_id(session_id: str):
    """Set the session ID for prompt aggregation.
    
    Args:
        session_id: Unique session identifier
    """
    with _prompt_capture_state["lock"]:
        # If switching to a new session, save the previous one first
        if (_prompt_capture_state["session_id"] is not None and 
            _prompt_capture_state["session_id"] != session_id and
            _prompt_capture_state["session_calls"]):
            _save_session_sync()
        
        _prompt_capture_state["session_id"] = session_id
        _prompt_capture_state["session_start_time"] = dt.now().strftime("%Y%m%d_%H%M%S")
        _prompt_capture_state["session_calls"] = []
        print(f"[INFO] Prompt capture session ID set to: {session_id}")


def _get_session_filename() -> str:
    """Generate filename for current session: agent-name_time_session_id"""
    agent_name = _prompt_capture_state["agent_name"]
    session_id = _prompt_capture_state["session_id"] or "unknown"
    start_time = _prompt_capture_state["session_start_time"] or dt.now().strftime("%Y%m%d_%H%M%S")
    return f"{agent_name}_{start_time}_{session_id}"


def _save_session_sync():
    """Save the current session data to files (synchronous version)."""
    if not _prompt_capture_state["session_calls"]:
        return
    
    output_dir = _prompt_capture_state["output_dir"]
    filename = _get_session_filename()
    
    # Save as JSON
    json_file = output_dir / f"{filename}.json"
    session_data = {
        "agent_name": _prompt_capture_state["agent_name"],
        "session_id": _prompt_capture_state["session_id"],
        "session_start_time": _prompt_capture_state["session_start_time"],
        "total_calls": len(_prompt_capture_state["session_calls"]),
        "calls": _prompt_capture_state["session_calls"],
    }
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2, ensure_ascii=False, cls=DateTimeEncoder)
    
    # Save as formatted text
    txt_file = output_dir / f"{filename}.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write(f"=== Session: {_prompt_capture_state['session_id']} ===\n")
        f.write(f"Agent: {_prompt_capture_state['agent_name']}\n")
        f.write(f"Start Time: {_prompt_capture_state['session_start_time']}\n")
        f.write(f"Total Calls: {len(_prompt_capture_state['session_calls'])}\n")
        f.write("=" * 60 + "\n\n")
        
        for call in _prompt_capture_state["session_calls"]:
            f.write(f"--- Call #{call['call_number']} ({call['timestamp']}) ---\n")
            f.write(f"Model: {call['model']}\n\n")
            f.write(call["formatted_prompt"])
            f.write(f"\n\n=== Response ===\n")
            if call["response"]["content"]:
                f.write(f"Content: {call['response']['content']}\n")
            if call["response"]["tool_calls"]:
                f.write(f"Tool Calls: {call['response']['tool_calls']}\n")
            if call["usage"]["prompt_tokens"] is not None:
                f.write(f"\nTokens: {call['usage']['prompt_tokens']} prompt, ")
                f.write(f"{call['usage']['completion_tokens']} completion, ")
                f.write(f"{call['usage']['total_tokens']} total\n")
            f.write("\n" + "=" * 60 + "\n\n")
    
    print(f"[Session Saved] {len(_prompt_capture_state['session_calls'])} calls -> {json_file.name}")


def save_session():
    """Manually save the current session data to files."""
    with _prompt_capture_state["lock"]:
        _save_session_sync()


def end_session():
    """End the current session, save data and reset state."""
    with _prompt_capture_state["lock"]:
        _save_session_sync()
        _prompt_capture_state["session_id"] = None
        _prompt_capture_state["session_start_time"] = None
        _prompt_capture_state["session_calls"] = []


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle datetime objects."""
    def default(self, obj):
        if isinstance(obj, dt):
            return obj.isoformat()
        # Handle coroutines and other async objects
        elif inspect.iscoroutine(obj) or inspect.iscoroutinefunction(obj):
            return f"<coroutine: {getattr(obj, '__name__', str(obj))}>"
        elif inspect.isgenerator(obj) or inspect.isasyncgen(obj):
            return f"<generator: {getattr(obj, '__name__', str(obj))}>"
        elif callable(obj):
            return f"<callable: {getattr(obj, '__name__', str(obj))}>"
        elif hasattr(obj, "__dict__"):
            return str(obj)
        return super().default(obj)


def _make_json_serializable(obj):
    """Recursively convert objects to JSON-serializable format."""
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, dt):
        return obj.isoformat()
    # Handle coroutines and other async objects early
    elif inspect.iscoroutine(obj) or inspect.iscoroutinefunction(obj):
        return f"<coroutine: {getattr(obj, '__name__', str(obj))}>"
    elif inspect.isgenerator(obj) or inspect.isasyncgen(obj):
        return f"<generator: {getattr(obj, '__name__', str(obj))}>"
    elif callable(obj) and not isinstance(obj, type):
        return f"<callable: {getattr(obj, '__name__', str(obj))}>"
    elif hasattr(obj, "__dict__") and not isinstance(obj, (str, int, float, bool, type(None))):
        try:
            # Try to convert to dict if possible
            if hasattr(obj, "__class__") and obj.__class__.__name__ == "datetime":
                return obj.isoformat() if hasattr(obj, "isoformat") else str(obj)
            return str(obj)
        except:
            return str(obj)
    return obj


def _format_prompt(messages: list, tools: Optional[list] = None) -> str:
    """Format messages and tools into a readable prompt string."""
    lines = []
    
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown") if isinstance(msg, dict) else getattr(msg, "role", "unknown")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        
        # Handle content that might be a list (for multimodal)
        if isinstance(content, list):
            content_str = "\n".join([
                f"  [{item.get('type', 'text')}]: {item.get('text', item.get('image_url', ''))}"
                for item in content
            ])
        else:
            content_str = str(content)
        
        lines.append(f"[{role.upper()}]")
        lines.append(content_str)
        lines.append("")
    
    if tools:
        lines.append("=== AVAILABLE TOOLS ===")
        for tool in tools:
            lines.append(json.dumps(tool, indent=2, ensure_ascii=False))
        lines.append("")
    
    return "\n".join(lines)


async def prompt_capture_callback(kwargs, response_obj, start_time, end_time):
    """Async callback function to capture and aggregate prompts by session."""
    try:
        # Skip if prompt capture is disabled
        if not _prompt_capture_state["enabled"]:
            return
        
        # Skip incomplete streaming events
        # Only capture when we have usage info (indicates complete response)
        if not response_obj or not hasattr(response_obj, "usage") or not response_obj.usage:
            return
        
        # Also skip if usage tokens are None (incomplete streaming chunk)
        if not hasattr(response_obj.usage, "prompt_tokens") or response_obj.usage.prompt_tokens is None:
            return
        
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S_%f")
        
        # Extract messages from kwargs
        messages = kwargs.get("messages", [])
        model = kwargs.get("model", "unknown")
        tools = kwargs.get("tools", None)
        
        # Format prompt as text
        prompt_text = _format_prompt(messages, tools)
        
        # Extract response information
        response_content = None
        response_role = None
        response_tool_calls = None
        if response_obj and hasattr(response_obj, "choices") and response_obj.choices:
            choice = response_obj.choices[0]
            if hasattr(choice, "message"):
                msg = choice.message
                response_content = getattr(msg, "content", None)
                response_role = getattr(msg, "role", None)
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    response_tool_calls = [str(tc) for tc in msg.tool_calls]
        
        # Extract usage information
        usage_prompt_tokens = None
        usage_completion_tokens = None
        usage_total_tokens = None
        if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
            usage_prompt_tokens = getattr(response_obj.usage, "prompt_tokens", None)
            usage_completion_tokens = getattr(response_obj.usage, "completion_tokens", None)
            usage_total_tokens = getattr(response_obj.usage, "total_tokens", None)
        
        # Prepare kwargs for serialization (exclude messages, tools, model, and make serializable)
        filtered_kwargs = {k: _make_json_serializable(v) for k, v in kwargs.items() 
                          if k not in ["messages", "tools", "model"]}
        
        # Build call data
        call_data = {
            "call_number": len(_prompt_capture_state["session_calls"]) + 1,
            "timestamp": timestamp,
            "model": model,
            "messages": _make_json_serializable(messages),
            "tools": _make_json_serializable(tools) if tools else None,
            "formatted_prompt": prompt_text,
            "response": {
                "content": response_content,
                "role": response_role,
                "tool_calls": response_tool_calls,
            },
            "usage": {
                "prompt_tokens": usage_prompt_tokens,
                "completion_tokens": usage_completion_tokens,
                "total_tokens": usage_total_tokens,
            },
            "kwargs": filtered_kwargs,
        }
        
        with _prompt_capture_state["lock"]:
            # Skip capture if session not configured
            if _prompt_capture_state["session_id"] is None:
                return
            
            _prompt_capture_state["session_calls"].append(call_data)
            call_count = len(_prompt_capture_state["session_calls"])
            
            # Auto-save after each call to prevent data loss
            _save_session_sync()
        
        print(f"[Prompt Captured #{call_count}] Session: {_prompt_capture_state['session_id']}")
    except Exception as e:
        print(f"[Error in prompt capture callback] {e}")
        import traceback
        traceback.print_exc()


# Register callback with litellm only if file logging is enabled
if _prompt_capture_state["enabled"]:
    # For async callbacks (used by openai-agents)
    if not hasattr(litellm, "success_callback") or litellm.success_callback is None:
        litellm.success_callback = []
    if prompt_capture_callback not in litellm.success_callback:
        litellm.success_callback.append(prompt_capture_callback)

    # Also register as async callback
    if not hasattr(litellm, "_async_success_callback") or litellm._async_success_callback is None:
        litellm._async_success_callback = []
    if prompt_capture_callback not in litellm._async_success_callback:
        litellm._async_success_callback.append(prompt_capture_callback)

    print(f"[INFO] Prompt capture callback registered (sync and async). Prompts will be aggregated by session_id to 'logs/prompt_logs' directory.")
