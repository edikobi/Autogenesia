"""
Tool for reading context lines around a specific line or pattern in a file.
"""

from pathlib import Path
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

def _escape_xml(text: str) -> str:
    """Escape XML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

def _add_line_numbers(lines: list[str], start: int) -> str:
    """Format lines with right-aligned line numbers."""
    if not lines:
        return ""
    max_num = start + len(lines) - 1
    width = len(str(max_num))
    result = []
    for i, line in enumerate(lines):
        num = start + i
        result.append(f"{num:>{width}} | {line}")
    return "\n".join(result)

def read_line_context_tool(
    file_path: str,
    line_number: Optional[int] = None,
    pattern: Optional[str] = None,
    context_lines: int = 5,
    direction: str = "after",
    project_dir: str = "",
    virtual_fs: Optional[Any] = None,
) -> str:
    """
    Shows context lines around a specific line number or text pattern in a file.
    Resolves content through VFS first, then disk.
    """
    # Security check: prevent path traversal
    if ".." in file_path or file_path.startswith("/"):
        return f"""<!-- ERROR -->
<error>
  <message>Invalid file path: {file_path}. Path must be relative to project root and cannot contain '..'.</message>
</error>"""

    # VFS-first resolution
    content = None
    source = "disk"
    if virtual_fs is not None:
        content = virtual_fs.read_file(file_path)
        if content is not None:
            source = "VFS"

    if content is None:
        full_path = Path(project_dir) / file_path
        if not full_path.exists():
            return f"""<!-- ERROR -->
<error>
  <message>File not found: {file_path}. Check the path and try again.</message>
</error>"""
        try:
            content = full_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            try:
                content = full_path.read_text(encoding='latin-1')
            except Exception as e:
                return f"""<!-- ERROR -->
<error>
  <message>Failed to read file {file_path}: {str(e)}</message>
</error>"""
        except Exception as e:
            return f"""<!-- ERROR -->
<error>
  <message>Failed to read file {file_path}: {str(e)}</message>
</error>"""

    if not content:
        return f"""<!-- ERROR -->
<error>
  <message>File is empty: {file_path}</message>
</error>"""

    lines = content.splitlines()
    total_lines = len(lines)

    # Validation
    if line_number is None and pattern is None:
        return f"""<!-- ERROR -->
<error>
  <message>Either 'line_number' or 'pattern' must be provided.</message>
</error>"""

    # Target line determination
    target_idx = -1
    if line_number is not None:
        if line_number < 1 or line_number > total_lines:
            return f"""<!-- ERROR -->
<error>
  <message>Line number {line_number} is out of range (1-{total_lines}).</message>
</error>"""
        target_idx = line_number - 1
    elif pattern is not None:
        for i, line in enumerate(lines):
            if pattern in line:
                target_idx = i
                break
        if target_idx == -1:
            return f"""<!-- ERROR -->
<error>
  <message>Pattern '{pattern}' not found in {file_path}. Try using 'grep_search' for fuzzy or regex matching.</message>
</error>"""

    # Direction validation
    note = ""
    if direction not in ["before", "after", "both"]:
        note = f"<!-- Note: Invalid direction '{direction}' provided. Falling back to 'after'. -->\n"
        direction = "after"

    # Extract context
    before_indices = []
    after_indices = []

    if direction in ["before", "both"]:
        start = max(0, target_idx - context_lines)
        before_indices = list(range(start, target_idx))

    if direction in ["after", "both"]:
        end = min(total_lines, target_idx + context_lines + 1)
        after_indices = list(range(target_idx + 1, end))

    # Build XML
    source_label = "VFS (staged)" if source == "VFS" else "disk"
    xml_output = [f"<!-- Source: {source_label} -->", note]
    xml_output.append(f'<line_context file="{_escape_xml(file_path)}" source="{source}" direction="{direction}" target_line="{target_idx + 1}">')

    # Before context
    for idx in before_indices:
        xml_output.append(f'  <context line="{idx + 1}">{_escape_xml(lines[idx])}</context>')

    # Target
    xml_output.append(f'  <target line="{target_idx + 1}">{_escape_xml(lines[target_idx])}</target>')

    # After context
    for idx in after_indices:
        xml_output.append(f'  <context line="{idx + 1}">{_escape_xml(lines[idx])}</context>')

    xml_output.append('</line_context>')

    return "\n".join([line for line in xml_output if line.strip() or line.startswith("<!--")])