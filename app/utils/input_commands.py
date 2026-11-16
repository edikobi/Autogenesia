"""Pure input-command normalisation and classification.

This module contains no I/O, no logging and no external dependencies. It
protects against wasted LLM tokens when a user mistypes an interactive
command (``!ок``, ``!OK``, ``!щл``, ``/плаh``, ``/gkfy``) that would
otherwise be sent to the model as an ordinary query.

TUI не использует этот модуль: его меню построены на фиксированных choices
(OptionList/tui_prompt), опечатка там невозможна.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Set

_HOMOGLYPH_RU_TO_EN: Dict[str, str] = {
    "а": "a",
    "с": "c",
    "е": "e",
    "о": "o",
    "р": "p",
    "к": "k",
    "х": "x",
    "у": "y",
    "в": "b",
    "м": "m",
    "н": "h",
    "т": "t",
}

_LAYOUT_RU_TO_EN: Dict[str, str] = {
    "й": "q",
    "ц": "w",
    "у": "e",
    "к": "r",
    "е": "t",
    "н": "y",
    "г": "u",
    "ш": "i",
    "щ": "o",
    "з": "p",
    "ф": "a",
    "ы": "s",
    "в": "d",
    "а": "f",
    "п": "g",
    "р": "h",
    "о": "j",
    "л": "k",
    "д": "l",
    "я": "z",
    "ч": "x",
    "с": "c",
    "м": "v",
    "и": "b",
    "т": "n",
    "ь": "m",
}

_LAYOUT_EN_TO_RU: Dict[str, str] = {v: k for k, v in _LAYOUT_RU_TO_EN.items()}

_ACCEPT_SET = {"!ok"}
_CANCEL_SET = {"!cancel", "!отмена"}
_EXPORT_SET = {"/план", "/plan"}


def normalize_command(text: str) -> str:
    """Return the canonical display form of a raw user command.

    Applies strip → casefold → remove ALL internal whitespace → replace
    visually indistinguishable Cyrillic homoglyphs with Latin letters.
    """
    if not text:
        return ""
    cleaned = "".join(text.strip().casefold().split())
    return "".join(_HOMOGLYPH_RU_TO_EN.get(ch, ch) for ch in cleaned)


def _candidates(text: str) -> Set[str]:
    """Return the set of three normalised interpretations of ``text``.

    Each candidate is built from the same cleaned input (strip + casefold +
    removal of internal whitespace), but with a different transformation:

    a) homoglyph form — Cyrillic letters replaced with visual Latin twins;
    b) RU→EN layout — interpret the input as typed on a Russian keyboard;
    c) EN→RU layout — interpret the input as typed on an English keyboard.

    The three simultaneous candidates resolve conflicts such as ``е``
    (homoglyph ``e`` but layout partner ``t``): ``!ок``→(a)``!ok``;
    ``!щл``→(b)``!ok``; ``/gkfy``→(c)``/план``.
    """
    if not text:
        return set()

    cleaned = "".join(text.strip().casefold().split())

    candidates: Set[str] = set()

    # (a) homoglyph form
    candidates.add("".join(_HOMOGLYPH_RU_TO_EN.get(ch, ch) for ch in cleaned))

    # (b) RU→EN layout
    candidates.add("".join(_LAYOUT_RU_TO_EN.get(ch, ch) for ch in cleaned))

    # (c) EN→RU layout
    candidates.add("".join(_LAYOUT_EN_TO_RU.get(ch, ch) for ch in cleaned))

    return candidates


def is_plan_acceptance(text: str) -> bool:
    """Return True for typo-tolerant ``!ok`` plan confirmations.

    True only when the stripped, casefolded input starts with ``!`` AND one
    of the normalisation candidates equals ``!ok``.
    """
    if not text:
        return False
    stripped = text.strip().casefold()
    if not stripped.startswith("!"):
        return False
    return bool(_candidates(text) & _ACCEPT_SET)


def is_plan_cancel(text: str) -> bool:
    """Return True for typo-tolerant plan cancellations.

    Recognises both the English ``!cancel`` and the Russian synonym
    ``!отмена`` (including its case variants ``!ОТМЕНА`` and ``!Отмена``).
    Cyrillic input passes through the homoglyph/layout candidates
    unchanged, so ``_candidates(text) & _CANCEL_SET`` matches non-empty.

    Plain text without a leading ``!`` never matches — such input is a
    legitimate feedback message for the planner.
    """
    if not text:
        return False
    stripped = text.strip().casefold()
    if not stripped.startswith("!"):
        return False
    return bool(_candidates(text) & _CANCEL_SET)



class PostTesterCommand(Enum):
    """Classification of user input in the post-Tester menu."""

    APPLY = "apply"
    REWORK = "rework"
    EXIT = "exit"
    EXPORT = "export"
    QUESTION = "question"
    UNKNOWN_COMMAND = "unknown"


def classify_post_tester_input(text: str) -> PostTesterCommand:
    """Classify post-Tester menu input without spending LLM tokens.

    Contracts:
        * first token ``1``/``2``/``3`` → APPLY/REWORK/EXIT;
        * input starting with ``/`` whose candidates include an export
          command → EXPORT;
        * a single-token input starting with ``/`` or ``!`` that was not
          recognised above is a command typo → UNKNOWN_COMMAND;
        * everything else → QUESTION.
    """
    if not text:
        return PostTesterCommand.QUESTION

    stripped = text.strip()
    if not stripped:
        return PostTesterCommand.QUESTION

    first_token = stripped.split()[0]
    if first_token in ("1", "2", "3"):
        return {
            "1": PostTesterCommand.APPLY,
            "2": PostTesterCommand.REWORK,
            "3": PostTesterCommand.EXIT,
        }[first_token]

    if stripped.startswith("/") and (_candidates(text) & _EXPORT_SET):
        return PostTesterCommand.EXPORT

    tokens = stripped.split()
    if len(tokens) == 1 and stripped[0] in ("/", "!"):
        return PostTesterCommand.UNKNOWN_COMMAND

    return PostTesterCommand.QUESTION