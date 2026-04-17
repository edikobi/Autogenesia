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

# Language to extensions mapping
LANGUAGE_EXTENSIONS = {
    "python": [".py"],
    "go": [".go"],
    "java": [".java"],
    "javascript": [".js", ".jsx", ".mjs"],
    "typescript": [".ts", ".tsx"],
    "csharp": [".cs"],
    "ruby": [".rb"],
    "php": [".php"],
    "rust": [".rs"],
    "cpp": [".cpp", ".cc", ".cxx", ".h", ".hpp"],
    "c": [".c", ".h"],
}

def _get_extensions_for_language(language: Optional[str]) -> Optional[Set[str]]:
    """Return set of extensions for a language (including dot), or None if language not supported."""
    if not language:
        return None
    language_lower = language.lower()
    if language_lower in LANGUAGE_EXTENSIONS:
        return set(LANGUAGE_EXTENSIONS[language_lower])
    return None

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
    is_regex: bool = False,
    case_sensitive: bool = False,
    file_pattern: Optional[str] = None,
    path: Optional[str] = None,
    max_files: int = 200,
    max_matches_per_file: int = 50,
    max_total_matches: int = 50,
    context_lines: int = 2,
    virtual_fs: Optional[Any] = None,
    language: Optional[str] = None,
    multiline: bool = False,
) -> str:
    """
    Grep-like search across project files with VFS support.
    """
    logger.info(f"grep_search: pattern='{pattern}', is_regex={is_regex}")

    # Build search root path
    search_root = Path(project_dir)
    if path:
        search_root = search_root / path

    if not search_root.exists():
        return _format_error(f"Search path not found: {search_root}")

    # Prepare regex flags
    regex_flags = (re.MULTILINE | re.DOTALL) if is_regex else 0
    if not case_sensitive:
        regex_flags |= re.IGNORECASE

    # Compile search pattern
    if is_regex:
        try:
            compiled_pattern = re.compile(pattern, regex_flags)
        except re.error as e:
            return _format_error(f"Invalid regex pattern: {e}")
    else:
        escaped_pattern = re.escape(pattern)
        compiled_pattern = re.compile(escaped_pattern, regex_flags)

    # Determine extensions for language filter
    language_extensions = _get_extensions_for_language(language)

    # First attempt: collect files with max_files limit
    files_to_search = _collect_files(
        project_dir=project_dir,
        search_root=search_root,
        path=path,
        language_extensions=language_extensions,
        file_pattern=file_pattern,
        max_files=max_files,
        virtual_fs=virtual_fs,
    )

    # Perform search
    matches, total_matches_found, file_total_matches = _search_in_files(
        files=files_to_search,
        compiled_pattern=compiled_pattern,
        max_matches_per_file=max_matches_per_file,
        max_total_matches=max_total_matches,
        context_lines=context_lines,
        project_dir=project_dir,
        multiline=multiline
    )

    auto_regex_note = ""
    # AUTO-REGEX LOGIC: If no matches were found and it was a literal search containing metacharacters
    if not matches and not is_regex:
        metacharacters = ['|', '*', '+', '(', '[', '^', '$']
        if any(char in pattern for char in metacharacters):
            logger.info(f"grep_search: No literal matches found for '{pattern}'. Retrying as regex.")
            try:
                # Retry with is_regex=True
                # If pattern contains '|', it's almost certainly intended as a regex
                retry_pattern = re.compile(pattern, regex_flags | re.MULTILINE | re.DOTALL)
                matches, total_matches_found, file_total_matches = _search_in_files(
                    files=files_to_search,
                    compiled_pattern=retry_pattern,
                    max_matches_per_file=max_matches_per_file,
                    max_total_matches=max_total_matches,
                    context_lines=context_lines,
                    project_dir=project_dir,
                    multiline=True # Force multiline for auto-regex to be effective
                )
                if matches:
                    is_regex = True # Update for format_results
                    auto_regex_note = "<!-- NOTE: Literal search found 0 results. Automatically retried as REGEX search (with multiline support) because pattern contains metacharacters. -->\n"
            except re.error:
                pass # If retry pattern is still invalid, just keep 0 results

    expanded_note = ""
    final_files = files_to_search
    # If no matches and there might be more files, try unlimited
    if not matches and max_files is not None:
        all_files = _collect_files(
            project_dir=project_dir,
            search_root=search_root,
            path=path,
            language_extensions=language_extensions,
            file_pattern=file_pattern,
            max_files=None,
            virtual_fs=virtual_fs,
        )
        if len(all_files) > len(files_to_search):
            # Re-search without file limit
            matches, total_matches_found, file_total_matches = _search_in_files(
                files=all_files,
                compiled_pattern=compiled_pattern,
                max_matches_per_file=max_matches_per_file,
                max_total_matches=max_total_matches,
                context_lines=context_lines,
                project_dir=project_dir,
                multiline=multiline
            )
            expanded_note = f"<!-- NOTE: No matches found in first {max_files} files. Expanded search to all {len(all_files)} files. -->\n"
            final_files = all_files

    # Count VFS files in the final files list (those with content not None)
    vfs_count = sum(1 for _, content in final_files if content is not None)

    # Determine if total matches exceeded limit
    exceeded_total = total_matches_found > max_total_matches

    # Build XML result
    result = _format_results(
        pattern=pattern,
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        total_files_searched=len(final_files),
        total_files_matched=len({m.file_path for m in matches}),
        total_matches=total_matches_found,
        matches=matches[:max_total_matches],
        context_lines=context_lines,
        vfs_files_count=vfs_count,
        exceeded_total=exceeded_total,
        file_total_matches=file_total_matches,
        max_matches_per_file=max_matches_per_file
    )
    return expanded_note + auto_regex_note + result

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
        rel_path = str(file_path.relative_to(Path(project_dir))).replace('\\', '/')
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
) -> tuple[List[GrepMatch], int]:
    """
    Search pattern in content and return matches and total matches count.
    Returns (matches_list, total_matches_count).
    """
    lines = content.splitlines()
    file_matches = []
    total_matches = 0
    for i, line in enumerate(lines, 1):
        if compiled_pattern.search(line):
            total_matches += 1
            if len(file_matches) < max_matches_per_file:
                # Get context
                start_ctx = max(0, i - 1 - context_lines)
                end_ctx = min(len(lines), i + context_lines)
                context_before = lines[start_ctx:i-1] if start_ctx < i-1 else []
                context_after = lines[i:end_ctx] if i < end_ctx else []
                match = GrepMatch(
                    file_path=file_path,
                    line_number=i,
                    line_content=line,
                    matches_in_file=0,  # will be updated later
                    context_before=context_before,
                    context_after=context_after
                )
                file_matches.append(match)
    return file_matches, total_matches

