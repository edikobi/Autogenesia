"""Все интерактивные диалоги TUI (замена Prompt.ask / Confirm.ask на ModalScreen).

Базовый механизм: диалог создаётся вызывающей стороной вместе с future
(``asyncio.get_running_loop().create_future()``), пушится через
``app.push_screen(dlg)`` и резолвится в ``_done``/``_cancel``. ``None`` из
future трактуется вызывающим кодом как «отмена».
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
# (удалено) from datetime import datetime — не используется
from typing import Any, List, Optional, Tuple

# (удалено) from textual import on — не используется
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

try:
    from config.settings import cfg
except ImportError:  # pragma: no cover - defensive
    cfg = None

from .adapters import DEFAULT_USER_ID, HistoryAdapter
from .settings_io import save_user_settings

logger = logging.getLogger(__name__)

#: Ограниченное время ожидания cross-loop proxy future в ``push_modal`` (секунды).
#: На таймауте ожидающий экран резолвится с ``None`` через потокобезопасный
#: ``resolve_modal`` — ни один worker-поток не может зависнуть бесконечно.
_PUSH_MODAL_PROXY_TIMEOUT: float = 60.0

#: Сентинелы выбора
AUTO_MODEL = "__auto_router__"
NEW_THREAD = "__new_thread__"
PREV_PAGE = "__prev_page__"
NEXT_PAGE = "__next_page__"
DEFAULT_TESTER = "__default_tester__"

_PROVIDERS = ["openrouter", "routerai", "deepseek", "glm", "opencode_go", "qwencloud"]

_DIALOG_CSS = """
PromptDialog, ConfirmDialog, ModelSelectDialog, GeneratorModelSelectDialog,
TesterModelDialog, ThreadSelectDialog, TextInputDialog, RestoreDialog,
ProjectSelectDialog, PrefilterSettingsDialog, ProvidersDialog,
AskUserDialog, EmergencyNetworkDialog, EmergencyLimitDialog {
    align: center middle;
}
Button {
    min-width: 12;
    height: 3;
    color: $text;
    content-align: center middle;
}
#dialog-buttons Button {
    margin-left: 1;
    min-width: 14;
}
Button.primary,
Button.-primary {
    background: $accent;
    color: #FFFFFF;
    text-style: bold;
}
Button:hover {
    background: $accent 20%;
    color: $text;
}
Button:focus {
    background: $accent 30%;
    color: $text;
    text-style: bold;
}
Button.-primary:hover {
    background: $accent 85%;
    color: #FFFFFF;
}
Button.-primary:focus {
    background: $accent;
    color: #FFFFFF;
    text-style: bold;
}
"""


def build_available_models(role: str = "orchestrator"):
    """Список доступных моделей роли: [(model_id, tagged_name, description, provider)].

    Строится на основе ``cfg.get_available_providers()`` и классификации из
    ``config.provider_models`` — main.py НЕ импортируется.
    """
    try:
        from config.provider_models import get_role_models
    except Exception:
        get_role_models = None
    try:
        available = list(cfg.get_available_providers())
    except Exception:
        available = []
    if not available:
        return []
    try:
        selected = cfg.get_selected_agent_provider()
    except Exception:
        selected = None
    try:
        manual = list(cfg.get_manually_entered_providers())
    except Exception:
        manual = []

    sorted_providers: List[str] = []
    if selected and selected in available:
        sorted_providers.append(selected)
    for p in manual:
        if p in available and p not in sorted_providers:
            sorted_providers.append(p)
    for p in available:
        if p not in sorted_providers:
            sorted_providers.append(p)

    models = []
    for provider in sorted_providers:
        entries = []
        if get_role_models is not None:
            try:
                entries = list(get_role_models(provider, role))
            except Exception:
                entries = []
        if not entries:
            try:
                from config.provider_models import PROVIDER_MODELS

                entries = list(PROVIDER_MODELS.get(provider, []))
            except Exception:
                entries = []
        for model_id, display_name, description in entries:
            models.append(
                (str(model_id), f"[{provider.upper()}] {display_name}", str(description), provider)
            )
    return models


def provider_for_model(model_id: str, role: str = "orchestrator") -> Optional[str]:
    """Найти провайдера для модели (или None)."""
    for mid, _tag, _desc, provider in build_available_models(role):
        if mid == model_id:
            return provider
    return None


# ============================================================================
# БАЗОВЫЙ МЕХАНИЗМ
# ============================================================================

class PromptDialog(ModalScreen):
    """Базовый модальный диалог: OptionList для выбора или Input для ввода."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 70%; max-width: 92; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #dialog-buttons Button { margin-left: 1; }
    OptionList { height: auto; max-height: 14; }
    Input { height: 3; }
    TextArea { height: 8; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(
        self,
        title: str,
        text: str,
        choices: Optional[List[str]] = None,
        free_input: bool = False,
        default: Optional[str] = None,
        multiline: bool = False,
    ) -> None:
        super().__init__()
        self.dialog_title = title or "Выбор"
        self.dialog_text = text or ""
        self.choices = [str(c) for c in (choices or [])]
        self.free_input = bool(free_input) or not self.choices
        self.default = default
        self.multiline = multiline
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Компоновать диалог: заголовок, текст, ввод/выбор, кнопки."""
        with Vertical(id="dialog-box"):
            yield Label(self.dialog_title, id="dialog-label")
            if self.free_input:
                if self.multiline:
                    yield TextArea(self.default or "", id="dialog-area")
                else:
                    yield Input(value=self.default or "", placeholder="Ваш ввод...", id="dialog-input")
            else:
                yield OptionList(
                    *[Option(c, id=str(i)) for i, c in enumerate(self.choices)], id="dialog-options"
                )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ OK ]", variant="primary", id="ok-btn")
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор опции → завершить диалог значением."""
        event.stop()
        idx = None
        raw_id = event.option.id
        if raw_id is not None and str(raw_id).isdigit():
            idx = int(str(raw_id))
        if idx is not None and 0 <= idx < len(self.choices):
            self._done(self.choices[idx])
        else:
            self._done(str(getattr(event.option, "prompt", "")))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter в поле ввода → валидация → завершение диалога значением."""
        event.stop()
        error = self._validate_input(event.value)
        if error is not None:
            self.app.notify(error, severity="error")
            return
        self._done(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """OK/Отмена: взять текущий выбор/ввод или закрыть с None."""
        event.stop()
        if event.button.id == "ok-btn":
            if self.free_input:
                if self.multiline:
                    self._done(self.query_one("#dialog-area", TextArea).text)
                else:
                    self._done(self.query_one("#dialog-input", Input).value)
            else:
                ol = self.query_one("#dialog-options", OptionList)
                idx = ol.highlighted
                if idx is None:
                    self.app.notify("Выберите вариант", severity="warning")
                    return
                self._done(self.choices[idx])
        else:
            self._cancel()

    def action_cancel(self) -> None:
        """Binding Escape → отмена."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)

    def _validate_input(self, value: str) -> Optional[str]:
        """Хук валидации ввода; None — ввод корректен (точка переопределения наследников)."""
        return None


class ConfirmDialog(ModalScreen):
    """Диалог Да/Нет (замена Confirm.ask)."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 60; height: auto; border: thick $border;
                  background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: center; }
    #dialog-buttons Button { margin-left: 1; }
    """
    BINDINGS = [("escape", "no", "Нет")]

    def __init__(self, text: str, default: bool = True) -> None:
        super().__init__()
        self.dialog_text = text or ""
        self.default = bool(default)
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Компоновать вопрос и кнопки Да/Нет."""
        with Vertical(id="dialog-box"):
            yield Label(self.dialog_text, id="dialog-label")
            with Horizontal(id="dialog-buttons"):
                yield Button("[ ✅ Да ]", variant="primary", id="yes-btn")
                yield Button("[ ❌ Нет ]", id="no-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Да/Нет → резолв future."""
        event.stop()
        self._done(event.button.id == "yes-btn")

    def action_no(self) -> None:
        """Escape → Нет."""
        self._done(False)

    def _done(self, value: bool) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)


