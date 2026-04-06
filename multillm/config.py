"""
Centralized configuration for MultiLLM.

All config comes from environment variables with sensible defaults.
Per-machine overrides can be stored in the MultiLLM data directory.

NO secrets, API keys, internal URLs, or PII should be hardcoded here.
All sensitive values MUST come from environment variables.
"""

import json
import os
from pathlib import Path

# Load .env file if present (before reading any env vars)
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip()
                if _key and _key not in os.environ:
                    os.environ[_key] = _val

# ── Data directory (portable across machines) ────────────────────────────────
MULTILLM_HOME = os.getenv("MULTILLM_HOME", "")
DATA_DIR = Path(
    os.getenv(
        "MULTILLM_DATA_DIR",
        MULTILLM_HOME if MULTILLM_HOME else (Path.home() / ".multillm"),
    )
)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Gateway ──────────────────────────────────────────────────────────────────
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8080"))

# ── Backend URLs (local) ────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234")

# ── API Keys (all from env, none hardcoded) ──────────────────────────────────
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_REAL_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))

# ── Cline-compatible backends (cloud, all env-configured) ───────────────────
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "")
TOGETHER_KEY = os.getenv("TOGETHER_API_KEY", "")
XAI_KEY = os.getenv("XAI_API_KEY", "")
FIREWORKS_KEY = os.getenv("FIREWORKS_API_KEY", "")

# Azure OpenAI
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")  # e.g. https://myresource.openai.azure.com
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

# AWS Bedrock
AWS_BEDROCK_REGION = os.getenv("AWS_BEDROCK_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
AWS_BEDROCK_PROFILE = os.getenv("AWS_BEDROCK_PROFILE", "")

# ── OCA (Oracle Code Assist) — fully env-configured ─────────────────────────
OCA_ENDPOINT = os.getenv("OCA_ENDPOINT", "")
OCA_API_VERSION = os.getenv("OCA_API_VERSION", "20250206")
OCA_TOKEN_CACHE = Path(os.getenv("OCA_CACHE_DIR", Path.home() / ".oca"))
OCA_IDCS_URL = os.getenv("OCA_IDCS_URL", "")
OCA_CLIENT_ID = os.getenv("OCA_CLIENT_ID", "")

# ── OpenTelemetry / OCI APM ──────────────────────────────────────────────────
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "multillm-gateway")

# OCI APM destination (set these to export traces/metrics to OCI APM)
OCI_APM_DOMAIN_ID = os.getenv("OCI_APM_DOMAIN_ID", "")
OCI_APM_DATA_KEY = os.getenv("OCI_APM_DATA_KEY", "")
OCI_APM_REGION = os.getenv("OCI_APM_REGION", "eu-frankfurt-1")
# The OTLP endpoint is derived from the APM domain:
# https://apm-trace.{region}.oci.oraclecloud.com/20200101/opentelemetry/
OCI_APM_ENDPOINT = os.getenv(
    "OCI_APM_ENDPOINT",
    f"https://apm-trace.{OCI_APM_REGION}.oci.oraclecloud.com/20200101/opentelemetry/"
    if OCI_APM_DOMAIN_ID else "",
)

# ── Langfuse (LLM Observability) ─────────────────────────────────────────────
LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").lower() in ("true", "1", "yes")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001")

# ── Project detection ────────────────────────────────────────────────────────
def detect_project() -> str:
    """Detect current project from env var or cwd."""
    explicit = os.getenv("MULTILLM_PROJECT")
    if explicit:
        return explicit
    return Path.cwd().name


