"""ToolRegistry — binds tool names to implementations for a given working directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sandbox


class ToolRegistry:
    def __init__(self, workdir: Path, allow_code_exec: bool = False) -> None:
        self.workdir = Path(workdir)
        self.allow_code_exec = allow_code_exec

    def execute(self, name: str, args: dict[str, Any]) -> str:
        if name == "python_exec":
            if not self.allow_code_exec:
                return "ERROR: code execution is disabled (run with --allow-code-exec to enable)"
            return sandbox.run_python(args.get("code", ""), self.workdir)
        if name == "read_file":
            return sandbox.read_text(self.workdir, args.get("path", ""))
        if name == "write_file":
            return sandbox.write_text(self.workdir, args.get("path", ""), args.get("content", ""))
        if name == "web_fetch":
            return sandbox.fetch_url(args.get("url", ""))
        return f"ERROR: unknown tool {name!r}"
