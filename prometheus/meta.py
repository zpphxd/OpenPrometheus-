"""The fixed Prometheus meta-agents: Architect, Strategist, Improver.

Each loads its role prompt, calls its configured provider, and parses structured JSON
into AgentSpec objects (or, for the Strategist, into a sub-agent roster via the factory).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .agent_spec import AgentSpec
from .config import Config
from .providers import get_provider, Provider
from .roles import load_role, extract_json
from . import subagent_factory


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
    def architect(self, task: str) -> AgentSpec:
        provider = self.provider_for("architect")
        resp = provider.complete(load_role("architect"), f"TASK:\n{task}")
        data = extract_json(resp.text)
        spec = AgentSpec(
            name=data["name"],
            description=data.get("description", ""),
            task=task,
            system_prompt=data["system_prompt"],
            io_contract=data.get("io_contract", ""),
            tools=data.get("tools", []) or [],
            model_hint=data.get("model_hint"),
            examples=data.get("examples", []) or [],
            kind="candidate",
            version=1,
            meta={"rationale": data.get("rationale", ""), "created_by": "prometheus-architect"},
        )
        return spec.validate()

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
    def improver(self, candidate: AgentSpec, eval_report: dict[str, Any]) -> AgentSpec:
        provider = self.provider_for("improver")
        user = (
            _describe_candidate(candidate)
            + "\n\nTEST RESULTS & DIAGNOSIS (JSON):\n"
            + json.dumps(eval_report, indent=2)[:12000]
        )
        resp = provider.complete(load_role("improver"), user)
        data = extract_json(resp.text)
        improved = candidate.bumped(
            name=data.get("name", candidate.name),
            description=data.get("description", candidate.description),
            system_prompt=data["system_prompt"],
            io_contract=data.get("io_contract", candidate.io_contract),
            tools=data.get("tools", candidate.tools) or [],
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
        f"io_contract: {c.io_contract}\n"
        f"--- system_prompt ---\n{c.system_prompt}\n--- end ---"
    )