async def tui_prompt(
    app,
    text: str,
    choices: Optional[List[str]] = None,
    default: Optional[str] = None,
    free_input: bool = False,
    title: str = "Выбор",
    multiline: bool = False,
) -> Optional[str]:
    """Модальный ввод/выбор через push_modal; None = отмена."""
    return await push_modal(
        app,
        lambda: PromptDialog(
            title=title, text=text, choices=choices, free_input=free_input,
            default=default, multiline=multiline,
        ),
    )



async def tui_confirm(app, text: str, default: bool = True) -> bool:
    """Модальный Да/Нет через push_modal; None трактуется как default."""
    result = await push_modal(app, lambda: ConfirmDialog(text=text, default=default))
    return default if result is None else bool(result)



# ============================================================================
# ГОТОВЫЕ ДИАЛОГИ
# ============================================================================

class ModelSelectDialog(ModalScreen):
    """Выбор модели оркестратора/роутера.

    Dismiss-значение: ``("auto", None)`` | ``("model", model_id)`` | ``None`` (отмена).
    """

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 78%; max-width: 100; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    OptionList { height: auto; max-height: 16; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(
        self,
        role: str = "orchestrator",
        current_model: Optional[str] = None,
        title: str = "🎯 Выбор модели оркестратора",
        show_auto: bool = True,
    ) -> None:
        super().__init__()
        self.role = role
        self.current_model = current_model
        self.show_auto = show_auto
        self.dialog_title = title
        self.models = build_available_models(role)
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Список моделей + пункт «Авто (роутер)»."""
        with Vertical(id="dialog-box"):
            yield Label(self.dialog_title, id="dialog-label")
            options = []
            if self.show_auto:
                options.append(Option("🔄 Авто (роутер)", id=AUTO_MODEL))
            for i, (model_id, tagged, description, _provider) in enumerate(self.models):
                marker = "✓ " if model_id == self.current_model else ""
                options.append(Option(f"{marker}{tagged} — {description[:70]}", id=f"m{i}"))
            if not options:
                options.append(Option("(нет доступных моделей — проверьте API-ключи)", id="__none__"))
            yield OptionList(*options, id="model-options")
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор модели / роутера → резолв future."""
        event.stop()
        opt_id = str(event.option.id)
        if opt_id == "__none__":
            self._cancel()
            return
        if opt_id == AUTO_MODEL:
            self._done(("auto", None))
            return
        try:
            entry = self.models[int(opt_id[1:])]
            model_id = entry[0]
        except Exception:
            self._cancel()
            return
        # Remember the provider of the chosen [PROVIDER] Model row so callers
        # can distinguish "same model under different provider" as a real change.
        self.selected_provider = entry[3] if len(entry) > 3 else None
        self._done(("model", model_id, self.selected_provider))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Отмена."""
        event.stop()
        self._cancel()

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


class GeneratorModelSelectDialog(ModelSelectDialog):
    """Выбор модели генератора (без опции роутера)."""

    def __init__(self, current_model: Optional[str] = None) -> None:
        super().__init__(
            role="generator",
            current_model=current_model,
            title="🔧 Выбор модели генератора",
            show_auto=False,
        )


class TesterModelDialog(ModalScreen):
    """Выбор модели Тестировщика.

    Dismiss-значение: ``(model_id|None, provider|None)`` или ``None`` (отмена).
    Пункт «по умолчанию» возвращает ``(None, None)`` = модель оркестратора.
    """

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 76%; max-width: 96; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    OptionList { height: auto; max-height: 14; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, default_model: Optional[str] = None) -> None:
        super().__init__()
        self.default_model = default_model
        self.models = build_available_models("orchestrator")
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Список: «модель оркестратора по умолчанию» + доступные модели."""
        default_name = "авто"
        if self.default_model:
            try:
                default_name = cfg.get_model_display_name(self.default_model)
            except Exception:
                default_name = str(self.default_model)
        options = [Option(f"⭐ Модель Оркестратора по умолчанию ({default_name})", id=DEFAULT_TESTER)]
        for i, (mid, tagged, description, _prov) in enumerate(self.models):
            options.append(Option(f"{tagged} — {description[:60]}", id=f"t{i}"))
        with Vertical(id="dialog-box"):
            yield Label("🧪 Выбор модели Тестировщика (пусто = модель Оркестратора):", id="dialog-label")
            yield OptionList(*options, id="tester-options")
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор модели тестера → (model, provider) или (None, None)."""
        event.stop()
        opt_id = str(event.option.id)
        if opt_id == DEFAULT_TESTER:
            self._done((None, None))
            return
        try:
            entry = self.models[int(opt_id[1:])]
            self._done((entry[0], entry[3]))
        except Exception:
            self._cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Отмена."""
        event.stop()
        self._cancel()

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def open_tester_model_selection(app, default_model: Optional[str] = None):
    """Показать диалог модели Тестировщика через push_modal; None = отмена."""
    return await push_modal(
        app, lambda: TesterModelDialog(default_model=default_model)
    )




class AskUserDialog(ModalScreen):
    """Модальный диалог уточнения от ИИ-агента (ask_user tool)."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 78%; max-width: 96; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    #dialog-question { margin-bottom: 1; }
    #dialog-context { margin-bottom: 1; color: $text-muted; }
    #dialog-options { height: auto; max-height: 10; margin-bottom: 1; }
    #ask-user-input { height: 3; margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #dialog-buttons Button { margin-left: 1; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(
        self,
        question: str,
        options: Optional[List[str]] = None,
        context: str = "",
    ) -> None:
        """Создать диалог уточнения с ≤5 вариантами и свободным вводом."""
        super().__init__()
        self.question = question or ""
        self.context = context or ""
        normalized: List[str] = []
        for opt in (options or []):
            if opt is None:
                continue
            value = str(opt).strip()
            if value:
                normalized.append(value)
            if len(normalized) >= 5:
                break
        self.options = normalized
        self.selected_option: Optional[str] = None
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Собрать заголовок, вопрос, контекст, опции, свободный ввод, кнопки."""
        with VerticalScroll(id="dialog-box"):
            yield Label("❓ Уточнение от ИИ-агента", id="dialog-label")
            yield Static(self.question, id="dialog-question")
            if self.context:
                yield Static(f"Контекст: {self.context}", id="dialog-context")
            if self.options:
                with Vertical(id="dialog-options"):
                    for i, opt in enumerate(self.options):
                        yield Button(f"[{i}] {opt}", id=f"opt-{i}")
            yield Input(
                placeholder="Или введите свой вариант в свободной форме...",
                id="ask-user-input",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ ОК ]", variant="primary", id="ok-btn")
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Выбор варианта, ОК или Отмена."""
        event.stop()
        btn_id = event.button.id or ""
        if btn_id.startswith("opt-"):
            try:
                idx = int(btn_id[4:])
                if 0 <= idx < len(self.options):
                    self.selected_option = self.options[idx]
            except Exception:
                return
            try:
                input_widget = self.query_one("#ask-user-input", Input)
                has_text = bool(input_widget.value.strip())
            except Exception:
                has_text = False
            if not has_text and self.selected_option:
                self._done(self.selected_option)
            return
        if btn_id == "ok-btn":
            try:
                input_widget = self.query_one("#ask-user-input", Input)
                free_value = input_widget.value.strip()
            except Exception:
                free_value = ""
            answer = free_value or self.selected_option or ""
            if not answer:
                self.app.notify("Введите или выберите ответ", severity="warning")
                return
            self._done(answer)
            return
        self._cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter в поле ввода → принять значение."""
        event.stop()
        value = (event.value or "").strip()
        if not value and self.selected_option:
            value = self.selected_option
        if not value:
            self.app.notify("Введите или выберите ответ", severity="warning")
            return
        self._done(value)

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def open_ask_user_dialog(
    app, question: str, options: Optional[List[str]] = None, context: str = ""
) -> Optional[str]:
    """Показать модальный диалог уточнения через push_modal (None = отмена)."""
    return await push_modal(
        app,
        lambda: AskUserDialog(question=question, options=options, context=context),
    )



class EmergencyNetworkDialog(ModalScreen):
    """Диалог восстановления при сетевой ошибке/ошибке провайдера."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 78%; max-width: 96; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; }
    #dialog-buttons Button { margin-bottom: 1; width: 100%; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, event) -> None:
        """Создать диалог на основе события emergency manager."""
        super().__init__()
        self.event = event
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Показать причину, сводку переподключений и статус сети."""
        try:
            from app.services.emergency_recovery import get_emergency_role_label
            role_label = get_emergency_role_label(getattr(self.event, "agent_role", "orchestrator"))
        except Exception:
            role_label = "Оркестратор"
        diag = getattr(self.event, "diagnostics", None)
        is_connected = bool(getattr(diag, "is_connected", False)) if diag else False
        latency = float(getattr(diag, "latency_ms", 0.0) or 0.0) if diag else 0.0
        download = float(getattr(diag, "download_mbps", 0.0) or 0.0) if diag else 0.0
        attempt = int(getattr(self.event, "reconnect_attempts", 3) or 3)
        role_display = getattr(self.event, "role_label", "") or role_label
        with VerticalScroll(id="dialog-box"):
            yield Label("🔄 Сбой связи с провайдером", id="dialog-label")
            yield Static(
                f"Последние {attempt} попыток переподключения не удались. Роль: {role_display}.",
                id="dialog-reason",
            )
            yield Static(
                f"Интернет: {'✅ OK' if is_connected else '❌ НЕТ'} | "
                f"Latency: {latency:.0f}ms | Download: {download:.2f} Mbps",
                id="dialog-network",
            )
            with Vertical(id="dialog-buttons"):
                if role_label in ("Пре-Фильтр", "Тестировщик"):
                    yield Button(f"🎯 Сменить модель ({role_label})", id="swap-orch")
                else:
                    yield Button("🎯 Сменить модель Оркестратора", id="swap-orch")
                yield Button("🔧 Сменить модель Генератора", id="swap-gen")
                yield Button("🔄 Сменить обе модели", id="swap-both")
                yield Button(f"🎯 Сменить модель {role_label}", id="swap-current-role")
                yield Button("🔁 Попробовать ещё раз", id="retry-reconnect")
                yield Button("🚪 Завершить сессию (сохранить контекст)", id="end-session")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Вернуть строковое решение (orchestrator/generator/both/end_session/current_role/retry)."""
        event.stop()
        btn_id = event.button.id or ""
        mapping = {
            "swap-orch": "orchestrator",
            "swap-gen": "generator",
            "swap-both": "both",
            "end-session": "end_session",
            "swap-current-role": "current_role",
            "retry-reconnect": "retry",
        }
        value = mapping.get(btn_id)
        if value is None:
            self._cancel()
            return
        self._done(value)

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def open_emergency_network_dialog(app, event) -> Optional[str]:
    """Показать диалог сетевой ошибки через push_modal (None = отмена)."""
    return await push_modal(app, lambda: EmergencyNetworkDialog(event))



class EmergencyLimitDialog(ModalScreen):
    """Диалог при достижении лимита (tool calls / iterations)."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 72%; max-width: 92; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; }
    #dialog-buttons Button { margin-bottom: 1; width: 100%; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, event) -> None:
        """Создать диалог лимита из события emergency manager."""
        super().__init__()
        self.event = event
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Показать роль, лимит и действия."""
        role = str(getattr(self.event, "agent_role", "orchestrator"))
        limit_type = str(getattr(self.event, "limit_type", "iterations"))
        cur = int(getattr(self.event, "current_value", 0) or 0)
        max_v = int(getattr(self.event, "max_value", 0) or 0)
        with VerticalScroll(id="dialog-box"):
            yield Label("⚠️ Достигнут лимит", id="dialog-label")
            yield Static(f"Роль: {role}", id="dialog-role")
            yield Static(f"Лимит: {limit_type} ({cur}/{max_v})", id="dialog-limit")
            with Vertical(id="dialog-buttons"):
                yield Button("➕ Дать ещё +10 вызовов / +5 итераций", id="extra-quota")
                yield Button("📝 Сформировать финальный ответ / инструкцию сейчас", id="finalize")
                yield Button("🚪 Завершить сессию", id="end-session")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Вернуть строковое решение (extra_quota/finalize/end_session)."""
        event.stop()
        btn_id = event.button.id or ""
        mapping = {
            "extra-quota": "extra_quota",
            "finalize": "finalize",
            "end-session": "end_session",
        }
        value = mapping.get(btn_id)
        if value is None:
            self._cancel()
            return
        self._done(value)

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value: str) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог (future → None) — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def open_emergency_limit_dialog(app, event) -> Optional[str]:
    """Показать диалог лимита через push_modal (None = отмена)."""
    return await push_modal(app, lambda: EmergencyLimitDialog(event))



