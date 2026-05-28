"""Minimal MCP stdio client (JSON-RPC 2.0 over stdin/stdout), stdlib-only.

The official `mcp` SDK requires Python 3.10+, but this project targets 3.9, so we speak
the protocol directly. Supports the handshake, tools/list, and tools/call for stdio MCP
servers — enough to give API-provider candidates real MCP tool access (the proxy half of
"any foundational model gets MCP"). The claude-code provider doesn't need this; it loads
MCP servers natively.

Server definitions are discovered from ~/.claude.json and a project .mcp.json (stdio only;
remote/OAuth servers are reachable through the claude-code provider, not this proxy).
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


def discover_stdio_servers() -> dict[str, dict[str, Any]]:
    """Return {server_name: {command, args, env, cwd}} for stdio servers in user/project config."""
    servers: dict[str, dict[str, Any]] = {}
    candidates = [Path.home() / ".claude.json", Path.cwd() / ".mcp.json"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        for name, defn in (data.get("mcpServers") or {}).items():
            if defn.get("type", "stdio") != "stdio" or "command" not in defn:
                continue
            servers[name] = {
                "command": defn["command"],
                "args": defn.get("args", []),
                "env": defn.get("env", {}),
                "cwd": defn.get("cwd"),
            }
    return servers


class MCPStdioClient:
    """One spawned stdio MCP server, kept alive for the client's lifetime."""

    def __init__(self, command: str, args: list[str], env: Optional[dict] = None,
                 cwd: Optional[str] = None, timeout: float = 30.0) -> None:
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------ #
    def __enter__(self) -> "MCPStdioClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        full_env = {**os.environ, **{k: str(v) for k, v in self.env.items()}}
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=full_env, cwd=self.cwd,
        )
        self._handshake()

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

    # -- JSON-RPC ------------------------------------------------------- #
    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _send(self, msg: dict) -> None:
        if not self._proc or self._proc.poll() is not None:
            raise MCPError(f"MCP server '{self.command}' is not running")
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _read_response(self, want_id: int) -> dict:
        """Read newline-delimited JSON until we get the response with `want_id`."""
        import time
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"timeout waiting for MCP response id={want_id}")
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if line == "":
                raise MCPError("MCP server closed the connection")
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip stray log output
            if obj.get("id") == want_id:
                if "error" in obj:
                    raise MCPError(f"MCP error: {obj['error']}")
                return obj.get("result", {})
            # otherwise: a notification or other id — ignore

    def _request(self, method: str, params: Optional[dict] = None) -> dict:
        rid = self._next_id()
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self._read_response(rid)

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _handshake(self) -> None:
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "OpenPrometheus", "version": "0.1"},
        })
        self._notify("notifications/initialized")

    # -- API ------------------------------------------------------------ #
    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block))
        text = "\n".join(parts) if parts else json.dumps(result)
        if result.get("isError"):
            return f"ERROR: {text}"
        return text


class MCPProxy:
    """Routes full tool names (mcp__server__tool) to stdio server clients, caching each
    spawned server for the proxy's lifetime. Used by API-provider candidates to execute
    MCP tools. Use as a context manager so servers are torn down."""

    def __init__(self) -> None:
        self.servers = discover_stdio_servers()
        self._clients: dict[str, MCPStdioClient] = {}

    def __enter__(self) -> "MCPProxy":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _split(self, full_name: str) -> tuple[str, str]:
        if not full_name.startswith("mcp__"):
            raise MCPError(f"not an MCP tool name: {full_name}")
        rest = full_name[len("mcp__"):]
        # longest known-server prefix wins (server names may contain '-')
        for srv in sorted(self.servers, key=len, reverse=True):
            if rest.startswith(srv + "__"):
                return srv, rest[len(srv) + 2:]
        # fallback: first "__" split
        srv, _, tool = rest.partition("__")
        return srv, tool

    def _client(self, server: str) -> MCPStdioClient:
        if server not in self._clients:
            if server not in self.servers:
                raise MCPError(f"unknown/unproxiable MCP server: {server}")
            d = self.servers[server]
            c = MCPStdioClient(d["command"], d["args"], d["env"], d.get("cwd"))
            c.start()
            self._clients[server] = c
        return self._clients[server]

    def call(self, full_name: str, arguments: dict[str, Any]) -> str:
        server, tool = self._split(full_name)
        return self._client(server).call_tool(tool, arguments)

    def close(self) -> None:
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()
