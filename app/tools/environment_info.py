"""Shared, read-only environment report collector and XML formatter.

Used by both :class:`app.tools.tool_executor.ToolExecutor` and
:class:`app.tools.tester_tool_executor.TesterToolExecutor` so both agents see an
identical environment picture. No I/O writes, no project code execution, no
network access. Never raises.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape, quoteattr

from app.utils.executable_resolver import resolve_executable


#: Ordered list of language/runtime probe descriptors (exactly 10 entries).
_LANGUAGE_PROBES: List[Dict[str, Any]] = [
    {
        "name": "Python",
        "primary": ("__PYTHON__", "--version"),
        "fallback": ("python", "--version"),
        "source": "stdout",
        "timeout": 8,
        "package_manager": None,
    },
    {
        "name": "JavaScript / Node.js",
        "primary": ("node", "--version"),
        "fallback": None,
        "source": "stdout",
        "timeout": 8,
        "package_manager": ("npm", "--version"),
    },
    {
        "name": "TypeScript",
        "primary": ("tsc", "--version"),
        "fallback": ("__TSC_LOCAL__", "--version"),
        "source": "stdout",
        "timeout": 10,
        "package_manager": None,
    },
    {
        "name": "Java",
        "primary": ("java", "-version"),
        "fallback": None,
        "source": "stderr",
        "timeout": 10,
        "package_manager": None,
    },
    {
        "name": "Go",
        "primary": ("go", "version"),
        "fallback": None,
        "source": "stdout",
        "timeout": 10,
        "package_manager": None,
    },
    {
        "name": "Rust",
        "primary": ("rustc", "--version"),
        "fallback": None,
        "source": "stdout",
        "timeout": 10,
        "package_manager": ("cargo", "--version"),
    },
    {
        "name": "C",
        "primary": ("gcc", "--version"),
        "fallback": ("cc", "--version"),
        "source": "stdout",
        "timeout": 8,
        "package_manager": None,
    },
    {
        "name": "C++",
        "primary": ("g++", "--version"),
        "fallback": ("c++", "--version"),
        "source": "stdout",
        "timeout": 8,
        "package_manager": None,
    },
    {
        "name": "C# / .NET",
        "primary": ("dotnet", "--version"),
        "fallback": None,
        "source": "stdout",
        "timeout": 10,
        "package_manager": None,
    },
    {
        "name": "PHP",
        "primary": ("php", "--version"),
        "fallback": None,
        "source": "stdout",
        "timeout": 8,
        "package_manager": None,
    },
]


def _first_meaningful_line(text: str) -> str:
    """Return the first non-empty, trimmed line of *text* (or "")."""
    if not text:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _run_probe(command: List[str], source: str, timeout: int) -> Dict[str, Any]:
    """Run a single version probe and normalize the result.

    Returns a dict with ``status``, ``version``, ``source`` keys. Never raises.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"status": "not_found", "version": "", "source": "unknown"}
    except subprocess.TimeoutExpired:
        return {"status": "not_found", "version": "", "source": "unknown"}
    except OSError:
        return {"status": "not_found", "version": "", "source": "unknown"}
    except Exception:
        return {"status": "unknown", "version": "", "source": "unknown"}

    if result.returncode != 0:
        return {"status": "not_found", "version": "", "source": "unknown"}

    if source == "stderr":
        text = result.stderr or result.stdout
    else:
        text = result.stdout or result.stderr
    version = _first_meaningful_line(text)
    if not version:
        return {"status": "not_found", "version": "", "source": "unknown"}
    return {"status": "found", "version": version, "source": source}


def _resolve_command(name: str) -> Optional[str]:
    """Resolve an executable name via ``resolve_executable`` (never raises)."""
    try:
        return resolve_executable(name)
    except Exception:
        return None


