"""Tool registry — the four guarded tools exposed over MCP."""

from __future__ import annotations

from mcp_toolkit.config import Settings
from mcp_toolkit.server.search import SearchProvider
from mcp_toolkit.server.tools.db_query import create_db_query_tool
from mcp_toolkit.server.tools.read_file import create_read_file_tool
from mcp_toolkit.server.tools.types import ToolDef
from mcp_toolkit.server.tools.web_search import create_web_search_tool
from mcp_toolkit.server.tools.write_file import create_write_file_tool

__all__ = ["ToolDef", "build_tools"]


def build_tools(settings: Settings, provider: SearchProvider) -> list[ToolDef]:
    """Build the four-tool registry for the MCP server."""
    return [
        create_web_search_tool(provider),
        create_db_query_tool(str(settings.db_path)),
        create_read_file_tool(settings.sandbox_root, settings.read_file_max_bytes),
        create_write_file_tool(settings.sandbox_root, settings.write_file_max_bytes),
    ]
