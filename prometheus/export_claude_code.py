"""Export a finished candidate AgentSpec as a Claude Code subagent .md file.

Format matches Claude Code's subagent convention: YAML frontmatter
(name / description / tools / model) followed by the system-prompt body. Written to
~/.claude/agents/<name>.md by default so the agent is immediately usable in Claude Code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agent_spec import AgentSpec

# Map portable tools -> Claude Code tool names for the frontmatter.
_TOOL_MAP = {
    "python_exec": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "web_fetch": "WebFetch",
}


def to_markdown(spec: AgentSpec) -> str:
    fm = [f"name: {spec.name}", f"description: {spec.description}"]
    cc_tools = sorted({_TOOL_MAP[t] for t in spec.tools if t in _TOOL_MAP})
    cc_tools += list(spec.mcp_tools)  # MCP tools are referenced by their full mcp__ name
    if cc_tools:
        fm.append(f"tools: {', '.join(cc_tools)}")
    if spec.model_hint:
        fm.append(f"model: {spec.model_hint}")

    body = [spec.system_prompt.strip()]
    if spec.io_contract:
        body.append(f"\n## Input / Output\n{spec.io_contract.strip()}")
    if spec.examples:
        body.append("\n## Examples")
        for ex in spec.examples[:5]:
            body.append(f"\nInput:\n{ex.get('input','')}\n\nOutput:\n{ex.get('output','')}")

    return "---\n" + "\n".join(fm) + "\n---\n\n" + "\n".join(body) + "\n"


def export(spec: AgentSpec, dest_dir: Optional[Path] = None) -> Path:
    dest_dir = Path(dest_dir) if dest_dir else (Path.home() / ".claude" / "agents")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{spec.name}.md"
    path.write_text(to_markdown(spec))
    return path
