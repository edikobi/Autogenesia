"""Diagnostics and repair of project / venv directory access rights.

Every public function returns a well-defined value even when the underlying
OS calls fail — this module never raises.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VENV_NAMES = ["venv", ".venv", "env", ".env"]


@dataclass
class AccessStatus:
    """Result of an access-rights probe for a project directory."""

    ok: bool
    venv_path: Optional[str]
    has_venv: bool
    has_write_access: bool
    platform: str
    reason: str = ""


def _find_venv(project_dir: Path) -> Optional[str]:
    """Return the path of a venv inside ``project_dir`` or None.

    Parity with ``VirtualFileSystem.ensure_project_venv``: a directory is
    considered a venv when it contains a ``pyvenv.cfg`` file.
    """
    for name in _VENV_NAMES:
        venv_dir = project_dir / name
        if venv_dir.is_dir() and (venv_dir / "pyvenv.cfg").exists():
            return str(venv_dir)
    return None


def _try_write(project_dir: Path) -> None:
    """Raise the underlying OS error when the directory is not writable."""
    fd, tmp_name = tempfile.mkstemp(prefix=".probe-", dir=str(project_dir))
    try:
        os.write(fd, b"probe")
    finally:
        os.close(fd)
        try:
            os.remove(tmp_name)
        except OSError:
            pass

    probe_dir = project_dir / ".probe-dir"
    probe_dir.mkdir(exist_ok=True)
    try:
        (probe_dir / "probe").write_text("probe", encoding="utf-8")
    finally:
        try:
            (probe_dir / "probe").unlink()
        except OSError:
            pass
        try:
            probe_dir.rmdir()
        except OSError:
            pass


def _classify_write_failure(project_path: Path, platform_name: str) -> str:
    """Build a human-readable explanation of a write-permission failure."""
    if platform_name.startswith("win"):
        return (
            f"Нет прав записи в «{project_path}». На Windows причиной может "
            "быть также Microsoft Defender «Контроль папок» (Controlled folder "
            "access)."
        )
    return (
        f"Нет прав записи в «{project_path}». Каталог принадлежит другому "
        "пользователю или смонтирован только для чтения."
    )



def _has_control_chars(value: str) -> bool:
    """Return True if ``value`` is empty or contains a Unicode ``Cc`` char.

    Control characters include ``\\n``, ``\\r``, ``\\t`` and ``\\x00``.
    This predicate never raises: any internal failure is treated as a
    control-character violation.
    """
    if not value:
        return True
    try:
        return any(unicodedata.category(ch) == "Cc" for ch in value)
    except Exception:
        return True



def _is_safe_windows_arg(value: str) -> bool:
    """Return False when ``value`` could inject into a Windows command line.

    Rejects empty strings, strings containing Unicode control characters
    (via :func:`_has_control_chars`), and strings containing any of the
    argument-injection metacharacters used by ``ShellExecuteW`` / ``icacls``:
    ``"`` ``'`` ``;`` ``&`` ``|`` ``%`` ``^`` ``<`` ``>`` `` ` ``.
    Legitimate Windows paths never contain these, so a rejection only
    happens for anomalous values (for example, a hostile user name).
    """
    if _has_control_chars(value):
        return False
    dangerous = "\"';&|%^<>`"
    return not any(ch in dangerous for ch in value)


def probe(project_dir: str) -> AccessStatus:
    """Diagnose write access to ``project_dir``. Synchronous, never raises.

    Looks for a venv among ``venv``/``.venv``/``env``/``.env`` (by the
    presence of ``pyvenv.cfg``), then tries to create+delete a temp file and
    a temp ``.probe-`` directory and additionally checks ``os.access``.
    """
    platform_name = sys.platform
    try:
        project_path = Path(project_dir or ".").resolve()
    except Exception:
        project_path = Path(".").resolve()

    venv_path: Optional[str] = None
    try:
        venv_path = _find_venv(project_path)
    except Exception:
        venv_path = None

    has_write_access = False
    reason = ""

    try:
        _try_write(project_path)
        has_write_access = True
    except PermissionError:
        reason = _classify_write_failure(project_path, platform_name)
    except OSError as exc:
        errno = getattr(exc, "errno", None)
        if errno in (13, 1):  # EACCES / EPERM
            reason = _classify_write_failure(project_path, platform_name)
        else:
            reason = f"Ошибка файловой системы: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        reason = str(exc)

    if has_write_access:
        try:
            has_write_access = os.access(str(project_path), os.W_OK)
        except Exception:
            pass

    return AccessStatus(
        ok=has_write_access,
        venv_path=venv_path,
        has_venv=venv_path is not None,
        has_write_access=has_write_access,
        platform=platform_name,
        reason=reason,
    )


def _fix_windows(project_path: Path) -> bool:
    """Grant the current user full ACL on ``project_path`` via a UAC prompt."""
    import ctypes

    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "")

    path_str = str(project_path)
    if not _is_safe_windows_arg(path_str):
        logger.warning(
            "Refusing Windows elevation: project path contains forbidden "
            "characters: %r",
            path_str,
        )
        return False
    if not _is_safe_windows_arg(user):
        logger.warning(
            "Refusing Windows elevation: user name contains forbidden "
            "characters: %r",
            user,
        )
        return False

    params = f'"{project_path}" /grant "{user}:(OI)(CI)F" /T'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "icacls", params, None, 1
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"ShellExecuteW failed: {exc}")
        return False

    if int(result) <= 32:
        logger.warning(f"icacls ShellExecuteW returned {result}")
        return False

    return probe(str(project_path)).has_write_access



def _fix_macos(project_path: Path) -> bool:
    """Grant the current user ownership via an ``osascript`` admin dialog."""
    import getpass

    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER", "")

    path_str = str(project_path)
    if _has_control_chars(user):
        logger.warning(
            "Refusing macOS elevation: user name contains control characters"
        )
        return False
    if _has_control_chars(path_str):
        logger.warning(
            "Refusing macOS elevation: project path contains control characters"
        )
        return False

    command = (
        f"chown -R {shlex.quote(user)} {shlex.quote(path_str)}"
        f" && chmod -R u+rwX {shlex.quote(path_str)}"
    )
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    script = f'do shell script "{escaped}" with administrator privileges'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        logger.warning(
            "osascript failed (rc=%s): stdout=%r stderr=%r",
            result.returncode,
            (result.stdout or "")[:500],
            (result.stderr or "")[:500],
        )
    return probe(str(project_path)).has_write_access



def request_elevation_and_fix(project_dir: str) -> bool:
    """Request an OS elevation dialog and repair write access. Never raises.

    Windows: ``icacls`` via ``ShellExecuteW(runas, ...)`` (standard UAC).
    macOS: ``osascript`` with administrator privileges (admin password).
    Linux and others: returns False (manual instructions only).
    """
    platform_name = sys.platform
    try:
        project_path = Path(project_dir or ".").resolve()
    except Exception:
        project_path = Path(".").resolve()

    try:
        if platform_name.startswith("win"):
            return _fix_windows(project_path)
        if platform_name == "darwin":
            return _fix_macos(project_path)
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"request_elevation_and_fix failed: {exc}")
        return False


def manual_instructions(platform_name: str) -> str:
    """Return Russian step-by-step manual access-repair instructions."""
    if platform_name.startswith("win") or platform_name == "windows":
        return (
            "Windows 10/11 — инструкция по исправлению прав доступа:\n"
            "1. Закройте редакторы и терминалы, работающие с проектом.\n"
            "2. Откройте «Терминал» (или PowerShell) ОТ ИМЕНИ АДМИНИСТРАТОРА.\n"
            "3. Перенесите проект из защищённых папок (Program Files, "
            "C:\\Windows, корень C:\\) — UAC часто запрещает там запись.\n"
            "4. Проверьте Microsoft Defender «Контроль папок»:\n"
            "   Параметры → Конфиденциальность и безопасность → Безопасность "
            "Windows → Защита от вирусов и угроз → Управление защитой от "
            "программ-шантажистов.\n"
            "5. Добавьте каталог проекта в исключения «Контроль папок»."
        )
    if platform_name == "darwin":
        return (
            "macOS — инструкция по исправлению прав доступа:\n"
            "1. Откройте Терминал.\n"
            "2. Выполните:\n"
            "   sudo chown -R $(whoami) <путь_к_проекту>\n"
            "   sudo chmod -R u+rwX <путь_к_проекту>\n"
            "3. Если включён managed TCC:\n"
            "   Системные настройки → Конфиденциальность и безопасность → "
            "Файлы и папки / Полный доступ к диску — разрешите доступ "
            "терминалу.\n"
            "4. При необходимости перенесите проект из ~/Desktop, "
            "~/Documents, ~/Downloads."
        )
    return (
        "Linux / прочее — инструкция по исправлению прав доступа:\n"
        "1. Проверьте владельца и права: ls -ld <путь_к_проекту>\n"
        "2. Выдайте права текущему пользователю:\n"
        "   sudo chown -R $(whoami) <путь_к_проекту>\n"
        "   sudo chmod -R u+rwX <путь_к_проекту>"
    )


def create_venv(project_dir: str) -> bool:
    """Create a ``.venv`` inside ``project_dir``; False on any failure."""
    try:
        venv_path = Path(project_dir or ".").resolve() / ".venv"
    except Exception:
        venv_path = Path(".venv")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            return True
        logger.error(f"Failed to create venv: {result.stderr}")
        return False
    except PermissionError as exc:
        logger.error(f"Permission denied creating venv: {exc}")
        return False
    except Exception as exc:
        logger.error(f"Exception creating venv: {exc}")
        return False