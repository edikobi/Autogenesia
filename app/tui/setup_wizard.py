"""Central TUI setup wizard mirroring ``main.py::setup_mode_session()`` UX.

Uses existing Textual dialogs and adapters only. Never imports ``main.py``,
never creates/closes workspace tabs, and never mutates widgets directly — the
final application is delegated to ``app._apply_setup_result(...)`` and
``WorkspacePane.rebind_project(...)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .adapters import HistoryAdapter, StateAdapter
from .dialogs import (
    NEW_THREAD,
    GeneratorModelSelectDialog,
    ModelSelectDialog,
    collect_recent_projects,
    provider_for_model,
    push_modal,
    select_project,
    select_thread,
    tui_confirm,
    tui_prompt,
)
from .settings_io import load_user_settings, save_user_settings

logger = logging.getLogger(__name__)

# --- Stage names ---
PROJECT_TYPE = "PROJECT_TYPE"
PROJECT = "PROJECT"
ORCHESTRATOR_MODEL = "ORCHESTRATOR_MODEL"
GENERATOR_MODEL = "GENERATOR_MODEL"
SUBMODE = "SUBMODE"
MODEL = "MODEL"
THREAD = "THREAD"
DONE = "DONE"

_BACK = "__back__"
_CANCEL = None


@dataclass
class SetupResult:
    """Result of a successful setup wizard run."""

    mode: str = "agent"
    project_dir: Optional[str] = None
    is_new_project: bool = False
    project_index: Dict[str, Any] = field(default_factory=dict)
    orchestrator_model: Optional[str] = None
    orchestrator_provider: Optional[str] = None
    generator_model: Optional[str] = None
    generator_provider: Optional[str] = None
    use_router: bool = True
    is_legal_mode: bool = False
    thread_id: Optional[str] = None
    current_thread: Optional[Any] = None
    setup_complete: bool = True
    setup_stage: str = "DONE"


def _load_project_index(project_dir: Optional[str]) -> Dict[str, Any]:
    """Load ``<project_dir>/.ai-agent/semantic_index.json`` (empty on absent/invalid)."""
    if not project_dir:
        return {}
    try:
        path = Path(project_dir) / ".ai-agent" / "semantic_index.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug(f"_load_project_index failed: {exc}")
    return {}


def _save_settings(state: StateAdapter) -> None:
    """Persist merge-on-save settings for the given state (idempotent)."""
    try:
        settings = load_user_settings()
        settings.update(
            {
                "mode": state.mode,
                "use_router": state.use_router,
                "fixed_orchestrator_model": state.orchestrator_model,
                "orchestrator_provider": state.orchestrator_provider,
                "generator_model": state.generator_model,
                "generator_provider": state.generator_provider,
                "is_legal_mode": state.is_legal_mode,
                "is_new_project": state.is_new_project,
                "setup_complete": bool(getattr(state, "setup_complete", True)),
                "setup_stage": str(getattr(state, "setup_stage", "DONE")),
            }
        )
        save_user_settings(settings)
    except Exception as exc:
        logger.debug(f"_save_settings failed: {exc}")


def _normalize_model_result(result: Any) -> Optional[Dict[str, Any]]:
    """Normalize a ModelSelectDialog result into a dict for the state."""
    if result is None:
        return None
    if isinstance(result, tuple):
        if len(result) >= 3:
            kind, model_id, provider = result[0], result[1], result[2]
        elif len(result) == 2:
            kind, model_id = result[0], result[1]
            provider = None
        else:
            kind, model_id, provider = result[0], None, None
    else:
        kind, model_id, provider = str(result), None, None
    if kind == "auto":
        return {"use_router": True, "model": None, "provider": None}
    if provider is None and model_id:
        provider = provider_for_model(model_id)
    return {"use_router": False, "model": model_id, "provider": provider}


async def _resolve_thread(app, state: StateAdapter, history_adapter: Optional[HistoryAdapter]) -> Optional[str]:
    """Resolve a conversation thread (exist or new) via HistoryAdapter; None on cancel."""
    if history_adapter is None or state.project_dir is None and state.mode != "general":
        # still allow general chat / no-project flows
        pass
    try:
        if history_adapter is None:
            mgr = app._ensure_history_manager() if hasattr(app, "_ensure_history_manager") else None
            if mgr is not None:
                history_adapter = HistoryAdapter(mgr)
    except Exception:
        history_adapter = None
    if history_adapter is None:
        return None
    try:
        result = await select_thread(app, history_adapter)
    except Exception as exc:
        logger.debug(f"_resolve_thread failed: {exc}")
        return None
    if result is None:
        return None
    if result == NEW_THREAD:
        try:
            thread = await history_adapter.new_conversation(state.project_dir)
            return getattr(thread, "id", None)
        except Exception:
            return None
    return result


async def run_setup_wizard(
    app,
    state: Optional[StateAdapter] = None,
    mode: Optional[str] = None,
    start_stage: Optional[str] = None,
    allow_resume: bool = False,
) -> Optional[SetupResult]:
    """Run the setup wizard FSM and return a SetupResult (or None on cancel).

    FSM for ask/agent: PROJECT_TYPE → PROJECT → ORCHESTRATOR_MODEL →
    GENERATOR_MODEL → THREAD → DONE. FSM for general: SUBMODE → MODEL → THREAD → DONE.
    Back navigation returns to the previous stage without resetting the project.
    Cancel from any dialog returns None and mutates nothing outside already-confirmed
    stage values.
    """
    state = state or StateAdapter()
    wizard_mode = str(mode or state.mode or "agent")

    # Local mutable copy of chosen values.
    values: Dict[str, Any] = {
        "mode": wizard_mode,
        "project_dir": state.project_dir,
        "is_new_project": state.is_new_project,
        "use_router": state.use_router,
        "orchestrator_model": state.orchestrator_model,
        "orchestrator_provider": state.orchestrator_provider,
        "generator_model": state.generator_model,
        "generator_provider": state.generator_provider,
        "is_legal_mode": state.is_legal_mode,
        "thread_id": state.thread_id,
    }

    current = start_stage or (SUBMODE if wizard_mode == "general" else PROJECT_TYPE)

    try:
        history_adapter: Optional[HistoryAdapter] = None
        try:
            mgr = app._ensure_history_manager() if hasattr(app, "_ensure_history_manager") else None
            if mgr is not None:
                history_adapter = HistoryAdapter(mgr)
        except Exception:
            history_adapter = None

        while True:
            if current == PROJECT_TYPE:
                choices = [
                    "1 — 📂 Существующий проект",
                    "2 — 🆕 Новый проект",
                ]
                answer = await tui_prompt(
                    app, "Выберите тип проекта:", choices=choices, title="Тип проекта"
                )
                if answer is None:
                    return None
                if answer.startswith("1"):
                    values["is_new_project"] = False
                else:
                    values["is_new_project"] = True
                current = PROJECT
                continue

            if current == PROJECT:
                recent = []
                try:
                    recent = await collect_recent_projects(history_adapter and history_adapter.history_manager or None)
                except Exception:
                    recent = []
                try:
                    result = await select_project(app, recent_projects=recent)
                except Exception:
                    result = None
                if result is None:
                    return None
                project_path, is_new = result
                values["project_dir"] = project_path
                values["is_new_project"] = bool(is_new)
                if is_new:
                    try:
                        os.makedirs(project_path, exist_ok=True)
                    except OSError as exc:
                        app.notify(f"Не удалось создать директорию: {exc}", severity="error")
                        continue
                if wizard_mode == "general":
                    current = SUBMODE
                else:
                    current = ORCHESTRATOR_MODEL
                continue

            if current == ORCHESTRATOR_MODEL:
                try:
                    answer = await push_modal(
                        app, lambda: ModelSelectDialog(current_model=values.get("orchestrator_model"))
                    )
                except Exception:
                    answer = None
                if answer is None:
                    return None
                norm = _normalize_model_result(answer)
                if norm is None:
                    return None
                values["use_router"] = norm["use_router"]
                values["orchestrator_model"] = norm["model"]
                values["orchestrator_provider"] = norm["provider"]
                current = GENERATOR_MODEL
                continue

            if current == GENERATOR_MODEL:
                try:
                    answer = await push_modal(
                        app,
                        lambda: GeneratorModelSelectDialog(current_model=values.get("generator_model")),
                    )
                except Exception:
                    answer = None
                if answer is None:
                    return None
                norm = _normalize_model_result(answer)
                if norm is None:
                    return None
                if norm["use_router"]:
                    values["generator_model"] = None
                    values["generator_provider"] = None
                else:
                    values["generator_model"] = norm["model"]
                    values["generator_provider"] = norm["provider"] or provider_for_model(norm["model"], "generator")
                current = THREAD
                continue

            if current == SUBMODE:
                choices = [
                    "1 — 💬 Обычный режим",
                    "2 — ⚖️ Legal режим",
                ]
                answer = await tui_prompt(
                    app, "Выбор подрежима:", choices=choices, title="Общий Чат"
                )
                if answer is None:
                    return None
                values["is_legal_mode"] = answer.startswith("2")
                current = MODEL
                continue

            if current == MODEL:
                try:
                    answer = await push_modal(
                        app,
                        lambda: ModelSelectDialog(current_model=values.get("orchestrator_model")),
                    )
                except Exception:
                    answer = None
                if answer is None:
                    return None
                norm = _normalize_model_result(answer)
                if norm is None:
                    return None
                values["use_router"] = norm["use_router"]
                values["orchestrator_model"] = norm["model"]
                values["orchestrator_provider"] = norm["provider"]
                current = THREAD
                continue

            if current == THREAD:
                if history_adapter is None:
                    thread_id = None
                else:
                    thread_id = await _resolve_thread(app, state, history_adapter)
                values["thread_id"] = thread_id
                current = DONE
                continue

            if current == DONE:
                project_index = _load_project_index(values.get("project_dir"))
                # Apply markers onto the state (progress only; final application
                # is delegated to app._apply_setup_result).
                try:
                    state.mode = values.get("mode") or wizard_mode
                    state.project_dir = values.get("project_dir")
                    state.project_index = project_index
                    state.is_new_project = bool(values.get("is_new_project"))
                    state.orchestrator_model = values.get("orchestrator_model")
                    state.orchestrator_provider = values.get("orchestrator_provider")
                    state.generator_model = values.get("generator_model")
                    state.generator_provider = values.get("generator_provider")
                    state.use_router = bool(values.get("use_router", True))
                    state.is_legal_mode = bool(values.get("is_legal_mode"))
                    state.thread_id = values.get("thread_id")
                    state.setup_stage = "DONE"
                    state.setup_complete = True
                except Exception:
                    pass
                _save_settings(state)

                current_thread = None
                if history_adapter is not None and values.get("thread_id"):
                    try:
                        current_thread = await history_adapter.get_thread(values["thread_id"])
                    except Exception:
                        current_thread = None

                return SetupResult(
                    mode=values.get("mode") or wizard_mode,
                    project_dir=values.get("project_dir"),
                    is_new_project=bool(values.get("is_new_project")),
                    project_index=project_index,
                    orchestrator_model=values.get("orchestrator_model"),
                    orchestrator_provider=values.get("orchestrator_provider"),
                    generator_model=values.get("generator_model"),
                    generator_provider=values.get("generator_provider"),
                    use_router=bool(values.get("use_router", True)),
                    is_legal_mode=bool(values.get("is_legal_mode")),
                    thread_id=values.get("thread_id"),
                    current_thread=current_thread,
                    setup_complete=True,
                    setup_stage="DONE",
                )

            # Unknown stage → treat as cancel
            return None
    except asyncio.CancelledError:
        return None
    except Exception as exc:
        logger.error(f"run_setup_wizard failed: {exc}", exc_info=True)
        return None