"""Sub-agent factory.

Turns the Strategist's roster into runnable AgentSpec sub-agents, injecting a strict
per-role OUTPUT CONTRACT so their results are always machine-parseable regardless of how
the Strategist phrased the system prompt. Also drives the sub-agents: test generators
produce test cases; scorers grade candidate outputs.
"""

from __future__ import annotations

from typing import Any, Optional

from .agent_spec import AgentSpec, SUBAGENT_ROLES
from .providers import Provider
from .roles import extract_json
from .runner import run_spec

# Injected so generated tests are always parseable.
_TEST_CONTRACT = """

## OUTPUT CONTRACT (mandatory — the harness parses this)
When asked to generate test cases, respond with ONLY this JSON object:
{"tests": [
  {"id": "t1", "tier": "basic|edge|adversarial",
   "input": "<the exact input string to feed the candidate agent>",
   "rubric": "<specific, checkable criteria an excellent answer must satisfy>",
   "weight": 1.0}
]}
Make each "input" concrete and directly usable as the candidate's input. Generate exactly
the number of tests requested, distributed across tiers with extra weight on edge/adversarial.
"""

# Injected so scores are always parseable.
_SCORE_CONTRACT = """

## OUTPUT CONTRACT (mandatory — the harness parses this)
You grade ONE candidate output against ONE rubric. Respond with ONLY this JSON object:
{"score": <number 0-10>,
 "passed": <true|false>,
 "reasoning": "<why this score, citing specific evidence from the output>",
 "failure_modes": ["<specific defect>"],
 "fix_suggestions": ["<concrete, actionable change the agent's DESIGN should adopt>"]}
Be ruthless and specific. 10 = flawless against the rubric; 7 = competent with real gaps;
<5 = materially wrong. Do not be charitable. Reward correctness over verbosity.
"""

# Injected so the capability analyst's verdict is always parseable.
_ANALYST_CONTRACT = """

## OUTPUT CONTRACT (mandatory — the harness parses this)
Respond with ONLY this JSON object:
{"grant_builtin_tools": [], "grant_mcp_tools": [],
 "prompt_fixes": ["concrete instruction to add"],
 "add_examples": [{"input": "...", "output": "..."}],
 "rejected": [{"request": "...", "reason": "..."}],
 "rationale": "why this set best fixes the diagnosed failures"}
Approve MCP tools ONLY by exact full name from the ALLOWED list. Prefer prompt fixes and
examples over new tools. Justify every grant against a specific failure.
"""

_GENERATOR_ROLES = {"test_generator", "adversary"}
_SCORER_ROLES = {"scorer", "benchmarker"}


def build_subagents(strategist_data: dict[str, Any], candidate: AgentSpec) -> list[AgentSpec]:
    out: list[AgentSpec] = []
    for raw in strategist_data.get("subagents", []):
        role = raw.get("subagent_role")
        if role not in SUBAGENT_ROLES:
            continue
        system = raw.get("system_prompt", "")
        if role in _GENERATOR_ROLES:
            system += _TEST_CONTRACT
        elif role in _SCORER_ROLES:
            system += _SCORE_CONTRACT
        elif role == "capability_analyst":
            system += _ANALYST_CONTRACT
        spec = AgentSpec(
            name=raw.get("name", f"{role}-agent"),
            description=raw.get("description", ""),
            task=candidate.task,
            system_prompt=system,
            io_contract="",
            tools=[],  # probing/judging is reasoning work; no tools by default
            kind="subagent",
            subagent_role=role,
            meta={"rationale": raw.get("rationale", ""), "for_candidate": candidate.name},
        )
        out.append(spec.validate())
    return out


