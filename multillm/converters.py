"""
Format converters between LLM API formats.

Handles conversion between Anthropic, OpenAI, Ollama, and Gemini message formats
including tool calling, multimodal content, and streaming events.

Anthropic is the canonical format (Claude Code speaks Anthropic natively).
All other formats are converted to/from Anthropic.
"""

import json
import uuid
from typing import Optional


# ── Message Conversion ──────────────────────────────────────────────────────

def anthropic_messages_to_openai(
    messages: list[dict],
    system: Optional[str | list] = None,
) -> list[dict]:
    """Convert Anthropic messages to OpenAI format, including tool_use and tool_result."""
    out: list[dict] = []

    # System prompt
    if system:
        if isinstance(system, list):
            system = " ".join(p.get("text", "") for p in system if p.get("type") == "text")
        out.append({"role": "system", "content": system})

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        # Simple string content
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # Complex content blocks (list)
        if isinstance(content, list):
            # Check for tool_use blocks (assistant) or tool_result blocks (user)
            tool_calls = []
            tool_results = []
            text_parts = []
            image_parts = []

            for block in content:
                btype = block.get("type", "")

                if btype == "text":
                    text_parts.append(block.get("text", ""))

                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })

                elif btype == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = "\n".join(
                            p.get("text", "") for p in result_content if p.get("type") == "text"
                        )
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(result_content),
                    })

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        image_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}",
                            },
                        })

            # Emit assistant message with tool_calls
            if role == "assistant" and tool_calls:
                msg: dict = {"role": "assistant"}
                if text_parts:
                    msg["content"] = "\n".join(text_parts)
                else:
                    msg["content"] = None
                msg["tool_calls"] = tool_calls
                out.append(msg)

            # Emit tool results as separate messages
            elif tool_results:
                # If there's also text, emit that first
                if text_parts:
                    out.append({"role": role, "content": "\n".join(text_parts)})
                out.extend(tool_results)

            # Emit multimodal content
            elif image_parts:
                oai_content: list[dict] = []
                if text_parts:
                    oai_content.append({"type": "text", "text": "\n".join(text_parts)})
                oai_content.extend(image_parts)
                out.append({"role": role, "content": oai_content})

            # Plain text blocks
            elif text_parts:
                out.append({"role": role, "content": "\n".join(text_parts)})

    return out


def openai_response_to_anthropic(response: dict, model_alias: str) -> dict:
    """Convert OpenAI chat completion to Anthropic message, including tool_calls."""
    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = response.get("usage", {})

    content_blocks: list[dict] = []

    # Text content
    text = message.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    # Tool calls -> tool_use blocks
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        try:
            input_data = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            input_data = {"raw": func.get("arguments", "")}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": func.get("name", "unknown"),
            "input": input_data,
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    stop = choice.get("finish_reason", "end_turn")
    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    stop_reason = stop_map.get(stop, stop)

    prompt_details = usage.get("prompt_tokens_details", {}) or {}

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model_alias,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_read_input_tokens": prompt_details.get("cached_tokens", 0),
        },
    }


# ── Tool Definition Conversion ──────────────────────────────────────────────

def anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    oai_tools = []
    for tool in tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return oai_tools


# ── Payload Builders ────────────────────────────────────────────────────────

def build_openai_payload(body: dict, model: str) -> dict:
    """Build OpenAI-compatible payload from Anthropic request body."""
    messages = anthropic_messages_to_openai(
        body.get("messages", []),
        system=body.get("system"),
    )
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
        "stream": body.get("stream", False),
    }

    # Convert tool definitions
    tools = body.get("tools")
    if tools:
        payload["tools"] = anthropic_tools_to_openai(tools)

    # Tool choice
    tool_choice = body.get("tool_choice")
    if tool_choice:
        tc_type = tool_choice.get("type", "auto")
        if tc_type == "any":
            payload["tool_choice"] = "required"
        elif tc_type == "tool":
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": tool_choice.get("name", "")},
            }
        elif tc_type == "none":
            payload["tool_choice"] = "none"
        else:
            payload["tool_choice"] = "auto"

    return payload


def build_ollama_payload(body: dict, model: str) -> dict:
    """Build Ollama API payload from Anthropic request body."""
    messages = anthropic_messages_to_openai(
        body.get("messages", []),
        system=body.get("system"),
    )

    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": body.get("stream", False),
        "options": {
            "num_predict": body.get("max_tokens", 4096),
            "temperature": body.get("temperature", 0.7),
        },
    }

    # Ollama supports tools in OpenAI format
    tools = body.get("tools")
    if tools:
        payload["tools"] = anthropic_tools_to_openai(tools)

    return payload


# ── Response Builders ───────────────────────────────────────────────────────

def make_anthropic_response(
    text: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    stop_reason: str = "end_turn",
    content_blocks: Optional[list[dict]] = None,
    usage_extras: Optional[dict] = None,
) -> dict:
    """Create an Anthropic-format response from raw text or content blocks."""
    if content_blocks is None:
        content_blocks = [{"type": "text", "text": text}]
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if usage_extras:
        usage.update({k: v for k, v in usage_extras.items() if v is not None})
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


def extract_text_from_anthropic(body: dict) -> str:
    """Extract a flat text prompt from Anthropic-format body (for non-chat backends)."""
    parts = []
    system = body.get("system")
    if system:
        if isinstance(system, list):
            system = " ".join(p.get("text", "") for p in system if p.get("type") == "text")
        parts.append(system)
    for m in body.get("messages", []):
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") for p in content if p.get("type") == "text")
        if content:
            parts.append(content)
    return "\n\n".join(parts)


# ── Streaming Event Conversion ──────────────────────────────────────────────

