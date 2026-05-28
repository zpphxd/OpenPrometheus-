"""Orchestrator — the recursive build/test/improve loop.

Flow:
  1. Architect designs candidate v1.
  2. Strategist creates a roster of specialized sub-agents (generators/adversaries/scorers).
  3. Each round:
       a. generator/adversary sub-agents produce a test curriculum (grows each round).
       b. the candidate runs against every test.
       c. scorer/benchmarker sub-agents grade every output; scores are aggregated.
       d. if the aggregate clears the threshold (or we plateau / hit max iters) -> stop.
       e. otherwise the Improver rewrites the candidate, and the Strategist is asked to
          spawn NEW specialists targeting the freshly-revealed weaknesses.

All artifacts (candidate versions, sub-agents, tests, evals, state) are written under
runs/<run-id>/ for inspection and resumability.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .agent_spec import AgentSpec
from .config import Config
from .meta import Meta
from .runner import run_spec
from . import subagent_factory

Logger = Callable[[str], None]


@dataclass
class RoundResult:
    iteration: int
    version: int
    aggregate: float
    hardest_tier_score: float
    per_test: list[dict[str, Any]] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)


_TIER_WEIGHT = {"basic": 1.0, "edge": 1.5, "adversarial": 2.0}


class Orchestrator:
    def __init__(self, config: Config, logger: Optional[Logger] = None) -> None:
        self.config = config
        self.meta = Meta(config)
        self.log = logger or (lambda msg: print(msg, flush=True))
        self._mcp_proxy = None
        self._mcp_menu = ""
        self._mcp_allowed: set[str] = set()
        self._mcp_schemas: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    def build(self, task: str) -> dict[str, Any]:
        self._mcp_proxy = None
        try:
            return self._build(task)
        finally:
            if self._mcp_proxy is not None:
                self._mcp_proxy.close()

    def _build(self, task: str) -> dict[str, Any]:
        run_id = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.config.runs_dir / run_id
        for sub in ("candidate", "subagents", "tests", "outputs", "evals"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        self.log(f"▶ Prometheus run {run_id}  (task: {task})")

        # 0. MCP catalog + proxy (tiered gate; empty unless --mcp-allow set) ---- #
        from .mcp_catalog import build_catalog
        catalog = build_catalog(self.config.mcp_allow, self.config.mcp_allow_sensitive, logger=self.log)
        self._mcp_menu = catalog.menu_text() if catalog.entries else ""
        self._mcp_allowed = set(catalog.entries)
        self._mcp_schemas = {
            name: {"description": e.description, "input_schema": e.input_schema}
            for name, e in catalog.proxiable_entries().items()
        }
        if catalog.entries:
            self.log(f"  MCP: {len(catalog.entries)} tools grantable "
                     f"({len(self._mcp_schemas)} proxiable to API providers)")
            from .mcp_client import MCPProxy
            self._mcp_proxy = MCPProxy()

        # 1. Architect -------------------------------------------------- #
        candidate = self.meta.architect(task, mcp_menu=self._mcp_menu)
        candidate = self._gate_mcp(candidate)
        candidate.save(run_dir / "candidate" / f"agent-v{candidate.version}.json")
        self.log(f"  architect → candidate '{candidate.name}' "
                 f"(tools={candidate.tools or '[]'}, mcp={candidate.mcp_tools or '[]'})")

        # 2. Strategist (initial roster) -------------------------------- #
        subagents = self.meta.strategist(candidate)
        self._save_subagents(subagents, run_dir)
        self.log(f"  strategist → {len(subagents)} sub-agents: "
                 + ", ".join(f"{s.subagent_role}:{s.name}" for s in subagents))

        generators = [s for s in subagents if s.subagent_role in ("test_generator", "adversary")]
        scorers = [s for s in subagents if s.subagent_role in ("scorer", "benchmarker")]
        analysts = [s for s in subagents if s.subagent_role == "capability_analyst"]
        if not generators or not scorers:
            raise RuntimeError("strategist did not produce both generators and scorers")
        analyst = analysts[0] if analysts else subagent_factory.default_capability_analyst(candidate)

        sub_provider = self.meta.provider_for("subagent")
        cand_provider = self.meta.provider_for("candidate")

        tests: list[dict[str, Any]] = []
        history: list[RoundResult] = []
        best_score = -1.0
        best_version = candidate.version
        plateau = 0

        # 3. Improvement loop ------------------------------------------- #
        for it in range(1, self.config.max_iters + 1):
            self.log(f"\n── iteration {it}/{self.config.max_iters}  (candidate v{candidate.version}) ──")

            # (a) grow the test curriculum
            new_tests = self._generate_tests(generators, candidate, sub_provider)
            tests = _dedupe_tests(tests + new_tests)
            self._save_json(run_dir / "tests" / f"suite-{it:03d}.json", {"tests": tests})
            self.log(f"  tests: {len(tests)} total ({len(new_tests)} new this round)")

            # (b) run candidate against every test (with MCP tools if granted)
            outputs = self._run_candidate(candidate, tests, cand_provider)
            self._save_json(run_dir / "outputs" / f"outputs-{it:03d}.json", outputs)

            # (c) score every output with every scorer, aggregate
            rr = self._evaluate(scorers, tests, outputs, sub_provider, it, candidate.version)
            history.append(rr)
            self._write_eval_md(run_dir / "evals" / f"eval-{it:03d}.md", rr, candidate)
            self.log(f"  ⇒ aggregate {rr.aggregate:.2f}/10  | hardest-tier {rr.hardest_tier_score:.2f}/10")

            # track best / plateau
            if rr.aggregate > best_score + 0.05:
                best_score, best_version, plateau = rr.aggregate, candidate.version, 0
            else:
                plateau += 1

            # (d) stop conditions
            if rr.hardest_tier_score >= self.config.threshold and rr.aggregate >= self.config.threshold:
                self.log(f"  ✓ threshold {self.config.threshold} cleared — stopping.")
                self._finalize(run_dir, candidate, history, run_id, "threshold")
                return self._result(run_dir, candidate, history, run_id, "threshold")
            if it >= self.config.max_iters:
                self.log("  ⛔ max iterations reached — stopping.")
                break
            if plateau >= self.config.patience:
                self.log(f"  ⚠ plateau ({plateau} rounds without gain) — stopping.")
                break

            # (e) negotiation: candidate self-reports needs → analyst validates → improver
            eval_report = _eval_report(rr)
            capability = None
            if self.config.negotiate:
                capability = self._negotiate(candidate, eval_report, analyst, sub_provider, run_dir, it)

            candidate = self.meta.improver(candidate, eval_report, capability=capability)
            candidate = self._gate_mcp(candidate)
            candidate.save(run_dir / "candidate" / f"agent-v{candidate.version}.json")
            self.log(f"  improver → candidate v{candidate.version} "
                     f"(tools={candidate.tools or '[]'}, mcp={candidate.mcp_tools or '[]'})")
            if rr.weaknesses:
                extra = self.meta.strategist(candidate, weaknesses=rr.weaknesses)
                if extra:
                    self._save_subagents(extra, run_dir)
                    for s in extra:
                        if s.subagent_role in ("test_generator", "adversary"):
                            generators.append(s)
                        elif s.subagent_role in ("scorer", "benchmarker"):
                            scorers.append(s)
                        elif s.subagent_role == "capability_analyst":
                            analyst = s
                    self.log(f"  strategist → +{len(extra)} specialists targeting weaknesses")

        # loop ended without clearing threshold: finalize the best version we saw
        self._finalize(run_dir, candidate, history, run_id, "exhausted")
        return self._result(run_dir, candidate, history, run_id, "exhausted")

    # ------------------------------------------------------------------ #
    def _generate_tests(self, generators, candidate, provider) -> list[dict[str, Any]]:
        n_each = max(1, self.config.tests_per_round // max(1, len(generators)))
        out: list[dict[str, Any]] = []
        for g in generators:
            try:
                out += subagent_factory.generate_tests(g, candidate, provider, n_each)
            except Exception as exc:  # one bad generator shouldn't kill the round
                self.log(f"    ! generator {g.name} failed: {exc}")
        return out

    def _run_candidate(self, candidate, tests, provider) -> list[dict[str, Any]]:
        results = []
        for t in tests:
            try:
                resp = run_spec(
                    candidate, t["input"], provider,
                    allow_code_exec=self.config.allow_candidate_code_exec,
                    mcp_proxy=self._mcp_proxy,
                    mcp_schemas=self._mcp_schemas,
                )
                text = resp.text
            except Exception as exc:
                text = f"[candidate error: {exc}]"
            results.append({"test_id": t["id"], "input": t["input"], "output": text})
        return results

    def _gate_mcp(self, candidate):
        """Drop any MCP tool the candidate requested that policy doesn't allow; log it."""
        if not candidate.mcp_tools:
            return candidate
        kept = [m for m in candidate.mcp_tools if m in self._mcp_allowed]
        dropped = [m for m in candidate.mcp_tools if m not in self._mcp_allowed]
        if dropped:
            self.log(f"  · gate: withheld non-allowed MCP tools {dropped}")
        if kept != candidate.mcp_tools:
            return AgentSpec.from_dict({**candidate.to_dict(), "mcp_tools": kept})  # preserve version
        return candidate

    def _negotiate(self, candidate, eval_report, analyst, provider, run_dir, it):
        """Candidate self-reports needs; the analyst validates against the allowed catalog."""
        try:
            self_report = self.meta.introspect(candidate, eval_report, self._mcp_menu)
            decision = subagent_factory.analyze_capability(
                analyst, candidate, self_report, eval_report,
                self._mcp_menu, self._mcp_allowed, provider,
            )
        except Exception as exc:
            self.log(f"  ! negotiation skipped: {exc}")
            return None
        self._save_json(run_dir / "evals" / f"capability-{it:03d}.json",
                        {"self_report": self_report, "decision": decision})
        granted = (decision.get("grant_builtin_tools") or []) + (decision.get("grant_mcp_tools") or [])
        if granted:
            self.log(f"  ⇄ negotiation: candidate requested + analyst approved {granted}")
        return decision

    def _evaluate(self, scorers, tests, outputs, provider, it, version) -> RoundResult:
        by_id = {o["test_id"]: o["output"] for o in outputs}
        per_test = []
        weaknesses: list[str] = []
        for t in tests:
            output = by_id.get(t["id"], "")
            scores = []
            for sc in scorers:
                try:
                    scores.append(subagent_factory.score_output(sc, t, output, provider))
                except Exception as exc:
                    self.log(f"    ! scorer {sc.name} failed on {t['id']}: {exc}")
            if not scores:
                continue
            avg = sum(s["score"] for s in scores) / len(scores)
            fails = [fm for s in scores for fm in s["failure_modes"]]
            fixes = [fx for s in scores for fx in s["fix_suggestions"]]
            per_test.append({
                "test_id": t["id"], "tier": t.get("tier", "basic"),
                "weight": t.get("weight", 1.0), "score": avg,
                "input": t["input"], "output": output,
                "failure_modes": fails, "fix_suggestions": fixes,
            })
            if avg < self.config.threshold:
                weaknesses.extend(fails[:2])

        aggregate = _weighted_mean(per_test)
        hardest = [p for p in per_test if p["tier"] == "adversarial"] or \
                  [p for p in per_test if p["tier"] == "edge"] or per_test
        hardest_score = _weighted_mean(hardest)
        return RoundResult(
            iteration=it, version=version, aggregate=aggregate,
            hardest_tier_score=hardest_score, per_test=per_test,
            weaknesses=_top(weaknesses, 6),
        )

    # ---- persistence helpers ----------------------------------------- #
    def _save_subagents(self, subagents, run_dir) -> None:
        for s in subagents:
            s.save(run_dir / "subagents" / f"{s.subagent_role}-{s.name}.json")

    def _save_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _write_eval_md(self, path: Path, rr: RoundResult, candidate: AgentSpec) -> None:
        lines = [
            f"# Eval — iteration {rr.iteration} (candidate v{rr.version}: {candidate.name})",
            "",
            f"- **Aggregate:** {rr.aggregate:.2f}/10",
            f"- **Hardest-tier:** {rr.hardest_tier_score:.2f}/10",
            f"- **Tests:** {len(rr.per_test)}",
            "",
            "## Per-test",
        ]
        for p in sorted(rr.per_test, key=lambda x: x["score"]):
            lines.append(f"### [{p['tier']}] {p['test_id']} — {p['score']:.1f}/10")
            lines.append(f"- input: `{str(p['input'])[:160]}`")
            lines.append(f"- output: `{str(p['output'])[:160]}`")
            if p["failure_modes"]:
                lines.append("- failure modes: " + "; ".join(p["failure_modes"][:4]))
            if p["fix_suggestions"]:
                lines.append("- fixes: " + "; ".join(p["fix_suggestions"][:4]))
            lines.append("")
        path.write_text("\n".join(lines))

    def _finalize(self, run_dir, candidate, history, run_id, reason) -> None:
        from .export_claude_code import export
        final_path = run_dir / "candidate" / "agent-final.json"
        candidate.save(final_path)
        if self.config.export_cc:
            try:
                cc_path = export(candidate)
            except Exception as exc:
                cc_path = f"[export failed: {exc}]"
        else:
            cc_path = "[skipped --no-export]"
        state = {
            "run_id": run_id, "task": candidate.task, "stop_reason": reason,
            "final_version": candidate.version,
            "final_agent": str(final_path),
            "claude_code_export": str(cc_path),
            "score_history": [
                {"iteration": h.iteration, "version": h.version,
                 "aggregate": round(h.aggregate, 2),
                 "hardest_tier": round(h.hardest_tier_score, 2)}
                for h in history
            ],
        }
        self._save_json(run_dir / "state.json", state)
        self.log(f"\n● final agent v{candidate.version} → {final_path}")
        self.log(f"● Claude Code export → {cc_path}")

    def _result(self, run_dir, candidate, history, run_id, reason) -> dict[str, Any]:
        return {
            "run_id": run_id, "run_dir": str(run_dir), "stop_reason": reason,
            "final_agent": candidate, "history": history,
        }


