import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from datetime import datetime

logger = logging.getLogger(__name__)

# Standard patterns to ignore (matching VirtualFileSystem)
IGNORE_PATTERNS = {
    '__pycache__', '.git', '.svn', '.hg',
    'node_modules', 'venv', '.venv', 'env',
    '.ai-agent', '.idea', '.vscode',
    '*.pyc', '*.pyo', '*.egg-info',
}

def list_files_tool(
    directory_path: str = ".",
    recursive: bool = True,
    project_dir: str = "",
    virtual_fs: Any = None,
    show_hidden: bool = False
) -> str:
    """
    Lists files and directories in the project.
    
    Args:
        directory_path: Directory to list (relative to project root)
        recursive: Whether to list subdirectories recursively
        project_dir: Path to project root
        virtual_fs: Optional VirtualFileSystem instance to include staged files
        show_hidden: Whether to show hidden files (starting with .)
        
    Returns:
        XML-formatted string with file and directory information
    """
    if not project_dir:
        return "<!-- ERROR: project_dir is required -->"

    root_path = Path(project_dir).resolve()
    target_dir = (root_path / directory_path).resolve()

    if not target_dir.exists():
        return f"<!-- ERROR: Directory not found: {directory_path} -->"
    
    if not str(target_dir).startswith(str(root_path)):
        return "<!-- ERROR: Access denied (outside of project root) -->"

    items: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()

    # 1. Scan physical disk
    try:
        if recursive:
            for root, dirs, files in os.walk(target_dir):
                # Filter directories in-place
                dirs[:] = [d for d in dirs if not _should_ignore(d)]
                if not show_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith('.')]

                rel_root = os.path.relpath(root, root_path).replace('\\', '/')
                if rel_root == '.':
                    rel_root = ""

                for d in dirs:
                    path = os.path.join(rel_root, d).replace('\\', '/')
                    items.append({
                        "name": d,
                        "path": path,
                        "type": "dir",
                        "source": "disk"
                    })
                    seen_paths.add(path)

                for f in files:
                    if _should_ignore(f):
                        continue
                    if not show_hidden and f.startswith('.'):
                        continue
                        
                    path = os.path.join(rel_root, f).replace('\\', '/')
                    full_path = Path(root) / f
                    try:
                        stat = full_path.stat()
                        items.append({
                            "name": f,
                            "path": path,
                            "type": "file",
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "source": "disk"
                        })
                    except Exception:
                        items.append({
                            "name": f,
                            "path": path,
                            "type": "file",
                            "source": "disk"
                        })
                    seen_paths.add(path)
        else:
            for entry in os.scandir(target_dir):
                if _should_ignore(entry.name):
                    continue
                if not show_hidden and entry.name.startswith('.'):
                    continue
                
                rel_path = os.path.relpath(entry.path, root_path).replace('\\', '/')
                if entry.is_dir():
                    items.append({
                        "name": entry.name,
                        "path": rel_path,
                        "type": "dir",
                        "source": "disk"
                    })
                else:
                    try:
                        stat = entry.stat()
                        items.append({
                            "name": entry.name,
                            "path": rel_path,
                            "type": "file",
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "source": "disk"
                        })
                    except Exception:
                        items.append({
                            "name": entry.name,
                            "path": rel_path,
                            "type": "file",
                            "source": "disk"
                        })
                seen_paths.add(rel_path)
    except Exception as e:
        logger.error(f"Error scanning disk: {e}")

    # 2. Add staged files from VFS
    if virtual_fs:
        try:
            staged_files = virtual_fs.get_staged_files()
            for rel_path in staged_files:
                # Check if it should be in the listed directory
                if not rel_path.startswith(directory_path.rstrip('./').replace('\\', '/')):
                     # Handle root case
                     if directory_path not in ('.', './', ''):
                         continue
                
                if rel_path in seen_paths:
                    # Mark as modified in VFS
                    for item in items:
                        if item["path"] == rel_path:
                            item["source"] = "disk + VFS (staged)"
                            break
                else:
                    # New file in VFS
                    name = os.path.basename(rel_path)
                    items.append({
                        "name": name,
                        "path": rel_path,
                        "type": "file",
                        "source": "VFS (new)"
                    })
        except Exception as e:
            logger.error(f"Error reading VFS files: {e}")

    # Sort items: dirs first, then files, both alphabetically
    items.sort(key=lambda x: (x["type"] != "dir", x["path"].lower()))

    # Build XML response
    res = [f'<!-- Directory listing for: {directory_path} -->']
    res.append(f'<!-- Project Root: {project_dir} -->')
    res.append(f'<!-- Total items: {len(items)} -->')
    res.append('')
    res.append('<file_list>')
    
    for item in items:
        attr_parts = [
            f'type="{item["type"]}"',
            f'path="{item["path"]}"',
            f'source="{item["source"]}"'
        ]
        if "size" in item:
            attr_parts.append(f'size="{item["size"]}"')
        if "modified" in item:
            attr_parts.append(f'modified="{item["modified"]}"')
            
        res.append(f'  <item {" ".join(attr_parts)} />')
        
    res.append('</file_list>')
    
    return "\n".join(res)

def _should_ignore(name: str) -> bool:
    """Check if file or directory should be ignored based on IGNORE_PATTERNS"""
    import fnmatch
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False
