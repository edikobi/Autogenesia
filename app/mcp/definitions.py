"""Bridge between the MCP subsystem and the rest of the project.

This is the only module orchestrator / pre_filter / tool_executor import,
so all interactions go through the isolated MCP namespace.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _format_error(message: str) -> str:
    """Format an error string — identical to ``ToolExecutor._format_error``."""
    return f"""<!-- ERROR -->
<error>
  <message>{message}</message>
</error>"""


def get_mcp_tool_definitions(role: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return OpenAI tool schemas for all connected MCP servers.

    Lazy import + broad ``except`` so the caller can call this unconditionally
    even when the MCP subsystem (or the ``mcp`` package) is absent.
    Calling without arguments (``role=None``) returns all tools — backward compatible.
    ``role`` filters servers by their ``target_agents`` scope.
    """
    try:
        from app.mcp.manager import get_mcp_manager

        return get_mcp_manager().list_definitions(role=role)
    except Exception as exc:
        logger.debug(f"get_mcp_tool_definitions failed: {exc}")
        return []



def dispatch_mcp_tool(
    name: str, arguments: Dict[str, Any], role: Optional[str] = None
) -> str:
    """Dispatch an MCP tool call by its prefixed name. Never raises.

    Unknown / foreign names — or servers out of scope for ``role`` — return
    an error string in the same XML shape used by ``ToolExecutor._format_error``.
    Existing 2-argument callers (``main.py`` history, tests) remain valid.
    """
    if not isinstance(name, str) or not name.startswith("mcp__"):
        return _format_error(f"Not an MCP tool: {name}")

    try:
        from app.mcp.manager import get_mcp_manager

        manager = get_mcp_manager()
        return manager.call_tool(name, arguments or {}, role=role)
    except Exception as exc:
        logger.warning(f"dispatch_mcp_tool failed for {name}: {exc}")
        return _format_error(str(exc))

