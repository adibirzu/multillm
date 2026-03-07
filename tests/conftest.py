"""Shared test fixtures for MultiLLM tests."""
import os
import tempfile

import pytest

# Override data dir to use temp directory for tests
_test_dir = tempfile.mkdtemp(prefix="multillm_test_")
os.environ["MULTILLM_DATA_DIR"] = _test_dir
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("LMSTUDIO_URL", "http://localhost:1234")


@pytest.fixture
def sample_anthropic_request():
    """A simple Anthropic messages request."""
    return {
        "model": "ollama/llama3",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 1024,
        "temperature": 0.7,
    }


@pytest.fixture
def sample_anthropic_request_with_system():
    """Anthropic request with system prompt."""
    return {
        "model": "ollama/llama3",
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 1024,
    }


@pytest.fixture
def sample_anthropic_request_with_tools():
    """Anthropic request with tool definitions and tool_use/tool_result."""
    return {
        "model": "openai/gpt-4o",
        "system": "You are a helpful assistant.",
        "tools": [
            {
                "name": "get_weather",
                "description": "Get the current weather in a location",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            }
        ],
        "messages": [
            {"role": "user", "content": "What's the weather in London?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check the weather."},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc123",
                        "name": "get_weather",
                        "input": {"location": "London", "unit": "celsius"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc123",
                        "content": "15°C, partly cloudy",
                    },
                ],
            },
        ],
        "max_tokens": 1024,
    }


@pytest.fixture
def sample_anthropic_request_with_images():
    """Anthropic request with image content."""
    return {
        "model": "openai/gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            },
        ],
        "max_tokens": 1024,
    }


@pytest.fixture
def sample_openai_response():
    """A typical OpenAI chat completion response."""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


@pytest.fixture
def sample_openai_response_with_tools():
    """OpenAI response with tool_calls."""
    return {
        "id": "chatcmpl-abc456",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_xyz789",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"location": "London", "unit": "celsius"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
    }


@pytest.fixture
def sample_openai_stream_chunks():
    """A sequence of OpenAI SSE chunks."""
    return [
        {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]


@pytest.fixture
def sample_openai_stream_chunks_with_tools():
    """OpenAI SSE chunks with tool_calls."""
    return [
        {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": ""},
                    }],
                },
                "finish_reason": None,
            }],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"loc'}}]},
                "finish_reason": None,
            }],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ation": "London"}'}}]},
                "finish_reason": None,
            }],
        },
        {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
    ]


@pytest.fixture
def sample_ollama_stream_chunks():
    """Ollama streaming JSON lines."""
    return [
        {"model": "llama3", "message": {"role": "assistant", "content": "Hello"}, "done": False},
        {"model": "llama3", "message": {"role": "assistant", "content": " there"}, "done": False},
        {"model": "llama3", "message": {"role": "assistant", "content": ""}, "done": True,
         "eval_count": 5, "prompt_eval_count": 10},
    ]
