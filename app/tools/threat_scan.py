"""Local, offline, Semgrep CE-based static suspicious-code scanner.

Exposed as a tool to the Orchestrator, Pre-Filter and Tester. Never executes
the scanned file; never writes into the project directory; never logs source
content. Findings are suspicious patterns — NOT a verdict.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape, quoteattr

from app.utils.executable_resolver import resolve_executable
from app.utils.path_security import is_forbidden_path, get_forbidden_reason

logger = logging.getLogger(__name__)

RULES_PATH = str(Path(__file__).resolve().parent / "semgrep_rules" / "malicious_code.yml")

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".c", ".cpp",
    ".cs", ".rb", ".php", ".sh", ".bash", ".ps1", ".yml", ".yaml",
    ".json", ".toml", ".xml", ".html", ".sql",
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
DEFAULT_MAX_FINDINGS = 20
SEMGREP_TIMEOUT_SEC = 60
TOOL_LABEL = "Semgrep CE 1.185+"

_LIMITATIONS_TEXT = (
    "Findings are suspicious patterns requiring manual review. "
    "This tool is static, local and offline; it does not execute scanned code "
    "and does not prove the absence of malware."
)

_SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}


def _severity_rank(sev: str) -> int:
    """Return numeric rank for a severity string (lower = more severe)."""
    return _SEVERITY_ORDER.get((sev or "").upper(), 3)


def _confidence_rank(conf: str) -> int:
    """Return numeric rank for a confidence string (lower = stronger)."""
    return {"HIGH": 0, "MEDIUM": 1}.get((conf or "").upper(), 2)


def _is_supported(file_path: str) -> bool:
    """Return True if the file extension is in the scan-supported set."""
    try:
        name = Path(file_path).name
        if name == "Dockerfile":
            return True
        return Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS
    except Exception:
        return False


def _serialize_finding(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a single Semgrep result into our finding shape."""
    extra = item.get("extra") or {}
    metadata = extra.get("metadata") or {}
    start = item.get("start") or {}
    evidence_raw = extra.get("lines") or ""
    evidence = str(evidence_raw).strip()
    if len(evidence) > 240:
        evidence = evidence[:240] + "..."
    return {
        "severity": str(extra.get("severity") or "ERROR").upper(),
        "rule_id": str(item.get("check_id") or ""),
        "confidence": str(metadata.get("confidence") or "MEDIUM").upper(),
        "line": int(start.get("line") or 0),
        "title": str(metadata.get("category") or item.get("check_id") or "suspicious-pattern"),
        "message": str(extra.get("message") or ""),
        "evidence": evidence,
        "recommendation": "Manual review of the referenced lines is recommended.",
    }


def _coerce_version_stub() -> str:
    """Return a best-effort semgrep version string (never raises)."""
    return TOOL_LABEL


