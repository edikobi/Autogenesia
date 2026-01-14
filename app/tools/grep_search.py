# app/tools/grep_search.py
"""
Grep-like full-text search across project files with VFS support.
"""

import os
import re
import fnmatch
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Set
import logging
from dataclasses import dataclass
from app.utils.file_types import FileTypeDetector

logger = logging.getLogger(__name__)


@dataclass
class GrepMatch:
    """Single grep match result"""
    file_path: str
    line_number: int
    line_content: str
    matches_in_file: int
    context_before: List[str] = None
    context_after: List[str] = None


def grep_search_tool(
    pattern: str,
    project_dir: str,
    use_regex: bool = False,
    case_sensitive: bool = False,
    file_pattern: Optional[str] = None,
    path: Optional[str] = None,
    max_files: int = 100,
    max_matches_per_file: int = 20,
    max_total_matches: int = 50,
    context_lines: int = 2,
    virtual_fs: Optional[Any] = None,
) -> str:
    """
    Grep-like search across project files with VFS support.
    
    Args:
        pattern: Text pattern to search for
        project_dir: Project root directory
        use_regex: Whether pattern is regex
        case_sensitive: Case-sensitive search
        file_pattern: Glob pattern for files
        path: Subdirectory to search in
        max_files: Max files to process
        max_matches_per_file: Max matches per file
        max_total_matches: Total matches limit
        context_lines: Context lines around match
        virtual_fs: VirtualFileSystem instance for staged files
        
    Returns:
        XML-formatted search results
    """
    logger.info(f"grep_search: pattern='{pattern}', use_regex={use_regex}")
    
    # Build search root path
    search_root = Path(project_dir)
    if path:
        search_root = search_root / path
    
    if not search_root.exists():
        return _format_error(f"Search path not found: {search_root}")
    
    # Compile search pattern
    if use_regex:
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            compiled_pattern = re.compile(pattern, flags)
        except re.error as e:
            return _format_error(f"Invalid regex pattern: {e}")
    else:
        # Escape special characters for literal search
        escaped_pattern = re.escape(pattern)
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled_pattern = re.compile(escaped_pattern, flags)
    
    # Collect files to search
    disk_files = []
    vfs_files = {}  # path -> content
    
    try:
        # 1. Collect from VFS (staged files) FIRST
        if virtual_fs is not None:
            # Get all staged files from VFS - правильно используем метод из VFS
            for rel_path in virtual_fs.get_staged_files():
                # Get content from VFS
                content = virtual_fs.read_file(rel_path)
                if content is not None:
                    vfs_files[rel_path] = content
        
        # 2. Collect from disk
        disk_files = _collect_disk_files(
            search_root=search_root,
            project_dir=project_dir,
            file_pattern=file_pattern,
            max_files=max_files,
            exclude_vfs_files=set(vfs_files.keys())  # Exclude files already in VFS
        )
            
    except Exception as e:
        return _format_error(f"Error collecting files: {e}")
    
    # Search in files
    matches = []
    total_matches = 0
    files_matched = 0
    
    # Search in VFS files (staged changes) FIRST
    for rel_path, content in vfs_files.items():
        if total_matches >= max_total_matches:
            break
        
        file_matches = _search_in_content(
            content=content,
            compiled_pattern=compiled_pattern,
            file_path=rel_path,
            context_lines=context_lines,
            max_matches_per_file=max_matches_per_file
        )
        
        if file_matches:
            for match in file_matches:
                match.matches_in_file = len(file_matches)
            
            matches.extend(file_matches)
            total_matches += len(file_matches)
            files_matched += 1
    
    # Search in disk files (already excluded VFS files)
    for file_path in disk_files:
        if total_matches >= max_total_matches:
            break
        
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            
            file_matches = _search_in_content(
                content=content,
                compiled_pattern=compiled_pattern,
                file_path=str(file_path.relative_to(project_dir)),
                context_lines=context_lines,
                max_matches_per_file=max_matches_per_file
            )
            
            if file_matches:
                for match in file_matches:
                    match.matches_in_file = len(file_matches)
                
                matches.extend(file_matches)
                total_matches += len(file_matches)
                files_matched += 1
                
        except Exception as e:
            logger.warning(f"Error reading {file_path}: {e}")
            continue
    
    # Format results as XML
    return _format_results(
        pattern=pattern,
        is_regex=use_regex,
        case_sensitive=case_sensitive,
        total_files_searched=len(disk_files) + len(vfs_files),
        total_files_matched=files_matched,
        total_matches=total_matches,
        matches=matches,
        context_lines=context_lines,
        vfs_files_count=len(vfs_files)
    )


