"""Перевод с защитой пайплайна: проблемы переводчика никогда не ломают TUI.

Импорт напрямую из ``app.utils.translator``; при ImportError — локальный
fallback-набор (точная копия main.py:209–217): все функции возвращают вход,
``is_mostly_russian`` всегда True, ``check_translation_available`` — False.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

try:
    from app.utils.translator import (
        translate_to_russian,
        translate_sync,
        translate_with_timeout,
        translate_thinking,
        translate_validator_verdict,
        translate_analysis,
        is_mostly_russian,
        check_translation_available,
        reset_translation_state,
    )
except ImportError:  # pragma: no cover - optional dependency
    logger.warning("app.utils.translator unavailable — using pass-through fallbacks")

    async def translate_to_russian(text, context=""): return text
    def translate_sync(text, context=""): return text
    async def translate_with_timeout(text, context="", timeout=10.0): return text
    async def translate_thinking(text): return text
    async def translate_validator_verdict(text): return text
    async def translate_analysis(text): return text
    def is_mostly_russian(text): return True
    def check_translation_available(): return False
    def reset_translation_state(): pass


__all__ = [
    "safe_translate",
    "translate_to_russian",
    "translate_sync",
    "translate_with_timeout",
    "translate_thinking",
    "translate_validator_verdict",
    "translate_analysis",
    "is_mostly_russian",
    "check_translation_available",
    "reset_translation_state",
]


async def safe_translate(text: str, context: str = "") -> str:
    """Translate text to Russian without ever raising.

    Пустой или преимущественно русский текст возвращается как есть. Любая
    ошибка (TimeoutError, ImportError,_network и т.д.) приводит к возврату
    оригинала — перевод не критичен для пайплайна.
    """
    if not text:
        return text
    try:
        if is_mostly_russian(text):
            return text
    except Exception:
        return text
    try:
        return await asyncio.wait_for(translate_with_timeout(text, context=context), timeout=8)
    except Exception as e:
        logger.debug(f"safe_translate: fallback to original ({e})")
        return text