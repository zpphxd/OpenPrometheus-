"""ToolRegistry — binds tool names to implementations for a given working directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sandbox

# Provider-neutral JSON-schema definitions for the built-in tools. Providers adapt these
# to their own tool-format (Anthropic input_schema, OpenAI function.parameters).
BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "python_exec": {
        "name": "python_exec",
        "description": "Execute a Python 3 snippet in a sandbox and return stdout/stderr.",
        "input_schema": {"type": "object", "properties": {"code": {"type": "string"}},
                         "required": ["code"]},
    },
    "read_file": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the working directory.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": ["path"]},
    },
    "write_file": {
        "name": "write_file",
        "description": "Write a UTF-8 text file in the working directory.",
        "input_schema": {"type": "object",
                         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                         "required": ["path", "content"]},
    },
    "web_fetch": {
        "name": "web_fetch",
        "description": "Fetch the text content of a URL.",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                         "required": ["url"]},
    },
}


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
