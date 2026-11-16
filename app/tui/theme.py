"""Палитра «светлые сине-белые тона» для Textual TUI Autogenesia."""

from __future__ import annotations

from typing import Dict

SKY = "#2196F3"
SKY_DARK = "#0D47A1"
BG = "#FFFFFF"
SURFACE = "#F0F7FF"
BORDER = "#BBDEFB"
TEXT = "#0D2B4E"
SUCCESS = "#2E7D32"
ERROR = "#C62828"
STOP = "#D32F2F"
WARNING = "#F9A825"
DIFF_BORDER = "#FFC107"

#: Переопределение дизайн-токенов Textual + собственные переменные TUI
#: (ключи без `$` — передаются в `App(CSS_VARIABLES=...)`).
CSS_VARIABLES: Dict[str, str] = {
    "primary": SKY,
    "accent": SKY_DARK,
    "background": BG,
    "surface": SURFACE,
    "panel": SURFACE,
    "border": BORDER,
    "text": TEXT,
    "text-muted": "#5B7EA6",
    "success": SUCCESS,
    "error": ERROR,
    "warning": WARNING,
    "block-cursor-background": SKY_DARK,
    # собственные переменные TUI
    "stop": STOP,
    "diff-border": DIFF_BORDER,
}


def build_css_variables() -> Dict[str, str]:
    """Return a defensive copy of the theme CSS variables."""
    return dict(CSS_VARIABLES)