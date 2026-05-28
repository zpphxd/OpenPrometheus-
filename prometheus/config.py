"""Configuration: per-role provider/model selection and loop parameters.

Each Prometheus role and the candidate/sub-agent execution can target a different
backend. Resolution order (lowest to highest precedence):
  built-in defaults  ->  PROMETHEUS_PROVIDER / PROMETHEUS_MODEL (globals)
  ->  PROMETHEUS_<ROLE>_PROVIDER / PROMETHEUS_<ROLE>_MODEL  ->  explicit CLI overrides
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROLES = ("architect", "strategist", "improver", "subagent", "candidate")

# Sonnet by default — strong enough for the meta-work and ~5x cheaper than Opus per call.
DEFAULT_PROVIDER = "claude-code"
DEFAULT_MODEL = "sonnet"


@dataclass
class RoleConfig:
    provider: str
    model: Optional[str]


@dataclass
class Config:
    roles: dict[str, RoleConfig]
    threshold: float = 8.5
    max_iters: int = 6
    patience: int = 2
    tests_per_round: int = 6
    allow_candidate_code_exec: bool = False
    runs_dir: Path = field(default_factory=lambda: Path("runs"))

    def role(self, name: str) -> RoleConfig:
        return self.roles[name]

    @classmethod
    def from_env(
        cls,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        threshold: Optional[float] = None,
        max_iters: Optional[int] = None,
        patience: Optional[int] = None,
        tests_per_round: Optional[int] = None,
        allow_candidate_code_exec: Optional[bool] = None,
        runs_dir: Optional[Path] = None,
    ) -> "Config":
        base_provider = provider or os.environ.get("PROMETHEUS_PROVIDER") or DEFAULT_PROVIDER
        base_model = model or os.environ.get("PROMETHEUS_MODEL") or DEFAULT_MODEL

        roles: dict[str, RoleConfig] = {}
        for r in ROLES:
            rp = os.environ.get(f"PROMETHEUS_{r.upper()}_PROVIDER") or base_provider
            rm = os.environ.get(f"PROMETHEUS_{r.upper()}_MODEL") or base_model
            roles[r] = RoleConfig(provider=rp, model=rm)

        def _f(env, default):
            v = os.environ.get(env)
            return float(v) if v else default

        def _i(env, default):
            v = os.environ.get(env)
            return int(v) if v else default

        return cls(
            roles=roles,
            threshold=threshold if threshold is not None else _f("PROMETHEUS_THRESHOLD", 8.5),
            max_iters=max_iters if max_iters is not None else _i("PROMETHEUS_MAX_ITERS", 6),
            patience=patience if patience is not None else _i("PROMETHEUS_PATIENCE", 2),
            tests_per_round=tests_per_round
            if tests_per_round is not None
            else _i("PROMETHEUS_TESTS_PER_ROUND", 6),
            allow_candidate_code_exec=bool(allow_candidate_code_exec)
            if allow_candidate_code_exec is not None
            else os.environ.get("PROMETHEUS_ALLOW_CODE_EXEC", "").lower() in ("1", "true", "yes"),
            runs_dir=runs_dir or Path(os.environ.get("PROMETHEUS_RUNS_DIR", "runs")),
        )
