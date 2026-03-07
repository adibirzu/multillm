"""
Backend adapters for MultiLLM Gateway.

Each adapter implements the BaseAdapter interface for a specific LLM backend.
The registry provides lookup by backend name.
"""

from .base import BaseAdapter
from .registry import get_adapter, register_adapter, list_adapters

__all__ = ["BaseAdapter", "get_adapter", "register_adapter", "list_adapters"]