# ---- pure helpers ---------------------------------------------------- #
def _weighted_mean(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    num = sum(r["score"] * _TIER_WEIGHT.get(r["tier"], r.get("weight", 1.0)) for r in rows)
    den = sum(_TIER_WEIGHT.get(r["tier"], r.get("weight", 1.0)) for r in rows)
    return num / den if den else 0.0


def _dedupe_tests(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for t in tests:
        key = (t.get("input", "")).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    # Reassign globally-unique, position-stable IDs. Sub-agent-supplied ids (often all
    # "t1") collide, which would collapse distinct tests onto one output when keyed by id.
    for i, t in enumerate(out, 1):
        t["id"] = f"test-{i:03d}"
    return out


def _top(items: list[str], n: int) -> list[str]:
    seen, out = set(), []
    for it in items:
        k = it.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(it)
        if len(out) >= n:
            break
    return out


def _eval_report(rr: RoundResult) -> dict[str, Any]:
    return {
        "aggregate": round(rr.aggregate, 2),
        "hardest_tier_score": round(rr.hardest_tier_score, 2),
        "failing_tests": [
            {"test_id": p["test_id"], "tier": p["tier"], "score": round(p["score"], 1),
             "input": p["input"], "output": str(p["output"])[:500],
             "failure_modes": p["failure_modes"][:4], "fix_suggestions": p["fix_suggestions"][:4]}
            for p in sorted(rr.per_test, key=lambda x: x["score"]) if p["score"] < 9.0
        ][:8],
    }
