"""Корневое Textual-приложение Autogenesia TUI.

Сплеш-экран, восстановление воркспейсов из sessions_db, управление вкладками
(до 3), глобальный Emergency Stop (F9) и главное меню (F2). Светлая сине-белая
тема подключается через ``CSS_VARIABLES`` из ``app.tui.theme``.
"""

from __future__ import annotations
from .setup_wizard import SetupResult

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static, Tab, TabbedContent

from . import sessions_db
from .adapters import HistoryAdapter, StateAdapter
from .banner import render_banner
from .dialogs import collect_recent_projects, select_project, tui_confirm
from .theme import build_css_variables
from .workspace import WorkspacePane

logger = logging.getLogger(__name__)

APP_VERSION = "0.0.1"


class SplashScreen(Screen):
    """Стартовый экран с баннером: закрывается любой клавишей или через ~2 сек."""

    BINDINGS = [("escape", "dismiss_splash", "Продолжить")]

    def compose(self) -> ComposeResult:
        """Баннер + подсказка + кнопка «Продолжить»."""
        yield Static(Text(render_banner(), style="bold #2196F3"), id="banner-art")
        yield Static(
            "Автономная генерация и анализ кода — нажмите любую клавишу",
            id="banner-title",
        )
        yield Button("Продолжить", id="continue-btn", variant="primary")

    def on_mount(self) -> None:
        """Инициализировать флаг и запустить воркер автозакрытия (~2 сек)."""
        self._dismissed = False
        self.run_worker(self._auto_dismiss_worker(), exclusive=True, group="splash-dismiss")

    async def _auto_dismiss_worker(self) -> None:
        """Через ~2 сек запланировать app-own снятие сплеша и выйти.

        Воркер остаётся на UI-loop; снимает сплеш ТОЛЬКО через
        `app.request_splash_dismiss("auto")` (никаких `pop_screen`,
        `_dismiss_if_current`-ownership и `call_after_refresh`) и безопасно
        выходит, если приложение/loop уже закрыты.
        """
        try:
            await asyncio.sleep(2.0)
            app = getattr(self, "app", None)
            if app is None:
                return
            try:
                app.call_later(app.request_splash_dismiss, "auto")
            except Exception:
                logger.debug("auto splash dismiss schedule failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("auto dismiss worker exited", exc_info=True)

    def on_key(self, event) -> None:
        """Любая клавиша → app-own снятие сплеша (без pop_screen)."""
        try:
            event.stop()
        except Exception:
            pass
        try:
            self.app.request_splash_dismiss("key")
        except Exception:
            logger.debug("splash key dismiss failed", exc_info=True)

    def on_click(self, event) -> None:
        """Any click on the splash surface → app-own dismissal (idempotent)."""
        try:
            self.app.request_splash_dismiss("click")
        except Exception:
            logger.debug("splash click dismiss failed", exc_info=True)

    def on_button_pressed(self, event) -> None:
        """Button press (esp. continue-btn) → app-own dismissal and stop event."""
        try:
            event.stop()
        except Exception:
            pass
        try:
            self.app.request_splash_dismiss("button")
        except Exception:
            logger.debug("splash button dismiss failed", exc_info=True)

    def _dismiss_if_current(self) -> None:
        """Deprecated: владение сплешем у приложения; делегирует request_splash_dismiss."""
        try:
            self.app.request_splash_dismiss("legacy")
        except Exception:
            logger.debug("legacy splash dismiss failed", exc_info=True)

    def action_dismiss_splash(self) -> None:
        """Binding Escape → app-own снятие сплеша."""
        try:
            self.app.request_splash_dismiss("escape")
        except Exception:
            logger.debug("splash escape dismiss failed", exc_info=True)


class AboutScreen(Screen):
    """Экран «О программе»."""

    BINDINGS = [("escape", "close_about", "Закрыть")]

    def compose(self) -> ComposeResult:
        """Баннер, версия и кнопка OK."""
        yield Static(Text(render_banner(), style="bold #2196F3"), id="banner-art")
        yield Label(
            f"🤖 Autogenesia v{APP_VERSION}\n"
            "Автономная генерация и анализ кода\n\n"
            "F2 — главное меню │ F9 — экстренная остановка │ /help — команды чата",
            id="banner-title",
        )
        with Horizontal(id="dialog-buttons"):
            yield Button("[ OK ]", variant="primary", id="about-ok-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """OK → закрыть экран."""
        event.stop()
        self._close()

    def action_close_about(self) -> None:
        """Escape → закрыть экран."""
        self._close()

    def _close(self) -> None:
        """Снять экран (best-effort)."""
        try:
            if self.is_current:
                self.app.pop_screen()
        except Exception:
            logger.debug("about dismiss failed", exc_info=True)


class AutogenesiaTUIApp(App):
    """Корневое приложение: вкладки воркспейсов, стоп-кнопка, меню, сессии."""

    CSS_PATH = "styles.tcss"
    CSS_VARIABLES = build_css_variables()
    TITLE = "Autogenesia"
    BINDINGS = [
        ("f2", "main_menu", "Меню"),
        ("f9", "emergency_stop", "Стоп"),
        ("ctrl+w", "close_tab", "Закрыть вкладку"),
        ("ctrl+m", "cycle_mode", "Режим"),
        ("ctrl+d", "show_diff", "Diff"),
        ("ctrl+l", "model_menu", "Модели"),
        ("f10", "main_menu", "Меню"),
        ("ctrl+o", "main_menu", "Меню"),
        ("escape", "soft_cancel", "Отмена шага"),
    ]
    MAX_TABS = 3

    def __init__(self) -> None:
        """Инициализировать приложение + состояние машины запуска."""
        super().__init__()
        self._workspace_count = 0
        self._stop_all_next = False
        self._history_manager: Optional[Any] = None
        self._startup_complete: bool = False
        self._startup_in_progress: bool = False
        self._startup_lock = asyncio.Lock()
        self._startup_task = None
        self._base_screen = None
        self._splash_screen = None
        self._app_loop = None
        self._repair_in_progress: bool = False
        # Startup/setup idempotency flags
        self._startup_menu_shown: bool = False
        self._startup_menu_in_progress: bool = False
        self._main_menu_in_progress: bool = False
        self._startup_setup_complete: bool = False
        self._startup_wizard_in_progress: bool = False

        # Single live ensure-live-ui worker handle (no un-awaited coroutines).
        self._ensure_live_ui_worker: Optional[Any] = None

    # ------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        """Каркас: заголовок, тулбар, вкладки, футер."""
        yield Header()
        with Horizontal(id="toolbar"):
            yield Button("➕ Новая вкладка", id="new-tab-btn")
            yield Button("📖 Меню", id="main-menu-btn")
            yield Button("📁 Директория", id="change-dir-btn")
            yield Button("🎯 Модели", id="models-btn")
            yield Static("", id="app-status")
            yield Button("⏹ Остановить", id="stop-btn")
        yield TabbedContent(id="tabs")
        yield Footer()

    async def on_mount(self) -> None:
        """Сразу показать живой базовый UI + сплеш; инициализация — в воркере.

        `on_mount` больше НЕ awaited весь стартап-цепочкой: он захватывает
        event-loop, сохраняет базовый экран из `compose()` и пушит единственный
        неклассический SplashScreen поверх уже живого UI. Вся долгая
        инициализация делегируется отдельному `_startup_worker`, запланированному
        ровно один раз. `_startup_complete` становится True только после
        завершения воркера. Никаких `select_project()` / `action_new_tab()`
        внутри запуска.
        """
        try:
            self._app_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._app_loop = None
        self._base_screen = self.screen
        try:
            self._splash_screen = SplashScreen()
            self.push_screen(self._splash_screen)
        except Exception:
            logger.debug("splash push failed", exc_info=True)
            self._splash_screen = None
        try:
            if self._startup_task is None or self._startup_task.done():
                self._startup_task = self.run_worker(
                    self._startup_worker(), group="startup", exclusive=True
                )
        except Exception:
            logger.exception("startup worker schedule failed")
            try:
                self.request_splash_dismiss("startup-error")
            except Exception:
                pass
            self._startup_complete = True

    async def _startup_worker(self) -> None:
        """Неблокирующая стартап-цепочка (MCP, БД, сессии, воркспейс).

        Guarded by `_startup_lock` + `_startup_in_progress`. В `finally`:
        логирует исключение, вызывает `_ensure_live_ui()`,
        `request_splash_dismiss("startup")` и выставляет
        `_startup_complete = True`. Воркер никогда не пушит сплеш повторно,
        не dismiss-ит пользовательские модалки и не снимает screen_stack[0].
        """
        if self._startup_in_progress:
            return
        self._startup_in_progress = True
        try:
            async with self._startup_lock:
                try:
                    from app.mcp import get_mcp_manager
                    get_mcp_manager().connect_all()
                except Exception as e:
                    logger.debug(f"MCP connect skipped: {e}")
                try:
                    await sessions_db.init_db()
                except Exception as e:
                    logger.warning(f"sessions_db init failed: {e}")
                try:
                    await self._restore_sessions()
                except Exception:
                    logger.exception("session restore failed")
                if not self._workspace_panes():
                    try:
                        default_project = os.getcwd() or None
                        await self._create_workspace(
                            default_project, is_new=False, setup_pending=True
                        )
                    except Exception:
                        logger.exception("startup workspace bootstrap failed")
                try:
                    self._activate_last_tab()
                except Exception:
                    pass
                try:
                    self._update_app_status()
                except Exception:
                    pass
                try:
                    ws = self.active_workspace
                    if ws is not None:
                        ws.query_one("#query-input").focus()
                except Exception:
                    logger.debug("startup focus skipped", exc_info=True)
        except asyncio.CancelledError:
            logger.debug("startup worker cancelled")
            raise
        except Exception as e:
            logger.error(f"startup worker failed: {e}", exc_info=True)
        finally:
            self._startup_in_progress = False
            try:
                await self._ensure_live_ui()
            except Exception:
                logger.exception("ensure_live_ui during startup failed")
            try:
                self.request_splash_dismiss("startup")
            except Exception:
                logger.debug("startup splash dismiss failed", exc_info=True)
            try:
                self._show_startup_menu_once()
            except Exception:
                logger.debug("startup menu schedule failed", exc_info=True)
            self._startup_complete = True

    async def _ensure_live_ui(self) -> None:
        """Идемпотентный guard живого UI (вызывается из startup/menu/new_tab).

        Гарантирует: непустой `screen_stack` с базовым экраном в `[0]`,
        наличие хотя бы одного `WorkspacePane` (детерминированно создаётся при
        необходимости), присутствие `#tabs`/`#app-status`/`#query-input` и
        обновление статуса. Легитимные активные модалки пользователя
        сохраняются; произвольный верхний экран никогда не снимается.

        Перед созданием bootstrap-воркспейса метод детерминированно проверяет
        доступность `_base_tabs()`: отсутствие базового `#tabs` во время
        teardown/shutdown — ожидаемое состояние (workspace-создание
        пропускается с DEBUG-логом, без ERROR и без мутаций `screen_stack[0]`).
        """
        if self._repair_in_progress:
            return
        self._repair_in_progress = True
        try:
            async with self._startup_lock:
                stack = list(getattr(self, "screen_stack", []) or [])
                base = self._base_screen
                if not stack and base is not None:
                    try:
                        self.push_screen(base)
                    except Exception:
                        logger.debug("ensure_live_ui: base push failed", exc_info=True)
                # Deterministic bootstrap guard: a missing base `#tabs` during
                # teardown/shutdown is an expected state — workspace creation
                # is skipped with DEBUG logging only (no ERROR log, and
                # `screen_stack[0]` is never removed).
                if self._base_tabs() is None:
                    logger.debug(
                        "ensure_live_ui: base #tabs unavailable "
                        "(teardown/shutdown) — workspace bootstrap skipped"
                    )
                elif not self._workspace_panes():
                    try:
                        default_project = os.getcwd() or None
                        await self._create_workspace(default_project, is_new=False)
                    except Exception:
                        # Expected teardown/shutdown failure: DEBUG only — an
                        # ERROR/exception record is never emitted here.
                        logger.debug(
                            "ensure_live_ui: workspace bootstrap skipped",
                            exc_info=True,
                        )
                try:
                    self._activate_last_tab()
                except Exception:
                    pass
                try:
                    self._update_app_status()
                except Exception:
                    pass
                try:
                    self._show_startup_menu_once()
                except Exception:
                    logger.debug("ensure_live_ui: startup menu schedule failed", exc_info=True)
                try:
                    ws = self.active_workspace
                    if ws is not None:
                        ws.query_one("#query-input").focus()
                except Exception:
                    logger.debug("ensure_live_ui: focus skipped", exc_info=True)
        finally:
            self._repair_in_progress = False

    def _schedule_ensure_live_ui(self) -> None:
        """Single safe scheduler for the live-UI bootstrap worker.

        Thread-safe: when invoked off the UI/main thread it re-dispatches
        itself via ``self.call_from_thread(self._schedule_ensure_live_ui)``
        and returns. Before creating any coroutine it checks whether
        ``_ensure_live_ui_worker`` is still live (not ``None`` and not
        finished); if so it returns immediately — no coroutine is created, so
        ``RuntimeWarning: coroutine '_ensure_live_ui' was never awaited`` is
        impossible. The ``_ensure_live_ui()`` coroutine is created only on the
        branch that actually calls ``run_worker(..., group="ensure-live-ui",
        exclusive=False)`` and the returned worker is stored in
        ``_ensure_live_ui_worker``. A PENDING worker is never cancelled (no
        ``exclusive=True``). Never pushes splash, never dismisses user modals
        and never pops ``screen_stack[0]``.
        """
        try:
            import threading as _threading

            if _threading.current_thread() is not _threading.main_thread():
                try:
                    self.call_from_thread(self._schedule_ensure_live_ui)
                except Exception:
                    logger.debug("ensure-live-ui re-dispatch failed", exc_info=True)
                return
        except Exception:
            pass
        worker = getattr(self, "_ensure_live_ui_worker", None)
        if worker is not None:
            try:
                if not bool(getattr(worker, "is_finished", True)):
                    # A live worker already exists — no new coroutine needed.
                    return
            except Exception:
                pass
        try:
            self._ensure_live_ui_worker = self.run_worker(
                self._ensure_live_ui(), group="ensure-live-ui", exclusive=False
            )
        except Exception:
            logger.debug("ensure_live_ui schedule failed", exc_info=True)

    def request_splash_dismiss(self, source: str = "manual") -> None:
        """ЕДИНСТВЕННЫЙ владелец снятия сплеша (только UI-loop; thread-safe).

        Из рабочего потока редиспетчится через `call_from_thread`. Снимает
        `self._splash_screen` только если этот точный объект присутствует в
        `screen_stack`, никогда не снимает `screen_stack[0]`, не трогает
        активные немодальные/модальные экраны не-сплеш (например,
        `ProjectSelectDialog`). Повторные вызовы — no-op. После снятия
        планирует `_ensure_live_ui()`.
        """
        try:
            import threading as _threading

            if _threading.current_thread() is not _threading.main_thread():
                try:
                    self.call_from_thread(self.request_splash_dismiss, source)
                except Exception:
                    logger.debug("splash dismiss re-dispatch failed", exc_info=True)
                return
        except Exception:
            pass
        splash = self._splash_screen
        if splash is None:
            try:
                self._schedule_ensure_live_ui()
            except Exception:
                pass
            return
        try:
            current = getattr(self, "screen", None)
            if current is splash:
                try:
                    splash._dismissed = True
                except Exception:
                    pass
                try:
                    self.pop_screen()
                except Exception:
                    logger.debug("splash pop failed", exc_info=True)
        except Exception:
            logger.debug("splash stack inspection failed", exc_info=True)
        self._splash_screen = None
        try:
            self._schedule_ensure_live_ui()
        except Exception:
            logger.debug("ensure_live_ui schedule failed", exc_info=True)
        try:
            self._show_startup_menu_once()
        except Exception:
            logger.debug("startup menu schedule failed", exc_info=True)

    def _push_user_screen(self, screen, callback=None) -> None:
        """Центральный пуш немодального пользовательского экрана.

        Используется для AboutScreen и DiffViewerScreen: проверяет, что
        `screen_stack[0]` — базовый экран, пушит только на UI-loop
        (`call_from_thread` иначе), сохраняет опциональный dismiss-callback
        (например `_refocus`) и никогда не оставляет пустой стек.
        """
        try:
            import threading as _threading

            if _threading.current_thread() is not _threading.main_thread():
                self.call_from_thread(self._push_user_screen, screen, callback)
                return
        except Exception:
            pass
        try:
            stack = list(getattr(self, "screen_stack", []) or [])
            if not stack and self._base_screen is not None:
                self.push_screen(self._base_screen)
            if callback is not None:
                self.push_screen(screen, callback)
            else:
                self.push_screen(screen)
        except Exception:
            logger.debug("push user screen failed", exc_info=True)

    def _base_tabs(self) -> Optional[TabbedContent]:
        """Return the ``TabbedContent`` with id ``#tabs`` on the BASE screen.

        The query is executed on ``self._base_screen`` (falling back to
        ``screen_stack[0]`` when available) — never on ``self.screen`` /
        ``self.query_one``, which target the active (possibly menu/modal)
        screen and produce ``NoMatches: '#tabs'``. Returns ``None`` when the
        base screen is absent or the widget is not found; the
        ``self.query_one`` fallback runs only when no base screen reference
        exists at all. ``screen_stack[0]`` is never removed or mutated.
        """
        base = getattr(self, "_base_screen", None)
        if base is None:
            try:
                stack = list(getattr(self, "screen_stack", []) or [])
                if stack:
                    base = stack[0]
            except Exception:
                base = None
        if base is not None:
            try:
                return base.query_one("#tabs", TabbedContent)
            except Exception:
                logger.debug("base screen has no #tabs", exc_info=True)
                return None
        # No base screen reference at all — best-effort fallback.
        try:
            return self.query_one("#tabs", TabbedContent)
        except Exception:
            logger.debug("no #tabs found anywhere", exc_info=True)
            return None

    # ------------------------------------------------------------- helpers

    def _ensure_history_manager(self) -> Optional[Any]:
        """Лениво создать HistoryManager (один на приложение)."""
        if self._history_manager is None:
            try:
                from app.history.manager import HistoryManager

                self._history_manager = HistoryManager()
            except Exception as e:
                logger.warning(f"HistoryManager unavailable: {e}")
        return self._history_manager

    def _workspace_panes(self) -> List[WorkspacePane]:
        """Все воркспейсы (TabPane-наследники) в TabbedContent."""
        try:
            tabs = self._base_tabs()
            return list(tabs.query(WorkspacePane))
        except Exception:
            return []

    @property
    def active_workspace(self) -> Optional[WorkspacePane]:
        """Активный воркспейс (или None)."""
        try:
            tabs = self._base_tabs()
            pane = getattr(tabs, "active_pane", None)
            if isinstance(pane, WorkspacePane):
                return pane
            active = tabs.active
            for candidate in tabs.query(WorkspacePane):
                if getattr(candidate, "id", None) == active:
                    return candidate
            found = list(tabs.query(WorkspacePane))
            return found[-1] if found else None
        except Exception:
            return None

    def _activate_last_tab(self) -> None:
        """Активировать последнюю добавленную вкладку (best-effort)."""
        try:
            tabs = self._base_tabs()
            tabs_widget = getattr(tabs, "tabs", None)
            if tabs_widget is None:
                return
            tab_ids = None
            for attr in ("tab_ids", "active_ids"):
                tab_ids = getattr(tabs_widget, attr, None)
                if tab_ids:
                    break
            if not tab_ids:
                tab_ids = [t.id for t in tabs_widget.query(Tab) if t.id]
            if tab_ids:
                tabs.active = tab_ids[-1]
        except Exception:
            logger.debug("activate last tab failed", exc_info=True)

    def _update_app_status(self) -> None:
        """Обновить строку статуса (модель/провайдер/проект/режим ≈ print_status_bar)."""
        ws = self.active_workspace
        if ws is None:
            text = "Нет активной вкладки — ➕ Новая вкладка или F2"
        else:
            state = ws.state
            mode_names = {"ask": "💬 ВОПРОС", "agent": "🤖 АГЕНТ", "general": "💡 ОБЩИЙ ЧАТ"}
            mode = mode_names.get(state.mode, state.mode.upper())
            if state.is_new_project:
                mode += " (новый проект)"
            if state.is_legal_mode and state.mode == "general":
                mode += " [Legal]"
            model = "Авто" if state.use_router else (state.orchestrator_model or "Авто")
            project = os.path.basename(state.project_dir) if state.project_dir else "Нет"
            thread = (state.thread_id or "")[:8]
            # Обогащаем строку из session_snapshot (best-effort)
            extra = ""
            try:
                snap = ws.bridge.session_snapshot()
                tester_on = snap.get("tester_on", True)
                gen = snap.get("generator", "") or "—"
                tokens = int(snap.get("tokens", 0) or 0)
                vfs_n = int(snap.get("vfs_files", 0) or 0)
                stage = str(snap.get("stage", "IDLE") or "IDLE")
                extra = (
                    f" │ 🧪 {'ON' if tester_on else 'OFF'}"
                    f" │ ⚙ {gen}"
                    f" │ 📊 {tokens}т"
                    f" │ VFS:{vfs_n}"
                )
                if stage != "IDLE":
                    extra += f" │ ⏳ {stage}"
            except Exception:
                extra = ""
            text = f"{mode} │ 📁 {project} │ 🎯 {model} │ 💬 {thread}…" + extra
            try:
                if not bool(getattr(state, "setup_complete", True)):
                    text += " │ ⏳ Настройка не завершена"
            except Exception:
                pass
        try:
            base = getattr(self, "_base_screen", None)
            if base is not None:
                base.query_one("#app-status", Static).update(text)
            else:
                self.query_one("#app-status", Static).update(text)
        except Exception:
            pass

    # ------------------------------------------------------------- sessions

    async def _restore_sessions(self) -> None:
        """Восстановить сохранённые воркспейсы (максимум MAX_TABS)."""
        try:
            saved = await sessions_db.load_sessions()
        except Exception:
            saved = []
        if not saved:
            return
        history_manager = self._ensure_history_manager()
        history_adapter = HistoryAdapter(history_manager) if history_manager else None
        for record in saved[: self.MAX_TABS]:
            project_path = getattr(record, "project_path", None)
            if project_path and not os.path.isdir(project_path):
                continue
            thread_id = getattr(record, "thread_id", None)
            if thread_id is not None and history_adapter is not None:
                try:
                    thread = await history_adapter.get_thread(thread_id)
                except Exception:
                    thread = None
                if thread is None:
                    thread_id = None
            tab_key = getattr(record, "tab_key", None) or f"ws-{self._workspace_count + 1}"
            try:
                self._workspace_count = max(self._workspace_count, int(str(tab_key).split("-")[-1]))
            except Exception:
                pass
            project_index: Dict[str, Any] = {}
            if project_path:
                try:
                    index_path = os.path.join(project_path, ".ai-agent", "semantic_index.json")
                    if os.path.exists(index_path):
                        with open(index_path, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            project_index = loaded
                except Exception:
                    project_index = {}
            pane = WorkspacePane(
                app=self,
                tab_key=str(tab_key),
                project_dir=project_path,
                thread_id=thread_id,
                session_id=getattr(record, "id", None),
                history_manager=history_manager,
                state=StateAdapter(
                    mode="agent",
                    project_dir=project_path,
                    thread_id=thread_id,
                    project_index=project_index,
                ),
            )
            tabs = self._base_tabs()
            if tabs is None:
                # Expected teardown/shutdown state: skip pane attachment with
                # DEBUG logging only — never an ERROR/exception log, and
                # `screen_stack[0]` is never touched.
                logger.debug(
                    "restore skipped: base #tabs unavailable (teardown/shutdown)"
                )
            else:
                try:
                    await tabs.add_pane(pane)
                except Exception:
                    # Expected teardown/shutdown failure: DEBUG only — no
                    # ERROR record is emitted for a detached base UI.
                    logger.debug(
                        "workspace tab restore skipped (teardown/shutdown)",
                        exc_info=True,
                    )
        self._activate_last_tab()
        self._update_app_status()

    # ------------------------------------------------------------- tabs

    async def action_new_tab(self) -> None:
        """Создать новую вкладку воркспейса (интерактивный выбор проекта).

        Единственная точка входа, вызывающая `select_project(...)`; после
        работы гарантирует живой UI через `_ensure_live_ui()`.
        """
        if len(self._workspace_panes()) >= self.MAX_TABS:
            self.notify(f"Достигнут лимит вкладок ({self.MAX_TABS})", severity="error")
            return
        recent: List[str] = []
        history_manager = self._ensure_history_manager()
        if history_manager is not None:
            try:
                recent = await collect_recent_projects(history_manager)
            except Exception:
                recent = []
        result = await select_project(self, recent_projects=recent)
        if result is None:
            try:
                await self._ensure_live_ui()
            except Exception:
                logger.debug("ensure_live_ui after cancel failed", exc_info=True)
            return
        project_path, is_new = result
        for pane in self._workspace_panes():
            if pane.state.project_dir == project_path:
                self.notify("Этот проект уже открыт в другой вкладке!", severity="error")
                return
        await self._create_workspace(project_path, is_new)
        try:
            await self._ensure_live_ui()
        except Exception:
            logger.debug("ensure_live_ui after new tab failed", exc_info=True)

    async def _create_workspace(
        self,
        project_path: Optional[str],
        is_new: bool = False,
        setup_result: Optional[SetupResult] = None,
        setup_pending: bool = False,
    ) -> WorkspacePane:
        """Создать воркспейс, добавить вкладку, активировать и сохранить сессию."""
        self._workspace_count += 1
        tab_key = f"ws-{self._workspace_count}"
        history_manager = self._ensure_history_manager()
        thread_id: Optional[str] = None
        if history_manager is not None:
            adapter = HistoryAdapter(history_manager)
            try:
                thread_id, _history = await adapter.open_conversation(project_path, "", None)
            except Exception:
                logger.debug("open_conversation failed", exc_info=True)
        pane = WorkspacePane(
            app=self,
            tab_key=tab_key,
            project_dir=project_path,
            thread_id=thread_id,
            history_manager=history_manager,
            mode="agent",
        )
        try:
            pane.state.is_new_project = bool(is_new)
        except Exception:
            pass
        if setup_pending:
            try:
                pane.state.setup_complete = False
            except Exception:
                pass
        if setup_result is not None:
            try:
                await self._apply_setup_result(pane, setup_result)
            except Exception:
                logger.debug("apply_setup_result during create failed", exc_info=True)
        tabs = self._base_tabs()
        if tabs is None:
            # Expected teardown/shutdown state: the pane is returned to the
            # caller without UI attachment (DEBUG log only, never an ERROR).
            logger.debug(
                "add_pane skipped: base #tabs unavailable (teardown/shutdown)"
            )
        else:
            try:
                await tabs.add_pane(pane)
            except Exception:
                # Base UI detached/unmounting (teardown/shutdown): the pane is
                # still returned and only a DEBUG record is ever emitted.
                logger.debug(
                    "add_pane skipped: base UI detached (teardown/shutdown)",
                    exc_info=True,
                )
        self._activate_last_tab()
        try:
            session = sessions_db.new_session(
                tab_key, project_path, pane.state.thread_id, f"⚙ {tab_key}"
            )
            await sessions_db.save_session(session)
            pane.session_id = session.id
        except Exception:
            logger.debug("save session failed", exc_info=True)
        self._update_app_status()
        return pane

    async def action_close_tab(self) -> None:
        """Ctrl+W: запланировать закрытие активной вкладки отдельным воркером.

        Сигнатура сохранена для биндингов/pilot/тестов, но подтверждение
        ``tui_confirm`` больше не ожидается инлайн в pump диспетчера
        действий — оно живёт в ``_run_close_tab_flow``.
        """
        self.run_worker(self._run_close_tab_flow(), group="close-tab", exclusive=True)

    async def _run_close_tab_flow(self) -> None:
        """Закрыть активную вкладку в отдельном воркере (с подтверждением).

        Тело прежнего ``action_close_tab`` дословно: активный воркспейс →
        ``tui_confirm(self, "Закрыть вкладку воркспейса?", default=True)``
        (контракт диалога: bool, default=True) → ``_close_workspace`` при
        подтверждении.
        """
        ws = self.active_workspace
        if ws is None:
            return
        confirmed = await tui_confirm(self, "Закрыть вкладку воркспейса?", default=True)
        if not confirmed:
            return
        await self._close_workspace(ws)

    async def _close_workspace(self, ws: WorkspacePane) -> None:
        """Закрыть воркспейс: cleanup + удаление вкладки + удаление сессии."""
        try:
            await ws.close_workspace()
        except Exception:
            logger.exception("close_workspace failed")
        tabs = self._base_tabs()
        if tabs is None:
            # Expected teardown/shutdown state: fall back to direct pane
            # removal without dereferencing None (DEBUG only, no ERROR).
            try:
                await ws.remove()
            except Exception:
                logger.debug("pane removal failed", exc_info=True)
        else:
            try:
                await tabs.remove_pane(ws)
            except Exception:
                try:
                    await ws.remove()
                except Exception:
                    logger.debug("pane removal failed", exc_info=True)
        if ws.session_id:
            try:
                await sessions_db.delete_session(ws.session_id)
            except Exception:
                logger.debug("session delete failed", exc_info=True)
        self._update_app_status()

    # ------------------------------------------------------------- actions

    async def action_emergency_stop(self) -> None:
        """F9: остановить активный воркспейс; повторное нажатие — остановить все."""
        ws = self.active_workspace
        if ws is None:
            self.notify("Нет активной вкладки", severity="warning")
            return
        self.run_worker(ws.emergency_stop(), group=f"stop-{ws.tab_key}", exclusive=True)
        if self._stop_all_next:
            for pane in self._workspace_panes():
                if pane is not ws:
                    self.run_worker(
                        pane.emergency_stop(), group=f"stop-{pane.tab_key}", exclusive=True
                    )
        self._stop_all_next = not self._stop_all_next
        self.notify("⏹ Запрос остановки отправлен", severity="warning")

    async def action_main_menu(self) -> None:
        """F2: запланировать меню-флоу отдельным воркером (не блокируя pump).

        Idempotency guard ``_main_menu_in_progress`` prevents duplicate menus on
        repeated F2. ``exclusive=True`` cancels any previous flow.
        """
        if self._main_menu_in_progress:
            return
        self._main_menu_in_progress = True
        self.run_worker(self._run_main_menu_flow(), group="main-menu", exclusive=True)

    async def _run_main_menu_flow(self) -> None:
        """Меню-флоу в отдельном воркере: показать меню, затем вернуть живой UI.

        Лениво импортирует ``menus`` и делегирует ``show_main_menu(self)`` —
        все пункты меню, значения ``choice`` и сайд-эффекты не изменены.
        В ``finally`` выполняет прежнюю обязанность «восстановить живой UI»
        без инлайн-await долгой операции в возможно отменённом воркере:
        планирует ``_ensure_live_ui()`` отдельным воркером (best-effort,
        любое исключение подавляется). Никогда не пушит сплеш, не снимает
        пользовательские модалки и не опустошает ``screen_stack``.
        """
        try:
            from . import menus

            await menus.show_main_menu(self)
        finally:
            self._startup_menu_shown = True
            self._startup_menu_in_progress = False
            self._main_menu_in_progress = False
            try:
                self._schedule_ensure_live_ui()
            except Exception:
                logger.debug("ensure_live_ui after menu failed", exc_info=True)

    def _show_startup_menu_once(self) -> None:
        """Idempotent startup-menu trigger (after splash dismissal).

        Returns early when the startup menu was already shown/in progress or a
        ``MainMenuScreen`` is already present. Otherwise schedules
        ``_run_startup_menu_flow`` exactly once. Never pushes splash or mutates
        ``screen_stack[0]``.
        """
        if self._startup_menu_shown or self._startup_menu_in_progress:
            return
        try:
            from .menus import MainMenuScreen

            for scr in list(getattr(self, "screen_stack", []) or []):
                if isinstance(scr, MainMenuScreen):
                    self._startup_menu_shown = True
                    return
        except Exception:
            logger.debug("startup menu presence check failed", exc_info=True)
        self._startup_menu_in_progress = True
        try:
            self.run_worker(
                self._run_startup_menu_flow(), group="main-menu", exclusive=True
            )
        except Exception:
            self._startup_menu_in_progress = False
            logger.debug("startup menu schedule failed", exc_info=True)

    async def _run_startup_menu_flow(self) -> None:
        """Primary startup menu route after splash: ensure live UI then delegate."""
        try:
            await self._ensure_live_ui()
        except Exception:
            logger.debug("startup ensure live ui failed", exc_info=True)
        try:
            await self._run_main_menu_flow()
        finally:
            self._startup_menu_in_progress = False
            self._startup_menu_shown = True

    async def _run_setup_wizard_flow(
        self,
        mode: str = "agent",
        start_stage: Optional[str] = None,
        allow_resume: bool = False,
    ) -> bool:
        """Run the setup wizard and apply its result to the active workspace.

        Creates exactly one bootstrap workspace if none exists. Returns True on
        success, False on cancellation (state unchanged on cancel).
        """
        if self._startup_wizard_in_progress:
            return False
        self._startup_wizard_in_progress = True
        try:
            from .setup_wizard import run_setup_wizard as _run_wizard
        except Exception as e:
            logger.error(f"setup wizard import failed: {e}")
            self._startup_wizard_in_progress = False
            return False
        try:
            ws = self.active_workspace
            if ws is None:
                try:
                    ws = await self._create_workspace(os.getcwd() or None, is_new=False, setup_pending=True)
                except Exception:
                    logger.exception("wizard bootstrap workspace failed")
                    return False
            result = await _run_wizard(
                self, state=ws.state, mode=mode, start_stage=start_stage, allow_resume=allow_resume
            )
            if result is None:
                return False
            try:
                await self._apply_setup_result(ws, result)
            except Exception:
                logger.exception("apply setup result failed")
                return False
            self._startup_setup_complete = True
            try:
                self._update_app_status()
            except Exception:
                pass
            try:
                await ws.persist_session()
            except Exception:
                logger.debug("persist after wizard failed", exc_info=True)
            try:
                ws.query_one("#query-input").focus()
            except Exception:
                pass
            return True
        except Exception:
            logger.exception("setup wizard flow failed")
            return False
        finally:
            self._startup_wizard_in_progress = False

    async def _run_change_directory_flow(self) -> bool:
        """Run the wizard starting at PROJECT, then rebind the current tab (no new tab)."""
        try:
            from .setup_wizard import PROJECT as _PROJECT_STAGE
        except Exception:
            _PROJECT_STAGE = "PROJECT"
        ws = self.active_workspace
        target_mode = ws.state.mode if ws is not None else "agent"
        ok = await self._run_setup_wizard_flow(mode=target_mode, start_stage=_PROJECT_STAGE)
        if not ok:
            return False
        return True

    async def _run_resume_session_flow(self) -> bool:
        """Resume the restored session: mark setup complete and return to chat."""
        try:
            await self._ensure_live_ui()
        except Exception:
            logger.debug("resume ensure live ui failed", exc_info=True)
        ws = self.active_workspace
        if ws is None:
            try:
                ws = await self._create_workspace(os.getcwd() or None, is_new=False)
            except Exception:
                logger.exception("resume bootstrap workspace failed")
                return False
        try:
            ws.state.setup_complete = True
            ws.state.setup_stage = "DONE"
        except Exception:
            pass
        try:
            self._update_app_status()
        except Exception:
            pass
        try:
            ws.query_one("#query-input").focus()
        except Exception:
            pass
        return True

    async def _apply_setup_result(self, ws: "WorkspacePane", result: SetupResult) -> None:
        """Apply a SetupResult to a WorkspacePane (no new tab)."""
        try:
            state = ws.state
            state.mode = result.mode or state.mode
            state.project_dir = result.project_dir
            state.project_index = result.project_index or {}
            state.is_new_project = bool(result.is_new_project)
            state.orchestrator_model = result.orchestrator_model
            state.orchestrator_provider = result.orchestrator_provider
            state.generator_model = result.generator_model
            state.generator_provider = result.generator_provider
            state.use_router = bool(result.use_router)
            state.is_legal_mode = bool(result.is_legal_mode)
            state.thread_id = result.thread_id
            state.current_thread = result.current_thread
            state.setup_complete = True
            state.setup_stage = "DONE"
        except Exception:
            logger.debug("apply setup result (state) failed", exc_info=True)
        try:
            ws.project_dir = result.project_dir
        except Exception:
            pass
        try:
            await ws.rebind_project(
                result.project_dir,
                result.project_index,
                result.thread_id,
                bool(result.is_new_project),
            )
        except Exception:
            logger.debug("WorkspacePane.rebind_project failed", exc_info=True)
        try:
            await ws.persist_session()
        except Exception:
            logger.debug("persist_session failed", exc_info=True)
        try:
            self._update_app_status()
        except Exception:
            pass

    async def action_cycle_mode(self) -> None:
        """Циклическое переключение режима активного воркспейса + persist."""
        ws = self.active_workspace
        if ws is None:
            self.notify("Нет активной вкладки", severity="warning")
            return
        try:
            order = ["agent", "ask", "general"]
            current = ws.state.mode if ws.state.mode in order else "agent"
            idx = order.index(current)
            ws.state.mode = order[(idx + 1) % len(order)]
            try:
                from .settings_io import load_user_settings, save_user_settings

                settings = load_user_settings()
                settings["mode"] = ws.state.mode
                save_user_settings(settings)
            except Exception:
                pass
            self.notify(f"Режим: {ws.state.mode}", severity="information")
            try:
                ws._update_session_status()
            except Exception:
                pass
            self._update_app_status()
        except Exception:
            logger.debug("action_cycle_mode failed", exc_info=True)

    async def action_show_diff(self) -> None:
        """Ctrl+D: открыть окно просмотра диффов активного воркспейса."""
        ws = self.active_workspace
        if ws is None:
            self.notify("Нет активной вкладки", severity="warning")
            return
        try:
            if not ws.bridge._diffs:
                self.notify("Нет изменений", severity="information")
                return
            from .diff_viewer import show_diff_viewer

            show_diff_viewer(
                self,
                dict(ws.bridge._diffs),
                pending_changes=ws.bridge._pending_changes,
            )
        except Exception:
            logger.debug("action_show_diff failed", exc_info=True)

    async def action_model_menu(self) -> None:
        """Ctrl+L: открыть выбор модели активного воркспейса (через /model)."""
        ws = self.active_workspace
        if ws is None:
            self.notify("Нет активной вкладки", severity="warning")
            return
        try:
            self.run_worker(ws._run_command_worker("/model"), group=f"cmd-{ws.tab_key}", exclusive=True)
        except Exception:
            logger.debug("action_model_menu failed", exc_info=True)

    async def action_soft_cancel(self) -> None:
        """Escape: отмена шага на активной контрольной точке (если она есть)."""
        ws = self.active_workspace
        if ws is None:
            return
        try:
            screen = getattr(self, "screen", None)
            is_modal = bool(getattr(screen, "is_modal", False))
        except Exception:
            is_modal = False
        if is_modal:
            return
        try:
            gate_kind = ws.bridge.active_gate()
        except Exception:
            gate_kind = None
        if gate_kind is None:
            return
        try:
            if gate_kind == "plan":
                ws.bridge.handle_action("plan_cancel")
            else:
                ws.bridge.handle_action("cancel")
        except Exception:
            logger.debug("action_soft_cancel failed", exc_info=True)

    async def on_screen_resume(self) -> None:
        """Best-effort guard живого UI на resume экрана (никогда не пушит сплеш/меню)."""
        try:
            await self._ensure_live_ui()
        except Exception:
            logger.debug("on_screen_resume ensure_live_ui failed", exc_info=True)

    async def action_quit(self) -> None:
        """Выход: остановить воркспейсы, отбросить изменения, dispose БД."""

        # Отменить незавершённый startup-воркер перед очисткой.
        try:
            _startup_task = getattr(self, "_startup_task", None)
            if _startup_task is not None and not _startup_task.done():
                _startup_task.cancel()
        except Exception:
            logger.debug("startup task cancel failed", exc_info=True)
        for pane in self._workspace_panes():
            try:
                await pane.emergency_stop()
            except Exception:
                logger.debug("emergency stop on quit failed", exc_info=True)
            if pane.bridge.has_pending:
                try:
                    await pane.bridge.pipeline.discard_pending_changes()
                except Exception:
                    pass
        try:
            from app.mcp import get_mcp_manager
            get_mcp_manager().disconnect_all()
        except Exception:
            logger.debug("MCP disconnect on quit failed", exc_info=True)
        try:
            await sessions_db.dispose()
        except Exception:
            pass
        self.exit()

    # ------------------------------------------------------------- events

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Кнопки тулбара: новая вкладка / меню / директория / модели / стоп."""
        event.stop()
        if event.button.id == "new-tab-btn":
            self.run_worker(self.action_new_tab(), exclusive=True, group="new-tab")
        elif event.button.id == "main-menu-btn":
            self.run_worker(self._run_main_menu_flow(), group="main-menu", exclusive=True)
        elif event.button.id == "change-dir-btn":
            self.run_worker(self._run_change_directory_flow(), group="change-dir", exclusive=True)
        elif event.button.id == "models-btn":
            ws = self.active_workspace
            if ws is not None:
                self.run_worker(
                    ws._run_command_worker("/model"), group=f"cmd-{ws.tab_key}", exclusive=True
                )
        elif event.button.id == "stop-btn":
            self.run_worker(
                self.action_emergency_stop(), exclusive=True, group="emergency-stop"
            )

    def on_tabbed_content_tab_activated(self, event) -> None:
        """Смена активной вкладки → обновить статус-строку."""
        try:
            self._update_app_status()
        except Exception:
            pass
    def get_css_variables(self) -> Dict[str, str]:
        """CSS-переменные приложения (Textual 8.x контракт: $stop, $diff-border и др.)."""
        return {**super().get_css_variables(), **build_css_variables()}
