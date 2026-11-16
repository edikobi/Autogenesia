"""Singleton manager for MCP server lifecycle.

Connections live in a dedicated background daemon thread with its own event
loop, so the orchestrator's loop is never touched and ``nest_asyncio`` is not
required. Every public method is synchronous and thread-safe; coroutines are
driven through ``asyncio.run_coroutine_threadsafe(...).result(timeout)``.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.mcp.config_store import (
    load_servers,
    save_servers,
    validate_server_entry,
)

logger = logging.getLogger(__name__)

TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")
MAX_TOOL_NAME_LEN = 56
# MAX_RESULT_CHARS = 20000  # Removed: MCP results are passed to the model in full (no truncation)

# _TRUNCATION_MARKER = "...[обрезано MCP-менеджером]"  # Removed: truncation marker no longer needed


def _prefixed_tool_name(server: str, native_name: str, used: set) -> str:
    """Build a collision-safe ``mcp__<server>__<tool>`` name.

    Both server and tool names are sanitised; collisions within a single
    server are resolved with ``_2``, ``_3`` suffixes. The ``mcp__`` prefix
    guarantees no collision with project tools.
    """
    clean_server = TOOL_NAME_RE.sub("_", server or "")[:MAX_TOOL_NAME_LEN] or "server"
    clean_tool = TOOL_NAME_RE.sub("_", native_name or "")[:MAX_TOOL_NAME_LEN] or "tool"

    candidate = f"mcp__{clean_server}__{clean_tool}"
    counter = 2
    while candidate in used:
        candidate = f"mcp__{clean_server}__{clean_tool}_{counter}"
        counter += 1
    return candidate


def _render_content(result: Any) -> str:
    """Render ``result.content`` into a complete plain-text string.

    ``TextContent`` is emitted verbatim. ``ImageContent`` / ``AudioContent`` /
    ``EmbeddedResource`` are replaced by a textual descriptor — the visual
    payload is unsupported but the MCP reply never breaks. The result is not
    truncated: whatever the MCP server returned is returned to the model in
    full, with no character/token limits imposed by the manager.
    """
    parts: List[str] = []
    contents = getattr(result, "content", None) or []
    for item in contents:
        item_type = type(item).__name__

        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue

        mime = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        uri = getattr(item, "uri", None)
        resource = getattr(item, "resource", None)
        if resource is not None:
            if uri is None:
                uri = getattr(resource, "uri", None)
            if mime is None:
                mime = getattr(resource, "mimeType", None)

        descriptor_bits = [f"type={item_type}"]
        if mime:
            descriptor_bits.append(f"mime={mime}")
        if uri:
            descriptor_bits.append(f"uri={uri}")

        data = getattr(item, "data", None)
        if data is None and resource is not None:
            data = getattr(resource, "text", None) or getattr(resource, "blob", None)
        if data is not None:
            descriptor_bits.append(f"size={len(str(data))}")

        parts.append(f"[нетекстовый контент: {', '.join(descriptor_bits)}]")

    return "\n".join(parts)



def _matches_role(target_agents: str, role: Optional[str]) -> bool:
    """Deterministic role-to-target-agents scope predicate.

    Semantics:
    - ``role is None`` or ``target_agents == "all"`` → True.
    - ``role in ("orchestrator", "pre_filter")`` → ``target_agents in ("orchestrator", "all")``.
    - ``role == "tester"`` → ``target_agents in ("tester", "all")``.
    - Any other role → False.
    """
    if role is None:
        return True
    if target_agents == "all":
        return True
    if role in ("orchestrator", "pre_filter"):
        return target_agents in ("orchestrator", "all")
    if role == "tester":
        return target_agents in ("tester", "all")
    return False




def _format_error(message: str) -> str:
    """Вернуть каноническую XML-форму ошибки MCP-диспетчера.

    Точная копия формы из ``app/tools/tool_executor.py::ToolExecutor._format_error``
    и ``app/mcp/definitions.py::_format_error`` (в проекте намеренно дублируется).
    Оркестраторы классифицируют результат как ошибку по префиксу
    ``startswith("<!-- ERROR")``, поэтому наличие этого префикса критично.
    Текст ``message`` вставляется как есть — информативность сохраняется.
    """
    return (
        "<!-- ERROR -->\n"
        "<error>\n"
        f"  <message>{message}</message>\n"
        "</error>"
    )


async def _open_session(entry: Dict[str, Any], stack: Any) -> Any:
    """Open a ClientSession with the right transport for ``entry``.

    The ``mcp`` SDK is imported lazily here so an absent package does not
    break module import.
    """
    transport = entry.get("transport", "stdio")

    if transport == "stdio":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=entry.get("command", ""),
            args=list(entry.get("args", []) or []),
            env=dict(entry.get("env", {}) or {}) or None,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        return await stack.enter_async_context(ClientSession(read, write))

    if transport == "http":
        from mcp import ClientSession

        try:
            from mcp.client.streamable_http import (
                streamable_http_client as _streamable_http,
            )
        except ImportError:
            from mcp.client.streamable_http import (
                streamablehttp_client as _streamable_http,
            )

        read, write, _get_session_id = await stack.enter_async_context(
            _streamable_http(entry.get("url", ""))
        )
        return await stack.enter_async_context(ClientSession(read, write))

    if transport == "sse":
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        read, write = await stack.enter_async_context(
            sse_client(entry.get("url", ""))
        )
        return await stack.enter_async_context(ClientSession(read, write))

    raise ValueError(f"Unsupported transport: {transport}")


class MCPManager:
    """Singleton owning MCP connections; exposes a synchronous façade."""

    def __init__(self) -> None:
        """Initialise an empty manager (no background thread yet)."""
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connections: Dict[str, Dict[str, Any]] = {}
        self._reverse_map: Dict[str, Tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._atexit_registered = False
        self._mcp_available: Optional[bool] = None

    # -------------------------------------------------------------- thread
    def _ensure_thread(self) -> bool:
        """Start the background daemon thread and its event loop if needed."""
        with self._lock:
            if (
                self._loop is not None
                and self._thread is not None
                and self._thread.is_alive()
            ):
                return True

            ready = threading.Event()

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()

            thread = threading.Thread(
                target=_runner, name="mcp-manager", daemon=True
            )
            thread.start()
            self._thread = thread

            if not ready.wait(timeout=10):
                logger.warning("MCP manager thread did not start in time")
                return False

            if not self._atexit_registered:
                try:
                    atexit.register(self.disconnect_all)
                    self._atexit_registered = True
                except Exception:
                    pass
            return True

    def _run(self, coro: Any, timeout: Optional[float] = None) -> Any:
        """Schedule ``coro`` on the background loop and wait for the result."""
        if self._loop is None:
            raise RuntimeError("MCP loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if timeout is not None:
            return future.result(timeout=timeout)
        return future.result()

    def _is_loop_running(self) -> bool:
        """Return True only when the background loop and thread are alive.

        Reads ``_loop`` and ``_thread`` without acquiring ``self._lock`` on
        purpose: acquiring the lock here could recurse into
        :meth:`_ensure_thread`, which already holds it. Attribute reads are
        atomic enough for a best-effort liveness check, and this predicate
        never raises.
        """
        try:
            return (
                self._loop is not None
                and self._thread is not None
                and self._thread.is_alive()
            )
        except Exception:
            return False

    # -------------------------------------------------------------- public
    def connect_all(self) -> None:
        """Start the background thread and connect all enabled servers.

        Non-blocking: the coroutine is scheduled on the background loop and
        the method returns immediately. Per-server errors are recorded and
        never affect the pipeline.
        """
        if not self._ensure_thread():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._connect_all_async(), self._loop)
        except Exception as exc:
            logger.warning(f"connect_all failed to schedule: {exc}")

    async def _connect_all_async(self) -> None:
        """Coroutine body of :meth:`connect_all`."""
        servers = load_servers()
        if not any(entry.get("enabled", True) for entry in servers):
            return

        try:
            import mcp  # noqa: F401
        except ImportError:
            logger.info("mcp package not installed — MCP disabled")
            return

        for entry in servers:
            if not entry.get("enabled", True):
                continue
            name = entry.get("name", "")
            if not name:
                continue
            try:
                await self._connect_one_async(entry)
            except Exception as exc:
                logger.warning(f"MCP server «{name}» failed to connect: {exc}")
                self._connections[name] = {
                    "session": None,
                    "exit_stack": None,
                    "tools": [],
                    "connected": False,
                    "error": str(exc),
                        "timeout": entry.get("timeout", 60),
                        "target_agents": entry.get("target_agents", "all"),
                }

    def disconnect_all(self) -> None:
        """Close every connection in the background loop. Idempotent.

        Safe to call from ``atexit``; missing loop or connections are a no-op.
        """
        with self._lock:
            connections = list(self._connections.items())
            self._connections.clear()
            self._reverse_map.clear()

        if self._loop is None:
            return

        async def _close() -> None:
            for _name, info in connections:
                stack = info.get("exit_stack")
                if stack is not None:
                    try:
                        await stack.aclose()
                    except Exception as exc:
                        logger.debug(f"MCP exit stack close error: {exc}")

        try:
            self._run(_close(), timeout=20)
        except Exception as exc:
            logger.debug(f"disconnect_all error: {exc}")

    def list_definitions(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible tool schemas; ``[]`` when unavailable.

        Guarded against a not-yet-started manager: when the background loop
        is not running we return ``[]`` WITHOUT constructing the coroutine,
        which avoids ``RuntimeWarning: coroutine was never awaited`` when
        this is called before :meth:`connect_all` (for example from the TUI).

        ``role`` (optional) filters servers by their ``target_agents`` scope.
        """
        if not self._is_loop_running():
            return []
        try:
            return self._run(
                self._list_definitions_async(role=role), timeout=10
            ) or []
        except Exception as exc:
            logger.debug(f"list_definitions error: {exc}")
            return []

    async def _list_definitions_async(
        self, role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Coroutine body of :meth:`list_definitions`."""
        result: List[Dict[str, Any]] = []
        for name, info in self._connections.items():
            if not info.get("connected"):
                continue
            if not _matches_role(info.get("target_agents", "all"), role):
                continue
            for tool in info.get("tools", []):
                schema = tool.get("schema", {})
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool["_prefixed_name"],
                            "description": (
                                f"[MCP server '{name}'] "
                                f"{schema.get('description', '') or ''}"
                            ),
                            "parameters": schema.get("parameters")
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
        return result

    def call_tool(
        self,
        prefixed_name: str,
        arguments: Dict[str, Any],
        role: Optional[str] = None,
    ) -> str:
        """Invoke an MCP tool by its prefixed name. Never raises.

        Guards against a stopped background loop before constructing the
        coroutine, so a call made before :meth:`connect_all` cannot leak an
        un-awaited coroutine. All error paths return the canonical XML error
        form ``<!-- ERROR -->`` (compatible with the orchestrator heuristic).
        When ``role`` is provided and the target server is not scoped to that
        role, a canonical error is returned without touching the session.
        """
        if not self._is_loop_running():
            return _format_error("MCP manager is not running")

        target = self._reverse_map.get(prefixed_name)
        if target is None:
            return _format_error(f"MCP tool not found: {prefixed_name}")

        server, native_name = target
        info = self._connections.get(server, {})

        if role is not None and not _matches_role(
            info.get("target_agents", "all"), role
        ):
            return _format_error(
                f"MCP server '{server}' is not enabled for role '{role}'"
            )

        session = info.get("session")
        if session is None:
            return _format_error(f"MCP server '{server}' is not connected")

        timeout = float(info.get("timeout") or 60)
        try:
            return self._run(
                self._call_tool_async(session, native_name, arguments),
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning(f"MCP call_tool '{prefixed_name}' failed: {exc}")
            return _format_error(f"MCP tool '{prefixed_name}' error: {exc}")

    async def _call_tool_async(
        self, session: Any, native_name: str, arguments: Dict[str, Any]
    ) -> str:
        """Coroutine body of :meth:`call_tool`."""
        result = await session.call_tool(native_name, arguments or {})
        return _render_content(result)

    def add_server(self, entry: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate, persist and (optionally) connect a new server entry."""
        servers = load_servers()
        existing_names = [s.get("name", "") for s in servers]
        ok, msg = validate_server_entry(entry, existing_names)
        if not ok:
            return False, msg

        servers.append(entry)
        if not save_servers(servers):
            return False, "Не удалось сохранить конфигурацию"

        if entry.get("enabled", True):
            if not self._ensure_thread():
                return False, "Не удалось запустить фоновый поток MCP"
            try:
                timeout = float(entry.get("timeout") or 60) + 30
                self._run(self._connect_one_async(entry), timeout=timeout)
            except Exception as exc:
                return False, f"Сервер сохранён, подключение не удалось: {exc}"

        return True, "Сервер успешно добавлен"

    def remove_server(self, name: str) -> None:
        """Disconnect the server and remove it from the config."""
        servers = load_servers()
        remaining = [s for s in servers if s.get("name") != name]
        save_servers(remaining)

        if self._loop is not None:
            try:
                self._run(self._disconnect_one_async(name), timeout=20)
            except Exception as exc:
                logger.debug(f"remove_server disconnect error: {exc}")

        with self._lock:
            self._connections.pop(name, None)
            self._reverse_map = {
                k: v for k, v in self._reverse_map.items() if v[0] != name
            }

    async def _disconnect_one_async(self, name: str) -> None:
        """Coroutine body: close a single server's exit stack."""
        info = self._connections.get(name)
        if not info:
            return
        stack = info.get("exit_stack")
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as exc:
                logger.debug(f"MCP close error for {name}: {exc}")

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Update ``enabled`` and connect/disconnect accordingly."""
        servers = load_servers()
        entry: Optional[Dict[str, Any]] = None
        for s in servers:
            if s.get("name") == name:
                s["enabled"] = enabled
                entry = s
                break
        if entry is None:
            return
        save_servers(servers)

        if enabled:
            if not self._ensure_thread():
                return
            if self._connections.get(name, {}).get("connected"):
                return
            try:
                timeout = float(entry.get("timeout") or 60) + 30
                self._run(self._connect_one_async(entry), timeout=timeout)
            except Exception as exc:
                logger.warning(f"set_enabled(connect) failed for {name}: {exc}")
        else:
            if self._loop is not None:
                try:
                    self._run(self._disconnect_one_async(name), timeout=20)
                except Exception as exc:
                    logger.debug(f"set_enabled(disable) failed for {name}: {exc}")
            with self._lock:
                self._connections.pop(name, None)
                self._reverse_map = {
                    k: v for k, v in self._reverse_map.items() if v[0] != name
                }

    def test_connection(self, name: str) -> Tuple[bool, str]:
        """Open a throwaway connection, initialize and list tools.

        After the background thread is ensured, verify the loop is actually
        running before constructing the coroutine — this prevents leaking an
        un-awaited coroutine if the worker thread died unexpectedly.
        """
        servers = load_servers()
        entry = next((s for s in servers if s.get("name") == name), None)
        if entry is None:
            return False, f"Сервер «{name}» не найден"

        if not self._ensure_thread():
            return False, "Не удалось запустить фоновый поток MCP"

        if not self._is_loop_running():
            return False, "MCP manager is not running"

        try:
            timeout = float(entry.get("timeout") or 60) + 30
            return self._run(self._test_connection_async(entry), timeout=timeout)
        except Exception as exc:
            return False, f"Ошибка подключения: {exc}"

    async def _test_connection_async(
        self, entry: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Coroutine body of :meth:`test_connection`."""
        from contextlib import AsyncExitStack

        stack = AsyncExitStack()
        try:
            session = await _open_session(entry, stack)
            await session.initialize()
            response = await session.list_tools()
            tools = getattr(response, "tools", []) or []
            names = ", ".join(getattr(t, "name", "?") for t in tools[:5])
            return True, f"OK: {len(tools)} инструментов: {names}"
        except Exception as exc:
            return False, str(exc)
        finally:
            try:
                await stack.aclose()
            except Exception:
                pass

    def status(self) -> List[Dict[str, Any]]:
        """Return a status snapshot suitable for menu rendering."""
        servers = load_servers()
        result: List[Dict[str, Any]] = []
        for entry in servers:
            name = entry.get("name", "")
            info = self._connections.get(name, {})
            result.append(
                {
                    "name": name,
                    "transport": entry.get("transport", ""),
                    "enabled": entry.get("enabled", True),
                    "connected": bool(info.get("connected")),
                    "tool_count": len(info.get("tools", [])),
                    "error": info.get("error"),
                    "target_agents": entry.get("target_agents", "all"),
                }
            )
        return result

    # -------------------------------------------------------- connecting
    async def _connect_one_async(self, entry: Dict[str, Any]) -> None:
        """Connect a single server and cache its tools / prefixed names."""
        name = entry.get("name", "")
        if not name:
            return

        with self._lock:
            self._reverse_map = {
                k: v for k, v in self._reverse_map.items() if v[0] != name
            }

        from contextlib import AsyncExitStack

        stack = AsyncExitStack()
        try:
            session = await _open_session(entry, stack)
            await session.initialize()
            response = await session.list_tools()

            tools: List[Dict[str, Any]] = []
            used_names: set = set()
            for tool in getattr(response, "tools", []) or []:
                native_name = getattr(tool, "name", "")
                prefixed = _prefixed_tool_name(name, native_name, used_names)
                used_names.add(prefixed)
                tools.append(
                    {
                        "_native_name": native_name,
                        "_prefixed_name": prefixed,
                        "schema": {
                            "description": getattr(tool, "description", "") or "",
                            "parameters": getattr(tool, "input_schema", None)
                            or getattr(tool, "inputSchema", None)
                            or {"type": "object", "properties": {}},
                        },
                    }
                )

            self._connections[name] = {
                "session": session,
                "exit_stack": stack,
                "tools": tools,
                "connected": True,
                "error": None,
                    "timeout": entry.get("timeout", 60),
                    "target_agents": entry.get("target_agents", "all"),
            }

            with self._lock:
                for tool in tools:
                    self._reverse_map[tool["_prefixed_name"]] = (
                        name,
                        tool["_native_name"],
                    )
        except Exception as exc:
            try:
                await stack.aclose()
            except Exception:
                pass
            self._connections[name] = {
                "session": None,
                "exit_stack": None,
                "tools": [],
                "connected": False,
                "error": str(exc),
                    "timeout": entry.get("timeout", 60),
                    "target_agents": entry.get("target_agents", "all"),
            }
            raise


_singleton_lock = threading.Lock()
_singleton_instance: Optional[MCPManager] = None


def _get_mcp_manager() -> MCPManager:
    """Return the process-wide :class:`MCPManager` singleton (thread-safe)."""
    global _singleton_instance
    if _singleton_instance is None:
        with _singleton_lock:
            if _singleton_instance is None:
                _singleton_instance = MCPManager()
    return _singleton_instance


def get_mcp_manager() -> MCPManager:
    """Public accessor for the MCP manager singleton."""
    return _get_mcp_manager()