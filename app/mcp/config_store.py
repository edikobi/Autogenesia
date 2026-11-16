"""Fault-tolerant storage for MCP server configuration.

Mirrors the ``app/tui/settings_io.py`` pattern: never raises, atomic writes,
loss-less round-trip. Configuration lives in ``data/mcp_servers.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

MCP_CONFIG_PATH = Path("data") / "mcp_servers.json"

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_TRANSPORTS = {"stdio", "http", "sse"}
_TARGET_AGENTS = {"orchestrator", "tester", "all"}


def load_servers() -> List[Dict[str, Any]]:
    """Load MCP server entries.

    Returns ``[]`` when the file is missing, corrupt or not a list.
    Logs a warning but never raises.
    """
    path = MCP_CONFIG_PATH
    try:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning(f"MCP config is not a list: {path}")
            return []
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to load MCP servers from {path}: {exc}")
        return []


def save_servers(servers: List[Dict[str, Any]]) -> bool:
    """Atomically persist ``servers``; returns a success flag. Never raises."""
    path = MCP_CONFIG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".mcp-", suffix=".json", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(servers, f, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
            raise
        return True
    except (OSError, TypeError) as exc:
        logger.warning(f"Failed to save MCP servers to {path}: {exc}")
        return False


def validate_server_entry(
    entry: Dict[str, Any], existing_names: List[str]
) -> Tuple[bool, str]:
    """Validate an MCP server entry for storage.

    Required fields: ``name`` (unique, ``[a-zA-Z0-9_-]{1,32}``),
    ``transport`` in ``{"stdio", "http", "sse"}``. For ``stdio`` a
    ``command`` is required; for ``http``/``sse`` a ``url`` is required.
    Optional fields: ``args: List[str]``, ``env: Dict[str, str]``,
    ``enabled: bool`` (default True), ``timeout: int`` (default 60).
    """
    if not isinstance(entry, dict):
        return False, "Запись должна быть словарём"

    name = entry.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return False, "Недопустимое имя: используйте [a-zA-Z0-9_-]{1,32}"

    if name in existing_names:
        return False, f"Сервер с именем «{name}» уже существует"

    transport = entry.get("transport")
    if transport not in _TRANSPORTS:
        return False, "transport должен быть одним из: stdio, http, sse"

    if transport == "stdio":
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            return False, "Для transport=stdio обязателен параметр command"
    else:
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            return False, f"Для transport={transport} обязателен параметр url"

    args = entry.get("args")
    if args is not None and not isinstance(args, list):
        return False, "args должен быть списком строк"

    env = entry.get("env")
    if env is not None and not isinstance(env, dict):
        return False, "env должен быть словарём"

    target_agents = entry.get("target_agents", "all")
    if target_agents not in _TARGET_AGENTS:
        return False, "target_agents должен быть одним из: orchestrator, tester, all"

    timeout = entry.get("timeout")
    if timeout is not None and not isinstance(timeout, int):
        return False, "timeout должен быть целым числом"

    return True, "OK"


def default_entry(
    name: str, transport: str, target_agents: str = "all"
) -> Dict[str, Any]:
    """Build a config entry with sensible defaults."""
    return {
        "name": name,
        "transport": transport,
        "command": None,
        "args": [],
        "env": {},
        "url": None,
        "enabled": True,
        "timeout": 60,
        "target_agents": target_agents if target_agents in _TARGET_AGENTS else "all",
    }