def _collect_disk_files(
    search_root: Path,
    project_dir: str,
    file_pattern: Optional[str],
    max_files: int,
    exclude_vfs_files: Set[str]
) -> List[Path]:
    """Collect files from disk, respecting symlinks and file types."""
    files_to_search = []
    detector = FileTypeDetector()
    
    try:
        if file_pattern:
            # Use glob pattern
            for file_path in search_root.rglob(file_pattern):
                if _should_process_file(file_path, detector, exclude_vfs_files, project_dir):
                    files_to_search.append(file_path)
                    if len(files_to_search) >= max_files:
                        break
        else:
            # Search all text files
            for file_path in search_root.rglob('*'):
                if _should_process_file(file_path, detector, exclude_vfs_files, project_dir):
                    files_to_search.append(file_path)
                    if len(files_to_search) >= max_files:
                        break
    except Exception as e:
        logger.error(f"Error collecting disk files: {e}")
        raise
    
    return files_to_search


def _should_process_file(
    file_path: Path, 
    detector: FileTypeDetector, 
    exclude_vfs_files: Set[str],
    project_dir: str
) -> bool:
    """Check if file should be processed."""
    # Skip symlinks to avoid recursion
    if file_path.is_symlink():
        return False
    
    if not file_path.is_file():
        return False
    
    # Check if file is excluded (already in VFS)
    try:
        rel_path = str(file_path.relative_to(project_dir)).replace('\\', '/')
        if rel_path in exclude_vfs_files:
            return False
    except ValueError:
        # File is not under project directory
        return False
    
    # Check if it's a text file using FileTypeDetector
    file_type = detector.detect(str(file_path))
    return detector.is_text_based(file_type)


def _search_in_content(
    content: str,
    compiled_pattern: re.Pattern,
    file_path: str,
    context_lines: int,
    max_matches_per_file: int
) -> List[GrepMatch]:
    """Search pattern in content and return matches."""
    lines = content.splitlines()
    file_matches = []
    
    for i, line in enumerate(lines, 1):
        if compiled_pattern.search(line):
            # Get context
            start_ctx = max(0, i - 1 - context_lines)
            end_ctx = min(len(lines), i + context_lines)
            
            context_before = lines[start_ctx:i-1] if start_ctx < i-1 else []
            context_after = lines[i:end_ctx] if i < end_ctx else []
            
            match = GrepMatch(
                file_path=file_path,
                line_number=i,
                line_content=line,
                matches_in_file=0,  # Will update later
                context_before=context_before,
                context_after=context_after
            )
            file_matches.append(match)
            
            if len(file_matches) >= max_matches_per_file:
                break
    
    return file_matches


def _format_results(
    pattern: str,
    is_regex: bool,
    case_sensitive: bool,
    total_files_searched: int,
    total_files_matched: int,
    total_matches: int,
    matches: List[GrepMatch],
    context_lines: int,
    vfs_files_count: int = 0
) -> str:
    """Format grep results as XML"""
    
    xml_parts = []
    xml_parts.append(f'<grep_results pattern="{pattern}" regex="{is_regex}" '
                     f'case_sensitive="{case_sensitive}" '
                     f'files_searched="{total_files_searched}" '
                     f'files_matched="{total_files_matched}" '
                     f'total_matches="{total_matches}" '
                     f'context_lines="{context_lines}" '
                     f'vfs_files="{vfs_files_count}">')
    
    # Group by file
    matches_by_file = {}
    for match in matches:
        if match.file_path not in matches_by_file:
            matches_by_file[match.file_path] = []
        matches_by_file[match.file_path].append(match)
    
    for file_path, file_matches in matches_by_file.items():
        xml_parts.append(f'  <file path="{_escape_xml(file_path)}" matches="{len(file_matches)}">')
        
        for match in file_matches:
            xml_parts.append(f'    <match line="{match.line_number}" file_matches="{match.matches_in_file}">')
            
            # Add context before
            if match.context_before:
                for i, ctx_line in enumerate(match.context_before, start=1):
                    xml_parts.append(f'      <context_before line="{match.line_number - len(match.context_before) + i - 1}">'
                                   f'{_escape_xml(ctx_line)}</context_before>')
            
            # The matching line
            xml_parts.append(f'      <line>{_escape_xml(match.line_content)}</line>')
            
            # Add context after
            if match.context_after:
                for i, ctx_line in enumerate(match.context_after, start=1):
                    xml_parts.append(f'      <context_after line="{match.line_number + i}">'
                                   f'{_escape_xml(ctx_line)}</context_after>')
            
            xml_parts.append('    </match>')
        
        xml_parts.append('  </file>')
    
    xml_parts.append('</grep_results>')
    
    # Add summary
    summary = [
        f"<!-- GREP SEARCH RESULTS -->",
        f"<!-- Pattern: {pattern} ({'regex' if is_regex else 'text'}, "
        f"{'case-sensitive' if case_sensitive else 'case-insensitive'}) -->",
        f"<!-- Searched {total_files_searched} files ({vfs_files_count} from VFS), "
        f"found {total_matches} matches in {total_files_matched} files -->",
        ""
    ]
    
    return "\n".join(summary + xml_parts)


def _escape_xml(text: str) -> str:
    """Escape XML special characters"""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))


def _format_error(message: str) -> str:
    """Format error message"""
    return f"""<!-- GREP SEARCH ERROR -->
<error>
  <message>{message}</message>
  <suggestion>Check your search pattern and file filters.</suggestion>
</error>"""