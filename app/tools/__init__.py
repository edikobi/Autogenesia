# app/tools/__init__.py
"""
Tools module for AI Code Agent.

Provides tools that Orchestrator can use:
- read_file: Read project files with XML wrapping
- search_code: Search for functions/classes in project index
- web_search: Search the internet and get relevant content
"""

from app.tools.tool_definitions import (
    ORCHESTRATOR_TOOLS,
    READ_FILE_TOOL,
    SEARCH_CODE_TOOL,
    WEB_SEARCH_TOOL,
)
from app.tools.tool_executor import execute_tool, ToolExecutor
from app.tools.read_file import read_file_tool
from app.tools.search_code import search_code_tool
from app.tools.web_search import web_search_tool

__all__ = [
    # Tool definitions
    "ORCHESTRATOR_TOOLS",
    "READ_FILE_TOOL",
    "SEARCH_CODE_TOOL",
    "WEB_SEARCH_TOOL",
    # Executor
    "execute_tool",
    "ToolExecutor",
    # Individual tools
    "read_file_tool",
    "search_code_tool",
    "web_search_tool",
]