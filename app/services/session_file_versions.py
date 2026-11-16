"""Session File Versions Tracker.

In-memory storage of the intra-session version history of staged files:
the original disk snapshot plus the full sequence of every staged state for
a single VFS instance (one working session).

Design principles:
- No process-wide singleton — every VFS instance owns its own tracker.
- Nothing is written to disk; all history lives exclusively in memory.
- Thread-safe: every mutation and read is guarded by a ``threading.RLock``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class FileVersion:
    """A single staged version of a file."""

    number: int
    content: str
    change_type: str
    operation: str
    timestamp: str
    lines_added: int
    lines_removed: int


class SessionFileVersionTracker:
    """In-memory tracker of per-file version history for one VFS session."""

    def __init__(self) -> None:
        """Initialize an empty tracker with a re-entrant lock."""
        self._data: Dict[str, dict] = {}
        self._lock = threading.RLock()

    def record_staging(
        self,
        file_path: str,
        original_content: Optional[str],
        new_content: str,
        change_type: str,
        operation: str,
    ) -> None:
        """Record a new staged version of ``file_path``.

        The very first call stores ``original_content`` as the immutable
        session-level snapshot; subsequent calls never overwrite it.
        """
        with self._lock:
            entry = self._data.get(file_path)
            if entry is None:
                entry = {
                    "original_content": original_content,
                    "versions": [],
                }
                self._data[file_path] = entry

            versions: List[FileVersion] = entry["versions"]

            if versions:
                prev_content = versions[-1].content
            elif entry["original_content"] is not None:
                prev_content = entry["original_content"]
            else:
                prev_content = ""

            prev_lines = (prev_content or "").splitlines()
            new_lines = (new_content or "").splitlines()
            prev_set = set(prev_lines)
            new_set = set(new_lines)

            lines_added = sum(1 for line in new_lines if line not in prev_set)
            lines_removed = sum(1 for line in prev_lines if line not in new_set)

            version = FileVersion(
                number=len(versions) + 1,
                content=new_content if new_content is not None else "",
                change_type=str(change_type) if change_type is not None else "",
                operation=operation,
                timestamp=datetime.now().isoformat(),
                lines_added=lines_added,
                lines_removed=lines_removed,
            )
            versions.append(version)

    def get_original(self, file_path: str) -> Optional[str]:
        """Return the original snapshot for ``file_path`` or ``None``."""
        with self._lock:
            entry = self._data.get(file_path)
            if entry is None:
                return None
            return entry["original_content"]

    def get_versions(self, file_path: str) -> List[FileVersion]:
        """Return a copy of the recorded versions list (empty if untracked)."""
        with self._lock:
            entry = self._data.get(file_path)
            if entry is None:
                return []
            return list(entry["versions"])

    def has_file(self, file_path: str) -> bool:
        """Return True if ``file_path`` has any version history."""
        with self._lock:
            return file_path in self._data

    def get_tracked_files(self) -> List[str]:
        """Return the sorted list of tracked file paths."""
        with self._lock:
            return sorted(self._data.keys())

    def reset(self) -> None:
        """Clear the entire history. Called exclusively from VFS.reset_version_history."""
        with self._lock:
            self._data.clear()