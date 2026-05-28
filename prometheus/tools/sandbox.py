"""Bounded execution primitives for candidate tool use.

SECURITY: `python_exec` runs candidate-authored code. It is gated behind an explicit
opt-in (Config.allow_candidate_code_exec) and always runs in a throwaway working
directory with a hard timeout. Network is NOT sandboxed at the OS level here, so only
enable code exec for tasks/sources you trust. The default OpenPrometheus run grants no
tools at all unless the Architect decides the task requires them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_python(code: str, workdir: Path, timeout: int = 20) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: execution exceeded {timeout}s timeout"
    out = proc.stdout or ""
    err = proc.stderr or ""
    parts = []
    if out:
        parts.append(f"STDOUT:\n{out}")
    if err:
        parts.append(f"STDERR:\n{err}")
    if proc.returncode != 0:
        parts.append(f"(exit code {proc.returncode})")
    return "\n".join(parts) if parts else "(no output)"


def _resolve_inside(workdir: Path, rel: str) -> Path:
    """Resolve `rel` against workdir, refusing to escape it."""
    target = (workdir / rel).resolve()
    workdir = workdir.resolve()
    if workdir not in target.parents and target != workdir:
        raise ValueError(f"path {rel!r} escapes the working directory")
    return target


def read_text(workdir: Path, rel: str, max_chars: int = 20000) -> str:
    target = _resolve_inside(workdir, rel)
    if not target.exists():
        return f"ERROR: file not found: {rel}"
    return target.read_text(errors="replace")[:max_chars]


def write_text(workdir: Path, rel: str, content: str) -> str:
    target = _resolve_inside(workdir, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} chars to {rel}"


def fetch_url(url: str, max_chars: int = 20000, timeout: int = 15) -> str:
    import urllib.request

    if not url.lower().startswith(("http://", "https://")):
        return "ERROR: only http(s) URLs are allowed"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenPrometheus/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_chars * 4)
        return data.decode("utf-8", errors="replace")[:max_chars]
    except Exception as exc:
        return f"ERROR fetching {url}: {exc}"