def _search_in_content_multiline(
    content: str,
    compiled_pattern: re.Pattern,
    file_path: str,
    context_lines: int,
    max_matches_per_file: int
) -> tuple[List[GrepMatch], int]:
    """
    Search pattern in full content (multiline) and return matches.
    """
    file_matches = []
    total_matches = 0
    lines = content.splitlines(keepends=True)
    
    # Pre-calculate line start offsets to map absolute offsets to line numbers
    line_offsets = []
    current_offset = 0
    for line in lines:
        line_offsets.append(current_offset)
        current_offset += len(line)
    
    import bisect
    
    for match_obj in compiled_pattern.finditer(content):
        total_matches += 1
        if len(file_matches) < max_matches_per_file:
            start_pos = match_obj.start()
            # Find line number for start_pos (0-indexed line index)
            line_idx = bisect.bisect_right(line_offsets, start_pos) - 1
            line_number = line_idx + 1
            
            # Extract actual line content (might be multiline)
            matched_text = match_obj.group(0)
            
            # Get context lines
            start_ctx_idx = max(0, line_idx - context_lines)
            end_ctx_idx = min(len(lines), line_idx + 1 + context_lines)
            
            context_before = [l.strip('\n\r') for l in lines[start_ctx_idx:line_idx]]
            context_after = [l.strip('\n\r') for l in lines[line_idx+1:end_ctx_idx]]
            
            match = GrepMatch(
                file_path=file_path,
                line_number=line_number,
                line_content=matched_text.strip('\n\r'),
                matches_in_file=0,
                context_before=context_before,
                context_after=context_after
            )
            file_matches.append(match)
            
    return file_matches, total_matches

