"""
Base adapter interface for all LLM backends.

Every backend must implement send() and optionally stream().
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseAdapter(ABC):
    """Interface that all backend adapters must implement."""

    name: str  # e.g. "ollama", "openai", "oca"

    @abstractmethod
    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        """Send a non-streaming request. Returns Anthropic-format response dict."""
        ...

    @abstractmethod
    async def stream(self, body: dict, model: str, model_alias: str):
        """Send a streaming request. Returns a StreamingResponse or JSONResponse."""
        ...

    def is_configured(self) -> bool:
        """Check if this adapter has the required configuration (keys, endpoints)."""
        return True

    def validate(self, model: str) -> Optional[str]:
        """Validate that a request can be handled. Returns error message or None."""
        if not self.is_configured():
            return f"{self.name} is not configured"
        return None
