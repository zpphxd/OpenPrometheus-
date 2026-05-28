"""Provider interface shared by every backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Callable


class ProviderError(RuntimeError):
    """Any failure talking to a model backend."""


@dataclass
class ProviderResponse:
    text: str
    raw: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)


# A tool executor maps (tool_name, args) -> string result. Supplied by the runner.
ToolExecutor = Callable[[str, dict[str, Any]], str]


class Provider:
    """Abstract backend.

    Two capabilities:
      complete()  — single-shot text completion. Used by Prometheus's own meta-agents
                    (architect/strategist/improver) and by tool-less candidate/sub-agents.
      run_agent() — an agentic loop with tool use. Used by the runner when a spec grants
                    tools. Default implementation falls back to complete() (no tools), so
                    providers that don't implement tool use still work for reasoning agents.
    """

    name: str = "base"

    def __init__(self, model: Optional[str] = None, **kwargs: Any) -> None:
        self.model = model
        self.options = kwargs

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
    ) -> ProviderResponse:
        raise NotImplementedError

    def run_agent(
        self,
        system: str,
        user: str,
        *,
        tool_names: Optional[list[str]] = None,
        tool_defs: Optional[list[dict[str, Any]]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        model: Optional[str] = None,
        max_steps: int = 12,
        max_tokens: int = 4096,
        workdir: Optional[str] = None,
        allow_risky: bool = False,
    ) -> ProviderResponse:
        """Run an agentic loop with tools.

        `tool_names`  — bare names (built-ins + mcp__...), used by providers with native
                        tool runtimes (claude-code).
        `tool_defs`   — provider-neutral [{name, description, input_schema}], used by API
                        providers that must declare schemas; executed via `tool_executor`.

        Default: reasoning-only fallback (ignore tools).
        """
        return self.complete(system, user, model=model, max_tokens=max_tokens)
