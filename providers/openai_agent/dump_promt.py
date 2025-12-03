# Setup LiteLLM callback to capture prompts
import litellm
from datetime import datetime as dt
import json
from pathlib import Path
import inspect
import sys


def _detect_caller_filename():
    """Auto-detect the filename of the script that imported this module."""
    try:
        # Try to get the main module filename
        if hasattr(sys.modules.get('__main__'), '__file__'):
            main_file = sys.modules['__main__'].__file__
            if main_file:
                return Path(main_file).stem
        
        # Fallback: inspect the call stack to find the caller
        frame = inspect.currentframe()
        if frame and frame.f_back and frame.f_back.f_back:
            caller_frame = frame.f_back.f_back
            caller_filename = caller_frame.f_globals.get('__file__')
            if caller_filename:
                return Path(caller_filename).stem
    except Exception:
        pass
    
    # Default fallback
    return "prompt"


# Global state for prompt capture
_prompt_capture_state = {
    "call_count": 0,
    "output_dir": Path("logs/prompt_logs"),
    "filename_prefix": _detect_caller_filename(),
}

# Create output directory
_prompt_capture_state["output_dir"].mkdir(parents=True, exist_ok=True)


def set_filename_prefix(prefix: str):
    """Set custom filename prefix for saved prompts.
    
    Args:
        prefix: The prefix to use for prompt filenames (e.g., 'my_script')
    """
    _prompt_capture_state["filename_prefix"] = prefix
    print(f"[INFO] Prompt filename prefix set to: {prefix}")


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


def _format_prompt(messages: list, tools: list = None) -> str:
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
    """Async callback function to capture and save complete prompts from LiteLLM calls."""
    try:
        # Skip incomplete streaming events
        # Only capture when we have usage info (indicates complete response)
        if not response_obj or not hasattr(response_obj, "usage") or not response_obj.usage:
            return
        
        # Also skip if usage tokens are None (incomplete streaming chunk)
        if not hasattr(response_obj.usage, "prompt_tokens") or response_obj.usage.prompt_tokens is None:
            return
        
        _prompt_capture_state["call_count"] += 1
        call_count = _prompt_capture_state["call_count"]
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S_%f")
        output_dir = _prompt_capture_state["output_dir"]
        filename_prefix = _prompt_capture_state["filename_prefix"]
        
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
        
        # Save detailed info
        prompt_data = {
            "call_number": call_count,
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
        
        # Save as JSON with custom encoder
        json_file = output_dir / f"{filename_prefix}_{call_count}_{timestamp}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(prompt_data, f, indent=2, ensure_ascii=False, cls=DateTimeEncoder)
        
        # Save formatted prompt as text
        txt_file = output_dir / f"{filename_prefix}_{call_count}_{timestamp}.txt"
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write(f"=== Prompt Call #{call_count} ===\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"Model: {model}\n")
            f.write(f"\n=== Formatted Prompt ===\n\n")
            f.write(prompt_text)
            f.write(f"\n\n=== Response ===\n\n")
            if prompt_data["response"]["content"]:
                f.write(f"Content: {prompt_data['response']['content']}\n")
            if prompt_data["response"]["tool_calls"]:
                f.write(f"Tool Calls: {prompt_data['response']['tool_calls']}\n")
            if prompt_data["usage"]["prompt_tokens"] is not None:
                f.write(f"\n=== Token Usage ===\n")
                f.write(f"Prompt Tokens: {prompt_data['usage']['prompt_tokens']}\n")
                f.write(f"Completion Tokens: {prompt_data['usage']['completion_tokens']}\n")
                f.write(f"Total Tokens: {prompt_data['usage']['total_tokens']}\n")
        
        print(f"[Prompt Captured #{call_count}] Saved to: {json_file.name}")
    except Exception as e:
        print(f"[Error in prompt capture callback] {e}")
        import traceback
        traceback.print_exc()


# Register callback with litellm - support both sync and async
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

print(f"[INFO] Prompt capture callback registered (sync and async). Filename prefix: '{_prompt_capture_state['filename_prefix']}'. All prompts will be saved to 'out/prompt_logs' directory.")
