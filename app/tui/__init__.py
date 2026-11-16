"""Autogenesia TUI package.

Публичные символы пакета TUI. Импорты ленивые (PEP 562), чтобы избежать
циклических зависимостей между подмодулями и тяжёлых импортов при старте.
"""

from __future__ import annotations

import importlib

__all__ = [
    "AutogenesiaTUIApp",
    "WorkspacePane",
    "PipelineBridge",
    "WorkspacePipeline",
    "HistoryAdapter",
    "PipelineAdapter",
    "StateAdapter",
    "safe_translate",
]

_LAZY_EXPORTS = {
    "AutogenesiaTUIApp": ("app.tui.app", "AutogenesiaTUIApp"),
    "WorkspacePane": ("app.tui.workspace", "WorkspacePane"),
    "PipelineBridge": ("app.tui.pipeline_bridge", "WorkspacePipeline"),
    "WorkspacePipeline": ("app.tui.pipeline_bridge", "WorkspacePipeline"),
    "HistoryAdapter": ("app.tui.adapters", "HistoryAdapter"),
    "PipelineAdapter": ("app.tui.adapters", "PipelineAdapter"),
    "StateAdapter": ("app.tui.adapters", "StateAdapter"),
    "safe_translate": ("app.tui.translation", "safe_translate"),
}


def __getattr__(name: str):
    """Lazily import and return a public symbol (PEP 562)."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target[0])
    return getattr(module, target[1])