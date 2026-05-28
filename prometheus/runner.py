"""Runner — execute an AgentSpec against a provider on a given input.

Used both to run the *candidate* against test cases and to run *sub-agents* (test
generators, adversaries, scorers). If the spec grants tools, the runner uses the
provider's agentic loop, wiring a workdir-scoped ToolRegistry for API providers.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from .agent_spec import AgentSpec
from .providers import Provider
from .providers.base import ProviderResponse
from .tools import ToolRegistry


def run_spec(
    spec: AgentSpec,
    user_input: str,
    provider: Provider,
    *,
    model: Optional[str] = None,
    allow_code_exec: bool = False,
    workdir: Optional[Path] = None,
    max_tokens: int = 4096,
) -> ProviderResponse:
    """Run `spec` on `user_input`. Returns the provider response (text + trace + usage)."""
    model = model or spec.model_hint

    if not spec.tools:
        return provider.complete(
            spec.system_prompt, user_input, model=model, max_tokens=max_tokens
        )

    # Tooled execution.
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="prometheus-agent-"))
    registry = ToolRegistry(wd, allow_code_exec=allow_code_exec)
    return provider.run_agent(
        spec.system_prompt,
        user_input,
        tools=spec.tools,
        tool_executor=registry.execute,
        model=model,
        max_tokens=max_tokens,
        workdir=str(wd),
        allow_risky=allow_code_exec,
    )