def _probe_language(descriptor: Dict[str, Any], project_python_path: Optional[str], project_dir: str) -> Dict[str, Any]:
    """Probe one language descriptor and return a normalized dict."""
    name = descriptor["name"]
    source = descriptor["source"]
    timeout = descriptor["timeout"]

    def _build_command(primary_or_fallback: tuple) -> Optional[List[str]]:
        exe_name, arg = primary_or_fallback[0], primary_or_fallback[1]
        if exe_name == "__PYTHON__":
            if project_python_path:
                return [project_python_path, arg]
            return [_resolve_command("python") or "python", arg]
        if exe_name == "__TSC_LOCAL__":
            local_tsc = Path(project_dir) / "node_modules" / ".bin" / "tsc"
            if local_tsc.exists():
                return [str(local_tsc), arg]
            return None
        resolved = _resolve_command(exe_name)
        if resolved is None:
            return None
        return [resolved, arg]

    command_attempted = ""
    entry: Dict[str, Any] = {
        "name": name,
        "status": "not_found",
        "version": "",
        "command": "",
        "source": "unknown",
    }

    # Primary attempt
    primary_cmd = _build_command(descriptor["primary"])
    if primary_cmd is not None:
        command_attempted = " ".join(primary_cmd)
        result = _run_probe(primary_cmd, source, timeout)
        if result["status"] == "found":
            entry.update(result)
            entry["command"] = command_attempted
            _maybe_add_package_manager(entry, descriptor, timeout)
            return entry

    # Fallback attempt
    fallback = descriptor.get("fallback")
    if fallback is not None:
        fallback_cmd = _build_command(fallback)
        if fallback_cmd is not None:
            command_attempted = " ".join(fallback_cmd)
            result = _run_probe(fallback_cmd, source, timeout)
            if result["status"] == "found":
                entry.update(result)
                entry["command"] = command_attempted
                _maybe_add_package_manager(entry, descriptor, timeout)
                return entry

    entry["command"] = command_attempted
    entry["source"] = "unknown" if entry["status"] == "found" else entry.get("source", "unknown")
    return entry


def _maybe_add_package_manager(entry: Dict[str, Any], descriptor: Dict[str, Any], timeout: int) -> None:
    """Attach an optional package-manager version to a language entry."""
    pm = descriptor.get("package_manager")
    if not pm:
        return
    exe_name, arg = pm
    resolved = _resolve_command(exe_name)
    if resolved is None:
        return
    result = _run_probe([resolved, arg], "stdout", timeout)
    if result["status"] == "found" and result["version"]:
        entry["package_manager_version"] = result["version"]


def _collect_local_node_tools(project_dir: str) -> str:
    """Comma-joined list of node tools present in <project>/node_modules/.bin."""
    try:
        bin_dir = Path(project_dir) / "node_modules" / ".bin"
        present: List[str] = []
        for tool in ("tsc", "ts-node", "eslint", "prettier"):
            if (bin_dir / tool).exists() or (bin_dir / f"{tool}.cmd").exists():
                present.append(tool)
        return ",".join(present)
    except Exception:
        return ""


def _collect_resources(project_dir: str) -> Dict[str, Any]:
    """Collect CPU/RAM/disk info using psutil when available, else fall back."""
    cpu_count: Any = "unknown"
    ram_total: Any = "unknown"
    disk_free: Any = "unknown"

    try:
        import psutil  # type: ignore

        cpu_count = psutil.cpu_count()
        ram_total = psutil.virtual_memory().total
        try:
            disk_free = psutil.disk_usage(project_dir).free
        except Exception:
            disk_free = "unknown"
    except Exception:
        try:
            cpu_count = os.cpu_count()
        except Exception:
            cpu_count = "unknown"
        try:
            disk_free = shutil.disk_usage(project_dir).free
        except Exception:
            disk_free = "unknown"

    return {"cpu_count": cpu_count, "ram_total_bytes": ram_total, "disk_free_bytes": disk_free}


