"""Provider layer: a pluggable interface over foundational-model backends.

Backends:
  - claude-code : shells out to the `claude` CLI (uses existing Claude Code auth, NO API key)
  - anthropic   : Anthropic API via the `anthropic` SDK + ANTHROPIC_API_KEY
  - openai      : OpenAI API via the `openai` SDK + OPENAI_API_KEY

Each Prometheus role (architect/strategist/improver) and each sub-agent can use a
different provider/model, selected via the registry.
"""

from .base import Provider, ProviderResponse, ProviderError
from .registry import get_provider, available_providers

__all__ = [
    "Provider",
    "ProviderResponse",
    "ProviderError",
    "get_provider",
    "available_providers",
]
