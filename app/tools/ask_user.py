"""
UI-agnostic execution core of the ``ask_user`` tool.

Provides a thread-safe callback registry (context-local + fallback), XML
result formatting identical in shape to ``ToolExecutor._format_error``, and
a CLI console handler that erases its temporary prompt block with ANSI codes.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import threading
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

_ask_user_context: contextvars.ContextVar[
    Optional[Callable[[str, List[str], str], str]]
] = contextvars.ContextVar("ask_user_callback", default=None)

_ask_user_fallback: Optional[Callable[[str, List[str], str], str]] = None
_ask_user_lock = threading.Lock()


def set_ask_user_callback(
    callback: Optional[Callable[[str, List[str], str], str]]
) -> None:
    """Set or clear the ``ask_user`` callback for the current context and global fallback.

    Safe to call repeatedly at startup and from UI threads. Passing ``None``
    clears both the context-local and fallback handlers.
    """
    global _ask_user_fallback
    with _ask_user_lock:
        _ask_user_fallback = callback
    try:
        _ask_user_context.set(callback)
    except Exception:
        pass


def get_ask_user_callback() -> Optional[Callable]:
    """Resolve the active callback: context-local first, then global fallback."""
    cb = _ask_user_context.get()
    if cb is not None:
        return cb
    with _ask_user_lock:
        return _ask_user_fallback


def _format_error(message: str) -> str:
    """Canonical error envelope — identical shape to ``ToolExecutor._format_error``."""
    return (
        "<!-- ERROR -->\n"
        "<error>\n"
        f"  <message>{message}</message>\n"
        "</error>"
    )


def _xml_escape(text: str) -> str:
    """Escape ``&``, ``<`` and ``>`` so XML text content stays valid."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def ask_user_tool(
    question: str,
    options: Optional[List[str]] = None,
    context: Optional[str] = None,
) -> str:
    """Execute the ``ask_user`` tool and format the answer as an XML string.

    This string is appended as a ``role="tool"`` message — the session is
    never terminated by this tool. When no callback is registered or the
    question is blank, the canonical error envelope is returned (never blocks,
    never raises).
    """
    if not question or not str(question).strip():
        return _format_error("question is required")

    normalized: List[str] = []
    if options:
        for item in options:
            if len(normalized) >= 5:
                break
            if item is None:
                continue
            value = item if isinstance(item, str) else str(item)
            value = value.strip()
            if value:
                normalized.append(value)

    callback = get_ask_user_callback()
    if callback is None:
        return _format_error("ask_user callback is not registered")

    try:
        answer = callback(str(question), normalized, context or "")
    except Exception as e:
        logger.warning(f"ask_user callback failed: {e}")
        return _format_error(f"ask_user failed: {e}")

    if answer is None:
        answer = ""

    return (
        "<user_clarification>\n"
        f"  <question>{_xml_escape(question)}</question>\n"
        f"  <selected_or_custom_answer>{_xml_escape(answer)}</selected_or_custom_answer>\n"
        "</user_clarification>"
    )


def cli_ask_user_handler(question: str, options: List[str], context: str) -> str:
    """CLI handler that renders a temporary prompt block and erases it via ANSI.

    Renders a leading newline, the question, optional context, numbered
    options and a Russian hint, then reads via ``input()``. A numeric answer
    in ``1..N`` maps to the corresponding option; any other text is used
    verbatim. The block is erased with repeated ``\\x1b[1A\\x1b[2K`` sequences,
    even on ``EOFError``/``KeyboardInterrupt``.
    """
    rendered_lines = 0
    try:
        width = 80
        try:
            from rich.console import Console as _Console

            _w = _Console().size.width
            if _w and _w > 0:
                width = int(_w)
        except Exception:
            pass

        def _wrap_count(text: str) -> int:
            """Estimate physical lines for one logical line given terminal width."""
            if not text:
                return 1
            try:
                import math

                return max(1, math.ceil(len(text) / max(width, 20)))
            except Exception:
                return 1

        # Render block
        sys.stdout.write("\n")
        rendered_lines += 1

        q_line = f"❓ {question}"
        sys.stdout.write(q_line + "\n")
        rendered_lines += _wrap_count(q_line)

        if context:
            sys.stdout.write("   Контекст:\n")
            rendered_lines += 1
            for cl in str(context).splitlines() or [str(context)]:
                line = "   " + cl
                sys.stdout.write(line + "\n")
                rendered_lines += _wrap_count(line)

        if options:
            for i, opt in enumerate(options, 1):
                line = f"   [{i}] {opt}"
                sys.stdout.write(line + "\n")
                rendered_lines += _wrap_count(line)
            hint = (
                f"Введите номер варианта (1-{len(options)}) "
                f"или напишите свой ответ в свободной форме"
            )
            sys.stdout.write("   " + hint + "\n")
            rendered_lines += _wrap_count("   " + hint)

        sys.stdout.flush()

        try:
            raw = input("┃ ")
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            rendered_lines += 1
            return _format_error("ask_user interrupted")

        answer = (raw or "").strip()
        if options and answer.isdigit():
            idx = int(answer)
            if 1 <= idx <= len(options):
                answer = options[idx - 1]
        return answer
    except Exception as e:
        logger.warning(f"cli_ask_user_handler failed: {e}")
        return _format_error(f"cli_ask_user_handler failed: {e}")
    finally:
        try:
            for _ in range(rendered_lines + 1):
                sys.stdout.write("\x1b[1A\x1b[2K")
            sys.stdout.flush()
        except Exception:
            pass