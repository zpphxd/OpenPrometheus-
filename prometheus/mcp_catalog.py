"""MCP tool catalog + tiered safety gate.

Discovers which MCP tools a built agent MAY be granted, and classifies each as `safe`
(read-only-ish: search/list/get/read) or `sensitive` (sends/writes/pays/books/deploys).

Tiered gate (per the chosen policy):
  - MCP is OFF unless the user names servers/tool-patterns via --mcp-allow (or
    PROMETHEUS_MCP_ALLOW). This both scopes access and avoids spawning heavy stdio
    servers nobody asked for.
  - Within allowed servers, SAFE tools are auto-eligible for the Architect to grant.
  - SENSITIVE tools are eligible only with --mcp-allow-sensitive (or an exact-name allow).

stdio servers are enumerated live (and are `proxiable` — API providers can call them via
mcp_client). Remote/OAuth servers can't be enumerated or proxied here; they're grantable
by explicit name and only execute through the claude-code provider.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Optional

from .mcp_client import discover_stdio_servers, MCPStdioClient, MCPError

_SENSITIVE_KW = (
    "send", "email", "mail", "message", "sms", "post", "publish", "deploy", "pay",
    "purchase", "buy", "order", "book", "checkout", "transfer", "delete", "remove",
    "drop", "revoke", "write", "create", "update", "edit", "modify", "move", "schedule",
    "cancel", "upload", "share", "invite", "charge", "refund", "approve", "execute",
)
_SAFE_KW = (
    "search", "list", "get", "read", "fetch", "query", "find", "lookup", "detail",
    "status", "info", "show", "view", "describe", "summary", "summarize", "analyze",
    "compare", "estimate", "recommend", "check",
)


def classify_sensitivity(name: str, description: str = "") -> str:
    n = name.lower()
    for kw in _SENSITIVE_KW:
        if kw in n:
            return "sensitive"
    for kw in _SAFE_KW:
        if n.startswith(kw) or f"_{kw}" in n or f"-{kw}" in n:
            return "safe"
    # fail-safe: unknown verbs are treated as sensitive
    return "sensitive"


@dataclass
class ToolEntry:
    full_name: str          # mcp__<server>__<tool>
    server: str
    tool: str
    description: str
    sensitivity: str        # "safe" | "sensitive"
    proxiable: bool         # True for stdio (API providers can call via mcp_client)
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class Catalog:
    entries: dict[str, ToolEntry] = field(default_factory=dict)  # full_name -> entry
    notes: list[str] = field(default_factory=list)

    def grantable(self) -> list[ToolEntry]:
        return list(self.entries.values())

    def is_grantable(self, full_name: str) -> bool:
        return full_name in self.entries

    def proxiable_entries(self) -> dict[str, ToolEntry]:
        return {k: v for k, v in self.entries.items() if v.proxiable}

    def menu_text(self) -> str:
        if not self.entries:
            return "(no MCP tools available — none allowed for this run)"
        lines = []
        for e in sorted(self.entries.values(), key=lambda x: x.full_name):
            tag = "" if e.sensitivity == "safe" else " [SENSITIVE]"
            via = "" if e.proxiable else " (claude-code only)"
            lines.append(f"- {e.full_name}{tag}{via}: {e.description[:120]}")
        return "\n".join(lines)


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _parse_allow(allow: list[str]) -> tuple[set[str], list[str]]:
    """Split allow entries into bare server names and full tool patterns (mcp__...)."""
    servers, tool_patterns = set(), []
    for a in allow:
        a = a.strip()
        if not a:
            continue
        if a.startswith("mcp__"):
            tool_patterns.append(a)
        else:
            servers.add(a)
    return servers, tool_patterns


def build_catalog(
    allow: Optional[list[str]] = None,
    allow_sensitive: bool = False,
    logger=None,
) -> Catalog:
    """Build the grantable catalog under the tiered gate. Empty if `allow` is empty."""
    log = logger or (lambda m: None)
    cat = Catalog()
    allow = [a for a in (allow or []) if a]
    if not allow:
        cat.notes.append("MCP disabled (no --mcp-allow specified)")
        return cat

    allow_servers, tool_patterns = _parse_allow(allow)
    stdio = discover_stdio_servers()

    # Servers we should enumerate: those named directly, plus those referenced by a
    # tool-pattern that targets a known stdio server.
    enum_servers = set(allow_servers)
    for pat in tool_patterns:
        # pat like mcp__server__tool or mcp__server__*
        rest = pat[len("mcp__"):]
        for srv in stdio:
            if rest.startswith(srv + "__"):
                enum_servers.add(srv)

    for srv in sorted(enum_servers):
        if srv not in stdio:
            cat.notes.append(f"server '{srv}' is not a local stdio server; "
                             f"its tools are grantable by exact name and run via claude-code only")
            continue
        defn = stdio[srv]
        try:
            with MCPStdioClient(defn["command"], defn["args"], defn["env"], defn.get("cwd")) as c:
                tools = c.list_tools()
        except (MCPError, Exception) as exc:  # noqa: BLE001 — one bad server shouldn't abort
            log(f"  ! could not enumerate MCP server '{srv}': {exc}")
            cat.notes.append(f"server '{srv}' enumeration failed: {exc}")
            continue
        for t in tools:
            full = f"mcp__{srv}__{t['name']}"
            # if explicit tool patterns exist, the tool must match one of them
            if tool_patterns and not _matches_any(full, tool_patterns) and srv not in allow_servers:
                continue
            sens = classify_sensitivity(t["name"], t.get("description", ""))
            if sens == "sensitive" and not allow_sensitive and not _matches_any(full, tool_patterns):
                cat.notes.append(f"withheld sensitive tool {full} (use --mcp-allow-sensitive)")
                continue
            cat.entries[full] = ToolEntry(
                full_name=full, server=srv, tool=t["name"],
                description=t.get("description", ""), sensitivity=sens, proxiable=True,
                input_schema=t.get("inputSchema", {}) or {},
            )

    # explicit remote tool patterns (non-stdio), grantable by exact name, claude-code only
    for pat in tool_patterns:
        if "*" in pat:
            continue
        rest = pat[len("mcp__"):]
        srv = rest.split("__", 1)[0]
        if srv in stdio or pat in cat.entries:
            continue
        tool = rest.split("__", 1)[1] if "__" in rest else rest
        sens = classify_sensitivity(tool)
        if sens == "sensitive" and not allow_sensitive:
            cat.notes.append(f"withheld sensitive remote tool {pat} (use --mcp-allow-sensitive)")
            continue
        cat.entries[pat] = ToolEntry(
            full_name=pat, server=srv, tool=tool, description="(remote MCP tool)",
            sensitivity=sens, proxiable=False,
        )

    for note in cat.notes:
        log(f"  · {note}")
    return cat
