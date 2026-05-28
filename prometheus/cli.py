"""OpenPrometheus CLI.

    prometheus "build an agent that ..."  [options]

Examples:
    prometheus "convert messy dates to ISO 8601"                 # zero-key, Claude Code auth
    prometheus "write correct SQL from questions" --max-iters 4
    prometheus "summarize legal docs" --provider anthropic --model claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .orchestrator import Orchestrator


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prometheus",
        description="Recursively build, test, and improve a specialized agent for a task.",
    )
    p.add_argument("task", help="the task to build an agent for")
    p.add_argument("--provider", default=None,
                   help="backend for all roles: claude-code (default) | anthropic | openai")
    p.add_argument("--model", default=None, help="model id/alias for all roles (e.g. sonnet, opus)")
    p.add_argument("--threshold", type=float, default=None, help="pass score 0-10 (default 8.5)")
    p.add_argument("--max-iters", type=int, default=None, help="max improvement rounds (default 6)")
    p.add_argument("--patience", type=int, default=None,
                   help="stop after N rounds without gain (default 2)")
    p.add_argument("--tests-per-round", type=int, default=None,
                   help="new tests generated per round (default 6)")
    p.add_argument("--allow-code-exec", action="store_true",
                   help="permit candidate agents to execute code (off by default; use with trust)")
    p.add_argument("--runs-dir", default=None, help="where to write run artifacts (default ./runs)")
    p.add_argument("--no-export", action="store_true",
                   help="skip writing the Claude Code .md export")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.from_env(
        provider=args.provider,
        model=args.model,
        threshold=args.threshold,
        max_iters=args.max_iters,
        patience=args.patience,
        tests_per_round=args.tests_per_round,
        allow_candidate_code_exec=True if args.allow_code_exec else None,
        export_cc=False if args.no_export else None,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
    )
    orch = Orchestrator(cfg)
    try:
        result = orch.build(args.task)
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    final = result["final_agent"]
    print(f"\n{'='*60}")
    print(f"DONE — stop reason: {result['stop_reason']}")
    print(f"final agent: {final.name} (v{final.version})")
    print(f"artifacts:   {result['run_dir']}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
