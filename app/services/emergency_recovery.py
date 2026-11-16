"""
Deterministic (NO LLM, NO AI) crash recovery for the AI agent pipeline.

Classifies failures into exactly three categories, preserves VFS/context,
performs 3×15s reconnects, measures internet connectivity, offers provider-aware
hot model swap and quota extension. All decisions go through injected user
callbacks; there are zero AI calls and zero imports of ``call_llm*``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import logging
# import os  # (removed: unused import)
import socket
import ssl
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EmergencyCategory(Enum):
    """Exactly three deterministic failure categories."""
    CRITICAL_CODE_ERROR = "critical_code_error"
    NETWORK_OR_PROVIDER_ERROR = "network_or_provider_error"
    APP_LIMIT_REACHED = "app_limit_reached"


class AppLimitReachedError(Exception):
    """Raised by callers when a tool/iteration quota is exhausted."""

    def __init__(
        self,
        limit_type: str,
        current_value: int,
        max_value: int,
        agent_role: str = "orchestrator",
        context_messages: Optional[List[Dict]] = None,
    ):
        super().__init__(
            f"App limit reached: {limit_type} ({current_value}/{max_value})"
        )
        self.limit_type = limit_type
        self.current_value = current_value
        self.max_value = max_value
        self.agent_role = agent_role
        self.context_messages = context_messages or []


@dataclass
class InternetDiagnostics:
    """Result of a deterministic connectivity measurement."""
    is_connected: bool = False
    latency_ms: float = 0.0
    download_mbps: float = 0.0
    details: str = ""


@dataclass
class EmergencyEvent:
    """Context handed to a resolution callback."""
    category: EmergencyCategory
    exception: Optional[BaseException] = None
    agent_role: str = "orchestrator"
    limit_type: str = ""
    current_value: int = 0
    max_value: int = 0
    reconnect_attempts: int = 0
    diagnostics: Optional[InternetDiagnostics] = None
    location: Dict[str, str] = field(default_factory=dict)
    message: str = ""
    role_label: str = ""


@dataclass
class EmergencyResolution:
    """Decision returned by the manager or a callback."""
    action: str = "propagate"
    extra_tool_calls: int = 0
    extra_iterations: int = 0
    orchestrator_model: Optional[str] = None
    orchestrator_provider: Optional[str] = None
    generator_model: Optional[str] = None
    generator_provider: Optional[str] = None
    force_final: bool = False
    end_session: bool = False
    message: str = ""
    agent_role: str = "orchestrator"
    target_role: Optional[str] = None
    prefilter_model: Optional[str] = None
    prefilter_provider: Optional[str] = None
    tester_model: Optional[str] = None
    tester_provider: Optional[str] = None
    retry_reconnect: bool = False


EXTRA_QUOTA_NOTICE = (
    "[SYSTEM NOTICE — EXTRA QUOTA GRANTED BY USER] You reached the application limit, "
    "and the user explicitly granted you an additional quota (+10 tool calls / +5 iterations) "
    "to complete the task properly. Use this extra budget efficiently: focus on the remaining "
    "critical steps, avoid redundant tool calls, and bring the solution to completion."
)

RECONNECT_ATTEMPTS = 3
RECONNECT_INTERVAL_SEC = 15.0
PING_HOSTS = ("77.88.8.8", "1.1.1.1", "8.8.8.8")
HTTP_ENDPOINTS = (
    "https://speed.cloudflare.com/__down?bytes=250000",
    "https://ya.ru",
    "https://www.google.com/generate_204",
)


# ---------------------------------------------------------------------------
# Classification sets (module-level, unit-testable)
# ---------------------------------------------------------------------------
NETWORK_EXCEPTION_NAMES = (
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "APIStatusError",
    "APIError",
    "HTTPError",
    "TransportError",
    "TimeoutException",
    "SSLError",
    "ConnectionError",
    "TimeoutError",
    "LLMAPIError",
    "RetryableError",
    "ServerOverloadError",
    "LLMTimeoutError",
)

CRITICAL_CODE_EXCEPTION_NAMES = (
    "SyntaxError",
    "IndentationError",
    "TabError",
    "ImportError",
    "ModuleNotFoundError",
    "NameError",
    "UnboundLocalError",
    "AttributeError",
    "TypeError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
    "RecursionError",
    "MemoryError",
    "NotImplementedError",
)


def _load_network_classes() -> tuple:
    """Best-effort collect network/provider exception classes (never raises)."""
    classes: List[type] = []
    try:
        import openai  # type: ignore

        for name in (
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
            "APIStatusError",
            "APIError",
        ):
            cls = getattr(openai, name, None)
            if isinstance(cls, type):
                classes.append(cls)
    except Exception:
        pass
    try:
        import httpx  # type: ignore

        for name in ("HTTPError", "TransportError", "TimeoutException"):
            cls = getattr(httpx, name, None)
            if isinstance(cls, type):
                classes.append(cls)
    except Exception:
        pass
    try:
        from app.llm.api_client import (
            LLMAPIError,
            RateLimitError,
            RetryableError,
            ServerOverloadError,
            LLMTimeoutError,
        )

        classes.extend(
            [
                LLMAPIError,
                RateLimitError,
                RetryableError,
                ServerOverloadError,
                LLMTimeoutError,
            ]
        )
    except Exception:
        pass
    # Builtins
    try:
        classes.extend([socket.error, ssl.SSLError, ConnectionError, TimeoutError])
    except Exception:
        pass
    return tuple(classes)


def _is_network_error(exc: BaseException) -> bool:
    """isinstance-only network/provider check — never misclassifies code bugs."""
    for cls in _load_network_classes():
        try:
            if isinstance(exc, cls):
                # Guard: openai.APIError is parent of APIStatusError etc, but also
                # base of API-level bugs — network set covers all.
                return True
        except Exception:
            continue
    return False


def classify_exception(exc: BaseException) -> EmergencyCategory:
    """Deterministically classify an exception into exactly one category.

    Uses ``isinstance`` checks only — no string matching, no message heuristics.
    Unknown exceptions default to ``CRITICAL_CODE_ERROR`` (fail-safe).
    """
    if isinstance(exc, AppLimitReachedError):
        return EmergencyCategory.APP_LIMIT_REACHED

    if _is_network_error(exc):
        return EmergencyCategory.NETWORK_OR_PROVIDER_ERROR

    # Explicit exclusion: type/attribute checks for code errors are unchanged,
    # but we already excluded all network/provider types above.
    critical_classes = (
        SyntaxError,
        IndentationError,
        TabError,
        ImportError,
        ModuleNotFoundError,
        NameError,
        UnboundLocalError,
        AttributeError,
        TypeError,
        KeyError,
        IndexError,
        ZeroDivisionError,
        RecursionError,
        MemoryError,
        NotImplementedError,
    )
    for cls in critical_classes:
        if isinstance(exc, cls):
            return EmergencyCategory.CRITICAL_CODE_ERROR

    return EmergencyCategory.CRITICAL_CODE_ERROR


def extract_failure_location(exc: BaseException) -> Dict[str, str]:
    """Deterministically extract the deepest traceback frame location."""
    info: Dict[str, str] = {"filename": "", "lineno": "", "name": ""}
    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        return info
    try:
        frames = traceback.extract_tb(tb)
        if frames:
            deepest = frames[-1]
            info["filename"] = str(deepest.filename or "")
            info["lineno"] = str(deepest.lineno if deepest.lineno is not None else "")
            info["name"] = str(deepest.name or "")
    except Exception:
        pass
    return info


# Module-level monotonic snapshot counter, incremented atomically under a lock.
# Used only by save_emergency_snapshot() to guarantee unique filenames.
_snapshot_counter_lock = threading.Lock()
_snapshot_counter = 0
def save_emergency_snapshot(
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    instruction: str = "",
    prefilter_advice: str = "",
    error: Optional[BaseException] = None,
    vfs: Any = None,
    thread_id: Optional[str] = None,
    history_manager: Any = None,
    project_dir: Optional[str] = None,
) -> Optional[str]:
    """Persist context to ``<project>/.ai-agent/emergency_snapshots/snapshot_*.json``.

    Best-effort: never raises, never mutates the VFS. Returns the snapshot path
    or ``None`` on failure.
    """
    try:
        category = (
            classify_exception(error)
            if error is not None
            else EmergencyCategory.CRITICAL_CODE_ERROR
        )
        location = extract_failure_location(error) if error is not None else {}

        vfs_state: Dict[str, str] = {}
        if vfs is not None:
            try:
                staged = vfs.get_staged_files() or []
                for path in staged:
                    try:
                        content = vfs.read_file(path)
                        if content is not None:
                            vfs_state[str(path)] = content
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"snapshot: could not read VFS: {e}")

        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "category": category.value,
            "exception_type": type(error).__name__ if error else "",
            "exception_message": str(error) if error else "",
            "failure_location": location,
            "messages": messages or [],
            "history": history or [],
            "instruction": instruction or "",
            "prefilter_advice": prefilter_advice or "",
            "thread_id": thread_id,
            "vfs_state": vfs_state,
        }

        base = project_dir or "."
        target_dir = Path(base) / ".ai-agent" / "emergency_snapshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        unique_suffix = uuid.uuid4().hex[:8]
        global _snapshot_counter
        with _snapshot_counter_lock:
            _snapshot_counter += 1
            _counter_value = _snapshot_counter
        path = target_dir / f"snapshot_{ts}_{_counter_value}_{unique_suffix}.json"
        while path.exists():
            unique_suffix = uuid.uuid4().hex[:8]
            with _snapshot_counter_lock:
                _snapshot_counter += 1
                _counter_value = _snapshot_counter
            path = target_dir / f"snapshot_{ts}_{_counter_value}_{unique_suffix}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        if history_manager is not None and thread_id:
            try:
                note = (
                    f"[EMERGENCY] Snapshot saved ({category.value}). "
                    f"Error: {type(error).__name__ if error else 'n/a'}: "
                    f"{str(error)[:200] if error else ''}"
                )
                maybe_coro = history_manager.add_message(thread_id, "system", note)
                if asyncio.iscoroutine(maybe_coro):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(maybe_coro)
                    except RuntimeError:
                        pass
            except Exception as e:
                logger.debug(f"snapshot: history_manager note failed: {e}")

        return str(path)
    except Exception as e:
        logger.warning(f"save_emergency_snapshot failed: {e}")
        return None


def measure_internet_connectivity() -> InternetDiagnostics:
    """Deterministically probe connectivity via psutil/ping3/httpx.

    Never raises; degrades to ``is_connected=False`` on missing deps or
    raw-socket permission errors.
    """
    diag = InternetDiagnostics()
    reasons: List[str] = []

    # Interface liveness cache (psutil optional) — used only as sanity hint, not gate
    try:
        import psutil  # type: ignore

        _ = psutil.net_if_stats()
    except Exception:
        pass

    # ICMP probes
    icmp_ok = False
    latencies: List[float] = []
    try:
        from ping3 import ping
        import ping3.errors as _perr  # type: ignore

        for host in PING_HOSTS:
            try:
                rtt = ping(host, timeout=2.0, unit="ms")
                if rtt and rtt is not False and not isinstance(rtt, bool):
                    icmp_ok = True
                    latencies.append(float(rtt))
                    break
            except _perr.PingError:
                continue
            except PermissionError:
                continue
            except OSError:
                continue
            except Exception:
                continue
    except ImportError:
        reasons.append("ping3 unavailable")
    except Exception as e:
        reasons.append(f"icmp probe error: {e}")

    if latencies:
        diag.latency_ms = sum(latencies) / len(latencies)

    # HTTP fallback
    http_ok = False
    try:
        import httpx  # type: ignore

        for url in HTTP_ENDPOINTS:
            try:
                start = time.time()
                resp = httpx.get(url, timeout=5.0, follow_redirects=True)
                elapsed = time.time() - start
                if resp.status_code < 500:
                    http_ok = True
                    if diag.latency_ms == 0.0:
                        diag.latency_ms = elapsed * 1000.0
                    try:
                        probe = "https://speed.cloudflare.com/__down?bytes=250000"
                        s0 = time.time()
                        r2 = httpx.get(probe, timeout=15.0)
                        elapsed2 = max(time.time() - s0, 1e-6)
                        nbytes = len(r2.content or b"")
                        if nbytes > 0:
                            diag.download_mbps = (nbytes * 8) / elapsed2 / 1_000_000
                    except Exception:
                        pass
                    break
            except Exception as e:
                reasons.append(f"http probe: {e}")
                continue
    except ImportError:
        reasons.append("httpx unavailable")

    diag.is_connected = icmp_ok or http_ok
    diag.details = "; ".join(reasons) if reasons else (
        "ok" if diag.is_connected else "offline"
    )
    return diag


class EmergencyRecoveryManager:
    """Deterministic recovery coordinator — never calls an LLM."""

    def __init__(self) -> None:
        """Initialize an empty manager (no callbacks, no attempt counter)."""
        self._lock = threading.RLock()
        self._resolution_callback: Optional[
            Callable[[EmergencyEvent], EmergencyResolution]
        ] = None
        self._notify_callback: Optional[Callable[[str], None]] = None
        self._reconnect_attempts = 0

    def set_resolution_callback(
        self, fn: Optional[Callable[[EmergencyEvent], EmergencyResolution]]
    ) -> None:
        """Register or clear the resolution callback."""
        with self._lock:
            self._resolution_callback = fn

    def set_notify_callback(self, fn: Optional[Callable[[str], None]]) -> None:
        """Register or clear the notify callback."""
        with self._lock:
            self._notify_callback = fn

    def _notify(self, message: str) -> None:
        """Emit a user-facing notification (best-effort)."""
        with self._lock:
            cb = self._notify_callback
        if cb is not None:
            try:
                cb(message)
                return
            except Exception as e:
                logger.debug(f"notify callback failed: {e}")
        logger.info(f"[EMERGENCY] {message}")

    def _resolve(
        self, event: EmergencyEvent, default: EmergencyResolution
    ) -> EmergencyResolution:
        """Dispatch event to the user callback, else return the supplied default."""
        with self._lock:
            cb = self._resolution_callback
        if cb is not None:
            try:
                result = cb(event)
                if isinstance(result, EmergencyResolution):
                    return result
            except Exception as e:
                logger.warning(f"resolution callback failed: {e}")
        return default

    async def handle_exception(
        self,
        exc: BaseException,
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
        vfs: Any = None,
        agent_role: str = "orchestrator",
        instruction: str = "",
        prefilter_advice: str = "",
        history: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[str] = None,
        history_manager: Any = None,
        project_dir: Optional[str] = None,
    ) -> EmergencyResolution:
        """Classify, snapshot, and dispatch the exception by category."""
        if isinstance(exc, AppLimitReachedError):
            return await self.handle_limit_reached(
                limit_type=exc.limit_type,
                current_value=exc.current_value,
                max_value=exc.max_value,
                agent_role=exc.agent_role or agent_role,
                messages=messages,
                vfs=vfs,
                history=history,
                thread_id=thread_id,
                history_manager=history_manager,
                project_dir=project_dir,
            )

        category = classify_exception(exc)
        try:
            save_emergency_snapshot(
                messages=messages,
                history=history,
                instruction=instruction,
                prefilter_advice=prefilter_advice,
                error=exc,
                vfs=vfs,
                thread_id=thread_id,
                history_manager=history_manager,
                project_dir=project_dir,
            )
        except Exception:
            pass

        canonical = normalize_emergency_role(agent_role)

        if category == EmergencyCategory.CRITICAL_CODE_ERROR:
            loc = extract_failure_location(exc)
            logger.error(
                "[EMERGENCY] Critical code error (%s) at %s:%s in %s",
                type(exc).__name__,
                loc.get("filename", "?"),
                loc.get("lineno", "?"),
                loc.get("name", "?"),
            )
            self._notify(
                f"❌ Критическая ошибка кода: {type(exc).__name__}: {exc} "
                f"(файл: {loc.get('filename', '?')}:{loc.get('lineno', '?')})"
            )
            resolution = EmergencyResolution(
                action="propagate",
                message=f"{type(exc).__name__}: {exc}",
            )
            try:
                if not getattr(resolution, "agent_role", None):
                    resolution.agent_role = canonical
                if getattr(resolution, "target_role", None) is None:
                    resolution.target_role = canonical
            except Exception:
                pass
            return resolution

        if category == EmergencyCategory.NETWORK_OR_PROVIDER_ERROR:
            resolution = await self._handle_network(
                exc, agent_role, project_dir, messages, vfs, history
            )
            try:
                if not getattr(resolution, "agent_role", None):
                    resolution.agent_role = canonical
                if getattr(resolution, "target_role", None) is None:
                    resolution.target_role = canonical
            except Exception:
                pass
            return resolution

        # Fail-safe
        resolution = EmergencyResolution(
            action="propagate",
            message=f"{type(exc).__name__}: {exc}",
        )
        try:
            if not getattr(resolution, "agent_role", None):
                resolution.agent_role = canonical
            if getattr(resolution, "target_role", None) is None:
                resolution.target_role = canonical
        except Exception:
            pass
        return resolution

    async def _clear_shared_http_pool(self) -> int:
        """Pop all entries from ``app.llm.api_client._provider_clients`` without aclose()."""
        try:
            from app.llm.api_client import _provider_clients  # local import

            keys = list(_provider_clients.keys())
            for key in keys:
                try:
                    _provider_clients.pop(key, None)
                except Exception:
                    pass
            return len(keys)
        except Exception as e:
            logger.debug(f"_clear_shared_http_pool failed: {e}")
            return 0

    async def _handle_network(
        self,
        exc: BaseException,
        agent_role: str,
        project_dir: Optional[str],
        messages: Optional[List[Dict[str, Any]]],
        vfs: Any,
        history: Optional[List[Dict[str, Any]]],
    ) -> EmergencyResolution:
        """Perform up to 3 reconnect attempts; then measure internet and offer options."""
        with self._lock:
            self._reconnect_attempts += 1
            current = self._reconnect_attempts

        if current <= RECONNECT_ATTEMPTS:
            self._notify(
                f"🔄 Сбой связи с провайдером. Попытка переподключения "
                f"{current}/{RECONNECT_ATTEMPTS} через {int(RECONNECT_INTERVAL_SEC)} сек..."
            )
            try:
                await asyncio.sleep(RECONNECT_INTERVAL_SEC)
            except asyncio.CancelledError:
                raise
            cleared = await self._clear_shared_http_pool()
            if cleared:
                logger.info(f"Emergency: cleared {cleared} shared HTTP pool entries")
            return EmergencyResolution(
                action="continue",
                message=f"Reconnect attempt {current}/{RECONNECT_ATTEMPTS}",
            )

        # 3 attempts exhausted
        with self._lock:
            self._reconnect_attempts = 0

        diag = await asyncio.to_thread(measure_internet_connectivity)

        role_label = get_emergency_role_label(agent_role)
        canonical = normalize_emergency_role(agent_role)

        event = EmergencyEvent(
            category=EmergencyCategory.NETWORK_OR_PROVIDER_ERROR,
            exception=exc,
            agent_role=agent_role,
            role_label=role_label,
            reconnect_attempts=RECONNECT_ATTEMPTS,
            diagnostics=diag,
            message=(
                f"Роль: {role_label}. Провайдер недоступен после {RECONNECT_ATTEMPTS} попыток. "
                f"Интернет: {'OK' if diag.is_connected else 'НЕТ'}, "
                f"latency={diag.latency_ms:.0f}ms, "
                f"download={diag.download_mbps:.2f}Mbps"
            ),
        )

        if not diag.is_connected:
            default = EmergencyResolution(
                action="end_session",
                end_session=True,
                message="No internet — ending session",
                agent_role=canonical,
                target_role=canonical,
            )
            resolution = self._resolve(event, default)
            if not isinstance(resolution, EmergencyResolution):
                resolution = default
            if not getattr(resolution, "agent_role", None):
                resolution.agent_role = canonical
            if getattr(resolution, "target_role", None) is None:
                resolution.target_role = canonical
            if resolution.action not in ("retry", "retry_reconnect", "end_session"):
                resolution.action = "end_session"
                resolution.end_session = True
        else:
            default = EmergencyResolution(
                action="swap_models",
                message="Internet up but provider failing — user should swap models",
                agent_role=canonical,
                target_role=canonical,
            )
            resolution = self._resolve(event, default)
            if not isinstance(resolution, EmergencyResolution):
                resolution = default
            if not getattr(resolution, "agent_role", None):
                resolution.agent_role = canonical
            if getattr(resolution, "target_role", None) is None:
                resolution.target_role = canonical

        # retry_reconnect handling (applies to both branches)
        if getattr(resolution, "retry_reconnect", False) or resolution.action == "retry":
            try:
                with self._lock:
                    self._reconnect_attempts = 0
            except Exception:
                pass
            try:
                await self._clear_shared_http_pool()
            except Exception:
                pass
            return EmergencyResolution(
                action="continue",
                retry_reconnect=True,
                agent_role=getattr(resolution, "agent_role", None) or canonical,
                target_role=getattr(resolution, "target_role", None) or canonical,
                message=getattr(resolution, "message", "")
                or f"Reconnect retry requested ({role_label})",
            )

        return resolution

    async def handle_limit_reached(
        self,
        *,
        limit_type: str,
        current_value: int,
        max_value: int,
        agent_role: str,
        messages: Optional[List[Dict[str, Any]]] = None,
        vfs: Any = None,
        history: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[str] = None,
        history_manager: Any = None,
        project_dir: Optional[str] = None,
    ) -> EmergencyResolution:
        """Offer (a) extra quota, (b) finalize now, (c) end session."""
        event = EmergencyEvent(
            category=EmergencyCategory.APP_LIMIT_REACHED,
            agent_role=agent_role,
            limit_type=limit_type,
            current_value=current_value,
            max_value=max_value,
            message=f"{agent_role}: {limit_type} {current_value}/{max_value}",
        )
        default = EmergencyResolution(
            action="finalize",
            force_final=True,
            message="Default finalize on limit",
        )
        resolution = self._resolve(event, default)

        if resolution.action == "extra_quota":
            # Inject notice in place so the AI is informed
            if messages is not None:
                try:
                    messages.append(
                        {"role": "system", "content": EXTRA_QUOTA_NOTICE}
                    )
                except Exception:
                    pass
            low = (limit_type or "").lower()
            if "iteration" in low or "feedback" in low or "loop" in low:
                resolution.extra_iterations = 5
            else:
                resolution.extra_tool_calls = 10
            try:
                save_emergency_snapshot(
                    messages=messages,
                    history=history,
                    vfs=vfs,
                    thread_id=thread_id,
                    history_manager=history_manager,
                    project_dir=project_dir,
                )
            except Exception:
                pass
            return resolution

        if resolution.action == "finalize":
            resolution.force_final = True
            return resolution

        if resolution.action == "end_session":
            resolution.end_session = True
            return resolution

        return resolution

    def apply_model_swap(
        self,
        *,
        orchestrator_model: Optional[str],
        orchestrator_provider: Optional[str],
        generator_model: Optional[str],
        generator_provider: Optional[str],
        state: Any,
        pipeline: Any,
        prefilter_model: Optional[str] = None,
        prefilter_provider: Optional[str] = None,
        tester_model: Optional[str] = None,
        tester_provider: Optional[str] = None,
        agent_role: str = "orchestrator",
        target_role: Optional[str] = None,
    ) -> None:
        """Assert the user's selection into state/pipeline and reset HTTP pool.

        Supports both CLI ``AppState`` and TUI ``StateAdapter`` shapes. A
        selection of the same model id under a different provider is treated as
        a real change (provider travels with the chosen menu entry).
        """
        try:
            if state is not None:
                try:
                    if orchestrator_model:
                        state.use_router = False
                        if hasattr(state, "fixed_orchestrator_model"):
                            state.fixed_orchestrator_model = orchestrator_model
                        if hasattr(state, "orchestrator_model"):
                            state.orchestrator_model = orchestrator_model
                        if hasattr(state, "prefilter_model"):
                            state.prefilter_model = orchestrator_model
                    if orchestrator_provider is not None:
                        if hasattr(state, "orchestrator_provider"):
                            state.orchestrator_provider = orchestrator_provider
                        if hasattr(state, "prefilter_provider"):
                            state.prefilter_provider = orchestrator_provider
                    if generator_model:
                        if hasattr(state, "generator_model"):
                            state.generator_model = generator_model
                    if generator_provider is not None:
                        if hasattr(state, "generator_provider"):
                            state.generator_provider = generator_provider
                    # Role-specific additions
                    if prefilter_model is not None and hasattr(state, "prefilter_model"):
                        state.prefilter_model = prefilter_model
                    if prefilter_provider is not None and hasattr(state, "prefilter_provider"):
                        state.prefilter_provider = prefilter_provider
                    if tester_model is not None and hasattr(state, "tester_model"):
                        state.tester_model = tester_model
                    if tester_provider is not None and hasattr(state, "tester_provider"):
                        state.tester_provider = tester_provider
                except Exception as e:
                    logger.warning(f"apply_model_swap: state update failed: {e}")

            if pipeline is not None:
                try:
                    if orchestrator_model:
                        pipeline.set_orchestrator_model(orchestrator_model)
                    if orchestrator_provider is not None and hasattr(
                        pipeline, "set_orchestrator_provider"
                    ):
                        pipeline.set_orchestrator_provider(orchestrator_provider)
                    if generator_model:
                        pipeline.set_generator_model(generator_model)
                    if generator_provider is not None and hasattr(
                        pipeline, "set_generator_provider"
                    ):
                        pipeline.set_generator_provider(generator_provider)
                except Exception as e:
                    logger.warning(f"apply_model_swap: pipeline update failed: {e}")

            # Persist if a persistence helper is available
            try:
                from app.tui.settings_io import save_user_settings  # type: ignore

                payload = {
                    "use_router": getattr(state, "use_router", False),
                    "fixed_orchestrator_model": getattr(
                        state, "orchestrator_model", None
                    ),
                    "orchestrator_provider": getattr(
                        state, "orchestrator_provider", None
                    ),
                    "generator_model": getattr(state, "generator_model", None),
                    "generator_provider": getattr(
                        state, "generator_provider", None
                    ),
                }
                if prefilter_model is not None:
                    payload["prefilter_model"] = getattr(state, "prefilter_model", None)
                if prefilter_provider is not None:
                    payload["prefilter_provider"] = getattr(state, "prefilter_provider", None)
                if tester_model is not None:
                    payload["tester_model"] = getattr(state, "tester_model", None)
                if tester_provider is not None:
                    payload["tester_provider"] = getattr(state, "tester_provider", None)
                save_user_settings(payload)
            except Exception:
                pass

            # Clear shared HTTP pool so a genuinely new AsyncOpenAI client is created
            try:
                from app.llm.api_client import _provider_clients

                _provider_clients.clear()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"apply_model_swap failed: {e}")


_singleton_lock = threading.Lock()
_singleton_instance: Optional[EmergencyRecoveryManager] = None


def get_emergency_manager() -> EmergencyRecoveryManager:
    """Return the process-wide ``EmergencyRecoveryManager`` singleton (thread-safe)."""
    global _singleton_instance
    if _singleton_instance is None:
        with _singleton_lock:
            if _singleton_instance is None:
                _singleton_instance = EmergencyRecoveryManager()
    return _singleton_instance


def get_inflight_model_swap(resolution: Any) -> Optional[Dict[str, Optional[str]]]:
    """Derive an in-flight model/provider swap from an emergency resolution.

    Returns a dict of the form ``{"model": <model or None>,
    "provider": <provider or None>}`` when the resolution approves a
    ``continue`` action AND carries a real model or provider change.

    The pair is selected based on the normalized role from
    ``resolution.target_role`` or ``resolution.agent_role``:
    - ``pre_filter`` prefers ``prefilter_model``/``prefilter_provider`` with a
      fallback to the orchestrator fields;
    - ``tester`` prefers ``tester_model``/``tester_provider`` with a fallback
      to the orchestrator fields;
    - every other role (including ``generator``, ``orchestrator``,
      ``pipeline``, unknown) uses exactly the orchestrator fields.

    Returns ``None`` when the resolution is absent, when its action is not
    ``"continue"``, or when both effective model and provider are empty.
    Pure, no I/O, never raises.
    """
    try:
        if resolution is None:
            return None
        if getattr(resolution, "action", None) != "continue":
            return None

        target_role = getattr(resolution, "target_role", None)
        if not target_role:
            target_role = getattr(resolution, "agent_role", None)
        canonical = normalize_emergency_role(target_role) if target_role else "orchestrator"

        if canonical == "pre_filter":
            model_raw = getattr(resolution, "prefilter_model", None) or getattr(
                resolution, "orchestrator_model", None
            )
            provider_raw = getattr(resolution, "prefilter_provider", None) or getattr(
                resolution, "orchestrator_provider", None
            )
        elif canonical == "tester":
            model_raw = getattr(resolution, "tester_model", None) or getattr(
                resolution, "orchestrator_model", None
            )
            provider_raw = getattr(resolution, "tester_provider", None) or getattr(
                resolution, "orchestrator_provider", None
            )
        else:
            model_raw = getattr(resolution, "orchestrator_model", None)
            provider_raw = getattr(resolution, "orchestrator_provider", None)

        model: Optional[str] = model_raw if model_raw else None
        provider: Optional[str] = provider_raw if provider_raw else None
        if model is None and provider is None:
            return None
        return {"model": model, "provider": provider}
    except Exception:
        return None




def normalize_emergency_role(agent_role: str) -> str:
    """Canonicalize an incoming agent role to a known emergency role.

    Maps aliases (e.g. ``prefilter``/``planner``/``code_generator``) to one of
    the canonical roles: ``orchestrator``, ``pre_filter``, ``tester``,
    ``generator`` or ``pipeline``. Empty/``None``/unknown values map to
    ``"orchestrator"``. Pure, deterministic, never raises.
    """
    try:
        if not agent_role:
            return "orchestrator"
        lowered = str(agent_role).strip().lower()
        aliases = {
            "orchestrator": "orchestrator",
            "planner": "orchestrator",
            "pre_filter": "pre_filter",
            "prefilter": "pre_filter",
            "pre-filter": "pre_filter",
            "tester": "tester",
            "test": "tester",
            "testing": "tester",
            "generator": "generator",
            "code_generator": "generator",
            "codegenerator": "generator",
            "pipeline": "pipeline",
        }
        return aliases.get(lowered, "orchestrator")
    except Exception:
        return "orchestrator"


def get_emergency_role_label(agent_role: str) -> str:
    """Return a human-readable Russian label for an emergency role."""
    try:
        canonical = normalize_emergency_role(agent_role)
        labels = {
            "orchestrator": "Оркестратор",
            "pre_filter": "Пре-Фильтр",
            "tester": "Тестировщик",
            "generator": "Генератор",
            "pipeline": "Пайплайн",
        }
        return labels.get(canonical, "Оркестратор")
    except Exception:
        return "Оркестратор"


def get_emergency_role_model_tier(agent_role: str) -> str:
    """Return the emergency model tier for a role.

    Returns ``"generator"`` only for the generator role; ``"orchestrator"``
    for every other role (Pre-Filter and Tester use orchestrator-tier model
    lists by existing project contract).
    """
    try:
        canonical = normalize_emergency_role(agent_role)
        return "generator" if canonical == "generator" else "orchestrator"
    except Exception:
        return "orchestrator"
