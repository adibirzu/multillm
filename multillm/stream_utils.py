import json
import logging
import time
from typing import AsyncIterator, Callable, Optional, Tuple

from starlette.responses import StreamingResponse

from .converters import StreamState, extract_text_from_anthropic_events, count_tokens

log = logging.getLogger("multillm.stream_utils")

class StreamTokenCounter:
    """
    A wrapper for an async generator that counts tokens in SSE chunks and
    calls a callback upon completion.
    """
    def __init__(
        self,
        original_generator: AsyncIterator[str],
        completion_callback: Callable[[int, int, float], None], # input_tokens, output_tokens, elapsed_ms
        input_tokens: int = 0,
        model_alias: Optional[str] = None,
    ):
        self.original_generator = original_generator
        self.completion_callback = completion_callback
        self.input_tokens = input_tokens
        self.output_tokens = 0
        self.start_time = time.time()
        self.model_alias = model_alias
        self._buffer = "" # For partial SSE lines

    async def __aiter__(self):
        async for chunk in self.original_generator:
            self._buffer += chunk
            while "
" in self._buffer:
                line, self._buffer = self._buffer.split("
", 1)
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        continue
                    try:
                        # Attempt to parse as a single JSON object (OpenAI-like)
                        event_data = json.loads(data_str)
                        if "choices" in event_data: # OpenAI-like
                            delta = event_data["choices"][0].get("delta", {})
                            if "content" in delta:
                                text = delta["content"]
                                self.output_tokens += count_tokens(text, self.model_alias)
                        elif "message" in event_data: # Anthropic-like in some cases
                            message_content = event_data.get("message", {}).get("content", [])
                            if message_content and message_content[0].get("type") == "text":
                                text = message_content[0].get("text", "")
                                self.output_tokens += count_tokens(text, self.model_alias)
                        elif "type" in event_data and event_data["type"] == "content_block_delta": # Anthropic
                            text = event_data.get("delta", {}).get("text", "")
                            self.output_tokens += count_tokens(text, self.model_alias)
                        elif "type" in event_data and event_data["type"] == "message_delta": # Anthropic output tokens
                            usage = event_data.get("usage", {})
                            self.output_tokens = usage.get("output_tokens", self.output_tokens) # Update with final value
                        elif "prompt_eval_count" in event_data: # Ollama input tokens
                            self.input_tokens = event_data.get("prompt_eval_count", self.input_tokens)
                        elif "eval_count" in event_data: # Ollama output tokens
                            self.output_tokens += event_data.get("eval_count", 0)

                    except json.JSONDecodeError:
                        pass # Ignore non-JSON or partial lines, wait for more data

                yield line + "
"
        
        # Yield any remaining buffer content
        if self._buffer:
            yield self._buffer

        self._call_completion_callback()

    def _call_completion_callback(self):
        elapsed_ms = (time.time() - self.start_time) * 1000
        # Ensure completion callback is called only once
        if self.completion_callback:
            self.completion_callback(self.input_tokens, self.output_tokens, elapsed_ms)
            self.completion_callback = None # Clear callback to prevent multiple calls
