"""Claude Code host adapter: hooks that enforce isolation, and generators for the play workspace."""

from mafia.harness.tools import MCP_SERVER_NAME

MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"

__all__ = ["MCP_SERVER_NAME", "MCP_TOOL_PREFIX"]