def _collect_files(
    project_dir: str,
    search_root: Path,
    path: Optional[str],
    language_extensions: Optional[Set[str]],
    file_pattern: Optional[str],
    max_files: Optional[int],
    virtual_fs: Optional[Any],
) -> List[Tuple[str, Optional[str]]]:
    result = []
    vfs_files_dict = {}
    detector = FileTypeDetector()

    # Все файлы, которые есть в staging (включая удалённые)
    staged_paths = set(virtual_fs.get_staged_files()) if virtual_fs else set()

    # 1. Collect from VFS (staged changes)
    if virtual_fs is not None:
        for rel_path in virtual_fs.get_staged_files():
            # Apply path filter
            if path:
                abs_vfs_path = Path(project_dir) / rel_path
                try:
                    abs_vfs_path.relative_to(search_root)
                except ValueError:
                    continue
            # Apply language filter
            if language_extensions:
                ext = Path(rel_path).suffix
                if ext not in language_extensions:
                    continue
            # Apply file_pattern filter (full path glob)
            if file_pattern:
                # Если паттерн содержит слэш, проверяем полный путь, иначе только имя файла
                if '/' in file_pattern:
                    if not fnmatch.fnmatch(rel_path, file_pattern):
                        continue
                else:
                    if not fnmatch.fnmatch(Path(rel_path).name, file_pattern):
                        continue            
            
            # Check text type
            file_type = detector.detect(rel_path)
            if not detector.is_text_based(file_type):
                continue
            content = virtual_fs.read_file(rel_path)
            if content is not None:
                vfs_files_dict[rel_path] = content

    # 2. Collect from disk, excluding files already in VFS (including deleted)
    disk_files = []
    for disk_path in search_root.rglob('*'):
        if not disk_path.is_file():
            continue
        try:
            rel_path = str(disk_path.relative_to(project_dir)).replace('\\', '/')
        except ValueError:
            continue
        # Пропускаем любые файлы, которые есть в staging (включая удалённые)
        if rel_path in staged_paths:
            continue
        # Apply language filter
        if language_extensions:
            ext = disk_path.suffix
            if ext not in language_extensions:
                continue
            
            # Apply file_pattern filter (full path glob)
            if file_pattern:
                # Если паттерн содержит слэш, проверяем полный путь, иначе только имя файла
                if '/' in file_pattern:
                    if not fnmatch.fnmatch(rel_path, file_pattern):
                        continue
                else:
                    if not fnmatch.fnmatch(Path(rel_path).name, file_pattern):
                        continue        
        
        # Check text type
        file_type = detector.detect(str(disk_path))
        if not detector.is_text_based(file_type):
            continue
        disk_files.append(rel_path)

    # 3. Build final list: VFS first (more important), then disk, up to max_files
    for rel_path, content in vfs_files_dict.items():
        if max_files is not None and len(result) >= max_files:
            break
        result.append((rel_path, content))
    if max_files is None or len(result) < max_files:
        remaining = None if max_files is None else max_files - len(result)
        for rel_path in disk_files[:remaining]:
            result.append((rel_path, None))

    return result

def _search_in_files(
    files: List[Tuple[str, Optional[str]]],
    compiled_pattern: re.Pattern,
    max_matches_per_file: int,
    max_total_matches: int,
    context_lines: int,
    project_dir: str,
    multiline: bool = False
) -> Tuple[List[GrepMatch], int, Dict[str, int]]:
    """
    Perform search across files.
    Returns (matches, total_matches_found, file_total_matches).
    file_total_matches: dict file_path -> total matches count in that file (including those not shown).
    """
    matches = []
    total_found = 0
    file_total_matches = {}
    for rel_path, content in files:
        # Read if needed
        if content is None:
            full_path = Path(project_dir) / rel_path
            try:
                content = full_path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
        # Search in content
        if multiline:
            file_matches, total_file_matches = _search_in_content_multiline(
                content, compiled_pattern, rel_path, context_lines, max_matches_per_file
            )
        else:
            file_matches, total_file_matches = _search_in_content(
                content, compiled_pattern, rel_path, context_lines, max_matches_per_file
            )
        if total_file_matches > 0:
            file_total_matches[rel_path] = total_file_matches
        if file_matches:
            # Update matches_in_file for each match
            for match in file_matches:
                match.matches_in_file = total_file_matches
            matches.extend(file_matches)
            total_found += len(file_matches)
            # Stop if we have enough matches (optional, but we break early to save time)
            if total_found >= max_total_matches:
                break
    return matches, total_found, file_total_matches

def _format_results(
    pattern: str,
    is_regex: bool,
    case_sensitive: bool,
    total_files_searched: int,
    total_files_matched: int,
    total_matches: int,
    matches: List[GrepMatch],
    context_lines: int,
    vfs_files_count: int = 0,
    exceeded_total: bool = False,
    file_total_matches: Optional[Dict[str, int]] = None,
    max_matches_per_file: int = 50
) -> str:
    """Format grep results as XML, including limit notes."""
    xml_parts = []
    # Add explanatory notes if limits exceeded
    if exceeded_total:
        xml_parts.append(f'<!-- NOTE: Total matches exceed limit, only showing first {len(matches)} of {total_matches} matches. -->')
    if file_total_matches:
        for file_path, total in file_total_matches.items():
            if total > max_matches_per_file:
                xml_parts.append(f'<!-- NOTE: File "{file_path}" has {total} total matches, showing only {max_matches_per_file} matches. -->')

    xml_parts.append(f'<grep_results pattern="{pattern}" is_regex="{is_regex}" '
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
            # context before
            if match.context_before:
                for i, ctx_line in enumerate(match.context_before, start=1):
                    xml_parts.append(f'      <context_before line="{match.line_number - len(match.context_before) + i - 1}">'
                                     f'{_escape_xml(ctx_line)}</context_before>')
            # matching line
            xml_parts.append(f'      <line>{_escape_xml(match.line_content)}</line>')
            # context after
            if match.context_after:
                for i, ctx_line in enumerate(match.context_after, start=1):
                    xml_parts.append(f'      <context_after line="{match.line_number + i}">'
                                     f'{_escape_xml(ctx_line)}</context_after>')
            xml_parts.append('    </match>')
        xml_parts.append('  </file>')

    xml_parts.append('</grep_results>')
    # Add summary comments
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