"""Воркспейс вкладки TUI: чат, жёлтая таблица изменений, кнопки действий, ввод запроса.

Каждая вкладка владеет одним ``WorkspacePipeline`` и изолированной группой
воркеров (``pipeline-{tab_key}``). Все мутации виджетов проходят через
единственный метод ``_on_dispatch`` — мост маршалит вызовы в UI-поток
(call_later / call_from_thread), поэтому обработчики безопасны из любого треда.
"""

from __future__ import annotations
import asyncio
import threading
import logging
from typing import Any, Dict, Optional

from rich.text import Text
from rich.markdown import Markdown
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Input, Label, RichLog, TabPane, TabbedContent

from .adapters import HistoryAdapter, StateAdapter
from .dialogs import NEW_THREAD, resolve_modal, tui_prompt
from .diff_viewer import show_diff_viewer
from .pipeline_bridge import WorkspacePipeline

logger = logging.getLogger(__name__)

#: Человекочитаемые подписи статусов пайплайна
_STATUS_LABELS: Dict[str, str] = {
    "idle": "⏸ Готов",
    "analyzing": "🎯 Анализ...",
    "generating": "⚙️ Генерация...",
    "validating": "🔍 Валидация...",
    "awaiting_confirmation": "⏳ Ожидание подтверждения",
    "applying": "📝 Применение...",
    "completed": "✅ Завершено",
    "failed": "❌ Ошибка",
    "cancelled": "🚫 Отменено",
}


