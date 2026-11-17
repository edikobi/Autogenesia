"""Мост UI ⇄ AgentPipeline: полный автомат состояний Agent-режима (1:1 с handle_agent_mode).

АУДИТ ASYNC (требование ТЗ):
- Уже асинхронные (используются напрямую):
    * AgentPipeline (process_request / run_tester / ask_tester_followup /
      run_feedback_cycle / apply_pending_changes / discard_pending_changes);
    * HistoryManager (все методы async; внутри — asyncio.to_thread);
    * api_client (async / httpx);
    * sessions_db (SQLAlchemy async / aiosqlite);
    * переводчик (async-функции, через safe_translate).
- Синхронные сервисы, оборачиваются через run_blocking / asyncio.to_thread:
    * BackupManager, FileModifier, TokenCounter, DependencyManager,
      app.services.index_manager / app.services.index_updater.

Ни один колбэк пайплайна не трогает виджеты напрямую — только dispatch(kind, payload),
который маршаллится в UI-поток (call_later / call_from_thread).
"""

from __future__ import annotations
from app.utils.input_commands import (
    PostTesterCommand,
    classify_post_tester_input,
    is_plan_acceptance,
    is_plan_cancel,
    normalize_command,
)

import asyncio
import contextlib
import sys
import inspect
import logging
import os
import threading
from collections import deque
# (удалено) from datetime import datetime — не используется на уровне модуля
from typing import Any, Callable, Deque, Dict, List, Optional

from app.agents.agent_pipeline import PipelineMode, PipelineStatus

try:
    from .adapters import PipelineAdapter, StateAdapter, create_pipeline
    from .dialogs import (
        open_tester_model_selection,
        push_modal,
        tui_confirm,
        tui_prompt,
    )
    from .translation import safe_translate
except ImportError:  # pragma: no cover — standalone execution (валидатор-демон копирует файл вне пакета)
    from app.tui.adapters import PipelineAdapter, StateAdapter, create_pipeline
    from app.tui.dialogs import (
        open_tester_model_selection,
        push_modal,
        tui_confirm,
        tui_prompt,
    )
    from app.tui.translation import safe_translate

logger = logging.getLogger(__name__)

Dispatcher = Callable[[str, Any], None]


