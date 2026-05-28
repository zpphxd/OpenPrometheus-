"""Tool implementations the runner exposes to API-provider candidates.

The claude-code provider uses Claude Code's own built-in tools instead; this registry
backs the anthropic/openai providers' run_agent() loops.
"""

from .registry import ToolRegistry

__all__ = ["ToolRegistry"]
