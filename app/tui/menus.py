"""Главное меню TUI (100% паритет пунктов с main.py:925–952) и полный флоу
настроек провайдеров API (паритет с main.py ``select_provider_settings``).

main.py не импортируется; используются ``config.settings.cfg``,
``app.llm.api_client``, диалоги ``app/tui/dialogs.py`` и ``settings_io``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

# from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option

try:
    from config.settings import cfg
except ImportError:  # pragma: no cover - defensive
    cfg = None

from .app import AboutScreen
from .dialogs import (
    NEW_THREAD,
    GeneratorModelSelectDialog,
    ModelSelectDialog,
    PrefilterSettingsDialog,
    ProvidersDialog,
    provider_for_model,
    push_modal,
    resolve_modal,
    tui_confirm,
    tui_prompt,
)
from .settings_io import load_user_settings, save_user_settings

logger = logging.getLogger(__name__)

_PROVIDERS = ["openrouter", "routerai", "deepseek", "glm", "opencode_go", "qwencloud"]

_MENU_CSS = """
    OptionList { height: auto; max-height: 16; }
    #provider-status { margin-bottom: 1; }
"""


def _mask_key(key: str) -> str:
    """Маскировать API-ключ для отображения."""
    return (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("***" if key else "")


def _provider_status_lines() -> List[str]:
    """Строки статуса провайдеров + текущий reasoning_effort."""
    lines: List[str] = []
    keys: Dict[str, Dict] = {}
    try:
        keys = cfg.get_provider_keys()
    except Exception:
        keys = {}
    for provider in _PROVIDERS:
        api_key = keys.get(provider, {}).get("api_key", "")
        if not api_key:
            status = "❌ отсутствует"
        else:
            masked = _mask_key(api_key)
            try:
                disabled = cfg.is_provider_disabled(provider)
            except Exception:
                disabled = False
            status = f"🚫 отключён (ключ: {masked})" if disabled else f"✅ активен (ключ: {masked})"
        lines.append(f"  [{provider}] {status}")
    effort = keys.get("reasoning_effort", "high")
    lines.append(f"  Текущий reasoning_effort: {effort}")
    return lines


class MainMenuScreen(ModalScreen):
    """Главное меню приложения (пункты 1:1 с main.py + [T] новая вкладка)."""

    DEFAULT_CSS = _MENU_CSS
    BINDINGS = [("escape", "back", "Назад"), ("n", "new_project", "🆕 Новый проект")]

    def __init__(self) -> None:
        """Создать экран меню с future для выбора."""
        super().__init__()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Список пунктов главного меню."""
        with Vertical(id="menu-box"):
            yield Label("📖 Главное меню", id="menu-title")
            yield OptionList(
                Option("[1] 💬 Режим Вопросов", id="1"),
                Option("[2] 🤖 Режим Агента", id="2"),
                Option("[3] 💡 Общий Чат", id="3"),
                Option("[4] 📜 История диалогов", id="4"),
                Option("[5] ⚙️ Настройки модели оркестратора", id="5"),
                Option("[6] 🔧 Настройки модели генератора", id="6"),
                Option("[7] 🔍 Проверка типов (mypy)", id="7"),
                Option("[8] 🔬 Настройки Pre-filter", id="8"),
                Option("[9] 📖 О программе", id="9"),
                Option("[10] 🔑 Настройки провайдеров API", id="10"),
                    Option("[11] 🛡 Доступ и venv", id="11"),
                    Option("[12] 🔌 MCP-серверы", id="12"),

                    Option("[13] 🧪 Тестировщик вкл/выкл", id="13"),
                        Option("[14] 📊 Индексация проекта", id="14"),
                        Option("[15] 🧠 Reasoning Effort", id="15"),

                        Option("[16] 🆕 Режим нового проекта (N)", id="16"),
                    Option("[0] 🚪 Выход", id="0"),
                Option("[T] ➕ Новая вкладка", id="T"),
                id="menu-options",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Закрыть ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор пункта → резолв future."""
        event.stop()
        self._done(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Закрыть меню."""
        event.stop()
        self._cancel()

    def action_back(self) -> None:
        """Escape → закрыть меню."""
        self._cancel()

    def action_new_project(self) -> None:
        """Клавиша 'n' → закрыть меню с выбором нового проекта."""
        self._done("N")

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить меню (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


class ProviderMenuScreen(ModalScreen):
    """Меню настроек провайдеров API (паритет с CLI)."""

    DEFAULT_CSS = _MENU_CSS
    BINDINGS = [("escape", "back", "Назад")]

    def __init__(self) -> None:
        """Создать экран с future для выбора."""
        super().__init__()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Статус-заголовок + список действий."""
        header = "\n".join(_provider_status_lines())
        with Vertical(id="menu-box"):
            yield Label("🔑 Настройки провайдеров API", id="menu-title")
            yield Static(header, id="provider-status")
            yield OptionList(
                Option("[1] Настроить API ключ провайдера", id="1"),
                Option("[2] Изменить reasoning_effort", id="2"),
                Option("[3] Отключить/включить провайдер", id="3"),
                Option("[4] Проверить действительность ключей", id="4"),
                Option("[5] Выбрать провайдер для фоновых ИИ-агентов", id="5"),
                Option("[0] Назад", id="0"),
                id="provider-options",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Закрыть ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор действия → резолв future."""
        event.stop()
        self._done(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Закрыть меню."""
        event.stop()
        self._cancel()

    def action_back(self) -> None:
        """Escape → закрыть меню."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить меню (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def show_main_menu(app) -> None:
    """Показать главное меню и применить выбранное действие к активному воркспейсу."""
    while True:
        # Каждый iteration создаёт свежий MainMenuScreen на UI-loop.
        choice = await push_modal(app, lambda: MainMenuScreen())
        if choice is None:
            return
        ws = app.active_workspace

        if choice in ("1", "2", "3"):
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            modes = {"1": "ask", "2": "agent", "3": "general"}
            ws.state.mode = modes[choice]
            settings = load_user_settings()
            settings["mode"] = ws.state.mode
            save_user_settings(settings)
            app.notify(f"Режим: {ws.state.mode}", severity="information")
            continue

        if choice == "4":
            if ws is None or ws.history_adapter is None:
                app.notify("История недоступна", severity="warning")
                continue
            from .dialogs import select_thread

            thread_id = await select_thread(app, ws.history_adapter)
            if thread_id in (None, NEW_THREAD):
                continue
            ws.state.thread_id = thread_id
            try:
                ws.bridge.history = await ws.history_adapter.get_session_history(
                    thread_id, "", ws.state.get_current_orchestrator_model()
                )
            except Exception:
                logger.debug("history reload failed", exc_info=True)
            ws.write_line(f"📜 Переключено на тред {thread_id}")
            continue

        if choice in ("5", "6"):
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            if choice == "5":
                result = await push_modal(
                    app,
                    lambda: ModelSelectDialog(current_model=ws.state.orchestrator_model),
                )
            else:
                result = await push_modal(
                    app,
                    lambda: GeneratorModelSelectDialog(
                        current_model=ws.state.generator_model
                    ),
                )
            if result:
                if len(result) >= 3:
                    kind = result[0]
                    model_id = result[1]
                    picked_provider = result[2]
                else:
                    kind = result[0]
                    model_id = result[1]
                    picked_provider = None
                if choice == "5":
                    if kind == "auto":
                        ws.state.use_router = True
                        ws.state.orchestrator_model = None
                        ws.state.orchestrator_provider = None
                    else:
                        ws.state.use_router = False
                        ws.state.orchestrator_model = model_id
                        ws.state.orchestrator_provider = picked_provider or provider_for_model(model_id, "orchestrator")
                else:
                    if kind == "auto":
                        ws.state.generator_model = None
                        ws.state.generator_provider = None
                    else:
                        ws.state.generator_model = model_id
                        ws.state.generator_provider = picked_provider or provider_for_model(model_id, "generator")
                ws.bridge.sync_models_to_pipeline()
                settings = load_user_settings()
                settings.update(
                    {
                        "use_router": ws.state.use_router,
                        "fixed_orchestrator_model": ws.state.orchestrator_model,
                        "orchestrator_provider": ws.state.orchestrator_provider,
                        "generator_model": ws.state.generator_model,
                        "generator_provider": ws.state.generator_provider,
                    }
                )
                save_user_settings(settings)
                app.notify("Настройки модели обновлены", severity="information")
            continue

        if choice == "7":
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            ws.state.enable_type_checking = not ws.state.enable_type_checking
            settings = load_user_settings()
            settings["enable_type_checking"] = ws.state.enable_type_checking
            save_user_settings(settings)
            app.notify(
                f"Проверка типов (mypy): {'ВКЛ' if ws.state.enable_type_checking else 'ВЫКЛ'}",
                severity="information",
            )
            continue

        if choice == "8":
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            result = await push_modal(
                app,
                lambda: PrefilterSettingsDialog(
                    current_mode=ws.state.prefilter_mode,
                    current_model=ws.state.prefilter_model,
                ),
            )
            if result:
                mode, model = result
                ws.state.prefilter_mode = mode
                ws.state.prefilter_model = model
                try:
                    ws.state.prefilter_provider = (
                        provider_for_model(model, "orchestrator") if model else None
                    )
                except Exception:
                    ws.state.prefilter_provider = None
                settings = load_user_settings()
                settings.update(
                    {
                        "prefilter_mode": ws.state.prefilter_mode,
                        "prefilter_model": ws.state.prefilter_model,
                        "prefilter_provider": ws.state.prefilter_provider,
                    }
                )
                save_user_settings(settings)
                app.notify(f"Pre-filter: {mode}", severity="information")
            continue

        if choice == "9":
            # О программе — через центральный пуш пользовательских экранов.
            app._push_user_screen(AboutScreen())
            continue

        if choice == "10":
            await show_provider_settings(app)

            continue

        if choice == "11":
            await show_env_access_settings(app)
            continue

        if choice == "12":
            await show_mcp_settings(app)
            continue

        if choice == "13":
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            try:
                ws.state.enable_tester = not bool(getattr(ws.state, "enable_tester", True))
                settings = load_user_settings()
                settings["enable_tester"] = bool(ws.state.enable_tester)
                save_user_settings(settings)
            except Exception as e:
                logger.debug(f"enable_tester toggle failed: {e}")
            state_txt = "ON" if getattr(ws.state, "enable_tester", True) else "OFF"
            app.notify(f"🧪 Тестер: {state_txt}", severity="information")
            try:
                ws.write_line(f"🧪 Тестер: {state_txt}")
            except Exception:
                pass
            continue

        if choice == "14":
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            if not ws.state.project_dir:
                app.notify("Проект не выбран", severity="warning")
                continue
            sub = await tui_prompt(
                app,
                "Индексация проекта:",
                choices=[
                    "status — 📊 Статус индекса",
                    "update — ♻️ Инкрементальное обновление",
                    "rebuild — 🔨 Полная пересборка",
                    "cancel — ❌ Отмена",
                ],
                title="📊 Индексация",
            )
            if not sub or sub.startswith("cancel"):
                continue
            try:
                from .pipeline_bridge import run_blocking

                if sub.startswith("status"):
                    from app.services.index_manager import get_index_stats

                    stats = await run_blocking(get_index_stats, ws.state.project_dir)
                    if isinstance(stats, dict):
                        lines = ["📊 Статистика индекса:"]
                        for k, v in stats.items():
                            lines.append(f"  • {k}: {v}")
                        ws.write_line("\n".join(lines), markup=False)
                    else:
                        ws.write_line(f"📊 Статистика индекса: {stats}", markup=False)
                elif sub.startswith("update"):
                    await ws.bridge.update_index(incremental=True)
                elif sub.startswith("rebuild"):
                    await ws.bridge.update_index(incremental=False)
            except Exception as e:
                app.notify(f"Ошибка индексации: {e}", severity="error")
            continue

        if choice == "15":
            if ws is None:
                app.notify("Нет активной вкладки", severity="warning")
                continue
            picked = await tui_prompt(
                app,
                "Уровень Reasoning Effort:",
                choices=["low", "medium", "high", "auto — сбросить"],
                title="🧠 Reasoning Effort",
            )
            if not picked:
                continue
            if picked.startswith("auto"):
                ws.state.reasoning_effort = None
            elif picked in ("low", "medium", "high"):
                ws.state.reasoning_effort = picked
            else:
                continue
            try:
                setter = getattr(ws.bridge, "set_orchestrator_reasoning_effort", None)
                if callable(setter):
                    setter(ws.state.reasoning_effort)
            except Exception:
                pass
            try:
                settings = load_user_settings()
                settings["reasoning_effort"] = ws.state.reasoning_effort
                save_user_settings(settings)
            except Exception:
                pass
            app.notify(
                f"🧠 Reasoning Effort: {ws.state.reasoning_effort or 'auto'}",
                severity="information",
            )
            continue

        if choice in ("16", "N"):
            await _handle_new_project_toggle(ws, app)
            continue

        if choice == "0":
            await app.action_quit()
            return

        if choice == "T":
            await app.action_new_tab()
            continue



async def _handle_new_project_toggle(ws, app) -> None:
    """Включить/выключить режим нового проекта (NEW_PROJECT поверх agent-режима).

    Никогда не возбуждает исключений наружу: все сбои сохранения настроек,
    обновления статус-строки и записи в чат поглощаются локально.
    """
    # Guard: нет активной вкладки
    if ws is None:
        app.notify("Нет активной вкладки", severity="warning")
        return

    # Toggle состояния (NEW_PROJECT работает поверх agent-режима)
    ws.state.mode = "agent"
    ws.state.is_new_project = not bool(getattr(ws.state, "is_new_project", False))

    # Сохранение настроек через существующие контракты (merge-on-save)
    try:
        settings = load_user_settings()
        settings["mode"] = "agent"
        settings["is_new_project"] = ws.state.is_new_project
        save_user_settings(settings)
    except Exception:
        pass

    # Обновление статус-строки защитным образом (getattr + callable)
    updater = getattr(app, "_update_app_status", None)
    if callable(updater):
        try:
            updater()
        except Exception:
            pass

    # Уведомление и дублирующая строка в чат вкладки
    state_txt = "ВКЛ" if ws.state.is_new_project else "ВЫКЛ"
    message = f"🆕 Новый проект: {state_txt} (режим agent)"
    app.notify(message, severity="information")
    try:
        ws.write_line(message, markup=False)
    except Exception:
        pass

    # Подсказка: каталог проекта не выбран
    if not getattr(ws.state, "project_dir", ""):
        app.notify("Выберите каталог проекта для полной индексации", severity="warning")


async def _check_provider_key(provider: str) -> str:
    """Минимальный тестовый запрос к провайдеру → строка результата."""
    from app.llm.api_client import LLMAPIError, call_llm

    try:
        api_key = cfg.get_provider_keys().get(provider, {}).get("api_key", "")
    except Exception:
        api_key = ""
    masked = _mask_key(api_key)
    try:
        disabled = cfg.is_provider_disabled(provider)
    except Exception:
        disabled = False
    if disabled:
        return f"  [{provider}] ключ: {masked or '—'} 🚫 отключён"
    try:
        test_model = cfg.get_test_model_for_provider(provider)
    except Exception:
        test_model = None
    try:
        await call_llm(
            model=test_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
            preferred_provider=provider,
        )
    except LLMAPIError as e:
        return f"  [{provider}] ключ: {masked or '—'} ❌ ошибка: {str(e)[:100]}"
    except Exception as e:
        return f"  [{provider}] ключ: {masked or '—'} ❌ ошибка: {str(e)[:100]}"
    return f"  [{provider}] ключ: {masked or '—'} ✅ действителен (модель: {test_model})"


async def show_provider_settings(app) -> None:
    """Полный флоу настроек провайдеров (паритет с main.py:7989–8190).

    После каждого действия (кроме «Назад») меню открывается заново.
    """
    while True:
        choice = await push_modal(app, lambda: ProviderMenuScreen())
        if choice is None or choice == "0":
            return

        if choice == "1":
            provider = await push_modal(app, lambda: ProvidersDialog())
            if not provider:
                continue
            key = await tui_prompt(
                app,
                f"Введите API ключ для {provider} (пусто — отмена):",
                free_input=True,
                title="🔑 API ключ",
            )
            if not key or not key.strip():
                continue
            try:
                keys = cfg.get_provider_keys()
                keys.setdefault(provider, {})["api_key"] = key.strip()
                try:
                    cfg.record_provider_entry(provider)
                except Exception:
                    pass
                cfg.save_provider_keys(keys)
                app.notify(f"✅ Ключ {provider} сохранён", severity="information")
                if await tui_confirm(app, "Проверить действительность ключа?", default=False):
                    line = await _check_provider_key(provider)
                    ws = getattr(app, "active_workspace", None)
                    if ws is not None:
                        ws.write_line(line, markup=False)
                    else:
                        app.notify(line, severity="information")
            except Exception as e:
                logger.error(f"provider key save failed: {e}")
                app.notify(f"Ошибка сохранения ключа: {e}", severity="error")
            continue

        if choice == "2":
            options = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
            selected = await tui_prompt(
                app, "Выберите reasoning_effort:", choices=options, title="⚙️ reasoning_effort"
            )
            if selected:
                try:
                    keys = cfg.get_provider_keys()
                    keys["reasoning_effort"] = selected
                    cfg.save_provider_keys(keys)
                    app.notify(f"reasoning_effort: {selected}", severity="information")
                except Exception as e:
                    app.notify(f"Ошибка сохранения: {e}", severity="error")
            continue

        if choice == "3":
            try:
                providers = list(cfg.get_all_configured_providers())
            except Exception:
                providers = []
            if not providers:
                app.notify("Нет настроенных провайдеров", severity="warning")
                continue
            mapping: Dict[str, str] = {}
            labels: List[str] = []
            for p in providers:
                try:
                    disabled = cfg.is_provider_disabled(p)
                except Exception:
                    disabled = False
                label = f"{p} — {'🚫 отключён' if disabled else '✅ активен'}"
                mapping[label] = p
                labels.append(label)
            selected = await tui_prompt(
                app, "Выберите провайдера для переключения:", choices=labels, title="🔀 Вкл/выкл"
            )
            provider = mapping.get(selected or "")
            if provider:
                try:
                    if cfg.is_provider_disabled(provider):
                        cfg.enable_provider(provider)
                        app.notify(f"Провайдер {provider} включён", severity="information")
                    else:
                        cfg.disable_provider(provider)
                        app.notify(f"Провайдер {provider} отключён", severity="information")
                except Exception as e:
                    app.notify(f"Ошибка: {e}", severity="error")
            continue

        if choice == "4":
            try:
                providers = list(cfg.get_all_configured_providers())
            except Exception:
                providers = []
            if not providers:
                app.notify("Нет настроенных провайдеров для проверки", severity="warning")
                continue
            lines = ["🔍 Проверка ключей провайдеров:"]
            for p in providers:
                lines.append(await _check_provider_key(p))
            ws = getattr(app, "active_workspace", None)
            text = "\n".join(lines)
            if ws is not None:
                ws.write_line(text, markup=False)
            app.notify("Проверка ключей завершена", severity="information")
            continue

        if choice == "5":
            try:
                avail = list(cfg.get_available_providers())
            except Exception:
                avail = []
            if len(avail) < 2:
                app.notify("Доступен только один провайдер — выбор не нужен.", severity="information")
                continue
            labels = ["Авто (по порядку ввода ключей)"] + [p.upper() for p in avail]
            selected = await tui_prompt(
                app, "Провайдер для фоновых ИИ-агентов:", choices=labels, title="🎯 Провайдер агентов"
            )
            if selected is None:
                continue
            if selected.startswith("Авто"):
                cfg.set_selected_agent_provider(None)
                app.notify("Провайдер для агентов: Авто", severity="information")
            else:
                lowered = selected.lower()
                if lowered in avail:
                    cfg.set_selected_agent_provider(lowered)
                    app.notify(f"Провайдер для агентов: {lowered.upper()}", severity="information")
            continue


class EnvAccessMenuScreen(ModalScreen):
    """Меню диагностики прав доступа к каталогу проекта и venv."""

    DEFAULT_CSS = _MENU_CSS
    BINDINGS = [("escape", "back", "Назад")]

    def __init__(self, status_lines: List[str]) -> None:
        """Создать экран с future и строками текущего статуса."""
        super().__init__()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._status_lines = status_lines

    def compose(self) -> ComposeResult:
        """Статус-заголовок + список действий."""
        header = "\n".join(self._status_lines)
        with Vertical(id="menu-box"):
            yield Label("🛡 Доступ и venv", id="menu-title")
            yield Static(header, id="env-status")
            yield OptionList(
                Option("[1] Исправить права автоматически (UAC/admin)", id="1"),
                Option("[2] Создать .venv сейчас", id="2"),
                Option("[3] Инструкции для моей ОС", id="3"),
                Option("[0] Назад", id="0"),
                id="env-options",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Закрыть ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор действия → резолв future."""
        event.stop()
        self._done(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Закрыть меню."""
        event.stop()
        self._cancel()

    def action_back(self) -> None:
        """Escape → закрыть меню."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить меню (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def show_env_access_settings(app) -> None:
    """Меню диагностики и исправления прав доступа к каталогу проекта."""
    import os as _os
    import sys as _sys

    from app.services.env_access import (
        create_venv,
        manual_instructions,
        probe,
        request_elevation_and_fix,
    )

    while True:
        project_dir = (
            getattr(getattr(app.active_workspace, "state", None), "project_dir", None)
            or _os.getcwd()
        )

        status = await asyncio.to_thread(probe, project_dir)

        status_lines: List[str] = []
        if status.has_venv:
            status_lines.append(f"venv: найден {status.venv_path}")
        else:
            status_lines.append("venv: не найден")
        status_lines.append(
            f"права записи: {'✅' if status.has_write_access else '❌'}"
        )
        if status.reason:
            status_lines.append(f"причина: {status.reason}")
        status_lines.append(f"платформа: {status.platform}")

        choice = await push_modal(app, lambda: EnvAccessMenuScreen(status_lines))
        if choice is None or choice == "0":
            return

        if choice == "1":
            if await tui_confirm(
                app, "Принимаю — запросить права (UAC/admin)?", default=False
            ):
                ok = await asyncio.to_thread(
                    request_elevation_and_fix, project_dir
                )
                if ok:
                    app.notify(
                        "Права записи предоставлены", severity="information"
                    )
                else:
                    app.notify(
                        "Не удалось получить права (см. инструкции для вашей ОС)",
                        severity="warning",
                    )
            continue

        if choice == "2":
            ok = await asyncio.to_thread(create_venv, project_dir)
            if ok:
                app.notify(
                    "Виртуальное окружение .venv создано",
                    severity="information",
                )
            else:
                app.notify("Не удалось создать .venv", severity="error")
            continue

        if choice == "3":
            text = manual_instructions(_sys.platform)
            ws = getattr(app, "active_workspace", None)
            if ws is not None:
                ws.write_line(text, markup=False)
            else:
                app.notify("Нет активной вкладки — откройте вкладку, чтобы увидеть инструкции", severity="warning")
            continue


class McpMenuScreen(ModalScreen):
    """Меню управления MCP-серверами."""

    DEFAULT_CSS = _MENU_CSS
    BINDINGS = [("escape", "back", "Назад")]

    def __init__(self, status_lines: List[str]) -> None:
        """Создать экран с future и статусом MCP-серверов."""
        super().__init__()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._status_lines = status_lines

    def compose(self) -> ComposeResult:
        """Статус-заголовок + список действий."""
        header = "\n".join(self._status_lines)
        with Vertical(id="menu-box"):
            yield Label("🔌 MCP-серверы", id="menu-title")
            yield Static(header, id="mcp-status")
            yield OptionList(
                Option("[1] Добавить сервер", id="1"),
                Option("[2] Включить/выключить", id="2"),
                Option("[3] Удалить", id="3"),
                Option("[4] Проверить подключение", id="4"),
                Option("[0] Назад", id="0"),
                id="mcp-options",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Закрыть ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор действия → резолв future."""
        event.stop()
        self._done(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Закрыть меню."""
        event.stop()
        self._cancel()

    def action_back(self) -> None:
        """Escape → закрыть меню."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить меню (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def show_mcp_settings(app) -> None:
    """Меню управления MCP-серверами (подключение, добавление, тест)."""
    from app.mcp import get_mcp_manager
    from app.mcp.config_store import default_entry, load_servers

    manager = get_mcp_manager()

    while True:
        status = await asyncio.to_thread(manager.status)

        status_lines: List[str] = []
        if not status:
            status_lines.append("Серверы не настроены — добавьте первый")
        else:
            for item in status:
                enabled = "✅" if item.get("enabled") else "🚫"
                connected = "подключён" if item.get("connected") else "не подключён"
                target_agents = item.get("target_agents", "all")
                scope_label = {
                    "orchestrator": "[Оркестратор+Пре-фильтр]",
                    "tester": "[Тестировщик]",
                    "all": "[Все]",
                }.get(target_agents, "[Все]")
                line = (
                    f"{item.get('name')} [{item.get('transport')}] {enabled} "
                    f"{connected}, N инструментов: {item.get('tool_count', 0)} "
                    f"{scope_label}"
                )
                if item.get("error"):
                    line += f" | ошибка: {item['error']}"
                status_lines.append(line)

        choice = await push_modal(app, lambda: McpMenuScreen(status_lines))
        if choice is None or choice == "0":
            return

        if choice == "1":
            name = await tui_prompt(
                app, "Имя сервера (латиница):", free_input=True, title="🔌 MCP"
            )
            if not name or not name.strip():
                continue
            transport = await tui_prompt(
                app,
                "Transport:",
                choices=["stdio", "http", "sse"],
                title="🔌 MCP transport",
            )
            if not transport:
                continue

            entry = default_entry(name.strip(), transport)

            scope = await tui_prompt(
                app,
                "Назначение MCP-сервера:\n"
                "  1 — orchestrator — 🎯 Только для Оркестратора и Пре-фильтра (анализ и планирование)\n"
                "  2 — tester — 🧪 Только для Тестировщика (тестирование и верификация)\n"
                "  3 — all — 🌐 Для всех агентов (Оркестратор, Пре-фильтр и Тестировщик)\n\n"
                "Введите 1/2/3, названия роли (orchestrator/tester/all) или Enter для 'all'.",
                choices=[
                    "1 — orchestrator — 🎯 Только для Оркестратора и Пре-фильтра",
                    "2 — tester — 🧪 Только для Тестировщика",
                    "3 — all — 🌐 Для всех агентов",
                ],
                free_input=True,
                title="🔌 Назначение MCP-сервера",
            )
            if scope is None:
                scope_value = "all"
            else:
                scope_norm = str(scope).strip().lower()
                if scope_norm.startswith("1") or scope_norm in ("orchestrator", "оркестратор"):
                    scope_value = "orchestrator"
                elif scope_norm.startswith("2") or scope_norm in ("tester", "тестировщик", "тестер"):
                    scope_value = "tester"
                else:
                    scope_value = "all"
            entry["target_agents"] = scope_value

            if transport == "stdio":
                command = await tui_prompt(
                    app,
                    "Команда (например npx):",
                    free_input=True,
                    title="🔌 MCP",
                )
                if not command or not command.strip():
                    continue
                entry["command"] = command.strip()

                args_raw = await tui_prompt(
                    app,
                    "Аргументы через пробел (Enter — нет):",
                    free_input=True,
                    title="🔌 MCP",
                )
                if args_raw and args_raw.strip():
                    entry["args"] = args_raw.split()
            else:
                url = await tui_prompt(
                    app, "URL:", free_input=True, title="🔌 MCP"
                )
                if not url or not url.strip():
                    continue
                entry["url"] = url.strip()

            timeout_raw = await tui_prompt(
                app, "Timeout, сек (Enter = 60):", free_input=True, title="🔌 MCP"
            )
            if timeout_raw and timeout_raw.strip().isdigit():
                entry["timeout"] = int(timeout_raw.strip())

            ok, msg = await asyncio.to_thread(manager.add_server, entry)
            if ok:
                app.notify(msg, severity="information")
            else:
                app.notify(msg, severity="error")
            continue

        if choice == "2":
            servers = load_servers()
            if not servers:
                app.notify("Нет настроенных серверов", severity="warning")
                continue
            labels: List[str] = []
            mapping: Dict[str, Dict] = {}
            for item in servers:
                name = item.get("name", "")
                enabled_mark = "✅" if item.get("enabled", True) else "🚫"
                label = f"{name} — {enabled_mark}"
                labels.append(label)
                mapping[label] = item
            selected = await tui_prompt(
                app, "Выберите сервер:", choices=labels, title="🔌 MCP"
            )
            target = mapping.get(selected or "")
            if target is None:
                continue
            new_enabled = not target.get("enabled", True)
            await asyncio.to_thread(
                manager.set_enabled, target.get("name"), new_enabled
            )
            app.notify(
                f"Сервер {target.get('name')} "
                f"{'включён' if new_enabled else 'выключен'}",
                severity="information",
            )
            continue

        if choice == "3":
            servers = load_servers()
            if not servers:
                app.notify("Нет настроенных серверов", severity="warning")
                continue
            labels = [item.get("name", "") for item in servers]
            selected = await tui_prompt(
                app,
                "Выберите сервер для удаления:",
                choices=labels,
                title="🔌 MCP",
            )
            if not selected:
                continue
            if await tui_confirm(
                app, f"Точно удалить сервер {selected}?", default=False
            ):
                await asyncio.to_thread(manager.remove_server, selected)
                app.notify(f"Сервер {selected} удалён", severity="information")
            continue

        if choice == "4":
            servers = load_servers()
            if not servers:
                app.notify("Нет настроенных серверов", severity="warning")
                continue
            labels = [item.get("name", "") for item in servers]
            selected = await tui_prompt(
                app,
                "Выберите сервер для проверки:",
                choices=labels,
                title="🔌 MCP",
            )
            if not selected:
                continue
            ok, msg = await asyncio.to_thread(manager.test_connection, selected)
            ws = getattr(app, "active_workspace", None)
            if ws is not None:
                ws.write_line(
                    f"[{selected}] {'✅ ' if ok else '❌ '}{msg}", markup=False
                )
            else:
                app.notify(msg, severity="information" if ok else "error")
            continue

