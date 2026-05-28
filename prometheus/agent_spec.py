"""Portable, model-agnostic agent specification.

This is the core artifact OpenPrometheus produces and manipulates. The SAME schema
represents both the *candidate* agent being built and the *sub-agents* the Strategist
creates to test and benchmark it — so the system is recursive: agents all the way down.

A spec is plain JSON, runnable on any provider (Claude Code auth, Anthropic API,
OpenAI API, ...). Nothing here is Claude-specific.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# Built-in tool names the Architect/Strategist may grant. Mapped to concrete
# implementations per-provider in the runner (e.g. python_exec -> Bash for claude-code).
KNOWN_TOOLS = {"python_exec", "read_file", "write_file", "web_fetch"}

# MCP tools are granted by their full convention name, e.g. mcp__leann-server__leann_search.
_MCP_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+$")

# Roles a sub-agent can play in the testing/benchmarking pipeline.
SUBAGENT_ROLES = {"test_generator", "adversary", "scorer", "benchmarker", "capability_analyst"}

KINDS = {"candidate", "subagent"}


class SpecError(ValueError):
    """Raised when an agent spec is malformed."""


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "agent"


@dataclass
class AgentSpec:
    """A portable agent definition.

    Fields:
        name:            kebab-case identifier.
        description:     one-line summary (used for the Claude Code export frontmatter).
        task:            the original task this agent serves.
        system_prompt:   the full system prompt that defines the agent's behavior.
        io_contract:     human-readable description of expected input and required output.
        tools:           subset of KNOWN_TOOLS the agent is permitted to use ([] = pure reasoning).
        model_hint:      optional preferred model id.
        examples:        few-shot examples [{"input": ..., "output": ...}].
        kind:            "candidate" or "subagent".
        subagent_role:   for sub-agents, one of SUBAGENT_ROLES; None for candidates.
        output_schema:   optional JSON-shape hint a sub-agent must emit (free-form dict).
        version:         integer version, bumped by the Improver.
        meta:            arbitrary metadata (changelog, created_by, etc.).
    """

    name: str
    description: str
    task: str
    system_prompt: str
    io_contract: str = ""
    tools: list[str] = field(default_factory=list)
    mcp_tools: list[str] = field(default_factory=list)
    model_hint: Optional[str] = None
    examples: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "candidate"
    subagent_role: Optional[str] = None
    output_schema: Optional[dict[str, Any]] = None
    version: int = 1
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def validate(self) -> "AgentSpec":
        if not self.name or not isinstance(self.name, str):
            raise SpecError("spec.name must be a non-empty string")
        if not self.system_prompt or not isinstance(self.system_prompt, str):
            raise SpecError("spec.system_prompt must be a non-empty string")
        if self.kind not in KINDS:
            raise SpecError(f"spec.kind must be one of {sorted(KINDS)}, got {self.kind!r}")
        if not isinstance(self.tools, list):
            raise SpecError("spec.tools must be a list")
        unknown = set(self.tools) - KNOWN_TOOLS
        if unknown:
            raise SpecError(f"unknown tools: {sorted(unknown)}; allowed: {sorted(KNOWN_TOOLS)}")
        if not isinstance(self.mcp_tools, list):
            raise SpecError("spec.mcp_tools must be a list")
        bad_mcp = [t for t in self.mcp_tools if not _MCP_TOOL_RE.match(str(t))]
        if bad_mcp:
            raise SpecError(f"malformed mcp_tools (expect mcp__server__tool): {bad_mcp}")
        if self.kind == "subagent":
            if self.subagent_role not in SUBAGENT_ROLES:
                raise SpecError(
                    f"sub-agent requires subagent_role in {sorted(SUBAGENT_ROLES)}, "
                    f"got {self.subagent_role!r}"
                )
        else:
            if self.subagent_role is not None:
                raise SpecError("candidate specs must not set subagent_role")
        if not isinstance(self.version, int) or self.version < 1:
            raise SpecError("spec.version must be a positive integer")
        if not isinstance(self.examples, list):
            raise SpecError("spec.examples must be a list")
        return self

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSpec":
        if not isinstance(data, dict):
            raise SpecError("agent spec must be a JSON object")
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise SpecError(f"unexpected fields in spec: {sorted(unknown)}")
        try:
            spec = cls(**data)
        except TypeError as exc:  # missing required fields
            raise SpecError(str(exc)) from exc
        # normalize
        spec.name = slugify(spec.name)
        return spec.validate()

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "AgentSpec":
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)

    def bumped(self, **changes: Any) -> "AgentSpec":
        """Return a new spec with version+1 and the given field overrides."""
        data = self.to_dict()
        data.update(changes)
        data["version"] = self.version + 1
        return AgentSpec.from_dict(data)
