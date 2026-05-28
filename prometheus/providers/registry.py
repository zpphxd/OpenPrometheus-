"""Provider registry — resolve a backend by name, with per-role model selection."""

from __future__ import annotations

from typing import Optional

from .base import Provider, ProviderError


def available_providers() -> list[str]:
    return ["claude-code", "anthropic", "openai"]


def get_provider(name: str, model: Optional[str] = None, **kwargs) -> Provider:
    """Instantiate a provider by name. Imports are lazy so missing optional SDKs
    (openai) or a missing `claude` CLI only error when that provider is actually used.
    """
    key = (name or "claude-code").strip().lower()

    if key in ("claude-code", "claude_code", "cc", "claude"):
        from .claude_code_provider import ClaudeCodeProvider

        return ClaudeCodeProvider(model=model, **kwargs)

    if key in ("anthropic", "anthropic-api"):
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=model, **kwargs)

    if key in ("openai", "openai-api"):
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(model=model, **kwargs)

    raise ProviderError(
        f"unknown provider {name!r}; choose one of {available_providers()}"
    )
