"""OpenAI API provider — uses the `openai` SDK + OPENAI_API_KEY (optional dependency).

Demonstrates the harness is genuinely model-agnostic: any foundational model with an
API key plugs in the same way. complete() is fully supported; run_agent() uses the
Chat Completions function-calling loop with the runner's tool_executor.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from .base import Provider, ProviderResponse, ProviderError, ToolExecutor

DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, model: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(model=model or DEFAULT_MODEL, **kwargs)
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError(
                "OPENAI_API_KEY is not set. Export it, or use --provider claude-code."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("`openai` SDK not installed: pip install openai") from exc
        self._client = OpenAI()

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
    ) -> ProviderResponse:
        resp = self._client.chat.completions.create(
            model=model or self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **({"temperature": temperature} if temperature is not None else {}),
        )
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return ProviderResponse(
            text=text,
            raw=resp,
            usage={
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
                "model": resp.model,
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

        openai_tools = [
            {"type": "function",
             "function": {"name": d["name"], "description": d.get("description", ""),
                          "parameters": d.get("input_schema", {"type": "object", "properties": {}})}}
            for d in tool_defs
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        trace: list[dict[str, Any]] = []

        for _ in range(max_steps):
            resp = self._client.chat.completions.create(
                model=model or self.model,
                max_tokens=max_tokens,
                messages=messages,
                tools=openai_tools,
            )
            choice = resp.choices[0].message
            if not choice.tool_calls:
                return ProviderResponse(
                    text=choice.content or "",
                    raw=resp,
                    tool_trace=trace,
                )
            messages.append(choice.model_dump(exclude_none=True))
            for tc in choice.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    out = tool_executor(tc.function.name, args)
                except Exception as exc:
                    out = f"ERROR: {exc}"
                trace.append({"tool": tc.function.name, "output": out[:2000]})
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": out[:8000]}
                )

        return ProviderResponse(
            text="[run_agent hit max_steps without final answer]", tool_trace=trace
        )