class StreamState:
    """Tracks state during streaming conversion."""

    def __init__(self, model: str, input_tokens: int = 0):
        self.model = model
        self.msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self.input_tokens = input_tokens
        self.output_tokens = 0
        self.content_started = False
        self.tool_index = 0
        self.active_tool_calls: dict[int, dict] = {}
        self.current_block_index = 0


def anthropic_sse_event(event_type: str, data: dict) -> str:
    """Format a single Anthropic SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def make_message_start_event(state: StreamState) -> str:
    """Create the message_start SSE event."""
    return anthropic_sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": state.msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": state.model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": state.input_tokens, "output_tokens": 0},
        },
    })


def make_content_block_start_event(index: int, block_type: str = "text", **kwargs) -> str:
    """Create a content_block_start SSE event."""
    block: dict = {"type": block_type}
    if block_type == "text":
        block["text"] = ""
    elif block_type == "tool_use":
        block["id"] = kwargs.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
        block["name"] = kwargs.get("name", "")
        block["input"] = {}
    return anthropic_sse_event("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": block,
    })


def make_text_delta_event(index: int, text: str) -> str:
    """Create a content_block_delta SSE event for text."""
    return anthropic_sse_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    })


def make_tool_input_delta_event(index: int, partial_json: str) -> str:
    """Create a content_block_delta SSE event for tool input."""
    return anthropic_sse_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial_json},
    })


def make_content_block_stop_event(index: int) -> str:
    """Create a content_block_stop SSE event."""
    return anthropic_sse_event("content_block_stop", {
        "type": "content_block_stop",
        "index": index,
    })


def make_message_delta_event(stop_reason: str, output_tokens: int) -> str:
    """Create the message_delta SSE event."""
    return anthropic_sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })


def make_message_stop_event() -> str:
    """Create the message_stop SSE event."""
    return anthropic_sse_event("message_stop", {"type": "message_stop"})


def make_ping_event() -> str:
    """Create a ping SSE event."""
    return anthropic_sse_event("ping", {"type": "ping"})


def openai_chunk_to_anthropic_events(chunk: dict, state: StreamState) -> list[str]:
    """Convert a single OpenAI SSE chunk to Anthropic SSE event strings."""
    events: list[str] = []
    choice = (chunk.get("choices") or [{}])[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # Start content block on first text delta
    text = delta.get("content")
    if text is not None and not state.content_started:
        state.content_started = True
        events.append(make_content_block_start_event(state.current_block_index, "text"))

    # Text delta
    if text:
        events.append(make_text_delta_event(state.current_block_index, text))
        state.output_tokens += max(1, len(text) // 4)

    # Tool calls
    tool_calls = delta.get("tool_calls", [])
    for tc in tool_calls:
        tc_index = tc.get("index", 0)
        func = tc.get("function", {})

        if tc_index not in state.active_tool_calls:
            # Close previous block if any
            if state.content_started:
                events.append(make_content_block_stop_event(state.current_block_index))
                state.current_block_index += 1
                state.content_started = False

            tool_id = tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
            tool_name = func.get("name", "")
            state.active_tool_calls[tc_index] = {
                "id": tool_id,
                "name": tool_name,
                "arguments": "",
            }
            events.append(make_content_block_start_event(
                state.current_block_index,
                "tool_use",
                id=tool_id,
                name=tool_name,
            ))
            state.content_started = True

        # Accumulate arguments
        args_chunk = func.get("arguments", "")
        if args_chunk:
            state.active_tool_calls[tc_index]["arguments"] += args_chunk
            events.append(make_tool_input_delta_event(state.current_block_index, args_chunk))
            state.output_tokens += max(1, len(args_chunk) // 4)

    # Finish
    if finish_reason:
        if state.content_started:
            events.append(make_content_block_stop_event(state.current_block_index))

        stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
        stop = stop_map.get(finish_reason, finish_reason)
        events.append(make_message_delta_event(stop, state.output_tokens))
        events.append(make_message_stop_event())

    # Usage update from chunk (some providers send this)
    chunk_usage = chunk.get("usage", {})
    if chunk_usage.get("completion_tokens"):
        state.output_tokens = chunk_usage["completion_tokens"]

    return events


def ollama_chunk_to_anthropic_events(chunk: dict, state: StreamState) -> list[str]:
    """Convert Ollama streaming JSON line to Anthropic SSE event strings."""
    events: list[str] = []
    message = chunk.get("message", {})
    text = message.get("content", "")
    done = chunk.get("done", False)

    if text and not state.content_started:
        state.content_started = True
        events.append(make_content_block_start_event(state.current_block_index, "text"))

    if text:
        events.append(make_text_delta_event(state.current_block_index, text))
        state.output_tokens += max(1, len(text) // 4)

    # Tool calls in Ollama response
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        if state.content_started:
            events.append(make_content_block_stop_event(state.current_block_index))
            state.current_block_index += 1

        tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
        events.append(make_content_block_start_event(
            state.current_block_index, "tool_use",
            id=tool_id, name=func.get("name", ""),
        ))
        args_json = json.dumps(func.get("arguments", {}))
        events.append(make_tool_input_delta_event(state.current_block_index, args_json))
        events.append(make_content_block_stop_event(state.current_block_index))
        state.current_block_index += 1
        state.content_started = False

    if done:
        if state.content_started:
            events.append(make_content_block_stop_event(state.current_block_index))

        state.output_tokens = chunk.get("eval_count", state.output_tokens)
        state.input_tokens = chunk.get("prompt_eval_count", state.input_tokens)

        stop = "tool_use" if tool_calls else "end_turn"
        events.append(make_message_delta_event(stop, state.output_tokens))
        events.append(make_message_stop_event())

    return events
