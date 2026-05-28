"""Anthropic API provider — uses the `anthropic` SDK + ANTHROPIC_API_KEY.

Implements complete() and a tool-use loop in run_agent() driven by the runner's
tool_executor (since, unlike the Claude Code CLI, the raw API has no built-in tools).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .base import Provider, ProviderResponse, ProviderError, ToolExecutor

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(model=model or DEFAULT_MODEL, **kwargs)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set. Export it, or use --provider claude-code "
                "(no key required)."
            )
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise ProviderError("`anthropic` SDK not installed: pip install anthropic") from exc
        from anthropic import Anthropic

        self._client = Anthropic()

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
    ) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        msg = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return ProviderResponse(
            text=text,
            raw=msg,
            usage={
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
                "model": msg.model,
            },
        )

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
        tool_defs = tool_defs or []
        if not tool_defs or tool_executor is None:
            return self.complete(system, user, model=model, max_tokens=max_tokens)

        anthropic_tools = [
            {"name": d["name"], "description": d.get("description", ""),
             "input_schema": d.get("input_schema", {"type": "object", "properties": {}})}
            for d in tool_defs
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        trace: list[dict[str, Any]] = []
        in_tok = out_tok = 0

        for _ in range(max_steps):
            msg = self._client.messages.create(
                model=model or self.model,
                max_tokens=max_tokens,
                system=system,
                tools=anthropic_tools,
                messages=messages,
            )
            in_tok += msg.usage.input_tokens
            out_tok += msg.usage.output_tokens
            messages.append({"role": "assistant", "content": msg.content})

            tool_uses = [b for b in msg.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                text = "".join(
                    b.text for b in msg.content if getattr(b, "type", None) == "text"
                )
                return ProviderResponse(
                    text=text,
                    raw=msg,
                    usage={"input_tokens": in_tok, "output_tokens": out_tok},
                    tool_trace=trace,
                )

            results = []
            for tu in tool_uses:
                try:
                    out = tool_executor(tu.name, tu.input)
                except Exception as exc:  # surface tool errors to the model
                    out = f"ERROR: {exc}"
                trace.append({"tool": tu.name, "input": tu.input, "output": out[:2000]})
                results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": out[:8000]}
                )
            messages.append({"role": "user", "content": results})

        return ProviderResponse(
            text="[run_agent hit max_steps without final answer]",
            usage={"input_tokens": in_tok, "output_tokens": out_tok},
            tool_trace=trace,
        )
