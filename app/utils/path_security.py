# app/utils/path_security.py
"""
Path Security — Restricts AI agent access to sensitive files.

Forbidden:
- .env files (including .env.local, .env.production, .env.development, etc.)
- .pyright_vfs_cache/ directory (entire folder and all contents)
"""

from __future__ import annotations
import os
from typing import Optional, List

# ── Forbidden filename exact matches ──────────────────────────────
FORBIDDEN_FILENAMES = {".env"}

# ── Forbidden filename prefixes (.env.* variants) ─────────────────
FORBIDDEN_FILENAME_PREFIXES = (".env.",)

# ── Forbidden directory names (entire folder is off-limits) ───────
FORBIDDEN_DIRNAMES = {
    ".pyright_vfs_cache",
}


def is_forbidden_path(file_path: str) -> bool:
    """
    Check if a file path is forbidden for AI agent access.

    Works with both relative and absolute paths.
    Checks basename (for .env) and all path components (for dirs).

    Args:
        file_path: Relative or absolute file path

    Returns:
        True if access should be denied
    """
    if not file_path:
        return False

    # Normalize separators and strip leading/trailing slashes
    normalized = file_path.replace("\\", "/").strip("/")

    # Get basename (file name)
    basename = os.path.basename(normalized)

    # Check exact filename match (.env)
    if basename in FORBIDDEN_FILENAMES:
        return True

    # Check filename prefix (.env.local, .env.production, etc.)
    for prefix in FORBIDDEN_FILENAME_PREFIXES:
        if basename.startswith(prefix):
            return True

    # Check if any path component is a forbidden directory
    parts = [p for p in normalized.split("/") if p]
    for part in parts:
        if part in FORBIDDEN_DIRNAMES:
            return True

    return False


def get_forbidden_reason(file_path: str) -> Optional[str]:
    """
    Return a human-readable reason why a path is forbidden.
    Returns None if the path is NOT forbidden.
    """
    if not file_path:
        return None

    normalized = file_path.replace("\\", "/").strip("/")
    basename = os.path.basename(normalized)

    if basename == ".env":
        return "Access to .env files is restricted (may contain secrets)"

    for prefix in FORBIDDEN_FILENAME_PREFIXES:
        if basename.startswith(prefix):
            return f"Access to '{basename}' is restricted (may contain secrets)"

    parts = [p for p in normalized.split("/") if p]
    for part in parts:
        if part in FORBIDDEN_DIRNAMES:
            return f"Access to '{part}/' directory is restricted"

    return None


def filter_forbidden_paths(paths: List[str]) -> List[str]:
    """Filter out forbidden paths from a list. Returns clean list."""
    return [p for p in paths if not is_forbidden_path(p)]