def collect_environment_info(
    project_dir: str,
    project_python_path: Optional[str] = None,
) -> dict:
    """Collect a read-only environment report. Never raises.

    Args:
        project_dir: Project root used for disk and node_modules probing.
        project_python_path: Optional project Python interpreter path.

    Returns:
        A dict with date/time/timezone, OS fields, Python info, a ``languages``
        list of exactly 10 entries, local node tools and system resources.
    """
    now_local = datetime.now().astimezone()
    utc_dt = datetime.now(timezone.utc)

    try:
        tz_name = now_local.tzname() or ""
        offset = now_local.strftime("%z") or ""
        timezone_str = f"{tz_name} {offset}".strip() or "unknown"
    except Exception:
        timezone_str = "unknown"

    info: Dict[str, Any] = {
        "local_date": now_local.strftime("%Y-%m-%d"),
        "local_time": now_local.strftime("%H:%M:%S"),
        "timezone": timezone_str,
        "utc_datetime": utc_dt.replace(tzinfo=timezone.utc).isoformat(),
        "os_system": platform.system() or "unknown",
        "os_release": platform.release() or "unknown",
        "os_version": platform.version() or "unknown",
        "os_machine": platform.machine() or "unknown",
        "python_version": platform.python_version() or "unknown",
        "python_path": project_python_path or sys.executable or "unknown",
        "languages": [],
        "local_node_tools": _collect_local_node_tools(project_dir),
    }

    languages: List[Dict[str, Any]] = []
    for descriptor in _LANGUAGE_PROBES:
        try:
            languages.append(
                _probe_language(descriptor, project_python_path, project_dir)
            )
        except Exception:
            languages.append(
                {
                    "name": descriptor["name"],
                    "status": "unknown",
                    "version": "",
                    "command": "",
                    "source": "unknown",
                }
            )
    info["languages"] = languages

    try:
        info.update(_collect_resources(project_dir))
    except Exception:
        info.setdefault("cpu_count", "unknown")
        info.setdefault("ram_total_bytes", "unknown")
        info.setdefault("disk_free_bytes", "unknown")

    return info


def _language_version(info: dict, language_name: str) -> str:
    """Look up a version string for a named language (or "not found")."""
    try:
        for entry in info.get("languages", []):
            if entry.get("name") == language_name:
                version = entry.get("version") or ""
                if version:
                    return version
    except Exception:
        pass
    return "not found"


def format_environment_xml(info: dict) -> str:
    """Render the canonical ``<environment>`` XML. Never raises."""
    info = info or {}
    parts: List[str] = []
    parts.append("<environment>")

    def _line(tag: str, value: Any) -> None:
        parts.append(f"  <{tag}>{escape(str(value))}</{tag}>")

    _line("local_date", info.get("local_date", "unknown"))
    _line("local_time", info.get("local_time", "unknown"))
    _line("timezone", info.get("timezone", "unknown"))
    _line("utc_datetime", info.get("utc_datetime", "unknown"))
    _line("os_system", info.get("os_system", "unknown"))
    _line("os_release", info.get("os_release", "unknown"))
    _line("os_version", info.get("os_version", "unknown"))
    _line("os_machine", info.get("os_machine", "unknown"))
    _line("python_version", info.get("python_version", "unknown"))
    _line("python_path", info.get("python_path", "unknown"))

    parts.append("  <languages>")
    for entry in info.get("languages", []):
        name = quoteattr(str(entry.get("name", "")))
        status = quoteattr(str(entry.get("status", "unknown")))
        version = quoteattr(str(entry.get("version", "")))
        command = quoteattr(str(entry.get("command", "")))
        source = quoteattr(str(entry.get("source", "unknown")))
        attrs = f"name={name} status={status} version={version} command={command} source={source}"
        pm = entry.get("package_manager_version")
        if pm:
            parts.append(f"    <language {attrs} package_manager_version={quoteattr(str(pm))} />")
        else:
            parts.append(f"    <language {attrs} />")
    parts.append("  </languages>")

    _line("local_node_tools", info.get("local_node_tools", ""))
    _line("cpu_count", info.get("cpu_count", "unknown"))
    _line("ram_total_bytes", info.get("ram_total_bytes", "unknown"))
    _line("disk_free_bytes", info.get("disk_free_bytes", "unknown"))

    # Legacy backward-compatible tags
    _line("java_version", _language_version(info, "Java"))
    _line("go_version", _language_version(info, "Go"))
    _line("node_version", _language_version(info, "JavaScript / Node.js"))
    _line("tsc_version", _language_version(info, "TypeScript"))

    parts.append("</environment>")
    return "\n".join(parts)

