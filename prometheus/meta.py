"""The fixed Prometheus meta-agents: Architect, Strategist, Improver.

Each loads its role prompt, calls its configured provider, and parses structured JSON
into AgentSpec objects (or, for the Strategist, into a sub-agent roster via the factory).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .agent_spec import AgentSpec, _MCP_TOOL_RE
from .config import Config
from .providers import get_provider, Provider
from .roles import load_role, extract_json
from . import subagent_factory


def _clean_mcp(names) -> list[str]:
    """Keep only well-formed mcp__server__tool names (orchestrator applies the policy gate)."""
    return [n for n in (names or []) if isinstance(n, str) and _MCP_TOOL_RE.match(n)]


class Meta:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._provider_cache: dict[tuple[str, Optional[str]], Provider] = {}

    def provider_for(self, role: str) -> Provider:
        rc = self.config.role(role)
        key = (rc.provider, rc.model)
        if key not in self._provider_cache:
            self._provider_cache[key] = get_provider(rc.provider, model=rc.model)
        return self._provider_cache[key]

    # ------------------------------------------------------------------ #
    def architect(self, task: str, mcp_menu: str = "") -> AgentSpec:
        provider = self.provider_for("architect")
        user = f"TASK:\n{task}"
        if mcp_menu:
            user += f"\n\nAVAILABLE MCP TOOLS (grant relevant ones by exact name, else []):\n{mcp_menu}"
        resp = provider.complete(load_role("architect"), user)
        data = extract_json(resp.text)
        spec = AgentSpec(
            name=data["name"],
            description=data.get("description", ""),
            task=task,
            system_prompt=data["system_prompt"],
            io_contract=data.get("io_contract", ""),
            tools=data.get("tools", []) or [],
            mcp_tools=_clean_mcp(data.get("mcp_tools")),
            model_hint=data.get("model_hint"),
            examples=data.get("examples", []) or [],
            kind="candidate",
            version=1,
            meta={"rationale": data.get("rationale", ""), "created_by": "prometheus-architect"},
        )
        return spec.validate()

    # ------------------------------------------------------------------ #
    def introspect(
        self, candidate: AgentSpec, eval_report: dict[str, Any], mcp_menu: str = ""
    ) -> dict[str, Any]:
        """The candidate reflects on its own design and reports what it needs."""
        provider = self.provider_for("candidate")
        user = (
            _describe_candidate(candidate)
            + "\n\nYOUR TEST RESULTS & FAILURES (JSON):\n"
            + json.dumps(eval_report, indent=2)[:10000]
            + "\n\nAVAILABLE MCP TOOLS (request only from this list):\n"
            + (mcp_menu or "(none)")
        )
        resp = provider.complete(load_role("introspect"), user)
        try:
            return extract_json(resp.text)
        except ValueError:
            return {"requested_builtin_tools": [], "requested_mcp_tools": [],
                    "requested_examples": [], "prompt_gaps": [], "output_contract_issues": [],
                    "rationale": "introspection produced no parseable request"}

    # ------------------------------------------------------------------ #
    def strategist(
        self, candidate: AgentSpec, weaknesses: Optional[list[str]] = None
    ) -> list[AgentSpec]:
        provider = self.provider_for("strategist")
        user = _describe_candidate(candidate)
        if weaknesses:
            user += "\n\nDISCOVERED WEAKNESSES TO TARGET:\n" + "\n".join(
                f"- {w}" for w in weaknesses
            )
        resp = provider.complete(load_role("strategist"), user)
        data = extract_json(resp.text)
        return subagent_factory.build_subagents(data, candidate)

    # ------------------------------------------------------------------ #
    def improver(
        self,
        candidate: AgentSpec,
        eval_report: dict[str, Any],
        capability: Optional[dict[str, Any]] = None,
    ) -> AgentSpec:
        provider = self.provider_for("improver")
        user = (
            _describe_candidate(candidate)
            + "\n\nTEST RESULTS & DIAGNOSIS (JSON):\n"
            + json.dumps(eval_report, indent=2)[:12000]
        )
        if capability:
            user += "\n\nAPPROVED CAPABILITY GRANT (you MUST incorporate exactly this):\n" + json.dumps(
                capability, indent=2
            )[:6000]
        resp = provider.complete(load_role("improver"), user)
        data = extract_json(resp.text)
        improved = candidate.bumped(
            name=data.get("name", candidate.name),
            description=data.get("description", candidate.description),
            system_prompt=data["system_prompt"],
            io_contract=data.get("io_contract", candidate.io_contract),
            tools=data.get("tools", candidate.tools) or [],
            mcp_tools=_clean_mcp(data.get("mcp_tools", candidate.mcp_tools)),
            model_hint=data.get("model_hint", candidate.model_hint),
            examples=data.get("examples", candidate.examples) or [],
            meta={
                **candidate.meta,
                "created_by": "prometheus-improver",
                "changelog": data.get("changelog", ""),
            },
        )
        return improved


def _describe_candidate(c: AgentSpec) -> str:
    return (
        f"CANDIDATE AGENT (v{c.version})\n"
        f"name: {c.name}\n"
        f"task: {c.task}\n"
        f"description: {c.description}\n"
        f"tools: {c.tools or '[]'}\n"
        f"mcp_tools: {c.mcp_tools or '[]'}\n"
        f"io_contract: {c.io_contract}\n"
        f"--- system_prompt ---\n{c.system_prompt}\n--- end ---"
    )
