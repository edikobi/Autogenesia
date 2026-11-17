# app/agents/plan_state.py
"""Versioned Pre-filter plan state with line-numbered diffs.

Canonical persistence layout (contract: app/agents/plan_diff_contract.json v1.1):

    {project_dir}/.ai-agent/
        current_plan.md              # canonical — LATEST plan (atomically written)
        plans/
            v1_plan.md               # frozen archive of version 1
            v2_plan.md               # frozen archive of version 2
            ...
            history.json             # PlanState (versions + diffs) JSON

Design rules:
- Local diff computation (``difflib``) — the LLM never authors the internal diff.
- Every revision increments the version number monotonically.
- ``current_plan.md`` ALWAYS mirrors ``get_current().content`` — never diverges.
- Persistence is HARD-FAIL and atomic: read-back equality is verified and any
  failure raises ``PlanStateError``.
- Loading remains tolerant of legacy 1.0 history and bootstraps from the
  canonical file when history is absent.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.1"
CANONICAL_FILE = "current_plan.md"
PLANS_DIR = "plans"
HISTORY_FILE = "history.json"
PLAN_MARKER = "###Plan#####"
PLAN_DIFF_MARKER = "###PlanDiff#####"


class PlanStateError(RuntimeError):
    """Raised for hard plan persistence / canonical write failures."""


def _now_iso() -> str:
    """Return current local time as ISO-8601 string (no timezone suffix)."""
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class PlanVersion:
    """Single immutable plan version."""

    version: int
    content: str
    created_at: str = field(default_factory=_now_iso)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanVersion":
        """Deserialize from JSON dict (tolerant of missing fields)."""
        return cls(
            version=int(data.get("version", 1)),
            content=str(data.get("content", "")),
            created_at=str(data.get("created_at", _now_iso())),
            summary=str(data.get("summary", "")),
        )


@dataclass
class PlanDiffHunk:
    """Single unified-diff hunk (line-numbered, 1-indexed starts)."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanDiffHunk":
        """Deserialize from JSON dict."""
        return cls(
            old_start=int(data.get("old_start", 0)),
            old_lines=int(data.get("old_lines", 0)),
            new_start=int(data.get("new_start", 0)),
            new_lines=int(data.get("new_lines", 0)),
            lines=[str(x) for x in data.get("lines", [])],
        )


