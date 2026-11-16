"""Lazy exports for the MCP subsystem (PEP 562).

Mirrors the ``app/tui/__init__.py`` pattern: symbols are imported lazily in
``__getattr__`` so an absent optional dependency never breaks the package.
"""

from __future__ import annotations

import importlib

__all__ = [
    "get_mcp_manager",
    "MCPManager",
    "get_mcp_tool_definitions",
    "dispatch_mcp_tool",
    "load_servers",
    "save_servers",
]

_LAZY_EXPORTS = {
    "get_mcp_manager": ("app.mcp.manager", "get_mcp_manager"),
    "MCPManager": ("app.mcp.manager", "MCPManager"),
    "get_mcp_tool_definitions": (
        "app.mcp.definitions",
        "get_mcp_tool_definitions",
    ),
    "dispatch_mcp_tool": ("app.mcp.definitions", "dispatch_mcp_tool"),
    "load_servers": ("app.mcp.config_store", "load_servers"),
    "save_servers": ("app.mcp.config_store", "save_servers"),
}


def __getattr__(name: str):
    """Lazily import and cache a public MCP symbol (PEP 562)."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value