async def run_blocking(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Выполнить синхронный вызов в рабочем потоке (никогда не блокирует цикл)."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await asyncio.to_thread(lambda: fn(*args, **kwargs))



#: Подстроки-маркеры отладочных print'ов пайплайна, которые зеркалим в чат TUI.
_DEBUG_MARKERS: tuple = (
    "[DEBUG-CODE]",
    "[TRACE-STAGE]",
    "[STAGING-P",
    "Старый код",
    "Новый код",
    "Изменения есть",
)

#: Именованные, конечные таймауты для всех ожиданий
#: ``run_coroutine_threadsafe(...).result(timeout=...)``: ask-user диалог,
#: emergency-диалог лимита, emergency-диалог сети и диалоги выбора моделей.
#: Значения сохраняют прежние щедрые окна (600/600/1800/900 с) — это
#: именование/тестируемость, а не сокращение времени на решение пользователя.
#: Константы читаются в момент вызова, поэтому тесты могут подменять их
#: monkeypatch-ом на малое значение.
_MODAL_WAIT_TIMEOUT: float = 600.0
_EMERGENCY_MODAL_TIMEOUT: float = 600.0
_EMERGENCY_NETWORK_TIMEOUT: float = 1800.0
_MODEL_PICK_TIMEOUT: float = 900.0


class DebugTee:
    """Pass-through stdout-обёртка: зеркалирует debug-строки пайплайна в чат.

    Главный контракт — lossless pass-through:
    - ``write`` ВСЕГДА пересылает текст в оригинальный stdout без изменений
      (терминальное поведение CLI/пайплайна сохраняется на 100%);
    - завершённые строки, содержащие любой маркер из ``_DEBUG_MARKERS``,
      дополнительно передаются в ``on_debug_line`` (best-effort);
    - ``write``/``flush`` никогда не бросают исключений.

    Вложенные tee (rework внутри основного запроса, параллельные вкладки)
    сцепляются естественно: pass-through идёт в захваченный «оригинал»,
    который сам может быть чужим tee.
    """

    def __init__(self, original_stdout: Any, on_debug_line: Callable[[str], None]) -> None:
        """Создать tee вокруг оригинального потока с колбэком зеркалирования."""
        self._original = original_stdout
        self._on_debug_line = on_debug_line
        self._buffer: str = ""

    def write(self, s: str) -> int:
        """Переслать запись в оригинальный stdout и, возможно, отзеркалить строку.

        Всегда возвращает количество записанных символов (результат
        оригинального write либо ``len(s)`` при его сбое/отсутствии).
        """
        count: int = len(s)
        try:
            written = self._original.write(s)
            if isinstance(written, int):
                count = written
        except Exception:
            count = len(s)
        try:
            self._buffer += s
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._emit(line)
        except Exception:
            pass
        return count

    def flush(self) -> None:
        """Переслать flush в оригинальный поток; незавершённый хвост — как строка."""
        try:
            if hasattr(self._original, "flush"):
                self._original.flush()
        except Exception:
            pass
        try:
            if self._buffer:
                pending, self._buffer = self._buffer, ""
                self._emit(pending)
        except Exception:
            pass

    def isatty(self) -> bool:
        """Делегировать isatty оригинальному потоку (False при сбое)."""
        try:
            return bool(self._original.isatty())
        except Exception:
            return False

    def _emit(self, line: str) -> None:
        """Проверить завершённую строку на маркеры и вызвать колбэк (best-effort)."""
        try:
            stripped = line.strip()
            if not stripped:
                return
            if any(marker in stripped for marker in _DEBUG_MARKERS):
                self._on_debug_line(stripped)
        except Exception:
            pass

    @property
    def encoding(self) -> Any:
        """Кодировка оригинального потока (некоторые библиотеки её инспектируют)."""
        return getattr(self._original, "encoding", None)

    @property
    def errors(self) -> Any:
        """Режим обработки ошибок кодировки оригинального потока."""
        return getattr(self._original, "errors", None)

    def __getattr__(self, name: str) -> Any:
        """Делегировать прочие атрибуты (например ``fileno``) оригинальному потоку.

        Если атрибут у оригинала отсутствует — возбуждается AttributeError,
        то есть атрибут фактически «не предоставляется» (как у обычного объекта
        без fileno). Внутренние имена (``_...``) делегированию не подлежат.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        original = self.__dict__.get("_original")
        return getattr(original, name)


def build_change_rows(pending_changes: List[Any], diffs: Dict[str, str]) -> List[Dict[str, Any]]:
    """Строки таблицы изменений «Файл | Тип | + | −».

    Доступ к атрибутам PendingChange — только через getattr с безопасными
    дефолтами (пайплайн имеет СВОЙ dataclass PendingChange). Спец-ключ
    ``diffs["__deletions__"]`` превращается в отдельные строки типа "deletion".
    """
    rows: List[Dict[str, Any]] = []
    for change in pending_changes or []:
        path = str(getattr(change, "file_path", "") or "")
        change_type = str(getattr(change, "change_type", "") or getattr(change, "type", "") or "modify")
        block = getattr(change, "code_block", None)
        if block is not None and getattr(block, "mode", None):
            change_type = str(getattr(block, "mode"))
        lines_added = getattr(change, "lines_added", 0) or 0
        lines_removed = getattr(change, "lines_removed", 0) or 0
        if not lines_added and not lines_removed:
            modify_result = getattr(change, "modify_result", None)
            lines_added = getattr(modify_result, "lines_added", 0) or 0
            lines_removed = getattr(modify_result, "lines_removed", 0) or 0
        if not lines_added and not lines_removed:
            diff = (diffs or {}).get(path, "") or ""
            for line in diff.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    lines_added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    lines_removed += 1
        rows.append(
            {
                "path": path,
                "type": change_type,
                "added": int(lines_added or 0),
                "removed": int(lines_removed or 0),
            }
        )

    deletions = (diffs or {}).get("__deletions__")
    if isinstance(deletions, list):
        for d in deletions:
            if not isinstance(d, dict):
                continue
            status = "✓" if d.get("success") else "✗"
            rows.append(
                {
                    "path": f"{status} {d.get('target', '')} ({d.get('type', '')})",
                    "type": "deletion",
                    "added": 0,
                    "removed": 0,
                    "deletion_file": d.get("file", ""),
                    "reason": d.get("reason", ""),
                }
            )
    return rows


class WorkspacePipeline:
    """Per-tab мост: один AgentPipeline, своя история, свой флаг отмены."""

    def __init__(
        self,
        tab_key: str,
        app: Any = None,
        project_dir: Optional[str] = None,
        history_manager: Any = None,
        thread_id: Optional[str] = None,
        project_index: Optional[Dict[str, Any]] = None,
        enable_type_checking: bool = False,
        generator_model: Optional[str] = None,
        dispatch: Optional[Dispatcher] = None,
        state: Optional[StateAdapter] = None,
        pipeline: Any = None,
        history_adapter: Any = None,
    ) -> None:
        self.tab_key = tab_key
        self._app = app

        # UI event loop captured as early as possible (None вне loop).
        try:
            self._app_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._app_loop = None
        # Re-entrant DebugTee state (вложенные/rework-вызовы одного пайплайна).
        self._debug_tee_lock = threading.Lock()
        self._debug_tee_depth = 0
        self._dispatch_fn = dispatch
        self.state = state or StateAdapter(
            project_dir=project_dir,
            thread_id=thread_id,
            project_index=project_index or {},
            enable_type_checking=enable_type_checking,
            generator_model=generator_model,
        )
        if pipeline is not None:
            self.pipeline = pipeline if isinstance(pipeline, PipelineAdapter) else PipelineAdapter(pipeline)
        else:
            self.pipeline = PipelineAdapter(
                create_pipeline(
                    project_dir=project_dir,
                    history_manager=history_manager,
                    thread_id=thread_id,
                    project_index=self.state.project_index,
                    enable_type_checking=self.state.enable_type_checking,
                    generator_model=self.state.generator_model,
                )
            )
        self.history_adapter = history_adapter
        self.history: List[Dict[str, str]] = []
        self._cancel_event = asyncio.Event()
        self._worker: Any = None
        self._token_buffer: Deque[str] = deque(maxlen=600)
        self._bg_tasks: set = set()
        self._last_result: Any = None
        self._last_canonical_plan: str = ""
        self._history_manager = history_manager
        # Асинхронный гейт пользовательского ввода (контрольные точки)
        self._gate: Optional[asyncio.Future] = None
        self._gate_kind: Optional[str] = None
        self._pending_changes: List[Any] = []
        self._diffs: Dict[str, str] = {}
        self._tester_report: Any = None
        self._applied_files: List[str] = []
        self.sync_models_to_pipeline()

        # Register TUI callbacks (idempotent, never raises).
        try:
            from app.tools.ask_user import set_ask_user_callback

            set_ask_user_callback(self._tui_ask_user_sync)
        except Exception as e:
            logger.debug(f"register ask_user callback failed: {e}")
        try:
            from app.services.emergency_recovery import get_emergency_manager

            mgr = get_emergency_manager()
            mgr.set_resolution_callback(self._tui_emergency_resolution)
            mgr.set_notify_callback(lambda msg: self.dispatch("log", msg))
        except Exception as e:
            logger.debug(f"register emergency callbacks failed: {e}")

    # ------------------------------------------------------------------ utils

    def sync_models_to_pipeline(self) -> None:
        """Применить модели/провайдеры из StateAdapter к пайплайну."""
        try:
            self.pipeline.set_orchestrator_model(self.state.get_current_orchestrator_model())
            self.pipeline.set_orchestrator_provider(self.state.get_current_orchestrator_provider())
            if self.state.get_current_generator_model():
                self.pipeline.set_generator_model(self.state.get_current_generator_model())
            # Emergency generator-provider swap passthrough
            try:
                self.pipeline.set_generator_provider(
                    getattr(self.state, "generator_provider", None)
                )
            except Exception as gen_err:
                logger.debug(f"sync_models_to_pipeline: generator provider failed: {gen_err}")
        except Exception as e:
            logger.debug(f"sync_models_to_pipeline failed: {e}")

    def rebind_project(
        self,
        project_dir: Optional[str],
        project_index: Optional[Dict[str, Any]],
        thread_id: Optional[str],
        is_new_project: bool,
    ) -> None:
        """Rebind the current workspace to a new project without creating a new tab.

        Updates StateAdapter fields, rebuilds the AgentPipeline via the stored
        history manager and ``create_pipeline(...)``, preserves the
        PipelineAdapter wrapper, dispatch behaviour, cancel/gate state and
        callbacks. Calls ``sync_models_to_pipeline()`` after rebuilding.
        On failure the previous pipeline is left usable.

        Args:
            project_dir: New project directory (or None).
            project_index: Loaded semantic index for the new project.
            thread_id: Conversation thread bound to the new project.
            is_new_project: Whether the new project has no on-disk index yet.
        """
        try:
            old_pipeline = self.pipeline
            old_history_manager = self._history_manager
        except Exception:
            old_pipeline = None
            old_history_manager = None

        try:
            self.state.project_dir = project_dir
            self.state.project_index = project_index or {}
            self.state.thread_id = thread_id
            self.state.is_new_project = bool(is_new_project)

            new_pipeline = create_pipeline(
                project_dir=project_dir,
                history_manager=self._history_manager,
                thread_id=thread_id,
                project_index=self.state.project_index,
                enable_type_checking=self.state.enable_type_checking,
                generator_model=self.state.generator_model,
            )
            self.pipeline = (
                new_pipeline
                if isinstance(new_pipeline, PipelineAdapter)
                else PipelineAdapter(new_pipeline)
            )
            self._last_canonical_plan = ""
            self.sync_models_to_pipeline()
            self.dispatch("session_status", {})
            logger.info(f"[{self.tab_key}] rebind_project → {project_dir}")
        except Exception as e:
            logger.error(f"[{self.tab_key}] rebind_project failed: {e}", exc_info=True)
            # Restore previous pipeline so the existing tab keeps working
            if old_pipeline is not None:
                self.pipeline = old_pipeline

    async def wait_gate_input(self, kind: str, prompt_text: str) -> str:
        """Открыть асинхронную контрольную точку и дождаться ввода пользователя.

        Устанавливает ``state.pending_stage`` по kind, отправляет dispatch
        ``control_point`` (UI покажет панель кнопок + подсказку в чате), ждёт
        ``asyncio.Future``, который резолвится через ``resolve_gate(value)``.
        """
        kind_to_stage = {
            "plan": "AWAITING_PLAN_CONFIRM",
            "confirmation": "AWAITING_CONFIRMATION",
            "post_tester": "AWAITING_POST_TESTER_DECISION",
        }
        try:
            self.state.pending_stage = kind_to_stage.get(kind, "IDLE")
        except Exception:
            pass
        self.dispatch("control_point", {"kind": kind, "prompt": prompt_text})
        loop = asyncio.get_running_loop()
        self._gate = loop.create_future()
        self._gate_kind = kind
        try:
            value = await self._gate
        except asyncio.CancelledError:
            self._gate = None
            self._gate_kind = None
            raise
        finally:
            self._gate_kind = None
            try:
                if self.state.pending_stage != "IDLE":
                    self.state.pending_stage = "RUNNING_GENERATOR"
            except Exception:
                pass
        return str(value) if value is not None else ""

    def resolve_gate(self, value: str) -> bool:
        """Резолвить активный гейт из UI-потока или ``_submit_query``."""
        if self._gate is not None and not self._gate.done():
            try:
                self._gate.set_result(value)
                self.dispatch("gate_resolved", {})
                return True
            except Exception as e:
                logger.debug(f"resolve_gate set_result failed: {e}")
                return False
        return False

    def active_gate(self) -> Optional[str]:
        """Текущий тип активного гейта или None."""
        return self._gate_kind

    def handle_action(self, action: str, payload: str = "") -> bool:
        """Публичный диспетчер кнопок панели контекстных действий.

        Использует уникальные сентинел-строки, которые каждый гейт
        интерпретирует сам ДО штатной классификации (устраняет конфликт
        "2"=tester в confirmation и REWORK в post_tester).
        """
        if action == "diff":
            self.dispatch("open_diff", {})
            return True
        mapping = {
            "apply": "!apply",
            "accept": "!apply",
            "rework": "!rework",
            "cancel": "!cancel",
            "tester": "!tester",
            "rerun_tester": "!tester",
            "plan_accept": "!ok",
            "plan_cancel": "!cancel",
            "plan_edit": "__plan_edit__",
        }
        value = mapping.get(action)
        if value is None:
            return False
        return self.resolve_gate(value)

    async def _maybe_translate(self, text: str, label: str) -> str:
        """Опциональный перевод текста (учитывает ``state.auto_translate``)."""
        try:
            auto = bool(getattr(self.state, "auto_translate", True))
        except Exception:
            auto = True
        if not auto:
            return text
        try:
            translated = await safe_translate(text, label)
            return translated if translated else text
        except Exception as e:
            logger.debug(f"_maybe_translate failed: {e}")
            return text

    def session_snapshot(self) -> Dict[str, Any]:
        """Словарь параметров сессии для статус-строки. Никогда не бросает."""
        try:
            state = self.state
            provider = "авто"
            try:
                prov = state.get_current_orchestrator_provider()
                if prov:
                    provider = str(prov)
            except Exception:
                pass
            orchestrator = "Роутер"
            try:
                if not getattr(state, "use_router", True):
                    orchestrator = str(state.orchestrator_model or "авто")
            except Exception:
                pass
            tokens = 0
            try:
                tokens = int(self.pipeline.estimate_context_tokens(self.history))
            except Exception:
                pass
            vfs_files = 0
            try:
                vfs_files = len([k for k in (self._diffs or {}) if k != "__deletions__"])
            except Exception:
                vfs_files = 0
            project = ""
            try:
                if state.project_dir:
                    project = os.path.basename(state.project_dir)
                    if project.endswith("/") or project.endswith("\\"):
                        project = project.rstrip("/\\")
            except Exception:
                project = ""
            return {
                "mode": getattr(state, "mode", "agent"),
                "project": project,
                "provider": provider,
                "orchestrator": orchestrator,
                "generator": str(getattr(state, "generator_model", "") or "—"),
                "tester_on": bool(getattr(state, "enable_tester", True)),
                "tokens": tokens,
                "vfs_files": vfs_files,
                "stage": str(getattr(state, "pending_stage", "IDLE")),
            }
        except Exception as e:
            logger.debug(f"session_snapshot failed: {e}")
            return {
                "mode": "agent",
                "project": "",
                "provider": "авто",
                "orchestrator": "Роутер",
                "generator": "—",
                "tester_on": True,
                "tokens": 0,
                "vfs_files": 0,
                "stage": "IDLE",
            }

    def dispatch(self, kind: str, payload: Any = None) -> None:
        """Отправить UI-сообщение (kind, payload); виджеты не трогаются напрямую."""
        fn = self._dispatch_fn
        if fn is None:
            return
        app = self._app
        try:
            if app is not None and threading.current_thread() is not threading.main_thread():
                app.call_from_thread(fn, kind, payload)
            else:
                fn(kind, payload)
        except Exception:
            logger.debug(f"dispatch({kind}) failed", exc_info=True)

    def _check_cancel(self) -> None:
        """Локальный guard отмены (дополнение к флагу пайплайна)."""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError()

    @property
    def has_pending(self) -> bool:
        """Есть ли неподтверждённые изменения."""
        return bool(self._pending_changes) or any(k != "__deletions__" for k in self._diffs)

    # ------------------------------------------------------------- callbacks

    def on_thinking(self, text: str) -> None:
        """Размышления агента → перевод (safe_translate) → UI."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._translate_and_send_thinking(text))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            # Колбэк из стороннего потока — отправляем как есть.
            self.dispatch("thinking", text)

    async def _translate_and_send_thinking(self, text: str) -> None:
        """Перевести размышления и отправить в UI."""
        try:
            translated = await safe_translate(text, "AI agent reasoning")
        except Exception:
            translated = text
        self.dispatch("thinking", translated)

    def on_tool_call(self, name: str, args: Dict, output: str, success: bool) -> None:
        """Вызов инструмента → UI."""
        args_str = str(args)
        if len(args_str) > 80:
            args_str = args_str[:77] + "..."
        self.dispatch(
            "tool_call",
            {
                "name": name,
                "args": args_str,
                "output": (output or "")[:200],
                "success": bool(success),
                "icon": "✅" if success else "❌",
            },
        )

    def on_validation(self, result: Dict[str, Any]) -> None:
        """Результаты валидации → UI."""
        data = result if isinstance(result, dict) else {}
        self.dispatch(
            "validation",
            {
                "success": bool(data.get("success", False)),
                "errors": data.get("error_count", 0),
                "warnings": data.get("warning_count", 0),
            },
        )

    def on_status(self, status: PipelineStatus, message: str) -> None:
        """Статус пайплайна → UI."""
        try:
            status_value = getattr(status, "value", str(status))
        except Exception:
            status_value = str(status)
        self.dispatch("status", {"status": status_value, "message": message or ""})

    def on_stage(self, stage: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Этап пайплайна → UI."""
        self.dispatch("stage", {"stage": stage, "message": message, "details": details or {}})

    def on_token(self, delta: str) -> None:
        """Высокочастотный поток токенов: буфер deque (flush через set_interval в UI)."""
        if delta:
            self._token_buffer.append(delta)

    def flush_tokens(self) -> None:
        """Слить буфер токенов в одно UI-сообщение (вызывается интервалом UI)."""
        if not self._token_buffer:
            return
        chunk = "".join(self._token_buffer)
        self._token_buffer.clear()
        if chunk:
            self.dispatch("token", chunk)

    async def on_user_decision(self, decision_type: str, ctx: Dict[str, Any]) -> str:
        """Модальное решение пользователя (конфликт Оркестратор ↔ Валидатор / DIRECT_ANSWER)."""
        if decision_type == "direct_answer_confirmation":
            analysis = str(ctx.get("analysis", "") or "")
            translated = await self._maybe_translate(analysis, "AI response")
            if translated:
                self.dispatch("markdown", translated[:6000])
            hint = (
                "Enter с пустым полем — ПРИНЯТЬ прямой ответ Оркестратора. "
                "Ввод текста — отправить его Оркестратору как НОВОЕ сообщение пользователя "
                "(перезапуск пайплайна с первой итерации)."
            )
            answer = await tui_prompt(
                self._app,
                hint,
                free_input=True,
                default="",
                multiline=False,
                title="💬 Подтверждение прямого ответа",
            )
            if answer is None:
                return "accept"
            if not answer.strip():
                return "accept"
            if answer.strip().lower() == "accept":
                return "accept"
            return answer

        if decision_type != "orchestrator_override":
            return "proceed"
        ai_result = ctx.get("ai_result") or {}
        verdict = str(ai_result.get("verdict", "") or "")
        reasoning = str(ctx.get("orchestrator_reasoning", "") or "")
        verdict = await safe_translate(verdict, "AI validator verdict")
        reasoning = await safe_translate(reasoning, "orchestrator reasoning")
        self.dispatch(
            "log",
            f"⚠️ Конфликт Валидатор↔Оркестратор:\nВалидатор: {verdict[:300]}\nОркестратор: {reasoning[:300]}",
        )
        choice = await tui_prompt(
            self._app,
            "Оркестратор НЕ согласен с Валидатором. Ваше решение?",
            choices=[
                "proceed — ✅ Доверять Оркестратору (идём на тесты)",
                "force_fix — ⚠️ Согласиться с Валидатором (исправить)",
                "cancel — ❌ Отменить запрос",
            ],
            title="⚠️ Требуется решение",
        )
        if choice is None:
            return "cancel"
        if choice.startswith("force_fix"):
            return "force_fix"
        if choice.startswith("cancel"):
            return "cancel"
        return "proceed"

    async def _show_ask_user_dialog(
        self, question: str, options: Optional[List[str]], context: str
    ) -> str:
        """Показать модальный диалог ask_user и вернуть ответ ("" при отмене)."""
        if self._app is None:
            return ""
        try:
            from .dialogs import open_ask_user_dialog
        except Exception:
            try:
                from app.tui.dialogs import open_ask_user_dialog
            except Exception:
                return ""
        try:
            answer = await open_ask_user_dialog(self._app, question, options, context)
            return answer or ""
        except Exception as e:
            logger.warning(f"_show_ask_user_dialog failed: {e}")
            return ""

    def _tui_ask_user_sync(
        self, question: str, options: List[str], context: str
    ) -> str:
        """Синхронный callback для ask_user_tool (вызывается из to_thread worker'а).

        Маршалит корутину через ``run_coroutine_threadsafe`` на сохранённый main
        loop. Если мы уже находимся на этом же loop — возвращает "" (никогда
        не блокирует UI и не бросает исключений).
        """
        try:
            if self._app is None:
                return ""
            loop = getattr(self, "_app_loop", None)
            if loop is None or not loop.is_running():
                return ""
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                # На main loop — нельзя блокировать себя.
                return ""
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._show_ask_user_dialog(question, options, context),
                    loop,
                )
            except Exception as e:
                logger.warning(f"_tui_ask_user_sync schedule failed: {e}")
                return ""
            try:
                return future.result(timeout=_MODAL_WAIT_TIMEOUT)
            except Exception as e:
                logger.warning(f"_tui_ask_user_sync wait failed: {e}")
                # Конечный таймаут: снять зависшую модалку на UI-loop, чтобы
                # ни один экран не остался и воркер не завис.
                try:
                    from .dialogs import resolve_modal as _rm

                    _app_ref = self._app
                    _loop_ref = self._app_loop

                    def _timeout_resolve() -> None:
                        """Резолв верхнего ожидающего ask/emergency модала."""
                        try:
                            stack = list(getattr(_app_ref, "screen_stack", []) or [])
                            for _scr in reversed(stack[1:]):
                                if type(_scr).__name__ in (
                                    "AskUserDialog",
                                    "EmergencyNetworkDialog",
                                    "EmergencyLimitDialog",
                                ):
                                    _rm(_scr, None)
                                    return
                        except Exception:
                            logger.debug("timeout modal resolve failed", exc_info=True)

                    if _loop_ref is not None and _loop_ref.is_running():
                        _loop_ref.call_soon_threadsafe(_timeout_resolve)
                except Exception:
                    logger.debug("timeout resolve schedule failed", exc_info=True)
                return ""
        except Exception as e:
            logger.warning(f"_tui_ask_user_sync failed: {e}")
            return ""

    async def _show_emergency_network(self, event) -> str:
        """Показать диалог сетевой ошибки; "end_session" при отмене."""
        if self._app is None:
            return "end_session"
        try:
            try:
                from .dialogs import open_emergency_network_dialog
            except Exception:
                from app.tui.dialogs import open_emergency_network_dialog
            result = await open_emergency_network_dialog(self._app, event)
            return result or "end_session"
        except Exception as e:
            logger.warning(f"_show_emergency_network failed: {e}")
            return "end_session"

    async def _show_emergency_limit(self, event) -> str:
        """Показать диалог лимита; "finalize" при отмене."""
        if self._app is None:
            return "finalize"
        try:
            try:
                from .dialogs import open_emergency_limit_dialog
            except Exception:
                from app.tui.dialogs import open_emergency_limit_dialog
            result = await open_emergency_limit_dialog(self._app, event)
            return result or "finalize"
        except Exception as e:
            logger.warning(f"_show_emergency_limit failed: {e}")
            return "finalize"

    async def _pick_models_for_swap(self, action: str, role: str = "orchestrator"):
        """Open model-selection dialogs for orchestrator/generator/both/current_role.

        Returns a dict with keys orchestrator_model/orchestrator_provider/
        generator_model/generator_provider/prefilter_model/prefilter_provider/
        tester_model/tester_provider/target_role/agent_role, or None on cancel.
        """
        try:
            try:
                from .dialogs import ModelSelectDialog, GeneratorModelSelectDialog
            except Exception:
                from app.tui.dialogs import ModelSelectDialog, GeneratorModelSelectDialog

            try:
                from app.services.emergency_recovery import normalize_emergency_role
                canonical_role = normalize_emergency_role(role)
            except Exception:
                canonical_role = "orchestrator"

            orch_model = None
            orch_provider = None
            gen_model = None
            gen_provider = None
            prefilter_model = None
            prefilter_provider = None
            tester_model = None
            tester_provider = None

            def _parse_result(res, dlg):
                if res is None:
                    return None
                if len(res) >= 3:
                    return res[0], res[1], res[2]
                return res[0], res[1], getattr(dlg, "selected_provider", None)

            # Role-specific (current_role / pre_filter / tester) OR orchestrator/both
            do_orchestrator_style = action in ("orchestrator", "both", "current_role", "pre_filter")
            do_tester = (action == "tester") or (action == "current_role" and canonical_role == "tester")
            do_generator = action in ("generator", "both")

            if action == "current_role":
                if canonical_role == "tester":
                    do_orchestrator_style = False
                    do_tester = True
                elif canonical_role == "generator":
                    do_orchestrator_style = False
                    do_generator = True
                elif canonical_role == "pre_filter":
                    do_orchestrator_style = True

            if do_tester:
                try:
                    from .dialogs import open_tester_model_selection
                except Exception:
                    from app.tui.dialogs import open_tester_model_selection
                choice = await open_tester_model_selection(
                    self._app,
                    default_model=getattr(self.state, "orchestrator_model", None),
                )
                if choice is None:
                    return None
                tester_model, tester_provider = choice
                # Mirror into orchestrator so get_inflight_model_swap works unchanged
                if tester_model:
                    orch_model = tester_model
                    orch_provider = tester_provider
                return {
                    "orchestrator_model": orch_model,
                    "orchestrator_provider": orch_provider,
                    "generator_model": gen_model,
                    "generator_provider": gen_provider,
                    "prefilter_model": prefilter_model,
                    "prefilter_provider": prefilter_provider,
                    "tester_model": tester_model,
                    "tester_provider": tester_provider,
                    "target_role": "tester",
                    "agent_role": "tester",
                }

            if do_orchestrator_style:
                result = await push_modal(
                    self._app,
                    lambda: ModelSelectDialog(
                        current_model=getattr(self.state, "orchestrator_model", None)
                    ),
                )
                if result is None:
                    return None
                kind, mid, prov = _parse_result(result, None)
                if kind != "auto":
                    orch_model = mid
                    orch_provider = prov

            if do_generator:
                result_g = await push_modal(
                    self._app,
                    lambda: GeneratorModelSelectDialog(
                        current_model=getattr(self.state, "generator_model", None)
                    ),
                )
                if result_g is None:
                    return None
                kind_g, mid_g, prov_g = _parse_result(result_g, None)
                if kind_g != "auto":
                    gen_model = mid_g
                    gen_provider = prov_g

            # pre_filter mirrors into orchestrator fields so swaps work unchanged
            if canonical_role == "pre_filter" and orch_model:
                prefilter_model = orch_model
                prefilter_provider = orch_provider

            return {
                "orchestrator_model": orch_model,
                "orchestrator_provider": orch_provider,
                "generator_model": gen_model,
                "generator_provider": gen_provider,
                "prefilter_model": prefilter_model,
                "prefilter_provider": prefilter_provider,
                "tester_model": tester_model,
                "tester_provider": tester_provider,
                "target_role": canonical_role,
                "agent_role": canonical_role,
            }
        except Exception as e:
            logger.warning(f"_pick_models_for_swap failed: {e}")
            return None

    def _tui_emergency_resolution(self, event):
        """Sync callback for emergency manager.

        If invoked on the main TUI loop a safe default is returned (cannot block
        the loop for a modal). If invoked from another thread the UI coroutine is
        marshalled onto the saved main loop.
        """
        try:
            from app.services.emergency_recovery import (
                EmergencyResolution,
                EmergencyCategory,
                normalize_emergency_role,
            )
        except Exception:
            return None

        try:
            category = getattr(event, "category", None)
            loop = getattr(self, "_app_loop", None)
            role = normalize_emergency_role(getattr(event, "agent_role", "orchestrator"))

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            on_main_loop = running is not None and running is loop

            if on_main_loop or loop is None or not loop.is_running():
                if category == EmergencyCategory.APP_LIMIT_REACHED:
                    return EmergencyResolution(action="finalize", force_final=True)
                if category == EmergencyCategory.NETWORK_OR_PROVIDER_ERROR:
                    return EmergencyResolution(action="continue")
                return EmergencyResolution(action="propagate")

            if category == EmergencyCategory.APP_LIMIT_REACHED:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._show_emergency_limit(event), loop
                    )
                    result = future.result(timeout=_EMERGENCY_MODAL_TIMEOUT)
                except Exception as e:
                    logger.warning(f"emergency limit dialog failed: {e}")
                    self._schedule_pending_modal_resolve(loop)
                    result = "finalize"
                if result == "extra_quota":
                    return EmergencyResolution(
                        action="extra_quota",
                        extra_tool_calls=10,
                        extra_iterations=5,
                    )
                if result == "end_session":
                    return EmergencyResolution(action="end_session", end_session=True)
                return EmergencyResolution(action="finalize", force_final=True)

            if category == EmergencyCategory.NETWORK_OR_PROVIDER_ERROR:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._show_emergency_network(event), loop
                    )
                    action = future.result(timeout=_EMERGENCY_NETWORK_TIMEOUT)
                except Exception as e:
                    logger.warning(f"emergency network dialog failed: {e}")
                    self._schedule_pending_modal_resolve(loop)
                    action = "end_session"

                if action == "end_session":
                    return EmergencyResolution(action="end_session", end_session=True)

                if action == "retry":
                    return EmergencyResolution(
                        action="retry",
                        retry_reconnect=True,
                        agent_role=role,
                        target_role=role,
                    )

                if action == "current_role" or action in ("pre_filter", "tester"):
                    try:
                        picks_future = asyncio.run_coroutine_threadsafe(
                            self._pick_models_for_swap("current_role", role=role), loop
                        )
                        picks = picks_future.result(timeout=_MODEL_PICK_TIMEOUT)
                    except Exception as e:
                        logger.warning(f"pick models (current_role) failed: {e}")
                        self._schedule_pending_modal_resolve(loop)
                        picks = None
                    if picks is None:
                        return EmergencyResolution(action="continue")
                    try:
                        from app.services.emergency_recovery import get_emergency_manager
                        get_emergency_manager().apply_model_swap(
                            orchestrator_model=picks.get("orchestrator_model"),
                            orchestrator_provider=picks.get("orchestrator_provider"),
                            generator_model=picks.get("generator_model"),
                            generator_provider=picks.get("generator_provider"),
                            prefilter_model=picks.get("prefilter_model"),
                            prefilter_provider=picks.get("prefilter_provider"),
                            tester_model=picks.get("tester_model"),
                            tester_provider=picks.get("tester_provider"),
                            agent_role=role,
                            target_role=picks.get("target_role") or role,
                            state=self.state,
                            pipeline=self.pipeline,
                        )
                    except Exception as e:
                        logger.warning(f"apply_model_swap failed: {e}")
                    try:
                        self.sync_models_to_pipeline()
                    except Exception:
                        pass
                    try:
                        try:
                            from .settings_io import load_user_settings, save_user_settings
                        except Exception:
                            from app.tui.settings_io import load_user_settings, save_user_settings
                        settings = load_user_settings()
                        update_payload = {
                            "use_router": getattr(self.state, "use_router", False),
                            "fixed_orchestrator_model": getattr(self.state, "orchestrator_model", None),
                            "orchestrator_provider": getattr(self.state, "orchestrator_provider", None),
                            "generator_model": getattr(self.state, "generator_model", None),
                            "generator_provider": getattr(self.state, "generator_provider", None),
                        }
                        if picks.get("prefilter_model") is not None:
                            update_payload["prefilter_model"] = getattr(self.state, "prefilter_model", None)
                        if picks.get("prefilter_provider") is not None:
                            update_payload["prefilter_provider"] = getattr(self.state, "prefilter_provider", None)
                        if picks.get("tester_model") is not None:
                            update_payload["tester_model"] = getattr(self.state, "tester_model", None)
                        if picks.get("tester_provider") is not None:
                            update_payload["tester_provider"] = getattr(self.state, "tester_provider", None)
                        settings.update(update_payload)
                        save_user_settings(settings)
                    except Exception:
                        pass
                    return EmergencyResolution(
                        action="continue",
                        agent_role=role,
                        target_role=picks.get("target_role") or role,
                        prefilter_model=picks.get("prefilter_model"),
                        prefilter_provider=picks.get("prefilter_provider"),
                        tester_model=picks.get("tester_model"),
                        tester_provider=picks.get("tester_provider"),
                        orchestrator_model=picks.get("orchestrator_model"),
                        orchestrator_provider=picks.get("orchestrator_provider"),
                        generator_model=picks.get("generator_model"),
                        generator_provider=picks.get("generator_provider"),
                    )

                if action in ("orchestrator", "generator", "both"):
                    try:
                        picks_future = asyncio.run_coroutine_threadsafe(
                            self._pick_models_for_swap(action, role=role), loop
                        )
                        picks = picks_future.result(timeout=_MODEL_PICK_TIMEOUT)
                    except Exception as e:
                        logger.warning(f"pick models failed: {e}")
                        self._schedule_pending_modal_resolve(loop)
                        picks = None
                    if picks is not None:
                        try:
                            from app.services.emergency_recovery import get_emergency_manager
                            get_emergency_manager().apply_model_swap(
                                orchestrator_model=picks.get("orchestrator_model"),
                                orchestrator_provider=picks.get("orchestrator_provider"),
                                generator_model=picks.get("generator_model"),
                                generator_provider=picks.get("generator_provider"),
                                state=self.state,
                                pipeline=self.pipeline,
                            )
                        except Exception as e:
                            logger.warning(f"apply_model_swap failed: {e}")
                        try:
                            self.sync_models_to_pipeline()
                        except Exception:
                            pass
                        try:
                            try:
                                from .settings_io import load_user_settings, save_user_settings
                            except Exception:
                                from app.tui.settings_io import load_user_settings, save_user_settings
                            settings = load_user_settings()
                            settings.update(
                                {
                                    "use_router": getattr(self.state, "use_router", False),
                                    "fixed_orchestrator_model": getattr(self.state, "orchestrator_model", None),
                                    "orchestrator_provider": getattr(self.state, "orchestrator_provider", None),
                                    "generator_model": getattr(self.state, "generator_model", None),
                                    "generator_provider": getattr(self.state, "generator_provider", None),
                                }
                            )
                            save_user_settings(settings)
                        except Exception:
                            pass
                        return EmergencyResolution(
                            action="continue",
                            agent_role=role,
                            target_role=role,
                        )

                return EmergencyResolution(action="continue")

            return EmergencyResolution(action="propagate")
        except Exception as e:
            logger.warning(f"_tui_emergency_resolution failed: {e}")
            try:
                from app.services.emergency_recovery import EmergencyResolution
                return EmergencyResolution(action="propagate")
            except Exception:
                return None

    # ------------------------------------------------------------- main flow

    async def run_request(self, user_request: str) -> None:
        """Основной автомат: pre-filter → process_request → изменения → меню."""
        try:
            self._app_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._app_loop = None
        try:
            with self._capture_debug_output():
                await self._run_request_impl(user_request)
        except asyncio.CancelledError:
            await self._handle_cancelled()
            # Исключение НЕ пробрасывается дальше: воркер завершается штатно.
        except Exception as e:
            logger.error(f"[{self.tab_key}] run_request failed: {e}", exc_info=True)
            self.dispatch("log", f"❌ Ошибка запроса: {e}")
            self.dispatch("status", {"status": PipelineStatus.FAILED.value, "message": str(e)})

    async def _run_request_impl(self, user_request: str) -> None:
        """Имплементация основного автомата."""
        self._cancel_event.clear()
        self.dispatch("status", {"status": PipelineStatus.ANALYZING.value, "message": "Старт запроса..."})

        # Общий чат: отдельный поток обработки (без пайплайна агента)
        if self.state.mode == "general":
            await self.run_general_chat(user_request)
            return

        # 0. История: сохранить запрос, авто-название, загрузить контекст
        if self.history_adapter is not None and self.state.thread_id:
            try:
                await self.history_adapter.save_message(self.state.thread_id, "user", user_request)
                await self.history_adapter.auto_title(self.state.thread_id, user_request)
            except Exception as e:
                logger.warning(f"history save failed: {e}")
            try:
                self.history = await self.history_adapter.get_session_history(
                    self.state.thread_id, user_request, self.state.get_current_orchestrator_model()
                )
            except Exception as e:
                logger.warning(f"history load failed: {e}")
                self.history = []
        else:
            self.history = list(self.history)

        # 1. Pre-filter (обязательная стадия, отказоустойчивая)
        prefilter_advice = ""
        if self.state.project_dir:
            try:
                prefilter_advice = await self._run_prefilter(user_request)
            except Exception as e:
                logger.warning(f"pre-filter skipped: {e}")
                self.dispatch("log", f"⚠️ Pre-filter пропущен: {e}")
        else:
            logger.debug("pre-filter skipped: no project dir")

        self._check_cancel()

        # 1.5. Контрольная точка 1 — гейт архитектурного плана (паритет main.py).
        # Активируется ТОЛЬКО при advanced prefilter + непустом плане (fail-safe).
        try:
            _plan_gate_active = (
                getattr(self.state, "mode", "agent") != "general"
                and getattr(self.state, "prefilter_mode", "normal") == "advanced"
                and bool(getattr(self.state, "project_dir", None))
                and bool(prefilter_advice)
            )
        except Exception:
            _plan_gate_active = False
        if _plan_gate_active:
            while True:
                self._check_cancel()
                gate_text = await self.wait_gate_input(
                    "plan",
                    "📋 План готов. Введите !ok — принять и генерировать, "
                    "!cancel — отменить, либо текст правок к плану:",
                )
                if is_plan_acceptance(gate_text):
                    break
                if is_plan_cancel(gate_text):
                    self.dispatch(
                        "status",
                        {
                            "status": PipelineStatus.CANCELLED.value,
                            "message": "План отклонён пользователем",
                        },
                    )
                    self.dispatch("log", "🚫 План отклонён — запрос отменён")
                    return
                _gm = (gate_text or "").strip()
                if _gm:
                    _project_dir_for_plan = getattr(self.state, "project_dir", None)
                    _version_before = 0
                    try:
                        if _project_dir_for_plan:
                            from app.agents import plan_state as _ps_before
                            _state_b = _ps_before.load_or_init(_project_dir_for_plan)
                            _cur_b = _state_b.get_current()
                            _version_before = _cur_b.version if _cur_b else 0
                    except Exception:
                        _version_before = 0
                    try:
                        _new_advice = await self._run_prefilter(
                            user_request + "\n\n[USER PLAN FEEDBACK]\n" + _gm
                        )
                        if _new_advice:
                            prefilter_advice = _new_advice
                            # Show line-numbered diff (best-effort, never breaks gate)
                            try:
                                if _project_dir_for_plan:
                                    from app.agents import plan_state as _ps_after
                                    _state_a = _ps_after.load_or_init(_project_dir_for_plan)
                                    _cur_a = _state_a.get_current()
                                    _version_after = _cur_a.version if _cur_a else 0
                                    if _version_after > _version_before:
                                        _last_diff = _state_a.get_last_diff()
                                        if _last_diff is not None and not _last_diff.is_empty():
                                            self.dispatch(
                                                "log",
                                                f"📝 План обновлён "
                                                f"(v{_version_before} → v{_version_after}):\n"
                                                + _last_diff.to_unified(),
                                            )
                            except Exception:
                                logger.debug("plan diff display failed", exc_info=True)
                    except Exception as _pe:
                        logger.debug(f"plan feedback regeneration failed: {_pe}")
                # Пустой ввод — повторное открытие гейта без перегенерации.
            # Canonical сохранение — plan_state держит current_plan.md как источник истины.
            try:
                _project_dir = getattr(self.state, "project_dir", None)
                _canonical_source = str(self._last_canonical_plan or "").strip()
                if not _canonical_source:
                    _canonical_source = str(prefilter_advice or "").strip()
                if _project_dir and _canonical_source:
                    from app.agents import plan_state as _plan_state_mod
                    _final_state = _plan_state_mod.load_or_init(_project_dir)
                    _cur = _final_state.get_current()
                    if _cur is None or _cur.content != _canonical_source:
                        _final_state.commit_plan(
                            _project_dir,
                            _canonical_source,
                            summary="Final accepted plan",
                        )
            except Exception as _save_err:
                from app.agents.plan_state import PlanStateError as _PlanStateError
                if isinstance(_save_err, _PlanStateError):
                    logger.error(f"canonical plan commit failed: {_save_err}")
                    self.dispatch("log", f"❌ Ошибка сохранения плана: {_save_err}")
                    self.dispatch(
                        "status",
                        {
                            "status": PipelineStatus.FAILED.value,
                            "message": "План не сохранён",
                        },
                    )
                    return
                logger.debug(f"current_plan.md save failed: {_save_err}")

        # 2. Основной пайплайн
        self.sync_models_to_pipeline()
        mode = PipelineMode.ASK if self.state.mode == "ask" else PipelineMode.AGENT

        # Rebuild advisory from canonical plan when planning (never treat formatted text as canonical).
        _process_advice = prefilter_advice
        if self.state.prefilter_mode == "advanced":
            _canon = str(self._last_canonical_plan or "").strip()
            if not _canon:
                self.dispatch("log", "❌ План не сформирован — отмена запроса")
                self.dispatch(
                    "status",
                    {
                        "status": PipelineStatus.FAILED.value,
                        "message": "Пустой план",
                    },
                )
                return
            _process_advice = self._format_canonical_plan_for_orchestrator(_canon)

        result = await self.pipeline.process_request(
            user_request=user_request,
            history=self.history,
            mode=mode,
            on_thinking=self.on_thinking,
            on_tool_call=self.on_tool_call,
            on_validation=self.on_validation,
            on_status=self.on_status,
            on_stage=self.on_stage,
            on_user_decision=self.on_user_decision,
            on_token=self.on_token,
            prefilter_advice=_process_advice,
        )
        self._last_result = result
        self._pending_changes = list(getattr(result, "pending_changes", []) or [])
        self._diffs = dict(getattr(result, "diffs", {}) or {})

        # 3. Таблица изменений + диффы
        self._publish_changes()

        status = getattr(result, "status", None)
        if getattr(result, "success", False) and not self._pending_changes:
            analysis = getattr(result, "analysis", "") or ""
            analysis = await safe_translate(analysis, "AI response")
            self.dispatch("log", f"💬 {analysis[:6000]}")
            await self._save_assistant_message(analysis)
            self.dispatch("status", {"status": PipelineStatus.COMPLETED.value, "message": "Ответ без изменений кода"})
            return
        if status == PipelineStatus.CANCELLED:
            self.dispatch("status", {"status": PipelineStatus.CANCELLED.value, "message": "Запрос отменён"})
            return
        if status == PipelineStatus.FAILED or not getattr(result, "success", False):
            errors = list(getattr(result, "errors", []) or [])
            self.dispatch("log", "❌ Пайплайн завершился с ошибкой:\n" + "\n".join(f"• {e}" for e in errors[:10]))
            await self._save_assistant_message("## ❌ Ошибка выполнения\n" + "\n".join(f"- {e}" for e in errors))
            self.dispatch("status", {"status": PipelineStatus.FAILED.value, "message": "; ".join(str(e) for e in errors[:3])})
            return

        self.dispatch(
            "status",
            {"status": PipelineStatus.AWAITING_CONFIRMATION.value, "message": "Изменения готовы к просмотру"},
        )

        # 3.7. Авто-тестер (если включён)
        if bool(getattr(self.state, "enable_tester", False)):
            try:
                outcome = await self._run_tester_stage()
                if outcome == "apply":
                    if await self._apply_changes():
                        await self._save_assistant_summary()
                        self.dispatch("status", {"status": PipelineStatus.IDLE.value, "message": "Готов к следующему запросу"})
                        return
                elif outcome == "cancel":
                    await self.discard()
                    await self._save_assistant_summary()
                    self.dispatch("status", {"status": PipelineStatus.IDLE.value, "message": "Готов к следующему запросу"})
                    return
                else:  # "rework" → переопубликованы; идём в обычный цикл подтверждения
                    self._pending_changes = list(getattr(self._last_result, "pending_changes", []) or [])
                    self._diffs = dict(getattr(self._last_result, "diffs", {}) or {})
                    self._publish_changes()
            except Exception as e:
                logger.warning(f"auto-tester failed: {e}")

        # 4..7. Цикл подтверждения (меню / тестировщик / доработка / применение)
        while True:
            self._check_cancel()
            action = await self._confirmation_menu()
            if action == "apply":
                if await self._apply_changes():
                    break
                continue
            if action == "cancel":
                await self.discard()
                break
            if action == "tester":
                outcome = await self._run_tester_stage()
                if outcome == "apply":
                    if await self._apply_changes():
                        break
                elif outcome == "cancel":
                    await self.discard()
                    break
                # после "rework" — ожидаем переопубликацию changes
                self._pending_changes = list(getattr(self._last_result, "pending_changes", []) or [])
                self._diffs = dict(getattr(self._last_result, "diffs", {}) or {})
                self._publish_changes()
                continue
            # rework_text без модального промпта
            if action.startswith("rework_text:"):
                feedback_text = action[len("rework_text:"):]
                await self.rework(feedback_text)
                self._pending_changes = list(getattr(self._last_result, "pending_changes", []) or [])
                self._diffs = dict(getattr(self._last_result, "diffs", {}) or {})
                self._publish_changes()
                continue
            # rework
            feedback = await tui_prompt(
                self._app,
                "Введите замечания для доработки:",
                free_input=True,
                title="✏️ Доработка",
                multiline=True,
            )
            await self.rework(feedback or "")

        await self._save_assistant_summary()
        self.dispatch("status", {"status": PipelineStatus.IDLE.value, "message": "Готов к следующему запросу"})

    async def _confirmation_menu(self) -> str:
        """Меню подтверждения изменений через гейт + локальный классификатор.

        Принятие/отмена/тестер — по локальным наборам (normalize_command).
        Любой другой непустой ввод трактуется как замечание → ``rework_text:<text>``.
        Пустой ввод / None → повторить гейт (не отменять).
        """
        files = [row.get("path", "") for row in build_change_rows(self._pending_changes, self._diffs)]
        listing = "\n".join(f"  • {p}" for p in files[:10]) or "  • (нет файлов)"
        accept_set = {"1", "да", "da", "ok", "y", "yes", "принять", "apply", "принял"}
        cancel_set = {"3", "нет", "no", "n", "отмена", "cancel", "выход", "exit"}
        tester_set = {"2", "тестер", "tester", "тест"}
        # Гомоглиф-толерантные нормализованные наборы алиасов
        accept_norm = {normalize_command(a) for a in accept_set}
        cancel_norm = {normalize_command(a) for a in cancel_set}
        tester_norm = {normalize_command(a) for a in tester_set}
        while True:
            prompt = (
                f"Изменения готовы:\n{listing}\n\n"
                "Введите да/нет/тестер или текст замечания для доработки:"
            )
            text = await self.wait_gate_input("confirmation", prompt)
            self._check_cancel()
            if text is None:
                continue
            raw = text.strip()
            if not raw:
                continue
            # 1. Сентинелы кнопок — первыми (детерминированные контрактные значения)
            if raw == "!apply":
                return "apply"
            if raw == "!cancel":
                return "cancel"
            if raw == "!tester":
                return "tester"
            if raw == "!rework":
                return "rework"
            # 2. Гомоглиф-толерантные наборы (aliases нормализуются тем же normalize_command)
            norm = normalize_command(text)
            if not norm:
                continue
            if norm in accept_set or norm in accept_norm:
                return "apply"
            if norm in cancel_set or norm in cancel_norm:
                return "cancel"
            if norm in tester_set or norm in tester_norm:
                return "tester"
            # 3. Любой другой непустой текст — замечание для доработки
            return f"rework_text:{text}"

    def _publish_changes(self) -> None:
        """Отправить таблицу изменений + диффы в UI."""
        rows = build_change_rows(self._pending_changes, self._diffs)
        self.dispatch("changes_ready", {"rows": rows, "diffs": self._diffs, "changes": self._pending_changes})

    # ------------------------------------------------------------- pre-filter

    async def _run_prefilter(self, query: str) -> str:
        """Pre-filter анализ (отказоустойчивый) → строка советов для Оркестратора."""
        if not self.state.project_dir:
            return ""
        try:
            from app.agents.pre_filter import PreFilterMode, analyze_query
        except ImportError as e:
            logger.warning(f"pre_filter unavailable: {e}")
            return ""
        try:
            compact_index = ""
            try:
                compact_path = os.path.join(self.state.project_dir, ".ai-agent", "compact_index.md")
                if os.path.exists(compact_path):
                    with open(compact_path, "r", encoding="utf-8") as f:
                        compact_index = f.read()
            except Exception:
                compact_index = ""
            mode = PreFilterMode.ADVANCED if self.state.prefilter_mode == "advanced" else PreFilterMode.NORMAL
            self.dispatch("log", "🔬 Pre-filter анализ...")
            advice = await analyze_query(
                user_query=query,
                project_map="",
                compact_index=compact_index,
                project_dir=self.state.project_dir,
                index=self.state.project_index or {},
                mode=mode,
                model=self.state.prefilter_model,
                is_planning=(self.state.prefilter_mode == "advanced"),
                is_new_project=self.state.is_new_project,
                on_tool_call=lambda n, a, r, s: self.on_tool_call(n, a, r, s),
                preferred_provider=self.state.prefilter_provider,
                on_token=self.on_token,
            )
            if advice is None:
                self._last_canonical_plan = ""
                return ""
            # In advanced planning mode, advice.raw_response holds the canonical plan.
            if self.state.prefilter_mode == "advanced":
                canonical = str(getattr(advice, "raw_response", "") or "").strip()
                self._last_canonical_plan = canonical
                try:
                    display_md = self._format_advice_for_display(advice)
                    if display_md:
                        self.dispatch("markdown", display_md)
                except Exception:
                    logger.debug("planning display failed", exc_info=True)
            else:
                self._last_canonical_plan = ""
                text_advice = self._format_advice(advice)
                try:
                    display_md = self._format_advice_for_display(advice)
                    if display_md:
                        translated = await safe_translate(display_md, "Pre-filter analysis advice")
                        if translated:
                            self.dispatch("markdown", translated)
                except Exception:
                    pass
                return text_advice
            return self._format_advice(advice)
        except Exception as e:
            logger.warning(f"pre-filter analysis failed: {e}")
            return ""

    def _format_advice(self, advice: Any) -> str:
        """Форматировать совет pre-filter в строку для Оркестратора (паритет main.py).

        Порт ``_format_prefilter_advice_for_orchestrator``: в planning-ветке
        (advanced) используется шаблон агента (для нового проекта — отдельный),
        в обычной — структурированные части заворачиваются в блок
        «🔮 PRE-ANALYSIS ADVISORY». Сбой ленивого импорта шаблонов деградирует
        до сырого текста — pre-filter никогда не ломает поток запроса.
        """
        if isinstance(advice, str):
            return advice
        raw = getattr(advice, "raw_response", None)
        if raw is None:
            # Fallback: словарь или произвольный объект (прежнее поведение).
            if isinstance(advice, dict):
                parts = []
                for key in ("summary", "advice", "recommendation", "suggestions"):
                    val = advice.get(key)
                    if val:
                        if isinstance(val, list):
                            parts.append("\n".join(f"- {item}" for item in val))
                        else:
                            parts.append(str(val))
                return "\n".join(parts)
            return str(advice)
        if not str(raw).strip():
            return ""
        planning = (self.state.prefilter_mode == "advanced")
        new_project = bool(getattr(self.state, "is_new_project", False))
        try:
            if planning:
                if new_project:
                    from app.llm.prompt_templates import (
                        _build_prefilter_advice_section_new_project_agent as _advice_builder,
                    )
                else:
                    from app.llm.prompt_templates import (
                        _build_prefilter_advice_section_agent as _advice_builder,
                    )
                return _advice_builder().format(prefilter_advice=str(raw).strip())
            from app.llm.prompt_templates import _build_prefilter_advice_section

            parts = []
            clarified = str(getattr(advice, "clarified_query", "") or "")
            if clarified.strip():
                parts.append(f"CLARIFIED_QUERY: {clarified}")
            cause = str(getattr(advice, "possible_cause", "") or "")
            if cause.strip() and cause != "N/A":
                parts.append(f"POSSIBLE_CAUSE: {cause}")
            actions = list(getattr(advice, "recommended_actions", []) or [])
            if actions:
                parts.append("RECOMMENDED_ACTIONS:")
                for action in actions:
                    parts.append(f"- {action}")
            files = list(getattr(advice, "files_to_check", []) or [])
            if files:
                parts.append("FILES_TO_CHECK:")
                for file_info in files:
                    parts.append(f"- {file_info}")
            context = str(getattr(advice, "additional_context", "") or "")
            if context.strip() and context != "N/A":
                parts.append(f"ADDITIONAL_CONTEXT: {context}")
            tool_calls = int(getattr(advice, "tool_calls_made", 0) or 0)
            if tool_calls > 0:
                parts.append(f"TOOL_CALLS_MADE: {tool_calls}")
            content = "\n".join(parts) if parts else str(raw).strip()
            return _build_prefilter_advice_section().format(prefilter_advice=content)
        except Exception:
            return str(raw).strip()

    def _format_canonical_plan_for_orchestrator(self, canonical_plan: str) -> str:
        """Wrap the canonical plan using the existing advisory templates for Orchestrator.

        Uses ``_build_prefilter_advice_section_agent`` (existing project) or
        ``_build_prefilter_advice_section_new_project_agent`` (new project).
        Falls back to raw canonical text on import/format failure. Never
        translates, truncates or includes diff markers.

        Args:
            canonical_plan: Full canonical plan body.

        Returns:
            Wrapped advisory string (or raw canonical text on failure).
        """
        raw = (canonical_plan or "").strip()
        if not raw:
            return ""
        try:
            new_project = bool(getattr(self.state, "is_new_project", False))
            if new_project:
                from app.llm.prompt_templates import (
                    _build_prefilter_advice_section_new_project_agent as _advice_builder,
                )
            else:
                from app.llm.prompt_templates import (
                    _build_prefilter_advice_section_agent as _advice_builder,
                )
            return _advice_builder().format(prefilter_advice=raw)
        except Exception as e:
            logger.debug(f"_format_canonical_plan_for_orchestrator fallback: {e}")
            return raw

    # ------------------------------------------------------------- apply / discard

    async def _apply_changes(self) -> bool:
        """Применить изменения через пайплайн (+ индекс, история, резюме, подсказка бэкапа)."""
        try:
            if not self.has_pending:
                self.dispatch("log", "ℹ️ Изменения уже применены или отсутствуют")
                return True
            self.dispatch(
                "status",
                {"status": PipelineStatus.APPLYING.value, "message": "Применение изменений..."},
            )
            result = await self.pipeline.apply_pending_changes()
            if getattr(result, "success", False):
                self._applied_files = list(getattr(result, "applied_files", []) or [])
                created_files = list(getattr(result, "created_files", []) or [])
                backup_id = getattr(result, "backup_session_id", None)
                self._last_apply = result
                self._pending_changes = []
                self._diffs = {}
                self.dispatch("log", "✅ Изменения успешно применены")
                # Обновление индекса проекта (не должно ломать применение)
                await self._update_project_index()
                # Отметить изменения применёнными в истории
                applied_change_ids = getattr(result, "applied_change_ids", None)
                if (
                    self.history_adapter is not None
                    and self.state.thread_id
                    and applied_change_ids
                ):
                    try:
                        await self.history_adapter.mark_changes_applied(list(applied_change_ids))
                    except Exception as e:
                        logger.warning(f"mark_changes_applied failed: {e}")
                # Резюме + подсказка восстановления в историю
                summary_lines: List[str] = ["## ✅ Изменения применены", ""]
                if self._applied_files:
                    summary_lines.append(f"**Изменённых файлов:** {len(self._applied_files)}")
                    for f in self._applied_files:
                        summary_lines.append(f"- ✓ {f}")
                if created_files:
                    summary_lines.append(f"**Созданных файлов:** {len(created_files)}")
                    for f in created_files:
                        summary_lines.append(f"- + {f}")
                if backup_id:
                    summary_lines.append("")
                    summary_lines.append(
                        f"💾 Бэкап: {backup_id} — для отката используйте /restore {backup_id}"
                    )
                summary = "\n".join(summary_lines).strip()
                if summary and summary != "## ✅ Изменения применены":
                    await self._save_assistant_message(summary)
                self.dispatch(
                    "changes_applied",
                    {
                        "files": self._applied_files,
                        "created": created_files,
                        "backup_session_id": backup_id,
                    },
                )
                return True
            errors = list(getattr(result, "errors", []) or [])
            self.dispatch("log", "❌ Ошибка применения изменений:\n" + "\n".join(f"• {e}" for e in errors[:10]))
            return False
        except Exception as e:
            logger.error(f"apply changes failed: {e}", exc_info=True)
            self.dispatch("log", f"❌ Ошибка применения изменений: {e}")
            return False

    async def discard(self) -> None:
        """Отменить все изменения."""
        try:
            await self.pipeline.discard_pending_changes()
        except Exception as e:
            logger.warning(f"discard failed: {e}")
        self._pending_changes = []
        self._diffs = {}
        self.dispatch("changes_discarded", {})
        self.dispatch("log", "🗑️ Изменения отменены")

    async def rework(self, feedback: str, tester_report: Optional[str] = None) -> None:
        """Отправить на доработку с фидбеком (опционально с отчётом Тестировщика).

        Args:
            feedback: Замечания пользователя.
            tester_report: Опциональный отчёт Тестировщика (original_report) для
                контекста Оркестратора.
        """
        if not feedback.strip():
            self.dispatch("log", "⚠️ Фидбек пуст, доработка отменена")
            return
        try:
            self.dispatch(
                "status",
                {"status": PipelineStatus.GENERATING.value, "message": "Доработка..."},
            )
            with self._capture_debug_output():
                result = await self.pipeline.run_feedback_cycle(
                    user_feedback=feedback,
                    history=self.history,
                    tester_report=tester_report,
                    on_user_decision=self.on_user_decision,
                )
            self._last_result = result
            self._pending_changes = list(getattr(result, "pending_changes", []) or [])
            self._diffs = dict(getattr(result, "diffs", {}) or {})
            self._publish_changes()
            if getattr(result, "success", False) and not self._pending_changes:
                analysis = getattr(result, "analysis", "") or ""
                analysis = await safe_translate(analysis, "AI response")
                self.dispatch("log", f"💬 {analysis[:6000]}")
                await self._save_assistant_message(analysis)
                self.dispatch(
                    "status",
                    {"status": PipelineStatus.COMPLETED.value, "message": "Доработка завершена"},
                )
            else:
                self.dispatch(
                    "status",
                    {
                        "status": PipelineStatus.AWAITING_CONFIRMATION.value,
                        "message": "Изменения после доработки",
                    },
                )
        except Exception as e:
            logger.error(f"rework failed: {e}", exc_info=True)
            self.dispatch("log", f"❌ Ошибка доработки: {e}")

    # ------------------------------------------------------------- tester

    async def _run_tester_stage(self) -> str:
        """Стадия Тестировщика → "apply" | "cancel" | "rework".

        Пост-отчётное меню — через гейт ``post_tester`` + контракт
        ``classify_post_tester_input`` (паритет main.py:6228–6280).
        Модальный ввод вопросов доступен как ``QUESTION``.
        Внешний цикл обеспечивает внутренний ретест через `!tester`.
        """
        try:
            while True:
                self.dispatch(
                    "status",
                    {"status": PipelineStatus.VALIDATING.value, "message": "Запуск тестировщика..."},
                )
                # 1. Выбор модели Тестировщика
                choice = await open_tester_model_selection(
                    self._app, default_model=self.state.get_current_orchestrator_model()
                )
                if choice is None:
                    return "cancel"
                tester_model, tester_provider = choice

                # 2. Дополнительные замечания
                additional = await tui_prompt(
                    self._app,
                    "Дополнительные замечания для Тестировщика (пусто — без замечаний):",
                    free_input=True,
                    title="🧪 Тестировщик",
                )
                result = await self.pipeline.run_tester(
                    user_additional_input=additional or "",
                    tester_model=tester_model,
                    tester_provider=tester_provider,
                    on_tool_call=self.on_tool_call,
                    on_token=self.on_token,
                )
                if result is None:
                    self.dispatch("log", "❌ Тестировщик не смог завершить анализ")
                    self.pipeline.cleanup_tester()
                    return "cancel"
                self._tester_report = result

                # 3. Показ отчёта
                text = result.translated_report or result.original_report
                translated = await self._maybe_translate(text, "Tester report")
                marker = "✅" if getattr(result, "success", False) else "❌"
                self.dispatch("log", f"🧪 Отчёт Тестировщика\n{marker} {str(translated)[:4000]}")

                # 4. Пост-отчётное меню через гейт + classify_post_tester_input
                while True:
                    text_input = await self.wait_gate_input(
                        "post_tester",
                        "Ваш выбор (1/2/3/план или текст вопроса):",
                    )
                    self._check_cancel()
                    if not text_input or not text_input.strip():
                        continue
                    _raw = text_input.strip()
                    # ─ Сентинелы кнопок ДО classify ─
                    if _raw == "!apply":
                        await self._apply_changes()
                        self.pipeline.cleanup_tester()
                        return "apply"
                    if _raw == "!cancel":
                        await self.discard()
                        await self._save_assistant_message(
                            "## 🚫 Запрос отменён\n\n*Пользователь вышел после отчёта Тестировщика.*"
                        )
                        self.pipeline.cleanup_tester()
                        return "cancel"
                    if _raw == "!rework":
                        attach = await tui_confirm(
                            self._app, "Прикрепить отчёт Тестировщика к замечанию?", default=True
                        )
                        feedback = await tui_prompt(
                            self._app,
                            "Введите замечания для доработки:",
                            free_input=True,
                            title="✏️ Доработка",
                        )
                        if feedback and feedback.strip():
                            await self.rework(
                                feedback,
                                tester_report=result.original_report if attach else None,
                            )
                        else:
                            self.dispatch("log", "⚠️ Замечание не может быть пустым")
                        self.pipeline.cleanup_tester()
                        return "rework"
                    if _raw == "!tester":
                        # Внутренний ретест: cleanup + полный повтор стадии
                        try:
                            self.pipeline.cleanup_tester()
                        except Exception as _retest_err:
                            logger.debug(f"cleanup_tester on retest failed: {_retest_err}")
                        self.dispatch("log", "🧪 Повторный запуск тестировщика...")
                        break

                    _cmd = classify_post_tester_input(text_input)
                    if _cmd == PostTesterCommand.UNKNOWN_COMMAND:
                        self.dispatch(
                            "log",
                            f"⚠️ Неизвестная команда: {text_input.strip()!r}. "
                            f"Доступно: 1 — применить, 2 — доработка, 3 — выход, "
                            f"/план — экспорт отчёта, либо текст вопроса.",
                        )
                        continue
                    if _cmd == PostTesterCommand.EXPORT:
                        try:
                            export_content = result.original_report or result.translated_report or ""
                            if export_content:
                                from datetime import datetime as _dt

                                base_dir = self.state.project_dir or os.getcwd()
                                export_path = os.path.join(
                                    base_dir,
                                    f"tester_report_{_dt.now().strftime('%Y%m%d_%H%M%S')}.md",
                                )

                                def _write_report(path: str, content: str) -> None:
                                    """Синхронная запись отчёта на диск."""
                                    with open(path, "w", encoding="utf-8") as f:
                                        f.write(content)

                                await run_blocking(_write_report, os.path.abspath(export_path), export_content)
                                self.dispatch("log", f"📄 Отчёт экспортирован: {os.path.abspath(export_path)}")
                            else:
                                self.dispatch("log", "⚠️ Нет содержимого для экспорта")
                        except Exception as export_err:
                            self.dispatch("log", f"❌ Ошибка экспорта: {export_err}")
                        continue
                    if _cmd == PostTesterCommand.APPLY:
                        await self._apply_changes()
                        self.pipeline.cleanup_tester()
                        return "apply"
                    if _cmd == PostTesterCommand.REWORK:
                        attach = await tui_confirm(
                            self._app, "Прикрепить отчёт Тестировщика к замечанию?", default=True
                        )
                        feedback = await tui_prompt(
                            self._app,
                            "Введите замечания для доработки:",
                            free_input=True,
                            title="✏️ Доработка",
                        )
                        if feedback and feedback.strip():
                            await self.rework(
                                feedback,
                                tester_report=result.original_report if attach else None,
                            )
                        else:
                            self.dispatch("log", "⚠️ Замечание не может быть пустым")
                        self.pipeline.cleanup_tester()
                        return "rework"
                    if _cmd == PostTesterCommand.EXIT:
                        await self.discard()
                        await self._save_assistant_message(
                            "## 🚫 Запрос отменён\n\n*Пользователь вышел после отчёта Тестировщика.*"
                        )
                        self.pipeline.cleanup_tester()
                        return "cancel"
                    # QUESTION — прямой вопрос Тестировщику
                    try:
                        followup = await self.pipeline.ask_tester_followup(text_input)
                    except Exception as fu_err:
                        logger.error(f"ask_tester_followup failed: {fu_err}", exc_info=True)
                        self.dispatch("log", f"❌ Ошибка при обращении к Тестировщику: {fu_err}")
                        continue
                    if followup is None:
                        self.dispatch("log", "❌ Тестировщик недоступен")
                        continue
                    fu_response = str(followup.get("response", "") or "")
                    if fu_response:
                        fu_translated = await self._maybe_translate(fu_response, "Tester follow-up")
                        self.dispatch("log", f"💬 Пояснение Тестировщика:\n{fu_translated[:4000]}")
                    if followup.get("is_new_report") and followup.get("new_report"):
                        new_report = await self._maybe_translate(
                            str(followup.get("new_report")), "Updated tester report"
                        )
                        self.dispatch(
                            "log",
                            f"🧪 Обновлённый отчёт Тестировщика:\n{str(new_report)[:4000]}",
                        )
                    continue
                # Inner loop exited via `!tester` → полный повтор стадии
                continue
        except Exception as e:
            logger.error(f"tester stage failed: {e}", exc_info=True)
            self.dispatch("log", f"❌ Ошибка тестировщика: {e}")
            self.pipeline.cleanup_tester()
            return "cancel"

    # ------------------------------------------------------------- history helpers

    async def _save_assistant_message(self, content: str) -> None:
        """Сохранить сообщение ассистента в историю."""
        if self.history_adapter is not None and self.state.thread_id:
            try:
                await self.history_adapter.save_message(self.state.thread_id, "assistant", content)
            except Exception as e:
                logger.warning(f"save assistant message failed: {e}")

    async def _save_assistant_summary(self) -> None:
        """Сохранить итоговое резюме применения в историю (на основе ApplyResult).

        Пайплайн не имеет поля ``PipelineResult.summary`` — резюме строится из
        ``_last_apply``: применённые/созданные файлы и подсказка /restore.
        """
        last_apply = getattr(self, "_last_apply", None)
        if last_apply is None:
            return
        applied_files = list(getattr(last_apply, "applied_files", []) or [])
        created_files = list(getattr(last_apply, "created_files", []) or [])
        backup_id = getattr(last_apply, "backup_session_id", None)
        summary_lines: List[str] = ["## ✅ Итог запроса", ""]
        if applied_files:
            summary_lines.append(f"**Изменённых файлов:** {len(applied_files)}")
            for f in applied_files:
                summary_lines.append(f"- ✓ {f}")
        if created_files:
            summary_lines.append(f"**Созданных файлов:** {len(created_files)}")
            for f in created_files:
                summary_lines.append(f"- + {f}")
        if backup_id:
            summary_lines.append("")
            summary_lines.append(
                f"💾 Бэкап: {backup_id} — для отката используйте /restore {backup_id}"
            )
        summary = "\n".join(summary_lines).strip()
        if not summary or summary == "## ✅ Итог запроса":
            return
        await self._save_assistant_message(summary)

    async def _handle_cancelled(self) -> None:
        """Emergency Stop cleanup: полный набор освобождений, каждое — в своём try/except."""
        self.dispatch("log", "⏹️ Запрос отменён")

        # Гейт: активный контрольный пункт — сбрасываем без resolve_gate
        try:
            if self._gate is not None:
                self._gate = None
                self._gate_kind = None
        except Exception:
            pass
        self.dispatch("status", {"status": PipelineStatus.CANCELLED.value, "message": "Запрос отменён"})
        try:
            self.pipeline.request_cancel()
        except Exception as e:
            logger.warning(f"request_cancel failed: {e}")
        try:
            await self.pipeline.discard_pending_changes()
        except Exception as e:
            logger.warning(f"discard_pending_changes failed: {e}")
        try:
            self.pipeline.cleanup_tester()
        except Exception as e:
            logger.warning(f"cleanup_tester failed: {e}")
        try:
            bm = self.pipeline.backup_manager
            if bm is not None:
                await run_blocking(bm.end_session)
        except Exception as e:
            logger.warning(f"backup end_session failed: {e}")
        try:
            self._pending_changes = []
            self._diffs = {}
            self.dispatch("changes_discarded", {})
        except Exception as e:
            logger.warning(f"changes_discarded dispatch failed: {e}")

    async def _update_project_index(self) -> None:
        """Обновить индекс проекта после применения изменений (никогда не бросает).

        Новый проект — полная индексация (FullIndexBuilder), существующий —
        инкрементальная синхронизация (IncrementalIndexUpdater). После сборки
        индекс перечитывается из ``.ai-agent/semantic_index.json`` и
        прокидывается в state и пайплайн.
        """
        try:
            if not self.state.project_dir:
                return

            def progress_fn(message: str, current: int, total: int) -> None:
                """Прогресс индексации → строка в чат."""
                self.dispatch("log", f"📊 {message} ({current}/{total})")

            if self.state.is_new_project:
                from app.services.index_manager import FullIndexBuilder

                await FullIndexBuilder(self.state.project_dir).build(on_progress=progress_fn)
                self.state.is_new_project = False
            else:
                from app.services.index_manager import IncrementalIndexUpdater

                await IncrementalIndexUpdater(self.state.project_dir).sync(on_progress=progress_fn)

            def _read_index() -> Dict[str, Any]:
                """Прочитать semantic_index.json ({} при отсутствии/повреждении)."""
                import json

                path = os.path.join(self.state.project_dir, ".ai-agent", "semantic_index.json")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            index = await run_blocking(_read_index)
            self.state.project_index = index
            self.pipeline.project_index = index
        except Exception as e:
            logger.warning(f"_update_project_index failed: {e}")
            self.dispatch("log", f"⚠️ Обновление индекса не удалось: {e}")

    def _reload_index_from_disk(self) -> Dict[str, Any]:
        """Прочитать ``.ai-agent/semantic_index.json`` ({} при отсутствии/повреждении)."""
        try:
            if not self.state.project_dir:
                return {}
            import json

            path = os.path.join(self.state.project_dir, ".ai-agent", "semantic_index.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug(f"_reload_index_from_disk failed: {e}")
            return {}

    async def update_index(self, incremental: bool = True) -> None:
        """Публичное управление индексацией для ``/index update|rebuild``."""
        try:
            if not self.state.project_dir:
                self.dispatch("log", "⚠️ Проект не выбран — индексация недоступна")
                return

            def progress_fn(message: str, current: int, total: int) -> None:
                """Прогресс индексации → строка в чат."""
                self.dispatch("log", f"📊 {message} ({current}/{total})")

            if incremental:
                from app.services.index_manager import IncrementalIndexUpdater

                await IncrementalIndexUpdater(self.state.project_dir).sync(on_progress=progress_fn)
            else:
                from app.services.index_manager import FullIndexBuilder

                await FullIndexBuilder(self.state.project_dir).build(on_progress=progress_fn)

            index = await run_blocking(self._reload_index_from_disk)
            self.state.project_index = index
            self.pipeline.project_index = index
            self.dispatch("log", "✅ Индекс обновлён")
        except Exception as e:
            logger.warning(f"update_index failed: {e}")
            self.dispatch("log", f"⚠️ Обновление индекса не удалось: {e}")

    async def run_general_chat(self, user_request: str) -> None:
        """Общий чат: GeneralChatOrchestrator без привязки к кодовой базе."""
        from app.agents.orchestrator import GeneralChatOrchestrator

        # 0. История: сохранить запрос, авто-название, загрузить контекст
        if self.history_adapter is not None and self.state.thread_id:
            try:
                await self.history_adapter.save_message(self.state.thread_id, "user", user_request)
                await self.history_adapter.auto_title(self.state.thread_id, user_request)
            except Exception as e:
                logger.warning(f"history save failed: {e}")
            try:
                self.history = await self.history_adapter.get_session_history(
                    self.state.thread_id, user_request, self.state.get_current_orchestrator_model()
                )
            except Exception as e:
                logger.warning(f"history load failed: {e}")
                self.history = []

        self.dispatch(
            "status",
            {
                "status": PipelineStatus.GENERATING.value,
                "message": "Общий чат: обработка запроса...",
            },
        )
        try:
            orchestrator = GeneralChatOrchestrator(
                model=self.state.get_current_orchestrator_model(),
                is_legal_mode=self.state.is_legal_mode,
                preferred_provider=self.state.orchestrator_provider,
            )
            result = await orchestrator.orchestrate_general(
                user_query=user_request, user_files=[], history=self.history
            )
            response = str(getattr(result, "response", "") or "")
            translated = await safe_translate(response, "AI response")
            self.dispatch("log", f"💬 {translated[:6000]}")
            await self._save_assistant_message(response or translated)
        except asyncio.CancelledError:
            # run_request выполняет Emergency Stop cleanup
            raise
        except Exception as e:
            logger.error(f"general chat failed: {e}", exc_info=True)
            self.dispatch("log", f"❌ Ошибка общего чата: {e}")
            self.dispatch("status", {"status": PipelineStatus.FAILED.value, "message": str(e)})
            return
        self.dispatch(
            "status",
            {"status": PipelineStatus.COMPLETED.value, "message": "Ответ готов"},
        )

    @contextlib.contextmanager
    def _capture_debug_output(self):
        """Зеркалировать debug-print'ы пайплайна в чат (re-entrant safe).

        Guarded новым lock/depth: вложенные/rework-вызовы одного пайплайна НЕ
        насаживают второй `DebugTee` поверх уже активного tee; `finally`
        восстанавливает ровно тот `original` `sys.stdout`, который захватило
        ВНЕШНЕЕ invocation — в том числе при `Exception` и
        `asyncio.CancelledError`. `DebugTee` (lossless pass-through) и
        `_DEBUG_MARKERS` не изменены.
        """

        def _on_debug_line(line: str) -> None:
            """Колбэк зеркалирования: debug-строка → dispatch('log', ...) в чат."""
            self.dispatch("log", f"🔍 {line}")

        nested = False
        original = None
        with self._debug_tee_lock:
            if self._debug_tee_depth > 0:
                # Вложенный вызов: внешний tee уже активен — не наслаиваем.
                self._debug_tee_depth += 1
                nested = True
            else:
                original = sys.stdout
                self._debug_tee_depth += 1
        if nested:
            try:
                yield
            finally:
                with self._debug_tee_lock:
                    self._debug_tee_depth -= 1
            return
        try:
            tee = DebugTee(original, _on_debug_line)
            with contextlib.redirect_stdout(tee):
                yield
        finally:
            with self._debug_tee_lock:
                self._debug_tee_depth -= 1
            try:
                sys.stdout = original
            except Exception:
                pass

    def _format_advice_for_display(self, advice: Any) -> str:
        """Пользовательский markdown-совет Pre-filter (паритет print_prefilter_advice).

        Без консольной Panel: возвращается строка markdown для чата
        (dispatch kind="markdown"). Все обращения к полям — через getattr
        с безопасными дефолтами; метод никогда не бросает исключений.
        """
        try:
            if advice is None:
                return ""
            if isinstance(advice, str):
                return advice
            # Planning branch: display the full canonical plan without truncation.
            if self.state.prefilter_mode == "advanced":
                canonical = str(getattr(advice, "raw_response", "") or "").strip()
                if not canonical:
                    canonical = str(getattr(advice, "full_response", "") or "").strip()
                if not canonical:
                    return ""
                tool_calls = int(getattr(advice, "tool_calls_made", 0) or 0)
                title = "📋 План (Продвинутый pre-filter)"
                if tool_calls > 0:
                    title += f" | 🛠️ {tool_calls} инструментов"
                return f"{title}\n\n{canonical}"

            parts = []
            clarified = str(getattr(advice, "clarified_query", "") or "")
            if clarified.strip():
                parts.append(f"**Уточнённый запрос:** {clarified}")
            cause = str(getattr(advice, "possible_cause", "") or "")
            if cause.strip() and cause != "N/A":
                parts.append(f"**Возможная причина:** {cause}")
            actions = list(getattr(advice, "recommended_actions", []) or [])
            if actions:
                parts.append("**Рекомендуемые действия:**")
                for action in actions[:5]:
                    parts.append(f"  • {action}")
            files = list(getattr(advice, "files_to_check", []) or [])
            if files:
                parts.append("**Файлы для проверки:**")
                for file_info in files[:5]:
                    parts.append(f"  • `{file_info}`")
            context = str(getattr(advice, "additional_context", "") or "")
            if context.strip() and context != "N/A":
                parts.append(f"**Дополнительный контекст:** {context}")
            if parts:
                body = "\n".join(parts)
            else:
                raw = str(getattr(advice, "raw_response", "") or "").strip()
                if not raw:
                    return ""
                body = raw
            if not body:
                return ""
            mode_label = (
                "🔬 Продвинутый" if self.state.prefilter_mode == "advanced" else "🔍 Обычный"
            )
            title = f"💡 Совет Pre-filter ({mode_label})"
            tool_calls = int(getattr(advice, "tool_calls_made", 0) or 0)
            if self.state.prefilter_mode == "advanced" and tool_calls > 0:
                title += f" | 🛠️ {tool_calls} инструментов"
            return f"{title}\n\n{body}"
        except Exception:
            return ""

    def _schedule_pending_modal_resolve(
        self, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        """Best-effort снять зависший ask/emergency-модал на UI-loop.

        Зеркалит cleanup-контракт ``_tui_ask_user_sync``: через
        ``call_soon_threadsafe`` резолвит верхний ожидающий
        ``AskUserDialog``/``EmergencyNetworkDialog``/``EmergencyLimitDialog``
        с ``None`` через потокобезопасный ``resolve_modal``, чтобы таймаут
        ожидания никогда не оставлял осиротевший или замёрзший экран.
        Никогда не бросает исключений и не блокирует UI-loop.
        """
        try:
            from .dialogs import resolve_modal as _rm

            _app_ref = self._app
            _loop_ref = loop if loop is not None else self._app_loop

            def _timeout_resolve() -> None:
                """Резолв верхнего ожидающего ask/emergency модала."""
                try:
                    stack = list(getattr(_app_ref, "screen_stack", []) or [])
                    for _scr in reversed(stack[1:]):
                        if type(_scr).__name__ in (
                            "AskUserDialog",
                            "EmergencyNetworkDialog",
                            "EmergencyLimitDialog",
                        ):
                            _rm(_scr, None)
                            return
                except Exception:
                    logger.debug("timeout modal resolve failed", exc_info=True)

            if _loop_ref is not None and _loop_ref.is_running():
                _loop_ref.call_soon_threadsafe(_timeout_resolve)
        except Exception:
            logger.debug("timeout resolve schedule failed", exc_info=True)