def scan_file_for_threats(
    file_path: str,
    content: str,
    *,
    severity: str = "ERROR",
    max_findings: int = DEFAULT_MAX_FINDINGS,
    include_context: bool = False,
) -> dict:
    """Scan **content** with Semgrep CE using the bundled local ruleset.

    The content is materialized into a temporary directory (preserving the
    original filename) and scanned there. Returns a normalized dict with
    ``status``, ``findings``, ``errors`` and metadata. Never raises.
    """
    result: Dict[str, Any] = {
        "status": "ok",
        "tool": TOOL_LABEL,
        "findings": [],
        "errors": 0,
        "severity_floor": severity,
    }

    semgrep_exe: Optional[str] = None
    try:
        semgrep_exe = resolve_executable("semgrep")  # type: ignore[arg-type]
    except Exception:
        semgrep_exe = None

    if not semgrep_exe:
        result["status"] = "dependency_missing"
        result["message"] = "semgrep executable not found; run: pip install semgrep"
        return result

    tmp_dir = None
    try:
        tmp_dir = tempfile.TemporaryDirectory()
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"tempdir creation failed: {e}"
        return result

    try:
        tmp_path = Path(tmp_dir.name)
        # Preserve original filename/extension for language detection
        name = Path(file_path).name or "input.txt"
        target = tmp_path / name
        try:
            target.write_text(content, encoding="utf-8")
        except Exception as e:
            result["status"] = "error"
            result["message"] = f"failed to materialize content: {e}"
            return result

        cmd = [
            semgrep_exe,
            "scan",
            "--config", RULES_PATH,
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--severity", severity,
            "--timeout", "5",
            "--max-target-bytes", str(MAX_FILE_BYTES),
            str(target),
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SEMGREP_TIMEOUT_SEC,
            cwd=tmp_dir.name,
            encoding="utf-8",
            errors="replace",
        )

        stdout = proc.stdout or ""
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            result["status"] = "error"
            result["message"] = "semgrep produced non-JSON output"
            return result

        results = data.get("results") or []
        errors = data.get("errors") or []
        result["errors"] = len(errors)

        findings = [_serialize_finding(item) for item in results]
        # Sort: severity -> confidence -> line
        findings.sort(
            key=lambda f: (
                _severity_rank(f.get("severity", "")),
                _confidence_rank(f.get("confidence", "")),
                int(f.get("line", 0)),
            )
        )
        try:
            limit = max(1, int(max_findings))
        except Exception:
            limit = DEFAULT_MAX_FINDINGS
        result["findings"] = findings[:limit]
        result["status"] = "ok"
        return result

    except subprocess.TimeoutExpired:
        result["status"] = "error"
        result["message"] = "semgrep timed out"
        return result
    except FileNotFoundError:
        result["status"] = "dependency_missing"
        result["message"] = "semgrep executable not found; run: pip install semgrep"
        return result
    except OSError as e:
        result["status"] = "error"
        result["message"] = f"semgrep OS error: {e}"
        return result
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"scan failed: {e}"
        return result
    finally:
        try:
            tmp_dir.cleanup()
        except Exception:
            pass



def _format_threat_xml(
    status: str,
    file_path: str,
    source: str,
    supported: bool,
    findings: List[Dict[str, Any]],
    errors: int,
    severity_floor: str,
    extra_message: Optional[str] = None,
) -> str:
    """Build the canonical <threat_scan> XML."""
    parts: List[str] = []
    parts.append("<threat_scan>")
    parts.append(f"  <status>{escape(status)}</status>")
    parts.append(f"  <tool>{escape(TOOL_LABEL)}</tool>")
    parts.append(
        f"  <file path={quoteattr(file_path)} source={quoteattr(source)} />"
    )
    parts.append("  <scan_mode>local_offline</scan_mode>")
    parts.append(f"  <supported>{str(bool(supported)).lower()}</supported>")
    parts.append(
        f'  <summary findings="{len(findings)}" errors="{int(errors)}" '
        f'severity_floor={quoteattr(severity_floor)} />'
    )
    parts.append("  <findings>")
    for f in findings:
        sev = quoteattr(str(f.get("severity", "")))
        rule_id = quoteattr(str(f.get("rule_id", "")))
        conf = quoteattr(str(f.get("confidence", "")))
        line = int(f.get("line", 0))
        parts.append(
            f"    <finding severity={sev} rule_id={rule_id} "
            f'confidence={conf} line="{line}">'
        )
        parts.append(f"      <title>{escape(str(f.get('title', '')))}</title>")
        parts.append(f"      <message>{escape(str(f.get('message', '')))}</message>")
        parts.append(f"      <evidence>{escape(str(f.get('evidence', '')))}</evidence>")
        parts.append(
            f"      <recommendation>{escape(str(f.get('recommendation', '')))}</recommendation>"
        )
        parts.append("    </finding>")
    parts.append("  </findings>")
    if extra_message:
        parts.append(f"  <message>{escape(extra_message)}</message>")
    parts.append(f"  <limitations>{escape(_LIMITATIONS_TEXT)}</limitations>")
    parts.append("</threat_scan>")
    return "\n".join(parts)



