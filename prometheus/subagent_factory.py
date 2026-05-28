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
