"""CLI-совместимое чтение/запись data/user_settings.json.

CLI (main.py) и TUI делят один файл настроек. Формат — словарь с полями:
модели (оркестратор/генератор/тестер), провайдеры, режим pre-filter и его
модель, флаг mypy и прочие пользовательские предпочтения. Неизвестные ключи
сохраняются как есть (round-trip без потерь).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path("data") / "user_settings.json"


def load_user_settings() -> Dict[str, Any]:
    """Load user settings; empty dict when missing or corrupt (never raises)."""
    path = SETTINGS_PATH
    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"user settings file is not a dict: {path}")
            return {}
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load user settings from {path}: {e}")
        return {}


def save_user_settings(settings: Dict[str, Any]) -> None:
    """Merge ``settings`` over the on-disk file and persist it.

    Keys that the TUI did not change are preserved as-is, so the CLI's own
    preferences survive a TUI save (round-trip without data loss).
    """
    path = SETTINGS_PATH
    try:
        merged: Dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    merged.update(existing)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not read existing settings ({e}); starting fresh")
        merged.update(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except (OSError, TypeError) as e:
        logger.warning(f"Failed to save user settings to {path}: {e}")