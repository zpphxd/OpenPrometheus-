"""Runner — execute an AgentSpec against a provider on a given input.

Used both to run the *candidate* against test cases and to run *sub-agents* (test
generators, adversaries, scorers). If the spec grants tools, the runner builds:
  - a bare name list  (built-ins + mcp__... ) for native-runtime providers (claude-code),
  - provider-neutral tool defs + a routing executor for API providers (anthropic/openai),
    routing built-ins to the sandboxed ToolRegistry and MCP calls to the MCPProxy.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from .agent_spec import AgentSpec
from .providers import Provider
from .providers.base import ProviderResponse
from .tools import ToolRegistry
from .tools.registry import BUILTIN_SCHEMAS


def run_spec(
    spec: AgentSpec,
    user_input: str,
    provider: Provider,
    *,
    model: Optional[str] = None,
    allow_code_exec: bool = False,
    workdir: Optional[Path] = None,
    max_tokens: int = 4096,
    mcp_proxy: Optional[Any] = None,
    mcp_schemas: Optional[dict[str, dict[str, Any]]] = None,
) -> ProviderResponse:
    """Run `spec` on `user_input`. Returns the provider response (text + trace + usage)."""
    model = model or spec.model_hint
    tool_names = list(spec.tools) + list(spec.mcp_tools)

    if not tool_names:
        return provider.complete(
            spec.system_prompt, user_input, model=model, max_tokens=max_tokens
        )

    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="prometheus-agent-"))
    registry = ToolRegistry(wd, allow_code_exec=allow_code_exec)
    mcp_schemas = mcp_schemas or {}

    # provider-neutral defs (API providers): built-ins + proxiable MCP tools only.
    tool_defs: list[dict[str, Any]] = [BUILTIN_SCHEMAS[t] for t in spec.tools if t in BUILTIN_SCHEMAS]
    for m in spec.mcp_tools:
        if m in mcp_schemas:
            tool_defs.append({
                "name": m,
                "description": mcp_schemas[m].get("description", f"MCP tool {m}"),
                "input_schema": mcp_schemas[m].get("input_schema") or {"type": "object", "properties": {}},
            })

    def executor(name: str, args: dict[str, Any]) -> str:
        if name in BUILTIN_SCHEMAS:
            return registry.execute(name, args)
        if name.startswith("mcp__"):
            if mcp_proxy is None:
                return f"ERROR: MCP tool {name} is not available in this run"
            try:
                return mcp_proxy.call(name, args)
            except Exception as exc:  # noqa: BLE001 — surface to the model
                return f"ERROR calling {name}: {exc}"
        return f"ERROR: unknown tool {name}"

    return provider.run_agent(
        spec.system_prompt,
        user_input,
        tool_names=tool_names,
        tool_defs=tool_defs,
        tool_executor=executor,
        model=model,
        max_tokens=max_tokens,
        workdir=str(wd),
        allow_risky=allow_code_exec,
    )
