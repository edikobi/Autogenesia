# app/tools/tool_executor.py
"""
Tool Executor - Central dispatcher for all tools.

Routes tool calls to appropriate implementations and handles errors.
"""

from __future__ import annotations
import json
import logging
from typing import Dict, Any, Optional, Callable
from pathlib import Path
from app.advice.advice_loader import execute_get_advice

from app.tools.read_file import read_file_tool
from app.tools.search_code import search_code_tool
from app.tools.web_search import web_search_tool
from app.tools.run_project_tests import run_project_tests  # NEW
from app.tools.grep_search import grep_search_tool
from app.tools.file_relations import show_file_relations_tool
from app.services.python_chunker import SmartPythonChunker

from app.tools.dependency_manager import (
    list_installed_packages_tool,
    install_dependency_tool,
    search_pypi_tool,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Executes tools by name with provided arguments.
    
    Supports:
    - read_file: Read project files
    - search_code: Search code in index
    - web_search: Search the internet
    
    Can be extended with custom tools.
    """
    
    def __init__(
        self,
        project_dir: str,
        index: Optional[Dict[str, Any]] = None,
        virtual_fs: Optional[Any] = None,  # NEW: VirtualFileSystem instance
    ):
        """
        Initialize tool executor.
        
        Args:
            project_dir: Path to project root (for file operations)
            index: Project semantic index (for code search)
        """
        self.project_dir = project_dir
        self.index = index or {}
        self.virtual_fs = virtual_fs  # NEW
        self._custom_tools: Dict[str, Callable] = {}
    
    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Execute a tool by name.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as dict
            
        Returns:
            Tool output as string (usually XML-formatted)
        """
        logger.info(f"Executing tool: {tool_name} with args: {list(arguments.keys())}")
        
        try:
            # Check custom tools first
            if tool_name in self._custom_tools:
                return self._custom_tools[tool_name](**arguments)
            
            
            # Built-in tools
            if tool_name == "read_code_chunk": # Добавили блок
                return self._execute_read_code_chunk(arguments)
            elif tool_name == "read_file":
                return self._execute_read_file(arguments)
           
            if tool_name == "read_file":
                return self._execute_read_file(arguments)
            
            elif tool_name == "search_code":
                return self._execute_search_code(arguments)
            
            elif tool_name == "web_search":
                return self._execute_web_search(arguments)
            
            elif tool_name == "get_advice":
                return self._execute_get_advice(arguments)

            elif tool_name == "run_project_tests":  # NEW
                return self._execute_run_project_tests(arguments)
            
            elif tool_name == "list_installed_packages":
                return self._execute_list_installed_packages(arguments)
            elif tool_name == "install_dependency":
                return self._execute_install_dependency(arguments)
            elif tool_name == "search_pypi":
                return self._execute_search_pypi(arguments)
            elif tool_name == "grep_search": 
                return self._execute_grep_search(arguments)
            elif tool_name == "show_file_relations":
                 return self._execute_show_file_relations(arguments)
            else:
                return self._format_error(f"Unknown tool: {tool_name}")
                
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            return self._format_error(f"Tool execution failed: {e}")
    
    
    def _execute_read_code_chunk(self, arguments: Dict[str, Any]) -> str:
        """
        Execute read_code_chunk tool using SmartPythonChunker.
        
        UPDATED: Checks VirtualFileSystem first for staged files.
        """
        file_path = arguments.get("file_path", "")
        chunk_name = arguments.get("chunk_name", "")
        
        # Check VFS first for staged files
        content = None
        source = "disk"
        
        if self.virtual_fs is not None:
            staged_content = self.virtual_fs.read_file(file_path)
            if staged_content is not None:
                content = staged_content
                source = "VFS"
                logger.info(f"read_code_chunk: Reading '{file_path}' from VFS (staged)")
        
        # Fall back to disk if not in VFS
        if content is None:
            full_path = Path(self.project_dir) / file_path
            if not full_path.exists():
                return self._format_error(f"File not found: {file_path}")
            try:
                content = full_path.read_text(encoding='utf-8')
                source = "disk"
            except Exception as e:
                return self._format_error(f"Failed to read file: {e}")
        
        try:
            # Parse content with chunker
            import tempfile
            import os
            
            # SmartPythonChunker needs a file path, so write to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(content)
                temp_path = f.name
            
            try:
                chunker = SmartPythonChunker()
                chunks = chunker.chunk_file(temp_path)
            finally:
                os.unlink(temp_path)
            
            # Find target chunk
            target_chunk = next((c for c in chunks if c.name == chunk_name), None)
            
            if not target_chunk:
                return self._format_error(f"Chunk '{chunk_name}' not found in {file_path}")
            
            # Format response
            return f"""<!-- Source: {source} -->
<code_chunk>
<file>{file_path}</file>
<name>{target_chunk.name}</name>
<type>{target_chunk.kind}</type>
<lines>{target_chunk.start_line}-{target_chunk.end_line}</lines>
<content>
{target_chunk.content}
</content>
</code_chunk>
"""
        except Exception as e:
            logger.error(f"Error reading chunk: {e}")
            return self._format_error(f"Failed to read chunk: {e}")
    
    
    
    def _execute_read_file(self, arguments: Dict[str, Any]) -> str:
        """
        Execute read_file tool.
        
        UPDATED: Checks VirtualFileSystem first for staged files.
        This ensures Orchestrator sees the latest generated code during feedback loops.
        """
        file_path = arguments.get("file_path", "")
        include_line_numbers = arguments.get("include_line_numbers", True)
        
        # Check VFS first for staged files
        if self.virtual_fs is not None:
            # Check if file is staged (created or modified in current session)
            staged_content = self.virtual_fs.read_file(file_path)
            if staged_content is not None:
                # File exists in VFS - return it with XML formatting
                logger.info(f"read_file: Reading '{file_path}' from VFS (staged)")
                
                # Count lines and tokens
                lines = staged_content.count('\n') + 1
                tokens = len(staged_content) // 4  # Approximate
                
                # Add line numbers if requested
                if include_line_numbers:
                    content_lines = staged_content.split('\n')
                    max_width = len(str(len(content_lines)))
                    numbered = [f"{i+1:>{max_width}} | {line}" for i, line in enumerate(content_lines)]
                    display_content = '\n'.join(numbered)
                else:
                    display_content = staged_content
                
                # Determine file type
                if file_path.endswith('.py'):
                    file_type = 'code/python'
                elif file_path.endswith('.json'):
                    file_type = 'data/json'
                else:
                    file_type = 'other'
                
                # Format as XML (matching read_file_tool output format)
                result = f"""<!-- File: {file_path} -->
<!-- Type: {file_type} | Lines: {lines} | Tokens: {tokens} -->
<!-- Source: VFS (staged changes) -->

<file path="{file_path}" type="{file_type}" tokens="{tokens}" encoding="utf-8">
<content><![CDATA[
{display_content}
]]></content>
</file>"""
                return result
        
        # Fall back to disk read
        logger.info(f"read_file: Reading '{file_path}' from disk")
        return read_file_tool(
            file_path=file_path,
            project_dir=self.project_dir,
            include_line_numbers=include_line_numbers
        )
    
    
    
    def _execute_search_code(self, arguments: Dict[str, Any]) -> str:
        """Execute search_code tool"""
        query = arguments.get("query", "")
        search_type = arguments.get("search_type", "all")
        
        return search_code_tool(
            query=query,
            index=self.index,
            project_dir=self.project_dir,
            search_type=search_type
        )
    
    
    def _execute_grep_search(self, arguments: Dict[str, Any]) -> str:
        """Execute grep_search tool with VFS support."""
        pattern = arguments.get("pattern", "")
        
        if not pattern:
            return self._format_error("pattern is required")
        
        return grep_search_tool(
            pattern=pattern,
            project_dir=self.project_dir,
            use_regex=arguments.get("use_regex", False),
            case_sensitive=arguments.get("case_sensitive", False),
            file_pattern=arguments.get("file_pattern"),
            path=arguments.get("path"),
            max_files=arguments.get("max_files", 100),
            max_matches_per_file=arguments.get("max_matches_per_file", 20),
            max_total_matches=arguments.get("max_total_matches", 50),
            context_lines=arguments.get("context_lines", 2),
            virtual_fs=self.virtual_fs  # Pass VFS for staged files
        )
    
    
    
    def _execute_show_file_relations(self, arguments: Dict[str, Any]) -> str:
        """Показывает связи файла в проекте."""
        file_path = arguments.get("file_path", "")
        
        if not file_path:
            return self._format_error("file_path is required")
        
        return show_file_relations_tool(
            file_path=file_path,
            project_dir=self.project_dir,
            virtual_fs=self.virtual_fs,
            include_tests=arguments.get("include_tests", True),
            include_siblings=arguments.get("include_siblings", True),
            max_relations=arguments.get("max_relations", 20)
        )    
        
    def _execute_web_search(self, arguments: Dict[str, Any]) -> str:
        """Execute web_search tool"""
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 10)
        region = arguments.get("region", "wt-wt")
        
        return web_search_tool(
            query=query,
            max_results=max_results,
            region=region
        )

    def _execute_get_advice(self, arguments: Dict[str, Any]) -> str:
        """Execute get_advice tool to load methodological thinking frameworks"""
        advice_ids = arguments.get("advice_ids", "")
        if not advice_ids:
            return self._format_error("advice_ids parameter is required")
        return execute_get_advice(advice_ids)
    
    def _execute_run_project_tests(self, arguments: Dict[str, Any]) -> str:
        """
        Execute run_project_tests tool.
        
        NEW: Runs unittest tests on VirtualFileSystem staged files.
        """
        test_path = arguments.get("test_path", "")
        chunk_name = arguments.get("chunk_name")
        timeout_sec = min(arguments.get("timeout_sec", 30), 60)  # Cap at 60 sec
        
        if not test_path:
            return self._format_error("test_path is required")
        
        return run_project_tests(
            project_dir=self.project_dir,
            test_path=test_path,
            virtual_fs=self.virtual_fs,
            chunk_name=chunk_name,
            timeout_sec=timeout_sec,
        )

    def _execute_list_installed_packages(self, arguments: Dict[str, Any]) -> str:
        """Execute list_installed_packages tool with VFS python_path."""
        python_path = None
        
        if self.virtual_fs is not None:
            python_path = self.virtual_fs.get_project_python()
        
        return list_installed_packages_tool(
            project_dir=self.project_dir,
            python_path=python_path
        )
    
    
    
    def _execute_install_dependency(self, arguments: Dict[str, Any]) -> str:
        """Execute install_dependency tool with VFS python_path."""
        import_name = arguments.get("import_name")
        
        if not import_name:
            return self._format_error("Missing required argument: import_name")
        
        version = arguments.get("version")
        python_path = None
        
        if self.virtual_fs is not None:
            python_path = self.virtual_fs.get_project_python()
        
        return install_dependency_tool(
            project_dir=self.project_dir,
            import_name=import_name,
            version=version,
            python_path=python_path
        )
    
    
    def _execute_search_pypi(self, arguments: Dict[str, Any]) -> str:
        """Execute search_pypi tool"""
        query = arguments.get("query", "")
        
        if not query:
            return self._format_error("query is required")
        
        return search_pypi_tool(query=query)

    
    def register_tool(self, name: str, func: Callable) -> None:
        """
        Register a custom tool.
        
        Args:
            name: Tool name (must be unique)
            func: Tool function (must accept **kwargs and return str)
        """
        if name in ["read_file", "search_code", "web_search"]:
            raise ValueError(f"Cannot override built-in tool: {name}")
        
        self._custom_tools[name] = func
        logger.info(f"Registered custom tool: {name}")
    
    def update_index(self, index: Dict[str, Any]) -> None:
        """Update the project index"""
        self.index = index
    
    def update_virtual_fs(self, virtual_fs: Any) -> None:
        """Update the VirtualFileSystem instance"""
        self.virtual_fs = virtual_fs
    
    def _format_error(self, message: str) -> str:
        """Format error message"""
        return f"""<!-- ERROR -->
<error>
  <message>{message}</message>
</error>"""


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    project_dir: str,
    virtual_fs: Optional[Any] = None,  # NEW
    index: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Execute a tool (convenience function).
    
    Creates a ToolExecutor instance and executes the tool.
    For repeated calls, prefer creating a ToolExecutor instance directly.
    
    Args:
        tool_name: Name of the tool
        arguments: Tool arguments
        project_dir: Path to project root
        index: Project semantic index
        
    Returns:
        Tool output as string
    """
    executor = ToolExecutor(project_dir=project_dir, index=index)
    return executor.execute(tool_name, arguments)


def parse_tool_call(tool_call: Dict[str, Any]) -> tuple:
    """
    Parse a tool call from LLM response.
    
    Args:
        tool_call: Tool call dict from LLM response
        
    Returns:
        Tuple of (tool_name, arguments_dict, tool_call_id)
    """
    func_info = tool_call.get("function", {})
    tool_name = func_info.get("name", "")
    arguments_str = func_info.get("arguments", "{}")
    tool_call_id = tool_call.get("id", "")
    
    try:
        arguments = json.loads(arguments_str)
    except json.JSONDecodeError:
        arguments = {}
    
    return tool_name, arguments, tool_call_id