def generate_tests(
    subagent: AgentSpec,
    candidate: AgentSpec,
    provider: Provider,
    n: int,
    *,
    model: Optional[str] = None,
) -> list[dict[str, Any]]:
    user = (
        f"Generate {n} test cases for this candidate agent.\n\n"
        f"CANDIDATE TASK: {candidate.task}\n"
        f"CANDIDATE DESCRIPTION: {candidate.description}\n"
        f"CANDIDATE IO CONTRACT: {candidate.io_contract}\n\n"
        f"Probe it according to your specialty ({subagent.subagent_role})."
    )
    resp = run_spec(subagent, user, provider, model=model)
    data = extract_json(resp.text)
    tests = []
    for i, t in enumerate(data.get("tests", [])):
        if "input" not in t:
            continue
        tests.append(
            {
                "id": t.get("id") or f"{subagent.name}-{i+1}",
                "tier": t.get("tier", "basic"),
                "input": t["input"],
                "rubric": t.get("rubric", "Produce a correct, well-formed answer."),
                "weight": float(t.get("weight", 1.0)),
                "source": subagent.name,
            }
        )
    return tests


def default_capability_analyst(candidate: AgentSpec) -> AgentSpec:
    """Built-in analyst used when the Strategist didn't create one."""
    from .roles import load_role
    return AgentSpec(
        name="capability-analyst",
        description="Gatekeeper that approves justified tools/examples/prompt-fixes.",
        task=candidate.task,
        system_prompt=load_role("capability_analyst") + _ANALYST_CONTRACT,
        kind="subagent",
        subagent_role="capability_analyst",
        meta={"for_candidate": candidate.name, "builtin": True},
    ).validate()


def analyze_capability(
    analyst: AgentSpec,
    candidate: AgentSpec,
    self_report: dict[str, Any],
    eval_report: dict[str, Any],
    allowed_mcp_menu: str,
    allowed_mcp_names: set[str],
    provider: Provider,
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    import json as _json
    user = (
        "CANDIDATE SELF-REPORT (its requested capabilities):\n"
        + _json.dumps(self_report, indent=2)[:6000]
        + "\n\nEXTERNAL EVAL DIAGNOSIS:\n"
        + _json.dumps(eval_report, indent=2)[:6000]
        + "\n\nALLOWED MCP TOOLS (you may approve ONLY these, by exact full name):\n"
        + (allowed_mcp_menu or "(none)")
        + "\n\nDecide what to grant per your OUTPUT CONTRACT."
    )
    resp = run_spec(analyst, user, provider, model=model)
    data = extract_json(resp.text)
    # defense in depth: drop any MCP grant not in the allowed set
    granted_mcp = [m for m in data.get("grant_mcp_tools", []) if m in allowed_mcp_names]
    dropped = [m for m in data.get("grant_mcp_tools", []) if m not in allowed_mcp_names]
    return {
        "grant_builtin_tools": [t for t in data.get("grant_builtin_tools", []) if t in {"python_exec", "read_file", "write_file", "web_fetch"}],
        "grant_mcp_tools": granted_mcp,
        "prompt_fixes": data.get("prompt_fixes", []) or [],
        "add_examples": data.get("add_examples", []) or [],
        "rejected": (data.get("rejected", []) or []) + [{"request": m, "reason": "not in allowed MCP set"} for m in dropped],
        "rationale": data.get("rationale", ""),
    }


def score_output(
    scorer: AgentSpec,
    test: dict[str, Any],
    candidate_output: str,
    provider: Provider,
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    user = (
        f"TEST INPUT:\n{test['input']}\n\n"
        f"RUBRIC (what an excellent answer must satisfy):\n{test['rubric']}\n\n"
        f"CANDIDATE OUTPUT:\n{candidate_output}\n\n"
        f"Grade strictly per your OUTPUT CONTRACT."
    )
    resp = run_spec(scorer, user, provider, model=model)
    data = extract_json(resp.text)
    score = float(data.get("score", 0))
    return {
        "score": max(0.0, min(10.0, score)),
        "passed": bool(data.get("passed", score >= 7)),
        "reasoning": data.get("reasoning", ""),
        "failure_modes": data.get("failure_modes", []) or [],
        "fix_suggestions": data.get("fix_suggestions", []) or [],
        "scorer": scorer.name,
    }
