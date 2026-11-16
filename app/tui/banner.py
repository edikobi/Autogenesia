"""Брендинг TUI: ASCII-баннер Autogenesia + арт «человек на шезлонге и робот».

Fallback-контракт: при отсутствии pyfiglet (ImportError) или ширине терминала
меньше 60 символов возвращается упрощённая строка "Autogenesia" — приложение
никогда не падает из-за баннера.
"""

from __future__ import annotations

import logging
import shutil

from rich.text import Text

from .theme import SKY

logger = logging.getLogger(__name__)

try:
    import pyfiglet
except ImportError:  # pragma: no cover - optional dependency
    pyfiglet = None

_MIN_WIDTH = 60
_GAP = "   "

# Рукописный ASCII-арт: слева человек отдыхает на шезлонге (зонт, солнце),
# справа робот работает с шестерёнками. Только чистые ASCII-символы.
_MASCOT_ART = [
    r"      z Z",
    r"     /                              .---------.",
    r"    z     _____            .--.    |  ROBOT   |",
    r"   ____  /     \   zZz    ( oo )   |  WORKS   |",
    r"  /    \| rest  | |    |___|__|__  |  HARD!   |",
    r" | (sun)|______ |_| () |        |  `---+-+-'",
    r" |______/______/   \___/________|______| |____",
    r" /___________________________________________/",
]


def render_banner() -> str:
    """Return the multi-line Autogenesia banner (figlet art + mascot).

    Returns a simplified ``"Autogenesia"`` string when pyfiglet is missing or
    the terminal is too narrow — this function never raises.
    """
    try:
        if pyfiglet is None:
            raise ImportError("pyfiglet is not installed")
        try:
            columns = shutil.get_terminal_size(fallback=(100, 24)).columns
        except Exception:
            columns = 100
        if columns < _MIN_WIDTH:
            return "Autogenesia"

        figlet_lines = pyfiglet.figlet_format("Autogenesia", font="slant").splitlines()
        art_lines = list(_MASCOT_ART)
        width = max((len(line) for line in figlet_lines), default=0)
        height = max(len(figlet_lines), len(art_lines))

        merged = []
        for i in range(height):
            left = figlet_lines[i] if i < len(figlet_lines) else ""
            right = art_lines[i] if i < len(art_lines) else ""
            merged.append(left.ljust(width) + _GAP + right)

        banner = "\n".join(merged)
        caption = "Автономная генерация и анализ кода"
        banner += "\n" + caption
        return banner
    except Exception:
        # ImportError from pyfiglet or any unexpected rendering problem.
        return "Autogenesia"


def render_banner_text() -> Text:
    """Wrap :func:`render_banner` in a rich Text styled with the theme color."""
    return Text(render_banner(), style=f"bold {SKY}")