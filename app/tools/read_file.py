# app/tools/read_file.py
"""
Read File Tool - Reads project files with XML wrapping.

Features:
- Full file content with metadata
- Optional line numbers
- XML wrapper for proper formatting
- Token counting
- File type detection
"""

from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass

from app.utils.xml_wrapper import XMLWrapper, FileContent
from app.utils.token_counter import TokenCounter
from app.utils.file_types import FileTypeDetector

logger = logging.getLogger(__name__)


@dataclass
class ReadFileResult:
    """Result of read_file operation"""
    success: bool
    content: str  # XML-wrapped content or error message
    file_path: str
    tokens: int
    lines: int
    file_type: str
    error: Optional[str] = None


def read_file_tool(
    file_path: str,
    project_dir: str,
    include_line_numbers: bool = True,
    max_tokens: int = 50000,
) -> str:
    """
    Read a file from the project with XML wrapping.
    
    This tool provides full file content wrapped in XML format with:
    - File path and type metadata
    - Token count
    - Optional line numbers for easy reference
    - CDATA wrapping for safe content handling
    
    Args:
        file_path: Path relative to project root
        project_dir: Absolute path to project root
        include_line_numbers: Add line numbers to content
        max_tokens: Maximum tokens allowed (truncate if exceeded)
        
    Returns:
        XML-wrapped file content or error message
    """
    logger.info(f"read_file_tool: Reading {file_path}")
    
    # Validate inputs
    if not file_path:
        return _format_error("file_path is required")
    
    if not project_dir:
        return _format_error("project_dir is required")
    
    # Build full path and validate
    full_path = Path(project_dir) / file_path
    
    # Security check: prevent path traversal
    try:
        full_path = full_path.resolve()
        project_path = Path(project_dir).resolve()
        if not str(full_path).startswith(str(project_path)):
            return _format_error(f"Access denied: path outside project directory")
    except Exception as e:
        return _format_error(f"Invalid path: {e}")
    
    # Check file exists
    if not full_path.exists():
        return _format_error(f"File not found: {file_path}")
    
    if not full_path.is_file():
        return _format_error(f"Not a file: {file_path}")
    
    # Read file
    try:
        content = full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Try with latin-1 fallback
        try:
            content = full_path.read_text(encoding="latin-1")
        except Exception as e:
            return _format_error(f"Cannot read file (encoding error): {e}")
    except Exception as e:
        return _format_error(f"Error reading file: {e}")
    
    # Detect file type
    detector = FileTypeDetector()
    file_type = detector.detect(str(full_path))
    
    # Count tokens
    counter = TokenCounter()
    tokens = counter.count(content)
    
    # Check token limit
    if tokens > max_tokens:
        logger.warning(f"File {file_path} exceeds token limit ({tokens} > {max_tokens}), truncating")
        content = _truncate_to_tokens(content, max_tokens, counter)
        tokens = max_tokens
    
    # Count lines
    lines = content.count('\n') + 1
    
    # Create XML wrapper
    wrapper = XMLWrapper()
    
    file_content = FileContent(
        path=file_path,
        file_type=file_type,
        content=content,
        tokens=tokens,
        encoding="utf-8"
    )
    
    # Wrap in XML
    xml_output = wrapper.wrap_file(file_content, include_line_numbers=include_line_numbers)
    
    # Add summary header
    result_parts = []
    result_parts.append(f"<!-- File: {file_path} -->")
    result_parts.append(f"<!-- Type: {file_type} | Lines: {lines} | Tokens: {tokens} -->")
    result_parts.append("")
    result_parts.append(xml_output)
    
    logger.info(f"read_file_tool: Successfully read {file_path} ({tokens} tokens, {lines} lines)")
    
    return "\n".join(result_parts)


def read_file_raw(
    file_path: str,
    project_dir: str,
    include_line_numbers: bool = True,
) -> ReadFileResult:
    """
    Read file and return structured result (not XML-wrapped).
    
    Useful for internal processing where XML wrapping is not needed.
    """
    if not file_path or not project_dir:
        return ReadFileResult(
            success=False,
            content="",
            file_path=file_path or "",
            tokens=0,
            lines=0,
            file_type="unknown",
            error="file_path and project_dir are required"
        )
    
    full_path = Path(project_dir) / file_path
    
    # Security check
    try:
        full_path = full_path.resolve()
        project_path = Path(project_dir).resolve()
        if not str(full_path).startswith(str(project_path)):
            return ReadFileResult(
                success=False,
                content="",
                file_path=file_path,
                tokens=0,
                lines=0,
                file_type="unknown",
                error="Access denied: path outside project directory"
            )
    except Exception as e:
        return ReadFileResult(
            success=False,
            content="",
            file_path=file_path,
            tokens=0,
            lines=0,
            file_type="unknown",
            error=f"Invalid path: {e}"
        )
    
    if not full_path.exists():
        return ReadFileResult(
            success=False,
            content="",
            file_path=file_path,
            tokens=0,
            lines=0,
            file_type="unknown",
            error=f"File not found: {file_path}"
        )
    
    try:
        content = full_path.read_text(encoding="utf-8")
    except Exception as e:
        return ReadFileResult(
            success=False,
            content="",
            file_path=file_path,
            tokens=0,
            lines=0,
            file_type="unknown",
            error=f"Error reading file: {e}"
        )
    
    detector = FileTypeDetector()
    file_type = detector.detect(str(full_path))
    
    counter = TokenCounter()
    tokens = counter.count(content)
    lines = content.count('\n') + 1
    
    if include_line_numbers:
        content = _add_line_numbers(content)
    
    return ReadFileResult(
        success=True,
        content=content,
        file_path=file_path,
        tokens=tokens,
        lines=lines,
        file_type=file_type
    )


def _format_error(message: str) -> str:
    """Format error message in XML-like structure"""
    return f"""<!-- ERROR -->
<error>
  <message>{message}</message>
  <suggestion>Check the file path and ensure it exists in the project directory.</suggestion>
</error>"""


def _add_line_numbers(content: str) -> str:
    """Add line numbers to content"""
    lines = content.split('\n')
    max_width = len(str(len(lines)))
    numbered = [f"{i+1:>{max_width}} | {line}" for i, line in enumerate(lines)]
    return '\n'.join(numbered)


def _truncate_to_tokens(content: str, max_tokens: int, counter: TokenCounter) -> str:
    """Truncate content to approximately max_tokens"""
    lines = content.split('\n')
    result_lines = []
    current_tokens = 0
    
    for line in lines:
        line_tokens = counter.count(line)
        if current_tokens + line_tokens > max_tokens:
            result_lines.append("... [truncated due to token limit] ...")
            break
        result_lines.append(line)
        current_tokens += line_tokens
    
    return '\n'.join(result_lines)