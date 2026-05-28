"""Claude Code provider — shells out to the `claude` CLI.

This is the DEFAULT, zero-setup backend: it uses the machine's existing Claude Code
auth (OAuth / keychain), so it needs NO API key. We deliberately avoid `--bare`,
which would force ANTHROPIC_API_KEY and skip OAuth.

complete():  `claude -p --output-format json --system-prompt <sys>` (clean, replaces the
             default system prompt) — used by meta-agents and tool-less agents.
run_agent(): `claude -p --append-system-prompt <sys> --allowedTools <mapped>` — lets the
             candidate use Claude Code's own tools (Bash/Read/Write/WebFetch) headlessly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Optional

from .base import Provider, ProviderResponse, ProviderError, ToolExecutor

# Map portable tool names -> Claude Code tool grants.
_TOOL_MAP = {
    "python_exec": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "web_fetch": "WebFetch",
}
# Tools that mutate state / run code — require explicit opt-in before headless execution.
_RISKY_TOOLS = {"python_exec", "write_file"}


class ClaudeCodeProvider(Provider):
    name = "claude-code"

    def __init__(self, model: Optional[str] = None, timeout: int = 900, **kwargs: Any) -> None:
        super().__init__(model=model, **kwargs)
        self.timeout = timeout
        if not shutil.which("claude"):
            raise ProviderError(
                "`claude` CLI not found on PATH. Install Claude Code, or use "
                "--provider anthropic/openai with an API key."
            )

    # ------------------------------------------------------------------ #
    def _run(self, cmd: list[str], prompt: str) -> ProviderResponse:
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"claude -p timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            raise ProviderError(
                f"claude -p exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"claude -p returned non-JSON output: {proc.stdout[:500]!r}") from exc

        if env.get("is_error"):
            raise ProviderError(f"claude -p reported error: {env.get('result') or env}")

        text = env.get("result", "")
        usage = env.get("usage", {}) or {}
        return ProviderResponse(
            text=text,
            raw=env,
            usage={
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cost_usd": env.get("total_cost_usd"),
                "model": next(iter(env.get("modelUsage", {})), None),
            },
        )

    # ------------------------------------------------------------------ #
    def complete(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
    ) -> ProviderResponse:
        cmd = ["claude", "-p", "--output-format", "json"]
        m = model or self.model
        if m:
            cmd += ["--model", m]
        if system:
            cmd += ["--system-prompt", system]
        return self._run(cmd, user)

    # ------------------------------------------------------------------ #
    def run_agent(
        self,
        system: str,
        user: str,
        *,
        tools: Optional[list[str]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        model: Optional[str] = None,
        max_steps: int = 12,
        max_tokens: int = 4096,
        workdir: Optional[str] = None,
        allow_risky: bool = False,
    ) -> ProviderResponse:
        tools = tools or []
        if not tools:
            return self.complete(system, user, model=model, max_tokens=max_tokens)

        granted = [_TOOL_MAP[t] for t in tools if t in _TOOL_MAP]
        risky = bool(set(tools) & _RISKY_TOOLS)

        # Claude Code runs its OWN tools headlessly; we don't need tool_executor here.
        cmd = ["claude", "-p", "--output-format", "json"]
        m = model or self.model
        if m:
            cmd += ["--model", m]
        # Append (not replace) so the candidate keeps Claude Code's tool-use machinery.
        cmd += ["--append-system-prompt", system]
        if granted:
            cmd += ["--allowedTools", ",".join(granted)]
        cmd += ["--max-turns", str(max_steps)]

        # Permission mode: code/file-mutation needs an explicit opt-in. Without it we run
        # in a mode where such tools are denied (the candidate still produces a best-effort
        # answer), keeping headless execution safe by default.
        if risky and allow_risky:
            cmd += ["--permission-mode", "bypassPermissions"]
        else:
            cmd += ["--permission-mode", "acceptEdits"]

        if workdir:
            cmd += ["--add-dir", workdir]

        return self._run(cmd, user)