def threat_scan_tool(
    file_path: str,
    *,
    project_dir: str,
    virtual_fs: Optional[Any] = None,
    python_path: Optional[str] = None,
    role: str = "orchestrator",
    severity: str = "ERROR",
    max_findings: int = DEFAULT_MAX_FINDINGS,
    include_context: bool = False,
) -> str:
    """Public adapter: validate path, read via VFS/disk, scan, return XML.

    Path security: normalizes separators and rejects absolute paths, drive
    letters, ``..`` traversal, symlink escapes outside the project, and
    forbidden paths. Never raises.
    """
    try:
        if not file_path:
            return _format_threat_xml(
                "error", "", "disk", False, [], 0, severity,
                extra_message="file_path is required",
            )

        normalized = str(file_path).replace("\\", "/")

        if (
            PurePosixPath(normalized).is_absolute()
            or normalized.startswith("/")
            or (len(normalized) >= 2 and normalized[1] == ":")
        ):
            return _format_threat_xml(
                "error", normalized, "disk", False, [], 0, severity,
                extra_message="absolute paths are not allowed",
            )

        if ".." in PurePosixPath(normalized).parts:
            return _format_threat_xml(
                "error", normalized, "disk", False, [], 0, severity,
                extra_message="path traversal is not allowed",
            )

        try:
            project_root = Path(project_dir).resolve()
        except Exception:
            project_root = Path(project_dir)

        try:
            candidate = (project_root / normalized).resolve()
        except Exception:
            candidate = project_root / normalized

        try:
            candidate.relative_to(project_root)
        except Exception:
            return _format_threat_xml(
                "error", normalized, "disk", False, [], 0, severity,
                extra_message="path escapes project directory",
            )

        try:
            if is_forbidden_path(normalized):
                reason = get_forbidden_reason(normalized) or "forbidden path"
                return _format_threat_xml(
                    "error", normalized, "disk", False, [], 0, severity,
                    extra_message=f"forbidden path: {reason}",
                )
        except Exception:
            pass

        if not _is_supported(normalized):
            return _format_threat_xml(
                "unsupported", normalized, "disk", False, [], 0, severity,
                extra_message="unsupported file type for static scan",
            )

        content: Optional[str] = None
        source = "disk"
        if virtual_fs is not None:
            try:
                staged = virtual_fs.read_file(normalized)
                if staged is not None:
                    content = staged
                    source = "vfs"
            except Exception:
                content = None
        if content is None:
            try:
                content = candidate.read_text(encoding="utf-8")
                source = "disk"
            except Exception:
                content = None

        if content is None:
            return _format_threat_xml(
                "unsupported", normalized, source, False, [], 0, severity,
                extra_message="file could not be read or decoded",
            )

        try:
            if len(content.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
                return _format_threat_xml(
                    "unsupported", normalized, source, False, [], 0, severity,
                    extra_message="file too large for static scan",
                )
        except Exception:
            pass

        # Dependency bootstrap (skip for tester)
        has_semgrep = False
        try:
            has_semgrep = importlib.util.find_spec("semgrep") is not None
        except Exception:
            has_semgrep = False
        if not has_semgrep:
            try:
                has_semgrep = shutil.which("semgrep") is not None
            except Exception:
                has_semgrep = False

        if not has_semgrep and role != "tester":
            try:
                from app.tools.dependency_manager import install_dependency_tool

                install_dependency_tool(
                    project_dir=project_dir,
                    package_name="semgrep",
                    language="python",
                    python_path=python_path,
                    vfs=None,
                )
            except Exception:
                pass
            try:
                has_semgrep = importlib.util.find_spec("semgrep") is not None or shutil.which("semgrep") is not None
            except Exception:
                has_semgrep = False

        if not has_semgrep:
            return _format_threat_xml(
                "dependency_missing", normalized, source, True, [], 0, severity,
                extra_message="semgrep is not installed; run: pip install semgrep",
            )

        scan_result = scan_file_for_threats(
            file_path=normalized,
            content=content,
            severity=severity,
            max_findings=max_findings,
            include_context=include_context,
        )

        status = scan_result.get("status", "error")
        findings = scan_result.get("findings") or []
        errors = scan_result.get("errors", 0)
        extra = scan_result.get("message") if status != "ok" else None
        return _format_threat_xml(
            status, normalized, source, True, findings, int(errors), severity,
            extra_message=extra,
        )

    except Exception as e:
        logger.warning(f"threat_scan_tool failed: {e}")
        try:
            return _format_threat_xml(
                "error", str(file_path or ""), "disk", False, [], 0, severity,
                extra_message="unexpected scan error",
            )
        except Exception:
            return "<threat_scan><status>error</status></threat_scan>"