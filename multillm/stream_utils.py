"""Streaming token counter for SSE responses.

Wraps an async generator to count output tokens from SSE chunks in real-time,
then fires a completion callback with accurate token counts — zero latency
overhead because counting happens inline as chunks flow through.
"""

import json
import logging
import time
from typing import AsyncIterator, Callable, Optional

from .converters import count_tokens

log = logging.getLogger("multillm.stream_utils")


class StreamTokenCounter:
    """Async generator wrapper that counts tokens in SSE chunks and calls
    a callback on stream completion with final token counts.

    Supports OpenAI, Anthropic, and Ollama SSE formats.
    Zero added latency — token counting happens as chunks pass through.
    """

    def __init__(
        self,
        original_generator: AsyncIterator[str],
        completion_callback: Callable[[int, int, float], None],
        input_tokens: int = 0,
        model_alias: Optional[str] = None,
    ):
        self.original_generator = original_generator
        self.completion_callback = completion_callback
        self.input_tokens = input_tokens
        self.output_tokens = 0
        self.start_time = time.time()
        self.model_alias = model_alias
        self._buffer = ""
        self._callback_fired = False

    async def __aiter__(self):
        try:
            async for chunk in self.original_generator:
                self._buffer += chunk
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    self._extract_tokens_from_line(line)
                    yield line + "\n"

            # Yield any remaining buffer content
            if self._buffer:
                self._extract_tokens_from_line(self._buffer)
                yield self._buffer
        finally:
            # Always fire callback, even on client disconnect or error
            self._fire_completion_callback()

    def _extract_tokens_from_line(self, line: str):
        """Parse an SSE line and extract token counts from the event data."""
        if not line.startswith("data: "):
            return
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            return
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            return

        # OpenAI format: choices[0].delta.content
        if "choices" in event:
            delta = event["choices"][0].get("delta", {})
            text = delta.get("content", "")
            if text:
                self.output_tokens += count_tokens(text, self.model_alias)
            # OpenAI may include usage in the final chunk
            usage = event.get("usage")
            if usage:
                self.input_tokens = usage.get("prompt_tokens", self.input_tokens)
                self.output_tokens = usage.get("completion_tokens", self.output_tokens)
            return

        # Anthropic format: content_block_delta with delta.text
        event_type = event.get("type", "")
        if event_type == "content_block_delta":
            text = event.get("delta", {}).get("text", "")
            if text:
                self.output_tokens += count_tokens(text, self.model_alias)
            return

        # Anthropic message_delta: final usage in the last event
        if event_type == "message_delta":
            usage = event.get("usage", {})
            if "output_tokens" in usage:
                self.output_tokens = usage["output_tokens"]
            return

        # Anthropic message_start: input token count
        if event_type == "message_start":
            usage = event.get("message", {}).get("usage", {})
            if "input_tokens" in usage:
                self.input_tokens = usage["input_tokens"]
            # Cache tokens
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            if cache_read or cache_create:
                self.cache_read_tokens = cache_read
                self.cache_create_tokens = cache_create
            return

        # Ollama format: prompt_eval_count / eval_count
        if "prompt_eval_count" in event:
            self.input_tokens = event["prompt_eval_count"]
        if "eval_count" in event:
            self.output_tokens = event["eval_count"]

    def _fire_completion_callback(self):
        """Call the completion callback exactly once."""
        if self._callback_fired or not self.completion_callback:
            return
        self._callback_fired = True
        elapsed_ms = (time.time() - self.start_time) * 1000
        try:
            self.completion_callback(self.input_tokens, self.output_tokens, elapsed_ms)
        except Exception:
            log.exception("Error in stream completion callback")