class WorkspacePane(TabPane):
    """Одна вкладка воркспейса: изолированный пайплайн + UI-виджеты."""

    def __init__(
        self,
        app: Any,
        tab_key: str,
        project_dir: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
        history_manager: Any = None,
        mode: str = "agent",
        state: Optional[StateAdapter] = None,
    ) -> None:
        """Создать воркспейс и связанный с ним мост пайплайна."""
        super().__init__(f"⚙ {tab_key}", id=f"pane-{tab_key}")
        self.tab_key = tab_key
        self.project_dir = project_dir
        self.session_id = session_id
        self.history_adapter = HistoryAdapter(history_manager) if history_manager is not None else None
        if state is None:
            state = StateAdapter(mode=mode, project_dir=project_dir, thread_id=thread_id)
        self.state = state
        self.bridge = WorkspacePipeline(
            tab_key=tab_key,
            app=app,
            project_dir=project_dir,
            history_manager=history_manager,
            thread_id=state.thread_id,
            project_index=state.project_index or {},
            enable_type_checking=state.enable_type_checking,
            generator_model=state.generator_model,
            dispatch=self._on_dispatch,
            state=state,
            history_adapter=self.history_adapter,
        )
        self._worker_group = f"pipeline-{tab_key}"
        # NOTE: `_running` принадлежит Textual MessagePump (флаг жизненного
        # цикла message pump) и не должен использоваться как состояние
        # приложения; собственный флаг воркспейса — `_query_running`.
        self._query_running = False
        self._stop_requested = False

    # ------------------------------------------------------------ logging

    def _log(self, level: int, message: str, exc_info: bool = False) -> None:
        """Лог уровня вкладки: одна строка app-лога с префиксом [WS-{tab_key}]."""
        logger.log(level, f"[WS-{self.tab_key}] {message}", exc_info=exc_info)

    # ------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        """Компоновать воркспейс: статус, чат, таблица, кнопки, ввод."""
        yield Label("⏸ Готов", id="status-label")
        yield Label("", id="session-status")
        yield RichLog(id="chat", wrap=True, markup=True, auto_scroll=True)
        table = DataTable(id="changes", classes="diff-table")
        table.add_columns("Файл", "Тип", "+", "−")
        table.display = False
        yield table
        action_panel = Horizontal(id="action-panel")
        action_panel.display = False
        with action_panel:
            yield Button("👀 Посмотреть изменения (Diff)", id="diff-btn")
            yield Button("✅ Принять", id="accept-btn")
            yield Button("✏️ Отправить на доработку", id="rework-btn")
            yield Button("🧪 Тестировщик", id="tester-btn")
            yield Button("❌ Отменить", id="cancel-btn")
        action_bar = Horizontal(id="action-bar")
        action_bar.display = False
        with action_bar:
            yield Button("✅ Принять план", id="btn-plan-accept")
            yield Button("✏️ Уточнить план", id="btn-plan-edit")
            yield Button("❌ Отмена", id="btn-plan-cancel")
            yield Button("💾 Принять (Commit)", id="btn-apply")
            yield Button("🔄 Доработать", id="btn-rework")
            yield Button("🧪 Тестер", id="btn-tester")
            yield Button("🧪 Повторить тест", id="btn-rerun")
            yield Button("🔍 Diff", id="btn-diff2")
            yield Button("🗑️ Отменить (Rollback)", id="btn-cancel2")
            yield Button("🔁 Режим", id="btn-mode")
            yield Button("🎯 Модели", id="btn-models")
            yield Button("📊 Индекс", id="btn-index")
            yield Button("💾 Бэкапы", id="btn-backup")
            yield Button("🗜 Сжать", id="btn-compress")
            yield Button("⏹ Остановить", id="btn-stop")
        yield Input(placeholder="Введите запрос или /команду...", id="query-input")

    def on_mount(self) -> None:
        """Запустить периодический слив токенов и поставить фокус в поле ввода."""
        self.set_interval(0.1, self._flush_tokens)

        try:
            self._configure_action_bar(None)
        except Exception:
            self._log(logging.DEBUG, "configure action bar on mount failed", exc_info=True)

        try:
            self._update_session_status()
        except Exception:
            pass
        try:
            self.query_one("#query-input", Input).focus()
        except Exception:
            self._log(logging.DEBUG, "focus query-input failed", exc_info=True)

    # ------------------------------------------------------------ dispatch

    def _on_dispatch(self, kind: str, payload: Any = None) -> None:
        """Единственная точка мутации виджетов (вызывается из моста)."""

        # Thread guard: прямые мутации виджетов — только на UI-loop.
        try:
            app_ref = getattr(self, "app", None)
            if (
                app_ref is not None
                and threading.current_thread() is not threading.main_thread()
            ):
                app_ref.call_from_thread(self._on_dispatch, kind, payload)
                return
        except Exception:
            self._log(logging.DEBUG, "dispatch thread re-route failed", exc_info=True)
        try:
            if kind == "log":
                self.write_line(str(payload), markup=True)
            elif kind == "thinking":
                self.write_line(f"🧠 {payload}", markup=True)
            elif kind == "tool_call":
                data = payload if isinstance(payload, dict) else {}
                icon = data.get("icon", "✅")
                self.write_line(
                    f"{icon} {data.get('name', '?')}({data.get('args', '')})"
                    f"\n    → {data.get('output', '')}",
                    markup=True,
                )
            elif kind == "validation":
                data = payload if isinstance(payload, dict) else {}
                if data.get("success"):
                    self.write_line("✅ Валидация пройдена", markup=True)
                else:
                    self.write_line(
                        f"❌ Валидация: ошибок {data.get('errors', 0)}, "
                        f"предупреждений {data.get('warnings', 0)}",
                        markup=True,
                    )
            elif kind == "status":
                self._update_status(payload)
            elif kind == "stage":
                data = payload if isinstance(payload, dict) else {}
                self._set_status_label(str(data.get("message", "")))
            elif kind == "token":
                self.write_line(str(payload), markup=False)

            elif kind == "markdown":
                self._write_markdown(str(payload))
            elif kind == "changes_ready":
                self._show_changes(payload)
            elif kind == "changes_applied":
                self._on_changes_applied(payload)
            elif kind == "changes_discarded":
                self._on_changes_discarded(payload)
            elif kind == "control_point":
                data = payload if isinstance(payload, dict) else {}
                prompt = str(data.get("prompt", ""))
                gate_kind = data.get("kind")
                if prompt:
                    self.write_line(f"⏳ {prompt}", markup=True)
                self._configure_action_bar(gate_kind)
                try:
                    self._update_session_status()
                except Exception:
                    pass
            elif kind == "gate_resolved":
                self._configure_action_bar(None)
            elif kind == "open_diff":
                show_diff_viewer(
                    self.app,
                    dict(self.bridge._diffs),
                    pending_changes=self.bridge._pending_changes,
                )
            elif kind == "session_status":
                try:
                    self._update_session_status()
                except Exception:
                    pass
        except Exception:
            self._log(logging.ERROR, "dispatch handling failed", exc_info=True)

    def write_line(self, text: str, markup: bool = True) -> None:
        """Написать строку в чат-лог вкладки (публичный API для commands/menus)."""
        try:
            chat = self.query_one("#chat", RichLog)
            if markup:
                try:
                    chat.write(text)
                    return
                except Exception:
                    pass
            chat.write(Text(str(text)))
        except Exception:
            self._log(logging.DEBUG, "chat write failed", exc_info=True)

    def _set_status_label(self, text: str) -> None:
        """Обновить текст статус-лейбла вкладки."""
        try:
            self.query_one("#status-label", Label).update(text)
        except Exception:
            self._log(logging.DEBUG, "status label update failed", exc_info=True)

    def _update_status(self, payload: Any) -> None:
        """Обновить статус-лейбл и заголовок вкладки."""
        data = payload if isinstance(payload, dict) else {}
        status_value = str(data.get("status", ""))
        message = str(data.get("message", ""))
        label = _STATUS_LABELS.get(status_value, status_value)
        if message:
            label = f"{label} — {message}"
        self._set_status_label(label)
        if status_value == "idle":
            tab_text = f"⚙ {self.tab_key}"
        else:
            tab_text = label[:40]
        self._update_tab_label(tab_text)

    # ------------------------------------------------------------ changes UI

    def _show_changes(self, payload: Any) -> None:
        """Перестроить таблицу изменений и показать панель действий."""
        data = payload if isinstance(payload, dict) else {}
        rows = list(data.get("rows", []) or [])
        try:
            table = self.query_one("#changes", DataTable)
            table.clear()
            for row in rows:
                table.add_row(
                    str(row.get("path", "")),
                    str(row.get("type", "")),
                    f"+{row.get('added', 0)}",
                    f"-{row.get('removed', 0)}",
                )
            table.display = bool(rows)
            self.query_one("#action-panel", Horizontal).display = bool(rows)
        except Exception:
            self._log(logging.ERROR, "show changes failed", exc_info=True)

    def _hide_changes(self) -> None:
        """Скрыть таблицу изменений и панель действий."""
        try:
            self.query_one("#changes", DataTable).display = False
            self.query_one("#action-panel", Horizontal).display = False
        except Exception:
            pass

    def _on_changes_applied(self, payload: Any) -> None:
        """Скрыть таблицу, показать применённые файлы + подсказку бэкапа."""
        data = payload if isinstance(payload, dict) else {}
        files = list(data.get("files", []) or [])
        created = list(data.get("created", []) or [])
        backup_id = data.get("backup_session_id")
        self._hide_changes()
        lines = ["✅ Изменения применены"]
        for f in files:
            lines.append(f"  ✓ {f}")
        for f in created:
            lines.append(f"  + {f}")
        if backup_id:
            lines.append(f"💾 Бэкап: {backup_id} — для отката используйте /restore {backup_id}")
        self.write_line("\n".join(lines), markup=True)

    def _on_changes_discarded(self, payload: Any) -> None:
        """Скрыть таблицу изменений (изменения отменены)."""
        self._hide_changes()

    # ------------------------------------------------------------ tokens

    def _flush_tokens(self) -> None:
        """Периодически сливать буфер токенов моста в чат."""
        try:
            if self.bridge._token_buffer:
                self.bridge.flush_tokens()
        except Exception:
            self._log(logging.DEBUG, "token flush failed", exc_info=True)

    # ------------------------------------------------------------ query input

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter в поле запроса → команда или новый запрос."""
        if event.input.id != "query-input":
            return
        event.stop()
        self._submit_query(event.value)

    def _submit_query(self, raw: str) -> None:
        """Обработать ввод: гейт-маршрутизация, команды, обычный запрос."""
        text = (raw or "").strip()
        if not text:
            return

        # Гейт-маршрутизация: ввод при активной контрольной точке
        try:
            gate_kind = self.bridge.active_gate()
        except Exception:
            gate_kind = None
        if gate_kind is not None and not text.startswith("/"):
            try:
                self.query_one("#query-input", Input).value = ""
            except Exception:
                pass
            self.bridge.resolve_gate(text)
            return

        if self._query_running and gate_kind is None:
            self.app.notify(
                "Запрос уже выполняется — дождитесь завершения или нажмите F9",
                severity="warning",
            )
            return

        # Guard: никогда не запускать тот же запрос/команду дважды, пока running
        if self._query_running:
            return

        try:
            self.query_one("#query-input", Input).value = ""
        except Exception:
            pass
        # Флаг running выставляется НА UI-LOOP до run_worker — двойной Enter
        # физически не может запустить два воркера.
        self._query_running = True
        if text.startswith("/"):
            try:
                self.run_worker(
                    self._run_command_worker(text, owns_running=True),
                    group=f"cmd-{self.tab_key}",
                    exclusive=True,
                )
            except Exception:
                # Воркер не запланирован — флаг нельзя оставлять взведённым,
                # иначе вкладка окажется навсегда заблокированной.
                self._query_running = False
                raise
            return
        self.run_worker(self._run_query(text), group=self._worker_group, exclusive=True)

    async def _run_command_worker(self, text: str, owns_running: bool = False) -> None:
        """Выполнить слэш-команду в воркере и разрулить сентинелы действий.

        ``owns_running`` — True только когда воркер запущен из ``_submit_query``
        и владеет флагом ``_query_running``; в этом случае ``finally`` гарантирует
        сброс флага при успехе, при сбое команды и при ``CancelledError``.
        Воркер, запущенный напрямую из кнопки (`#btn-models`, `#btn-index`,
        `#btn-backup`, `#btn-compress`) или ``action_model_menu``, флаг не
        трогает и не может снять флаг конкурентного запроса.
        """
        try:
            from . import commands

            result = await commands.handle_command(self, text)

            if result == "quit":
                self.app.exit()
            elif result == "menu":
                from . import menus

                await menus.show_main_menu(self.app)
            elif result == "switch_tab":
                await self._handle_switch_tab()
        except asyncio.CancelledError:
            # CancelledError корректно ре-raise; флаг сбрасывается в finally.
            self._log(logging.DEBUG, "command worker cancelled")
            raise
        except Exception:
            self._log(logging.ERROR, "command failed", exc_info=True)
            self.write_line("❌ Ошибка выполнения команды")
        finally:
            if owns_running:
                self._query_running = False
                try:
                    self._configure_action_bar(None)
                except Exception:
                    self._log(
                        logging.DEBUG,
                        "configure action bar (idle) failed",
                        exc_info=True,
                    )

    async def _run_query(self, text: str) -> None:
        """Выполнить запрос пользователя через мост (воркер вкладки)."""
        self._query_running = True
        try:
            self._configure_action_bar(None)
        except Exception:
            self._log(logging.DEBUG, "configure action bar (running) failed", exc_info=True)
        try:
            try:
                self.bridge._cancel_event.clear()
            except Exception:
                pass
            try:
                self.bridge.pipeline.reset_cancel()
            except Exception:
                pass
            await self.bridge.run_request(text)
        except asyncio.CancelledError:
            self._log(logging.DEBUG, "query worker cancelled")
            raise
        finally:
            self._query_running = False
            try:
                self._configure_action_bar(None)
            except Exception:
                self._log(logging.DEBUG, "configure action bar (idle) failed", exc_info=True)

    async def _handle_switch_tab(self) -> None:
        """Диалог выбора треда → переключение (создание нового при выборе «Новый»)."""
        from .dialogs import select_thread

        if self.history_adapter is None:
            self.write_line("📜 История недоступна")
            return
        result = await select_thread(self.app, self.history_adapter)
        if result is None:
            return
        if result == NEW_THREAD:
            thread = await self.history_adapter.new_conversation(self.state.project_dir)
            self.state.thread_id = getattr(thread, "id", None)
            self.bridge.history = []
            self.write_line(f"🆕 Новый диалог: {getattr(thread, 'id', '')}")
        else:
            await self._switch_thread(result)
        await self.persist_session()

    async def _switch_thread(self, thread_id: str) -> None:
        """Переключить тред: обновить state и перезагрузить историю моста."""
        self.state.thread_id = thread_id
        try:
            if self.history_adapter is not None:
                self.bridge.history = await self.history_adapter.get_session_history(
                    thread_id, "", self.state.get_current_orchestrator_model()
                )
        except Exception:
            self._log(logging.DEBUG, "history reload failed", exc_info=True)
        self.write_line(f"📜 Переключено на тред {thread_id}")

    async def persist_session(self) -> None:
        """Сохранить привязку воркспейса в sessions_db (best-effort)."""
        try:
            from . import sessions_db

            session = sessions_db.new_session(
                self.tab_key, self.state.project_dir, self.state.thread_id, f"⚙ {self.tab_key}"
            )
            await sessions_db.save_session(session)
            self.session_id = session.id
        except Exception:
            self._log(logging.DEBUG, "persist session failed", exc_info=True)

    # ------------------------------------------------------------ buttons

    @on(Button.Pressed, "#diff-btn")
    def _on_diff_btn(self, event: Button.Pressed) -> None:
        """Показать окно просмотра диффов."""
        event.stop()
        show_diff_viewer(
            self.app,
            dict(self.bridge._diffs),
            pending_changes=self.bridge._pending_changes,
        )

    @on(Button.Pressed, "#accept-btn")
    def _on_accept_btn(self, event: Button.Pressed) -> None:
        """Применить изменения (воркер)."""
        event.stop()
        if not self.bridge.has_pending:
            self.app.notify("Нет изменений для применения", severity="warning")
            return
        self.run_worker(self.bridge._apply_changes(), group=f"apply-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#rework-btn")
    def _on_rework_btn(self, event: Button.Pressed) -> None:
        """Запросить фидбек и отправить на доработку (воркер)."""
        event.stop()

        async def _do_rework() -> None:
            """Запросить замечания и запустить цикл доработки."""
            feedback = await tui_prompt(
                self.app,
                "Введите замечания для доработки:",
                free_input=True,
                title="✏️ Доработка",
                multiline=True,
            )
            if feedback and feedback.strip():
                await self.bridge.rework(feedback)

        self.run_worker(_do_rework(), group=f"rework-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#tester-btn")
    def _on_tester_btn(self, event: Button.Pressed) -> None:
        """Запустить стадию Тестировщика (воркер)."""
        event.stop()

        async def _do_tester() -> None:
            """Прогнать стадию Тестировщика; 'apply' → применить изменения."""
            outcome = await self.bridge._run_tester_stage()
            if outcome == "apply":
                await self.bridge._apply_changes()

        self.run_worker(_do_tester(), group=f"tester-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#cancel-btn")
    def _on_cancel_btn(self, event: Button.Pressed) -> None:
        """Отменить изменения (воркер)."""
        event.stop()
        self.run_worker(self.bridge.discard(), group=f"discard-{self.tab_key}", exclusive=True)

    def _configure_action_bar(self, gate_kind: Optional[str]) -> None:
        """Управление видимостью кнопок ``#action-bar`` по фазе.

        - ``gate_kind is None``: активен быстрый набор фаз вкладки:
            * ``self._query_running`` → ``{"btn-stop"}``
            * IDLE → ``{"btn-mode", "btn-models", "btn-index", "btn-backup", "btn-compress"}``
              плюс ``"btn-diff2"`` при ``self.bridge.has_pending``.
        - ``gate_kind`` из ``visibility_map`` → соответствующий набор.
        """
        bar = None
        try:
            bar = self.query_one("#action-bar", Horizontal)
        except Exception:
            return
        if gate_kind is None:
            # Фаза вкладки (IDLE/RUNNING) — быстрые кнопки всегда видимы
            if self._query_running:
                active = {"btn-stop"}
            else:
                active = {"btn-mode", "btn-models", "btn-index", "btn-backup", "btn-compress"}
                try:
                    if self.bridge.has_pending:
                        active.add("btn-diff2")
                except Exception:
                    pass
        else:
            visibility_map = {
                "plan": {"btn-plan-accept", "btn-plan-edit", "btn-plan-cancel"},
                "confirmation": {"btn-apply", "btn-rework", "btn-tester", "btn-diff2", "btn-cancel2"},
                "post_tester": {"btn-apply", "btn-rework", "btn-rerun", "btn-diff2", "btn-cancel2"},
            }
            active = visibility_map.get(gate_kind, set())
        all_buttons = [
            "btn-plan-accept", "btn-plan-edit", "btn-plan-cancel",
            "btn-apply", "btn-rework", "btn-tester", "btn-rerun",
            "btn-diff2", "btn-cancel2",
            "btn-mode", "btn-models", "btn-index", "btn-backup", "btn-compress", "btn-stop",
        ]
        for bid in all_buttons:
            try:
                btn = self.query_one(f"#{bid}", Button)
                btn.display = bid in active
            except Exception:
                pass
        try:
            bar.display = bool(active)
        except Exception:
            pass

    def _update_session_status(self) -> None:
        """Обновить ``#session-status`` из ``bridge.session_snapshot()``."""
        try:
            snap = self.bridge.session_snapshot()
        except Exception:
            return
        try:
            mode = str(snap.get("mode", "agent")).upper()
            project = str(snap.get("project", "—") or "—")
            orch = str(snap.get("orchestrator", "Роутер") or "Роутер")
            gen = str(snap.get("generator", "—") or "—")
            tester = "ON" if snap.get("tester_on") else "OFF"
            tokens = int(snap.get("tokens", 0) or 0)
            vfs_n = int(snap.get("vfs_files", 0) or 0)
            text = (
                f"📁 {project} │ Режим: {mode} │ 🎯 {orch} │ ⚙ {gen} │ "
                f"🧪 {tester} │ 📊 {tokens} ток │ VFS: {vfs_n}"
            )
            self.query_one("#session-status", Label).update(text)
        except Exception:
            self._log(logging.DEBUG, "session status update failed", exc_info=True)

    @on(Button.Pressed, "#btn-plan-accept")
    def _on_btn_plan_accept(self, event: Button.Pressed) -> None:
        """Кнопка 'Принять план' → resolve_gate('!ok')."""
        event.stop()
        self.bridge.handle_action("plan_accept")

    @on(Button.Pressed, "#btn-plan-cancel")
    def _on_btn_plan_cancel(self, event: Button.Pressed) -> None:
        """Кнопка 'Отмена' в фазе плана → resolve_gate('!cancel')."""
        event.stop()
        self.bridge.handle_action("plan_cancel")

    @on(Button.Pressed, "#btn-plan-edit")
    def _on_btn_plan_edit(self, event: Button.Pressed) -> None:
        """Кнопка 'Уточнить план' → multiline-промпт → resolve_gate(text)."""
        event.stop()

        async def _do_edit() -> None:
            """Запросить правки к плану и передать их через resolve_gate."""
            text = await tui_prompt(
                self.app,
                "Введите правки к плану:",
                free_input=True,
                multiline=True,
                title="✏️ Правки к плану",
            )
            if text and text.strip():
                self.bridge.resolve_gate(text)

        self.run_worker(_do_edit(), group=f"plan-edit-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#btn-apply")
    def _on_btn_apply(self, event: Button.Pressed) -> None:
        """Кнопка 'Принять (Commit)' → резолв гейта '1' или прямое применение."""
        event.stop()
        if self.bridge.active_gate():
            self.bridge.handle_action("apply")
        elif self.bridge.has_pending:
            self.run_worker(
                self.bridge._apply_changes(),
                group=f"apply-{self.tab_key}",
                exclusive=True,
            )

    @on(Button.Pressed, "#btn-rework")
    def _on_btn_rework(self, event: Button.Pressed) -> None:
        """Кнопка 'Доработать' → резолв гейта '2' или запрос фидбека."""
        event.stop()
        if self.bridge.active_gate():
            self.bridge.handle_action("rework")
            return

        async def _do_rework() -> None:
            """Запросить замечания и запустить цикл доработки."""
            feedback = await tui_prompt(
                self.app,
                "Введите замечания для доработки:",
                free_input=True,
                title="✏️ Доработка",
                multiline=True,
            )
            if feedback and feedback.strip():
                await self.bridge.rework(feedback)

        self.run_worker(_do_rework(), group=f"rework-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#btn-tester")
    def _on_btn_tester2(self, event: Button.Pressed) -> None:
        """Кнопка 'Тестер' → резолв гейта 'tester' или запуск стадии."""
        event.stop()
        if self.bridge.active_gate():
            self.bridge.handle_action("tester")
            return

        async def _do_tester() -> None:
            """Прогнать стадию Тестировщика; 'apply' → применить изменения."""
            outcome = await self.bridge._run_tester_stage()
            if outcome == "apply":
                await self.bridge._apply_changes()

        self.run_worker(_do_tester(), group=f"tester-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#btn-rerun")
    def _on_btn_rerun(self, event: Button.Pressed) -> None:
        """Кнопка 'Повторить тест' → резолв гейта 'tester'."""
        event.stop()
        self.bridge.handle_action("rerun_tester")

    @on(Button.Pressed, "#btn-cancel2")
    def _on_btn_cancel2(self, event: Button.Pressed) -> None:
        """Кнопка 'Отменить (Rollback)' → резолв гейта '3' или discard."""
        event.stop()
        if self.bridge.active_gate():
            self.bridge.handle_action("cancel")
            return
        self.run_worker(
            self.bridge.discard(), group=f"discard-{self.tab_key}", exclusive=True
        )

    @on(Button.Pressed, "#btn-diff2")
    def _on_btn_diff2(self, event: Button.Pressed) -> None:
        """Кнопка 'Diff' → открыть окно просмотра диффов."""
        event.stop()
        show_diff_viewer(
            self.app,
            dict(self.bridge._diffs),
            pending_changes=self.bridge._pending_changes,
        )

    @on(Button.Pressed, "#btn-stop")
    def _on_btn_stop(self, event: Button.Pressed) -> None:
        """Кнопка 'Остановить' → emergency_stop в отдельном воркере."""
        event.stop()
        self.run_worker(
            self.emergency_stop(), group=f"stop-{self.tab_key}", exclusive=True
        )

    @on(Button.Pressed, "#btn-mode")
    def _on_btn_mode(self, event: Button.Pressed) -> None:
        """Кнопка 'Режим' — циклическое переключение state.mode + persist."""
        event.stop()
        try:
            order = ["agent", "ask", "general"]
            current = self.state.mode if self.state.mode in order else "agent"
            idx = order.index(current)
            next_mode = order[(idx + 1) % len(order)]
            self.state.mode = next_mode
            try:
                from .settings_io import load_user_settings, save_user_settings

                settings = load_user_settings()
                settings["mode"] = next_mode
                save_user_settings(settings)
            except Exception:
                pass
            self._update_session_status()
            try:
                self.app._update_app_status()
            except Exception:
                pass
        except Exception:
            self._log(logging.ERROR, "cycle mode failed", exc_info=True)

    @on(Button.Pressed, "#btn-models")
    def _on_btn_models(self, event: Button.Pressed) -> None:
        """Кнопка 'Модели' → запуск /model."""
        event.stop()
        self.run_worker(self._run_command_worker("/model"), group=f"cmd-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#btn-index")
    def _on_btn_index(self, event: Button.Pressed) -> None:
        """Кнопка 'Индекс' → запуск /index."""
        event.stop()
        self.run_worker(self._run_command_worker("/index"), group=f"cmd-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#btn-backup")
    def _on_btn_backup(self, event: Button.Pressed) -> None:
        """Кнопка 'Бэкапы' → запуск /restore."""
        event.stop()
        self.run_worker(self._run_command_worker("/restore"), group=f"cmd-{self.tab_key}", exclusive=True)

    @on(Button.Pressed, "#btn-compress")
    def _on_btn_compress(self, event: Button.Pressed) -> None:
        """Кнопка 'Сжать' → запуск /compress."""
        event.stop()
        self.run_worker(self._run_command_worker("/compress"), group=f"cmd-{self.tab_key}", exclusive=True)

    # ------------------------------------------------------------ lifecycle

    @property
    def is_running(self) -> bool:
        """Выполняется ли сейчас запрос/команда в этой вкладке."""
        return self._query_running

    async def emergency_stop(self) -> None:
        """Экстренная остановка: флаги отмены + снятие воркера + сброс модалок."""
        self._stop_requested = True
        self._query_running = False
        try:
            self.bridge._cancel_event.set()
        except Exception:
            self._log(logging.WARNING, "cancel_event set failed")
        try:
            self.bridge.pipeline.request_cancel()
        except Exception:
            self._log(logging.WARNING, "request_cancel failed")
        try:
            self.app.workers.cancel_group(self, self._worker_group)
        except Exception:
            self._log(logging.WARNING, "worker group cancel failed")
        # Снять активные модальные future'ы этого воркспейса: отменённый
        # запрос не должен оставлять ни экрана, ни флага "running".
        try:
            stack = list(getattr(self.app, "screen_stack", []) or [])
            for scr in reversed(stack[1:]):
                future = getattr(scr, "future", None)
                if future is not None and not future.done():
                    resolve_modal(scr, None)
        except Exception:
            self._log(logging.DEBUG, "modal resolve on stop failed", exc_info=True)
        try:
            if self.history_adapter is not None and self.state.thread_id:
                await self.history_adapter.save_message(
                    self.state.thread_id,
                    "assistant",
                    "## ⏹️ Запрос остановлен пользователем (Emergency Stop)",
                )
        except Exception:
            self._log(logging.DEBUG, "cancel message save failed", exc_info=True)

    async def close_workspace(self) -> Optional[Dict[str, Any]]:
        """Закрыть воркспейс: стоп, discard, вернуть данные для персистенции."""
        if self._query_running:
            await self.emergency_stop()
        if self.bridge.has_pending:
            try:
                await self.bridge.pipeline.discard_pending_changes()
            except Exception:
                self._log(logging.WARNING, "discard on close failed", exc_info=True)
        return {
            "tab_key": self.tab_key,
            "project_dir": self.state.project_dir,
            "thread_id": self.state.thread_id,
            "session_id": self.session_id,
            "title": f"⚙ {self.tab_key}",
        }
    def _update_tab_label(self, text: str) -> None:
        """Обновить подпись вкладки через ``Tab.label`` (Textual 8.2.8).

        TabPane не имеет публичного title/label после монтирования: визуальная
        подпись живёт на виджете Tab, получаемом через
        ``TabbedContent.get_tab(pane)``. Best-effort: любая ошибка логируется
        на DEBUG и не ломает UI (гарантированный fallback — ``#status-label``).
        """
        try:
            if self.app is None:
                return
            tabs = self.app.query_one("#tabs", TabbedContent)
            tab = tabs.get_tab(self)
            tab.label = text
        except Exception:
            self._log(logging.DEBUG, "tab label update failed", exc_info=True)

    def _write_markdown(self, text: str) -> None:
        """Написать markdown-блок в чат (rich-рендер, как ``Markdown(advice_text)``).

        Best-effort: при любой ошибке (виджет недоступен, рендер отклонён,
        проблема разметки) — откат на обычный текстовый путь ``write_line``.
        Никогда не бросает исключений.
        """
        try:
            chat = self.query_one("#chat", RichLog)
            chat.write(Markdown(text))
        except Exception:
            self.write_line(text)
