"""
Register all backend adapters.

Called once at gateway startup to populate the adapter registry.
"""

from ..config import (
    GROQ_KEY, DEEPSEEK_KEY, MISTRAL_KEY,
    TOGETHER_KEY, XAI_KEY, FIREWORKS_KEY,
)
from .registry import register_adapter
from .ollama import OllamaAdapter
from .lmstudio import LMStudioAdapter
from .openai import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .openrouter import OpenRouterAdapter
from .oca import OCAAdapter
from .gemini import GeminiAdapter
from .codex_cli import CodexCLIAdapter
from .cloud_openai_compat import CloudOpenAICompatAdapter
from .azure_openai import AzureOpenAIAdapter
from .bedrock import BedrockAdapter


def register_all_adapters():
    """Register all 16 backend adapters."""
    # Local backends
    register_adapter(OllamaAdapter())
    register_adapter(LMStudioAdapter())
    register_adapter(CodexCLIAdapter())

    # Direct cloud backends
    register_adapter(OpenAIAdapter())
    register_adapter(AnthropicAdapter())
    register_adapter(OpenRouterAdapter())
    register_adapter(OCAAdapter())
    register_adapter(GeminiAdapter())

    # OpenAI-compatible cloud backends
    register_adapter(CloudOpenAICompatAdapter("groq", "https://api.groq.com/openai", lambda: GROQ_KEY))
    register_adapter(CloudOpenAICompatAdapter("deepseek", "https://api.deepseek.com", lambda: DEEPSEEK_KEY))
    register_adapter(CloudOpenAICompatAdapter("mistral", "https://api.mistral.ai", lambda: MISTRAL_KEY))
    register_adapter(CloudOpenAICompatAdapter("together", "https://api.together.xyz", lambda: TOGETHER_KEY))
    register_adapter(CloudOpenAICompatAdapter("xai", "https://api.x.ai", lambda: XAI_KEY))
    register_adapter(CloudOpenAICompatAdapter("fireworks", "https://api.fireworks.ai/inference", lambda: FIREWORKS_KEY))

    # Specialized cloud backends
    register_adapter(AzureOpenAIAdapter())
    register_adapter(BedrockAdapter())