class TextInputDialog(PromptDialog):
    """Свободный ввод (одно- или многострочный): критика, название и т.п."""

    def __init__(
        self,
        title: str,
        text: str,
        default: Optional[str] = None,
        multiline: bool = False,
    ) -> None:
        super().__init__(title=title, text=text, free_input=True, default=default, multiline=multiline)


class RestoreDialog(PromptDialog):
    """Ввод ID сессии бэкапа для /restore с валидацией формата."""

    _VALID = re.compile(r"^[A-Za-z0-9_\-\.]{4,}$|^\d+$")

    def __init__(self) -> None:
        super().__init__(
            title="♻️ Восстановление из бэкапа",
            text="Введите ID сессии бэкапа (или номер из списка):",
            free_input=True,
        )

    def _validate_input(self, value: str) -> Optional[str]:
        """Валидация формата ID сессии бэкапа; при ошибке диалог остаётся открытым."""
        if not self._VALID.match((value or "").strip()):
            return "Неверный формат ID сессии"
        return None


class ThreadSelectDialog(ModalScreen):
    """Пагинированный выбор треда.

    Dismiss-значение: thread_id | NEW_THREAD | PREV_PAGE | NEXT_PAGE | None.
    """

    PREV = PREV_PAGE
    NEXT = NEXT_PAGE
    NEW = NEW_THREAD

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 82%; max-width: 104; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    OptionList { height: auto; max-height: 16; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, threads, previews, total: int, pages: int, page: int) -> None:
        super().__init__()
        self.threads = list(threads or [])
        self.previews = list(previews or [])
        self.total = total
        self.pages = pages
        self.page = page
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Список тредов с превью + навигация + «Новый диалог»."""
        with Vertical(id="dialog-box"):
            yield Label(
                f"📜 История диалогов ({self.total} шт.) — страница {self.page}/{max(self.pages, 1)}",
                id="dialog-label",
            )
            options = []
            if self.page > 1:
                options.append(Option("« Предыдущая страница", id=PREV_PAGE))
            for i, t in enumerate(self.threads):
                title = (getattr(t, "title", "") or "")[:28]
                count = getattr(t, "message_count", 0)
                preview = self.previews[i] if i < len(self.previews) else ""
                options.append(Option(f"{i + 1}. {title} [{count} сообщ.] — {preview}", id=str(t.id)))
            if self.pages > self.page:
                options.append(Option("» Следующая страница", id=NEXT_PAGE))
            options.append(Option("➕ Новый диалог", id=NEW_THREAD))
            yield OptionList(*options, id="thread-options")
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор треда/навигации/нового → резолв future."""
        event.stop()
        self._done(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Отмена."""
        event.stop()
        self._cancel()

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def select_thread(app, history_adapter: Optional[HistoryAdapter], per_page: int = 5) -> Optional[str]:
    """Цикл пагинации тредов через push_modal → thread_id | NEW_THREAD | None."""
    if history_adapter is None:
        app.notify("История недоступна", severity="warning")
        return None
    page = 1
    while True:
        try:
            threads, total, pages = await history_adapter.list_threads_paginated(
                DEFAULT_USER_ID, page, per_page
            )
        except Exception as e:
            app.notify(f"Ошибка истории: {e}", severity="error")
            return None
        previews = []
        for t in threads:
            try:
                previews.append(await history_adapter.thread_preview(t.id))
            except Exception:
                previews.append("")
        result = await push_modal(
            app,
            lambda: ThreadSelectDialog(threads, previews, total, pages, page),
        )
        if result is None:
            return None
        if result == PREV_PAGE and page > 1:
            page -= 1
            continue
        if result == NEXT_PAGE and page < pages:
            page += 1
            continue
        return result



class ProjectSelectDialog(ModalScreen):
    """Выбор проекта. Dismiss-значение: (kind, value) | None.

    kind: ``"path"`` | ``"enter_existing"`` | ``"create_new"``.
    """

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 80%; max-width: 100; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    OptionList { height: auto; max-height: 12; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, recent_projects: Optional[List[str]] = None, current_dir: Optional[str] = None) -> None:
        super().__init__()
        self.recent = [p for p in (recent_projects or []) if p]
        self.current_dir = current_dir or os.getcwd()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Опции: текущая директория, недавние, ввод пути, новый проект."""
        options = [Option(f"📁 Текущая директория: {self.current_dir}", id="cwd")]
        for i, path in enumerate(self.recent[:5]):
            options.append(Option(f"📂 Недавний проект: {path}", id=f"r{i}"))
        options.append(Option("✏️ Ввести путь существующего проекта", id="enter"))
        options.append(Option("🆕 Создать новый проект", id="create"))
        with Vertical(id="dialog-box"):
            yield Label("📁 Выбор проекта", id="dialog-label")
            yield OptionList(*options, id="project-options")
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор варианта проекта → резолв future."""
        event.stop()
        opt_id = str(event.option.id)
        if opt_id == "cwd":
            self._done(("path", self.current_dir))
        elif opt_id.startswith("r"):
            try:
                idx = int(opt_id[1:])
                self._done(("path", self.recent[idx]))
            except Exception:
                self._cancel()
        elif opt_id == "enter":
            self._done(("enter_existing", None))
        elif opt_id == "create":
            self._done(("create_new", None))
        else:
            self._cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Отмена."""
        event.stop()
        self._cancel()

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def select_project(
    app,
    recent_projects: Optional[List[str]] = None,
) -> Optional[Tuple[str, bool]]:
    """Полный флоу выбора проекта через push_modal → (path, is_new) | None."""
    try:
        current_dir = os.path.abspath(os.getcwd())
    except Exception:
        current_dir = os.getcwd()
    result = await push_modal(
        app,
        lambda: ProjectSelectDialog(recent_projects=recent_projects, current_dir=current_dir),
    )
    if result is None:
        return None
    kind, value = result
    if kind == "path":
        return (value, False)
    if kind == "enter_existing":
        path = await tui_prompt(
            app,
            "Введите полный путь к существующей директории проекта:",
            free_input=True,
            title="📂 Существующий проект",
        )
        if not path or not path.strip():
            return None
        path = os.path.expanduser(path.strip().strip("\"'"))
        if not os.path.isdir(path):
            app.notify(f"Директория не найдена: {path}", severity="error")
            return None
        return (os.path.abspath(path), False)
    if kind == "create_new":
        path = await tui_prompt(
            app,
            "Введите путь нового проекта (директория будет создана):",
            free_input=True,
            default="./new_project",
            title="🆕 Новый проект",
        )
        if not path or not path.strip():
            return None
        path = os.path.expanduser(path.strip().strip("\"'"))
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            app.notify(f"Не удалось создать директорию: {e}", severity="error")
            return None
        return (os.path.abspath(path), True)
    return None



async def collect_recent_projects(history_manager, limit: int = 5) -> List[str]:
    """Собрать недавние пути проектов из истории (для ProjectSelectDialog)."""
    try:
        adapter = HistoryAdapter(history_manager)
        threads, _total, _pages = await adapter.list_threads_paginated(DEFAULT_USER_ID, 1, 20)
        seen: List[str] = []
        for t in threads:
            p = getattr(t, "project_path", None)
            if p and p not in seen:
                seen.append(p)
            if len(seen) >= limit:
                break
        return seen
    except Exception:
        return []


class PrefilterSettingsDialog(ModalScreen):
    """Настройки Pre-filter. Dismiss-значение: (mode, model|None) | None."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 72; height: auto; max-height: 85%;
                  border: thick $border; background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    OptionList { height: auto; max-height: 9; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, current_mode: str = "normal", current_model: Optional[str] = None) -> None:
        super().__init__()
        self.current_mode = current_mode or "normal"
        self.current_model = current_model
        self.models = build_available_models("orchestrator")
        self._id_to_model = {
            f"m{i}": (mid, prov) for i, (mid, _tag, _desc, prov) in enumerate(self.models)
        }
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        """Два списка: режим и модель pre-filter."""
        with Vertical(id="dialog-box"):
            yield Label("🔬 Настройки Pre-filter", id="dialog-label")
            yield Label("Режим:")
            yield OptionList(
                Option("Обычный (normal)", id="normal"),
                Option("Продвинутый (advanced)", id="advanced"),
                id="pf-mode",
            )
            yield Label("Модель (Авто = из конфига):")
            options = [Option("Авто (из конфига)", id=AUTO_MODEL)]
            for i, (mid, tagged, description, _prov) in enumerate(self.models):
                options.append(Option(f"{tagged} — {description[:60]}", id=f"m{i}"))
            yield OptionList(*options, id="pf-model")
            with Horizontal(id="dialog-buttons"):
                yield Button("[ OK ]", variant="primary", id="ok-btn")
                yield Button("[ Отмена ]", id="cancel-btn")

    def on_mount(self) -> None:
        """Подсветить текущий режим."""
        try:
            mode_ol = self.query_one("#pf-mode", OptionList)
            mode_ol.highlighted = 0 if self.current_mode == "normal" else 1
        except Exception:
            pass

    @staticmethod
    def _selected_id(ol: OptionList) -> Optional[str]:
        """id подсвеченной опции (или None)."""
        idx = ol.highlighted
        if idx is None:
            return None
        try:
            opt = ol.get_option_at_index(idx)
        except Exception:
            return None
        return str(opt.id) if opt.id is not None else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """OK → (mode, model); Отмена → None."""
        event.stop()
        if event.button.id != "ok-btn":
            self._cancel()
            return
        mode = self._selected_id(self.query_one("#pf-mode", OptionList)) or self.current_mode
        model_opt = self._selected_id(self.query_one("#pf-model", OptionList))
        if model_opt == AUTO_MODEL or model_opt is None:
            model = None
        else:
            pair = self._id_to_model.get(model_opt)
            model = pair[0] if pair else None
        self._done((mode, model))

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


class ProvidersDialog(ModalScreen):
    """Менеджер API-ключей: выбор провайдера. Dismiss-значение: имя | None."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #dialog-box { width: 66; height: auto; border: thick $border;
                  background: $surface; padding: 1 2; }
    #dialog-label { margin-bottom: 1; }
    OptionList { height: auto; max-height: 10; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self) -> None:
        super().__init__()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    @staticmethod
    def _describe(provider: str) -> str:
        """Статусная строка провайдера с маскированным ключом."""
        try:
            key = cfg.get_provider_keys().get(provider, {}).get("api_key", "")
        except Exception:
            key = ""
        if not key:
            return f"{provider}: ❌ ключ отсутствует"
        masked = key[:6] + "..." + key[-4:] if len(key) > 10 else "***"
        try:
            disabled = cfg.is_provider_disabled(provider)
        except Exception:
            disabled = False
        return f"{provider}: {'🚫 отключён' if disabled else '✅ активен'} ({masked})"

    def compose(self) -> ComposeResult:
        """Список провайдеров со статусами."""
        with Vertical(id="dialog-box"):
            yield Label("🔑 Настройки провайдеров API — выберите провайдера:", id="dialog-label")
            yield OptionList(
                *[Option(self._describe(p), id=p) for p in _PROVIDERS], id="provider-options"
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[ Закрыть ]", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Выбор провайдера → имя провайдера."""
        event.stop()
        self._done(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Закрыть."""
        event.stop()
        self._cancel()

    def action_cancel(self) -> None:
        """Escape → отмена."""
        self._cancel()

    def _done(self, value) -> None:
        """Резолвить future и закрыть экран (идемпотентно, через resolve_modal)."""
        resolve_modal(self, value)

    def _cancel(self) -> None:
        """Отменить диалог — идемпотентно, через resolve_modal."""
        resolve_modal(self, None)


async def open_providers_settings(app) -> None:
    """Флоу настройки ключей через push_modal (сохранение side-effects сохранены)."""
    while True:
        provider = await push_modal(app, lambda: ProvidersDialog())
        if not provider:
            return
        key = await tui_prompt(
            app,
            f"Введите API ключ для {provider} (пусто — отмена):",
            free_input=True,
            title="🔑 API ключ",
        )
        if not key or not key.strip():
            continue
        key = key.strip()
        try:
            keys = cfg.get_provider_keys()
            keys.setdefault(provider, {})["api_key"] = key
            try:
                cfg.record_provider_entry(provider)
            except Exception:
                pass
            cfg.save_provider_keys(keys)
            settings = {}
            try:
                from .settings_io import load_user_settings

                settings = load_user_settings()
            except Exception:
                settings = {}
            settings[f"provider_key_set_{provider}"] = True
            save_user_settings(settings)
            app.notify(f"✅ Ключ {provider} сохранён", severity="information")
        except Exception as e:
            logger.error(f"provider key save failed: {e}")
            app.notify(f"Ошибка сохранения ключа: {e}", severity="error")



def resolve_modal(screen, value) -> None:
    """
    Single idempotent modal dismissal primitive.

    Resolves ``screen.future`` with ``value`` only when the future exists and
    is not done (a cancelled future is left resolved, never re-set), then
    closes the screen via ``screen.dismiss(value)`` only when the screen is
    currently active or genuinely present in ``app.screen_stack`` — never
    removing ``screen_stack[0]``. When a screen was removed without
    resolution (its future is still pending but the screen is neither current
    nor present in the stack) the pending future is best-effort resolved with
    ``None`` so no waiter hangs; a future that is already done still allows
    ``screen.dismiss(value)`` when the screen is current/present. Never uses
    ``call_after_refresh`` and never raises (failures are logged at DEBUG).
    When invoked from a non-UI thread it re-dispatches itself through
    ``app.call_from_thread`` with a plain callable (never an un-awaited
    coroutine).
    """
    try:
        app = getattr(screen, "app", None)
        if app is None:
            return
        try:
            import threading as _threading

            if _threading.current_thread() is not _threading.main_thread():
                try:
                    app.call_from_thread(resolve_modal, screen, value)
                except Exception:
                    logger.debug("resolve_modal: call_from_thread failed", exc_info=True)
                return
        except Exception:
            pass
        future = getattr(screen, "future", None)
        try:
            stack = list(getattr(app, "screen_stack", []) or [])
            current = getattr(app, "screen", None)
            present = screen in stack
            if (
                future is not None
                and not future.done()
                and current is not screen
                and not present
            ):
                # Screen removed without resolution: best-effort resolve the
                # pending future with None so no waiter hangs.
                try:
                    future.set_result(None)
                except Exception:
                    logger.debug(
                        "resolve_modal: orphan future set_result failed",
                        exc_info=True,
                    )
            if future is not None and not future.done():
                try:
                    future.set_result(value)
                except Exception:
                    logger.debug("resolve_modal: set_result failed", exc_info=True)
            if present and stack.index(screen) == 0:
                return  # never remove screen_stack[0]
            if current is screen or present:
                try:
                    screen.dismiss(value)
                except Exception:
                    logger.debug("resolve_modal: dismiss failed", exc_info=True)
        except Exception:
            logger.debug("resolve_modal: stack inspection failed", exc_info=True)
    except Exception:
        logger.debug("resolve_modal failed", exc_info=True)



async def push_modal(app, screen_factory) -> Any:
    """
    Create a modal on the UI event loop, push it and await its future value.

    ``screen_factory`` is a zero-argument callable constructing a
    ``ModalScreen``; it is invoked on the UI event loop, where the
    ``asyncio.Future`` is created and bound to ``app._app_loop`` (fallback
    ``asyncio.get_running_loop()``) and assigned to ``screen.future`` when
    not already present. ``app.push_screen(screen)`` executes only on the UI
    loop; from a worker thread it is dispatched via ``app.call_from_thread``.
    The coroutine awaits the future and returns its value (``None`` =
    cancel). On ``asyncio.CancelledError`` or any exception the pending modal
    is resolved with ``None`` via ``resolve_modal`` before propagating, so no
    modal is left on the stack and no worker hangs.

    The cross-loop branch uses a NON-BLOCKING async bridge: an
    ``asyncio.Future`` bound to the caller's running loop is created, the
    screen is constructed and pushed on the UI loop through
    ``app.call_from_thread(...)``, and a done-callback attached to
    ``screen.future`` transfers the result/exception back to the caller via
    ``caller_loop.call_soon_threadsafe(...)``. There is no blocking
    ``concurrent.futures.Future.result(...)`` wait anywhere — the root cause
    of "very slow / dead buttons" on the UI loop is eliminated. The wait is
    FINITE, bounded by the module-level constant ``_PUSH_MODAL_PROXY_TIMEOUT``:
    on timeout the pending screen is resolved with ``None`` through the
    thread-safe ``resolve_modal`` and ``None`` is returned — no worker can
    hang indefinitely. ``asyncio.CancelledError`` is never swallowed.
    """
    screen = None
    caller_future: Optional[asyncio.Future] = None
    caller_loop = None
    try:
        try:
            ui_loop = getattr(app, "_app_loop", None)
        except Exception:
            ui_loop = None
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        def _create_and_push() -> None:
            """Construct the screen on the UI loop and push it."""
            nonlocal screen
            screen = screen_factory()
            if getattr(screen, "future", None) is None:
                loop = ui_loop
                if loop is None:
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                if loop is not None:
                    screen.future = loop.create_future()
            app.push_screen(screen)

        if running is ui_loop or ui_loop is None:
            _create_and_push()
        else:
            # Another loop/thread: non-blocking async bridge. Construct+push
            # on the UI loop via call_from_thread, then transfer the future's
            # outcome back to the caller's loop without ever blocking it.
            caller_loop = running
            caller_future = caller_loop.create_future()

            def _transfer(f) -> None:
                """Move the UI-loop future's outcome to the caller future."""

                def _apply() -> None:
                    """Set the result/exception on the caller loop."""
                    if caller_future is None or caller_future.done():
                        return
                    if f is None:
                        caller_future.set_result(None)
                        return
                    try:
                        if f.cancelled():
                            caller_future.cancel()
                        else:
                            exc = f.exception()
                            if exc is not None:
                                caller_future.set_exception(exc)
                            else:
                                caller_future.set_result(f.result())
                    except Exception:
                        logger.debug(
                            "push_modal: future transfer failed", exc_info=True
                        )
                        try:
                            caller_future.set_result(None)
                        except Exception:
                            pass

                try:
                    caller_loop.call_soon_threadsafe(_apply)
                except RuntimeError:
                    logger.debug("push_modal: caller loop closed", exc_info=True)

            def _runner() -> None:
                """Create+push on the UI loop and attach the transfer callback."""
                try:
                    _create_and_push()
                except Exception:
                    logger.debug("push_modal: create/push failed", exc_info=True)
                    if caller_future is not None and not caller_future.done():
                        try:
                            caller_loop.call_soon_threadsafe(
                                lambda: caller_future.set_result(None)
                            )
                        except RuntimeError:
                            pass
                    return
                fut = getattr(screen, "future", None)
                if fut is not None:
                    fut.add_done_callback(_transfer)
                else:
                    _transfer(None)

            app.call_from_thread(_runner)
            try:
                return await asyncio.wait_for(
                    caller_future, timeout=_PUSH_MODAL_PROXY_TIMEOUT
                )
            except asyncio.TimeoutError:
                # Finite wait: resolve the pending screen (thread-safe) and
                # return None so no worker hangs indefinitely.
                logger.debug(
                    "push_modal: async bridge wait timed out after "
                    f"{_PUSH_MODAL_PROXY_TIMEOUT}s"
                )
                if screen is not None:
                    resolve_modal(screen, None)
                return None

        fut = getattr(screen, "future", None)
        if fut is None:
            return None
        return await fut
    except asyncio.CancelledError:
        if screen is not None:
            resolve_modal(screen, None)
        raise
    except Exception as e:
        logger.debug(f"push_modal failed: {e}", exc_info=True)
        if screen is not None:
            resolve_modal(screen, None)
        return None


