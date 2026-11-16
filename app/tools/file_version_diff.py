"""Read-only unified diff for file versions staged in the current VFS session.

Provides the ``get_file_version_diff`` tool: it compares the on-disk
original file, every sequential staged version, and the current disk state
WITHOUT writing anything. The only external I/O is reading the file from
disk in ``disk_vs_latest`` mode — all other data comes from the in-memory
``SessionFileVersionTracker`` attached to the VFS.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


def _unified_diff(old: str, new: str, fromfile: str, tofile: str) -> str:
    """Return a unified diff between two strings, or '(no differences)'."""
    diff = difflib.unified_diff(
        (old or "").splitlines(keepends=True),
        (new or "").splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
    )
    result = "".join(diff)
    return result if result else "(no differences)"


def _format_error(message: str) -> str:
    """Render the canonical ``<!-- ERROR -->`` XML envelope."""
    return (
        "<!-- ERROR -->\n"
        "<error>\n"
        f"  <message>{message}</message>\n"
        "</error>"
    )


def file_version_diff_tool(
    file_path: str,
    mode: str = "previous_vs_latest",
    project_dir: str = "",
    virtual_fs: Any = None,
) -> str:
    """Build a unified diff between tracked versions of ``file_path``.

    Args:
        file_path: Relative path (normalized to forward slashes internally).
        mode: One of ``previous_vs_latest``, ``disk_vs_latest``, ``history``.
        project_dir: Project root, used only for ``disk_vs_latest``.
        virtual_fs: VirtualFileSystem exposing ``version_tracker``.

    Returns:
        XML document; a canonical ``<!-- ERROR -->`` XML on fatal errors.
    """
    if not file_path:
        return _format_error("file_path is required")

    normalized = str(file_path).replace("\\", "/")

    tracker = getattr(virtual_fs, "version_tracker", None) if virtual_fs is not None else None
    if virtual_fs is None or tracker is None:
        return _format_error("VirtualFileSystem or version tracker is not available")

    if not tracker.has_file(normalized):
        return (
            f"<file_version_diff mode=\"{escape(mode)}\" "
            f"file_path=\"{escape(normalized)}\" status=\"no_history\">\n"
            f"  <message>No version history for this file in the current session</message>\n"
            f"</file_version_diff>"
        )

    try:
        versions = tracker.get_versions(normalized)
        original = tracker.get_original(normalized)

        if mode == "previous_vs_latest":
            if len(versions) >= 2:
                left = versions[-2]
                right = versions[-1]
                diff_text = _unified_diff(
                    left.content,
                    right.content,
                    f"v{left.number}",
                    f"v{right.number}",
                )
                label_left = f"v{left.number}"
                label_right = f"v{right.number}"
            elif len(versions) == 1:
                v = versions[0]
                left_content = original if original is not None else ""
                diff_text = _unified_diff(left_content, v.content, "original", f"v{v.number}")
                label_left = "original"
                label_right = f"v{v.number}"
            else:
                return (
                    f"<file_version_diff mode=\"previous_vs_latest\" "
                    f"file_path=\"{escape(normalized)}\" status=\"no_versions\">\n"
                    f"  <message>No versions recorded for this file</message>\n"
                    f"</file_version_diff>"
                )

            return (
                f"<file_version_diff mode=\"previous_vs_latest\" "
                f"file_path=\"{escape(normalized)}\">\n"
                f"  <from>{escape(label_left)}</from>\n"
                f"  <to>{escape(label_right)}</to>\n"
                f"  <diff>{escape(diff_text)}</diff>\n"
                f"</file_version_diff>"
            )

        if mode == "disk_vs_latest":
            disk_content = ""
            if project_dir:
                try:
                    disk_path = Path(project_dir) / normalized
                    if disk_path.exists():
                        disk_content = disk_path.read_text(encoding="utf-8")
                except Exception:
                    disk_content = ""

            if versions:
                latest = versions[-1]
                latest_content = "" if latest.change_type == "delete" else latest.content
                label_latest = f"v{latest.number}"
            else:
                latest_content = ""
                label_latest = "latest"

            diff_text = _unified_diff(disk_content, latest_content, "disk", label_latest)
            return (
                f"<file_version_diff mode=\"disk_vs_latest\" "
                f"file_path=\"{escape(normalized)}\">\n"
                f"  <from>disk</from>\n"
                f"  <to>{escape(label_latest)}</to>\n"
                f"  <diff>{escape(diff_text)}</diff>\n"
                f"</file_version_diff>"
            )

        if mode == "history":
            parts = [
                f"<file_version_diff mode=\"history\" file_path=\"{escape(normalized)}\">",
                "  <versions>",
            ]
            for v in versions:
                parts.append(
                    f"    <version number=\"{v.number}\" "
                    f"change_type=\"{escape(v.change_type)}\" "
                    f"operation=\"{escape(v.operation)}\" "
                    f"timestamp=\"{escape(v.timestamp)}\" "
                    f"lines_added=\"{v.lines_added}\" "
                    f"lines_removed=\"{v.lines_removed}\"/>"
                )
            parts.append("  </versions>")
            parts.append("  <diffs>")

            prev_content = original if original is not None else ""
            prev_label = "original"
            for v in versions:
                diff_text = _unified_diff(prev_content, v.content, prev_label, f"v{v.number}")
                parts.append(
                    f"    <step from=\"{escape(prev_label)}\" to=\"v{v.number}\">"
                    f"{escape(diff_text)}</step>"
                )
                prev_content = v.content
                prev_label = f"v{v.number}"

            parts.append("  </diffs>")
            parts.append("</file_version_diff>")
            return "\n".join(parts)

        return _format_error(f"Unknown mode: {mode}")

    except Exception as e:
        return _format_error(f"Failed to build diff: {e}")