@dataclass
class PlanDiff:
    """Full diff between two plan versions."""

    from_version: int
    to_version: int
    hunks: List[PlanDiffHunk] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "hunks": [h.to_dict() for h in self.hunks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanDiff":
        """Deserialize from JSON dict."""
        return cls(
            from_version=int(data.get("from_version", 0)),
            to_version=int(data.get("to_version", 0)),
            hunks=[PlanDiffHunk.from_dict(h) for h in data.get("hunks", [])],
        )

    def is_empty(self) -> bool:
        """Return True if the diff contains no hunks."""
        return not self.hunks

    def to_unified(self) -> str:
        """Render the diff as a unified-diff string with line-numbered hunk headers."""
        parts: List[str] = []
        for h in self.hunks:
            parts.append(f"@@ -{h.old_start},{h.old_lines} +{h.new_start},{h.new_lines} @@")
            for line in h.lines:
                parts.append(line)
        return "\n".join(parts)


@dataclass
class PlanDiffOperation:
    """Deterministic line operation against the previous canonical plan.

    Operation types:
      - ``replace_range`` : replace [start_line, end_line] with ``lines``
      - ``insert_before`` : insert ``lines`` before ``start_line``
      - ``insert_after``  : insert ``lines`` after ``end_line``
      - ``delete_range``  : delete [start_line, end_line]
    Ranges are 1-based inclusive; ``end_line >= start_line`` for non-empty ranges.
    """

    op: str
    start_line: int
    end_line: int
    lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return asdict(self)


@dataclass
class PlanUpdateResult:
    """Result of parsing a Pre-filter plan response against the previous plan."""

    success: bool
    content: str
    mode: str  # "full_rewrite" | "diff" | "empty_diff" | "error"
    source_version: int
    target_version: int
    error: Optional[str] = None
    diff: Optional[PlanDiff] = None


# ---------------------------------------------------------------------------
# Line helpers
# ---------------------------------------------------------------------------

def _split_lines(text: str) -> List[str]:
    """Split text on physical newlines, normalising CRLF/CR to LF."""
    if not text:
        return []
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _plan_lines(text: str) -> List[str]:
    """Return physical plan lines, dropping at most one trailing empty from a final newline."""
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    return normalized.split("\n")


def format_numbered_plan(plan: str) -> str:
    """Produce stable numbered lines ``NNNN| <line>`` (1-based, zero-padded to 4 digits).

    Empty physical lines are numbered too. The numbering is advisory and refers
    only to the canonical plan version supplied to Pre-filter.
    """
    lines = _plan_lines(plan or "")
    return "\n".join(f"{i:04d}| {line}" for i, line in enumerate(lines, 1))


# ---------------------------------------------------------------------------
# Atomic persistence
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically (same-dir temp + os.replace) and verify read-back.

    Raises ``PlanStateError`` on I/O failure or on read-back mismatch.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlanStateError(f"Cannot create directory {path.parent}: {exc}") from exc
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_plan_", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, str(path))
        tmp_name = None
    except OSError as exc:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
        raise PlanStateError(f"Atomic plan write failed for {path}: {exc}") from exc
    try:
        read_back = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanStateError(f"Plan read-back failed for {path}: {exc}") from exc
    if read_back != content:
        raise PlanStateError(
            f"Plan write read-back mismatch at {path}: "
            f"expected {len(content)} chars, got {len(read_back)}"
        )


def write_current_plan(project_dir: Optional[str], content: str) -> Path:
    """Atomically write ``<project_dir>/.ai-agent/current_plan.md`` and verify read-back.

    Raises ``PlanStateError`` on any failure (no partial write).
    """
    if not project_dir:
        raise PlanStateError("write_current_plan requires a project_dir")
    canonical = Path(project_dir) / ".ai-agent" / CANONICAL_FILE
    _atomic_write(canonical, content or "")
    return canonical


def read_current_plan(project_dir: Optional[str]) -> str:
    """Return canonical plan text, or ``""`` when absent/unreadable (no raise on absence)."""
    if not project_dir:
        return ""
    try:
        canonical = Path(project_dir) / ".ai-agent" / CANONICAL_FILE
        if canonical.exists():
            return canonical.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        logger.warning(f"read_current_plan failed: {exc}")
    return ""


# ---------------------------------------------------------------------------
# Plan state aggregate
# ---------------------------------------------------------------------------

@dataclass
class PlanState:
    """Aggregate of all plan versions plus the diffs between them."""

    versions: List[PlanVersion] = field(default_factory=list)
    diffs: List[PlanDiff] = field(default_factory=list)
    current_version: int = 0

    # ------------------------------------------------------------------ queries

    def get_current(self) -> Optional[PlanVersion]:
        """Return the currently-active version (or None when empty)."""
        if not self.versions:
            return None
        for v in self.versions:
            if v.version == self.current_version:
                return v
        return self.versions[-1]

    def get_version(self, version: int) -> Optional[PlanVersion]:
        """Return the specific version or None."""
        for v in self.versions:
            if v.version == version:
                return v
        return None

    def get_last_diff(self) -> Optional[PlanDiff]:
        """Return the most recent recorded diff (or None)."""
        return self.diffs[-1] if self.diffs else None

    def next_version_number(self) -> int:
        """Return the next integer to use for a new version."""
        if not self.versions:
            return 1
        return max(v.version for v in self.versions) + 1

    # ---------------------------------------------------------------- mutations

    def add_version(self, content: str, summary: str = "") -> PlanVersion:
        """Register a new version (auto-incremented). Records the diff vs the previous."""
        prev = self.get_current()
        new_version = PlanVersion(
            version=self.next_version_number(),
            content=content,
            summary=summary,
        )
        self.versions.append(new_version)
        self.current_version = new_version.version
        if prev is not None and prev.content != content:
            diff = compute_diff(prev.content, content, prev.version, new_version.version)
            if not diff.is_empty():
                self.diffs.append(diff)
        return new_version

    # -------------------------------------------------------------- persistence

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the whole state to a JSON-friendly dict."""
        return {
            "schema_version": SCHEMA_VERSION,
            "current_version": self.current_version,
            "versions": [v.to_dict() for v in self.versions],
            "diffs": [d.to_dict() for d in self.diffs],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanState":
        """Deserialize a state from a JSON dict (tolerant of missing/legacy fields)."""
        try:
            return cls(
                versions=[PlanVersion.from_dict(v) for v in data.get("versions", [])],
                diffs=[PlanDiff.from_dict(d) for d in data.get("diffs", [])],
                current_version=int(data.get("current_version", 0)),
            )
        except (ValueError, TypeError) as exc:
            raise PlanStateError(f"Invalid plan history JSON: {exc}") from exc

    def save_to_disk(self, project_dir: Optional[str]) -> Optional[Path]:
        """Persist history + archive + canonical with atomic writes and read-back verification.

        Returns the canonical path on success. Raises ``PlanStateError`` on failure.
        """
        if not project_dir:
            return None
        agent_dir = Path(project_dir) / ".ai-agent"
        plans_dir = agent_dir / PLANS_DIR
        try:
            plans_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PlanStateError(f"Cannot create plans dir {plans_dir}: {exc}") from exc

        history_path = plans_dir / HISTORY_FILE
        _atomic_write(history_path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

        current = self.get_current()
        if current is None:
            return None

        archive = plans_dir / f"v{current.version}_plan.md"
        _atomic_write(archive, current.content)

        canonical = agent_dir / CANONICAL_FILE
        _atomic_write(canonical, current.content)
        return canonical

    @classmethod
    def load_from_disk(cls, project_dir: Optional[str]) -> "PlanState":
        """Load from ``.ai-agent/plans/history.json`` or bootstrap from canonical (tolerant)."""
        if not project_dir:
            return cls()
        try:
            agent_dir = Path(project_dir) / ".ai-agent"
            history_path = agent_dir / PLANS_DIR / HISTORY_FILE
            if history_path.exists():
                raw = history_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    return cls.from_dict(data)
            canonical = agent_dir / CANONICAL_FILE
            if canonical.exists():
                content = canonical.read_text(encoding="utf-8")
                if content.strip():
                    state = cls()
                    state.add_version(
                        content=content,
                        summary="Imported canonical plan (bootstrap)",
                    )
                    return state
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"PlanState.load_from_disk failed: {exc}")
        return cls()

    def commit_plan(self, project_dir: Optional[str], content: str, summary: str = "") -> "PlanState":
        """Compatibility shim delegating to the module-level ``commit_plan``."""
        return commit_plan(project_dir, content, summary=summary)


# ---------------------------------------------------------------------------
# Local diff computation
# ---------------------------------------------------------------------------

def compute_diff(
    old_content: str,
    new_content: str,
    from_version: int,
    to_version: int,
    context: int = 3,
) -> PlanDiff:
    """Compute a line-numbered unified diff between two plan versions (deterministic)."""
    old_lines = _split_lines(old_content)
    new_lines = _split_lines(new_content)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks: List[PlanDiffHunk] = []

    for group in matcher.get_grouped_opcodes(n=context):
        if not group:
            continue
        first, last = group[0], group[-1]
        i1, i2 = first[1], last[2]
        j1, j2 = first[3], last[4]

        hunk_lines: List[str] = []
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                for line in old_lines[a1:a2]:
                    hunk_lines.append(" " + line)
            elif tag == "delete":
                for line in old_lines[a1:a2]:
                    hunk_lines.append("-" + line)
            elif tag == "insert":
                for line in new_lines[b1:b2]:
                    hunk_lines.append("+" + line)
            elif tag == "replace":
                for line in old_lines[a1:a2]:
                    hunk_lines.append("-" + line)
                for line in new_lines[b1:b2]:
                    hunk_lines.append("+" + line)

        hunks.append(
            PlanDiffHunk(
                old_start=i1 + 1,
                old_lines=i2 - i1,
                new_start=j1 + 1,
                new_lines=j2 - j1,
                lines=hunk_lines,
            )
        )

    return PlanDiff(from_version=from_version, to_version=to_version, hunks=hunks)


# ---------------------------------------------------------------------------
# PLAN DIFF parsing / application
# ---------------------------------------------------------------------------

class _DiffError(Exception):
    """Internal classifier for plan-diff failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_HUNK_RE = re.compile(r"^@@\s+-(\d+),(\d+)\s+\+(\d+),(\d+)\s+@@\s*$")


def _extract_between(text: str, start_marker: str, end_marker: str) -> Optional[str]:
    """Return the text strictly between the first start marker and the next end marker."""
    start = text.find(start_marker)
    if start == -1:
        return None
    start += len(start_marker)
    end = text.find(end_marker, start)
    if end == -1:
        return None
    return text[start:end]


def _extract_plan_block(text: str) -> Optional[str]:
    """Extract the region between the first and last ``###Plan#####`` markers."""
    positions = [m.start() for m in re.finditer(re.escape(PLAN_MARKER), text)]
    if len(positions) < 2:
        return None
    first_end = positions[0] + len(PLAN_MARKER)
    last_start = positions[-1]
    if last_start < first_end:
        return None
    return text[first_end:last_start]


def _finalize_hunk(raw: Dict[str, Any]) -> PlanDiffHunk:
    """Validate body-vs-header counts and produce a ``PlanDiffHunk``."""
    body: List[str] = raw["body"]
    old_count = sum(1 for ln in body if ln[:1] in (" ", "-"))
    new_count = sum(1 for ln in body if ln[:1] in (" ", "+"))
    if old_count != raw["old_lines"]:
        raise _DiffError(
            "invalid_range",
            f"old_lines={raw['old_lines']} but body has {old_count} context/removal lines",
        )
    if new_count != raw["new_lines"]:
        raise _DiffError(
            "invalid_range",
            f"new_lines={raw['new_lines']} but body has {new_count} context/addition lines",
        )
    return PlanDiffHunk(
        old_start=raw["old_start"],
        old_lines=raw["old_lines"],
        new_start=raw["new_start"],
        new_lines=raw["new_lines"],
        lines=list(body),
    )


def _parse_diff_hunks(body: str) -> List[PlanDiffHunk]:
    """Parse a PlanDiff body into validated hunks (raises ``_DiffError``)."""
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    hunks: List[PlanDiffHunk] = []
    i = 0
    # Skip optional --- vN / +++ vN+1 lines
    while i < len(lines) and (lines[i].startswith("---") or lines[i].startswith("+++")):
        i += 1

    current: Optional[Dict[str, Any]] = None
    pending: List[PlanDiffHunk] = []
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        match = _HUNK_RE.match(line)
        if match:
            if current is not None:
                pending.append(_finalize_hunk(current))
            current = {
                "old_start": int(match.group(1)),
                "old_lines": int(match.group(2)),
                "new_start": int(match.group(3)),
                "new_lines": int(match.group(4)),
                "body": [],
            }
            i += 1
            continue
        if current is None:
            raise _DiffError(
                "unreadable_diff",
                f"Unexpected content outside hunk: {line[:60]!r}",
            )
        if not line or line[0] not in (" ", "-", "+"):
            raise _DiffError(
                "unreadable_diff",
                f"Invalid body line prefix: {line[:60]!r}",
            )
        current["body"].append(line)
        i += 1
    if current is not None:
        pending.append(_finalize_hunk(current))

    # Reject non-monotonic hunks (out-of-order). Equal starts are handled by apply.
    prev_start = 0
    for hunk in pending:
        if hunk.old_start < prev_start:
            raise _DiffError("conflict", "non-monotonic hunks")
        prev_start = hunk.old_start
    return pending


def _apply_hunks(previous_plan: str, hunks: List[PlanDiffHunk]) -> str:
    """Apply validated hunks to the previous plan (1-based inclusive; raises ``_DiffError``)."""
    prev_lines = _plan_lines(previous_plan)
    had_trailing = previous_plan.endswith("\n")
    total = len(prev_lines)
    result: List[str] = []
    cursor = 0

    for hunk in sorted(hunks, key=lambda h: h.old_start):
        start0 = hunk.old_start - 1
        if start0 < 0:
            raise _DiffError("invalid_range", "start_line must be >= 1")
        if hunk.old_lines == 0:
            if start0 < cursor:
                raise _DiffError("conflict", "overlapping hunks")
            if start0 > total:
                raise _DiffError(
                    "invalid_range",
                    f"insertion start {hunk.old_start} beyond plan length {total}",
                )
            result.extend(prev_lines[cursor:start0])
            for ln in hunk.lines:
                if ln[:1] in (" ", "+"):
                    result.append(ln[1:])
            cursor = start0
        else:
            end0 = start0 + hunk.old_lines
            if start0 < cursor:
                raise _DiffError("conflict", "overlapping hunks")
            if end0 > total:
                raise _DiffError(
                    "invalid_range",
                    f"hunk {hunk.old_start}+{hunk.old_lines} exceeds plan length {total}",
                )
            old_segment = prev_lines[start0:end0]
            expected_old = [ln[1:] for ln in hunk.lines if ln[:1] in (" ", "-")]
            if expected_old != old_segment:
                raise _DiffError(
                    "conflict",
                    f"context mismatch at line {hunk.old_start}",
                )
            result.extend(prev_lines[cursor:start0])
            for ln in hunk.lines:
                if ln[:1] in (" ", "+"):
                    result.append(ln[1:])
            cursor = end0

    result.extend(prev_lines[cursor:])
    out = "\n".join(result)
    if had_trailing and not out.endswith("\n"):
        out += "\n"
    return out


def _error_result(code: str, message: str, previous_version: int, next_version: int) -> PlanUpdateResult:
    """Build a ``PlanUpdateResult`` in error mode."""
    return PlanUpdateResult(
        success=False,
        content="",
        mode="error",
        source_version=previous_version,
        target_version=next_version,
        error=f"[{code}] {message}",
        diff=None,
    )


def parse_plan_response(
    response: str,
    previous_plan: str,
    previous_version: int,
    next_version: int,
) -> PlanUpdateResult:
    """Parse a Pre-filter planning response against the previous canonical plan.

    Supports:
      - Full rewrite enclosed in ``###Plan#####`` markers (always wins).
      - Diff-only ``###PlanDiff#####`` block applied to ``previous_plan``.
      - Empty diff meaning unchanged plan (``mode="empty_diff"``).
      - Legacy full-rewrite fallback when non-empty and no diff marker.
    All invalid cases return ``PlanUpdateResult(success=False, mode="error")`` with
    no write side effects.
    """
    text = response or ""

    # 1. Full (rewritten) plan block — always wins when present.
    full_block = _extract_plan_block(text)
    if full_block is not None:
        content = full_block.strip("\n")
        if not content.strip():
            return _error_result("missing_plan", "Full-plan block is empty", previous_version, next_version)
        diff = None
        try:
            diff = compute_diff(previous_plan or "", content, previous_version, next_version)
        except Exception:
            diff = None
        if diff is not None and diff.is_empty():
            diff = None
        return PlanUpdateResult(
            success=True,
            content=content,
            mode="full_rewrite",
            source_version=previous_version,
            target_version=next_version,
            error=None,
            diff=diff,
        )

    # 2. Diff-only block.
    if PLAN_DIFF_MARKER in text:
        diff_body = _extract_between(text, PLAN_DIFF_MARKER, PLAN_DIFF_MARKER)
        if diff_body is None:
            return _error_result("unreadable_diff", "Unterminated PlanDiff block", previous_version, next_version)
        if not (previous_plan or "").strip():
            return _error_result(
                "missing_plan",
                "Cannot apply diff without a previous plan",
                previous_version,
                next_version,
            )
        try:
            hunks = _parse_diff_hunks(diff_body)
        except _DiffError as exc:
            return _error_result(exc.code, exc.message, previous_version, next_version)
        if not hunks:
            return PlanUpdateResult(
                success=True,
                content=previous_plan,
                mode="empty_diff",
                source_version=previous_version,
                target_version=next_version,
                error=None,
                diff=None,
            )
        try:
            new_plan = _apply_hunks(previous_plan, hunks)
        except _DiffError as exc:
            return _error_result(exc.code, exc.message, previous_version, next_version)
        diff_model = PlanDiff(
            from_version=previous_version,
            to_version=next_version,
            hunks=hunks,
        )
        return PlanUpdateResult(
            success=True,
            content=new_plan,
            mode="diff",
            source_version=previous_version,
            target_version=next_version,
            error=None,
            diff=diff_model,
        )

    # 3. Legacy full rewrite fallback (non-empty response).
    stripped = text.strip()
    if stripped:
        diff = None
        try:
            diff = compute_diff(previous_plan or "", stripped, previous_version, next_version)
        except Exception:
            diff = None
        if diff is not None and diff.is_empty():
            diff = None
        return PlanUpdateResult(
            success=True,
            content=stripped,
            mode="full_rewrite",
            source_version=previous_version,
            target_version=next_version,
            error=None,
            diff=diff,
        )

    return _error_result("missing_plan", "Empty plan response", previous_version, next_version)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def load_or_init(project_dir: Optional[str]) -> PlanState:
    """Load an existing PlanState from disk or return an empty one."""
    if not project_dir:
        return PlanState()
    return PlanState.load_from_disk(project_dir)


def commit_plan(
    project_dir: Optional[str],
    content: str,
    summary: str = "",
) -> PlanState:
    """Register a new plan version and persist (canonical + archive + history).

    Avoids duplicate versions when ``content`` equals the current version, but
    still verifies canonical file equality. Raises ``PlanStateError`` on any
    persistence failure.
    """
    state = load_or_init(project_dir)
    current = state.get_current()
    if current is not None and current.content == content:
        if project_dir:
            existing = read_current_plan(project_dir)
            if existing != content:
                write_current_plan(project_dir, content)
        return state
    state.add_version(content=content, summary=summary)
    if project_dir:
        state.save_to_disk(project_dir)
    return state