# ── Routing table ────────────────────────────────────────────────────────────
DEFAULT_ROUTES: dict[str, dict] = {
    # Ollama local
    "ollama/llama3":         {"backend": "ollama",     "model": "llama3"},
    "ollama/llama3.1":       {"backend": "ollama",     "model": "llama3.1"},
    "ollama/mistral":        {"backend": "ollama",     "model": "mistral"},
    "ollama/codellama":      {"backend": "ollama",     "model": "codellama"},
    "ollama/deepseek-coder": {"backend": "ollama",     "model": "deepseek-coder"},
    "ollama/gemma2":         {"backend": "ollama",     "model": "gemma2"},
    "ollama/phi3":           {"backend": "ollama",     "model": "phi3"},
    "ollama/qwen2.5-coder":  {"backend": "ollama",     "model": "qwen2.5-coder:14b"},
    "ollama/qwen3-30b":      {"backend": "ollama",     "model": "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:Q4_K_M"},
    # LM Studio
    "lmstudio/current":      {"backend": "lmstudio",   "model": "local-model"},
    "lmstudio/deepseek":     {"backend": "lmstudio",   "model": "deepseek-coder-v2"},
    # OpenRouter
    "openrouter/gpt4o":      {"backend": "openrouter", "model": "openai/gpt-4o"},
    "openrouter/gpt4o-mini": {"backend": "openrouter", "model": "openai/gpt-4o-mini"},
    "openrouter/gemini-pro": {"backend": "openrouter", "model": "google/gemini-pro-1.5"},
    "openrouter/deepseek":   {"backend": "openrouter", "model": "deepseek/deepseek-chat"},
    "openrouter/mixtral":    {"backend": "openrouter", "model": "mistralai/mixtral-8x7b-instruct"},
    # OpenAI direct
    "openai/gpt-4o":         {"backend": "openai",     "model": "gpt-4o"},
    "openai/gpt-4o-mini":    {"backend": "openai",     "model": "gpt-4o-mini"},
    "openai/o1-mini":        {"backend": "openai",     "model": "o1-mini"},
    "openai/codex":          {"backend": "openai",     "model": "codex-mini-latest"},
    # Anthropic
    "claude-haiku":          {"backend": "anthropic",  "model": "claude-haiku-4-5-20251001"},
    "claude-sonnet":         {"backend": "anthropic",  "model": "claude-sonnet-4-6"},
    # OCA (Oracle Code Assist)
    "oca/gpt5":              {"backend": "oca",        "model": "oca/gpt5"},
    "oca/llama4":            {"backend": "oca",        "model": "oca/llama4"},
    "oca/grok4":             {"backend": "oca",        "model": "oca/grok4"},
    "oca/openai-o3":         {"backend": "oca",        "model": "oca/openai-o3"},
    "oca/gpt-4.1":           {"backend": "oca",        "model": "oca/gpt-4.1"},
    "oca/grok3":             {"backend": "oca",        "model": "oca/grok3"},
    "oca/grok4-fast-reasoning": {"backend": "oca",     "model": "oca/grok4-fast-reasoning"},
    "oca/grok-code-fast-1":  {"backend": "oca",        "model": "oca/grok-code-fast-1"},
    "oca/gpt-oss-120b":      {"backend": "oca",        "model": "oca/gpt-oss-120b"},
    "oca/gpt-5.4":           {"backend": "oca",        "model": "oca/gpt-5.4"},
    "oca/gpt-5-codex":       {"backend": "oca",        "model": "oca/gpt-5-codex"},
    "oca/gpt-5.1-codex":     {"backend": "oca",        "model": "oca/gpt-5.1-codex"},
    "oca/gpt-5.1-codex-mini": {"backend": "oca",       "model": "oca/gpt-5.1-codex-mini"},
    "oca/gpt-5.1-codex-max": {"backend": "oca",        "model": "oca/gpt-5.1-codex-max"},
    "oca/gpt-5.2":           {"backend": "oca",        "model": "oca/gpt-5.2"},
    "oca/gpt-5.2-codex":     {"backend": "oca",        "model": "oca/gpt-5.2-codex"},
    "oca/gpt-5.3-codex":     {"backend": "oca",        "model": "oca/gpt-5.3-codex"},
    # Gemini (Google SDK)
    "gemini/flash":          {"backend": "gemini",     "model": "gemini-2.0-flash"},
    "gemini/pro":            {"backend": "gemini",     "model": "gemini-2.0-pro"},
    "gemini/flash-lite":     {"backend": "gemini",     "model": "gemini-2.0-flash-lite"},
    # Gemini CLI (subprocess-based, uses `gemini` binary)
    "gemini-cli/default":    {"backend": "gemini_cli", "model": "gemini-cli:"},
    "gemini-cli/flash":      {"backend": "gemini_cli", "model": "gemini-cli:gemini-2.5-flash"},
    "gemini-cli/pro":        {"backend": "gemini_cli", "model": "gemini-cli:gemini-2.5-pro"},
    "gemini-cli/flash-lite": {"backend": "gemini_cli", "model": "gemini-cli:gemini-2.5-flash-lite"},
    # Codex CLI (subprocess, profile-based via ~/.codex/config.toml)
    "codex/cli":             {"backend": "codex_cli",  "model": "codex:gpt-5-4"},
    "codex/gpt-5-4":         {"backend": "codex_cli",  "model": "codex:gpt-5-4"},
    "codex/gpt-5-codex":     {"backend": "codex_cli",  "model": "codex:gpt-5-codex"},
    "codex/gpt-5-2-codex":   {"backend": "codex_cli",  "model": "codex:gpt-5-2-codex"},
    "codex/gpt-5-3-codex":   {"backend": "codex_cli",  "model": "codex:gpt-5-3-codex"},
    # ── Cline-compatible backends ──────────────────────────────────────────
    # Groq (ultra-fast inference)
    "groq/llama-3.3-70b":   {"backend": "groq",       "model": "llama-3.3-70b-versatile"},
    "groq/llama-3.1-8b":    {"backend": "groq",       "model": "llama-3.1-8b-instant"},
    "groq/mixtral-8x7b":    {"backend": "groq",       "model": "mixtral-8x7b-32768"},
    "groq/gemma2-9b":       {"backend": "groq",       "model": "gemma2-9b-it"},
    # DeepSeek (reasoning + code)
    "deepseek/chat":         {"backend": "deepseek",   "model": "deepseek-chat"},
    "deepseek/reasoner":     {"backend": "deepseek",   "model": "deepseek-reasoner"},
    # Mistral
    "mistral/large":         {"backend": "mistral",    "model": "mistral-large-latest"},
    "mistral/small":         {"backend": "mistral",    "model": "mistral-small-latest"},
    "mistral/codestral":     {"backend": "mistral",    "model": "codestral-latest"},
    # Together AI
    "together/llama-3.3-70b": {"backend": "together",  "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    "together/qwen-2.5-72b": {"backend": "together",   "model": "Qwen/Qwen2.5-72B-Instruct-Turbo"},
    "together/deepseek-v3":  {"backend": "together",   "model": "deepseek-ai/DeepSeek-V3"},
    # xAI (Grok)
    "xai/grok-3":            {"backend": "xai",        "model": "grok-3"},
    "xai/grok-3-fast":       {"backend": "xai",        "model": "grok-3-fast"},
    "xai/grok-3-mini":       {"backend": "xai",        "model": "grok-3-mini"},
    # Fireworks AI
    "fireworks/llama-3.3-70b": {"backend": "fireworks", "model": "accounts/fireworks/models/llama-v3p3-70b-instruct"},
    "fireworks/qwen-2.5-72b": {"backend": "fireworks",  "model": "accounts/fireworks/models/qwen2p5-72b-instruct"},
    # Azure OpenAI (requires AZURE_OPENAI_ENDPOINT)
    "azure/gpt-4o":          {"backend": "azure_openai", "model": "gpt-4o"},
    "azure/gpt-4o-mini":     {"backend": "azure_openai", "model": "gpt-4o-mini"},
    # AWS Bedrock (requires AWS credentials)
    "bedrock/claude-sonnet":   {"backend": "bedrock", "model": "anthropic.claude-sonnet-4-20250514-v1:0"},
    "bedrock/claude-haiku":    {"backend": "bedrock", "model": "anthropic.claude-haiku-4-5-20251001-v1:0"},
    "bedrock/llama-3.3-70b":   {"backend": "bedrock", "model": "meta.llama3-3-70b-instruct-v1:0"},
    "bedrock/mistral-large":   {"backend": "bedrock", "model": "mistral.mistral-large-2411-v1:0"},
}


def load_routes() -> dict:
    """Load routes from DEFAULT_ROUTES + optional custom_routes.json."""
    routes = dict(DEFAULT_ROUTES)
    # Check env var path
    path = os.getenv("ROUTER_CONFIG_PATH", "")
    if path and os.path.exists(path):
        with open(path) as f:
            custom = json.load(f)
        routes.update({k: v for k, v in custom.items() if not k.startswith("_")})
    # Check the shared/local MultiLLM data directory
    local_routes = DATA_DIR / "routes.json"
    if local_routes.exists():
        with open(local_routes) as f:
            custom = json.load(f)
        routes.update({k: v for k, v in custom.items() if not k.startswith("_")})
    return routes
