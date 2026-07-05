"""
Translates TesterAgent markdown reports to Russian using MODEL_GEMINI_FLASH_LITE
with a 25-second timeout. Returns original text on failure.
"""
import asyncio
import logging
from typing import Optional

from app.utils.translator import is_mostly_russian

logger = logging.getLogger(__name__)


async def translate_tester_report(report_md: str) -> str:
    """
    Translate a tester report from English to Russian.

    Uses Gemini Flash Lite with a 25-second timeout.
    Returns the original report if translation fails or is unnecessary.

    Args:
        report_md: Markdown report text to translate.

    Returns:
        Translated report in Russian, or original text on failure.
    """
    if not report_md:
        return report_md

    if is_mostly_russian(report_md):
        return report_md

    try:
        result = await asyncio.wait_for(
            _translate_with_gemini_flash_lite(report_md),
            timeout=25.0,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("Tester report translation timed out after 25s, using original")
        return report_md
    except Exception as e:
        logger.warning(f"Tester report translation failed: {e}, using original")
        return report_md


async def _translate_with_gemini_flash_lite(text: str) -> str:
    """
    Translate text to Russian using Gemini Flash Lite model.

    Preserves file names, paths, function names, class names, code blocks,
    technical terms, and all markdown formatting.

    Args:
        text: Text to translate.

    Returns:
        Translated text, or original text if translation returns empty.
    """
    from config.settings import cfg
    from app.llm.api_client import call_llm

    prompt = (
        "Translate the following technical testing report to Russian.\n\n"
        "CRITICAL RULES — preserve UNCHANGED:\n"
        "- File names and file paths (e.g., app/services/auth.py)\n"
        "- Function names, class names, variable names\n"
        "- Code blocks in triple backticks (```...```)\n"
        "- Technical terms: PASS, FAIL, WARNING, ERROR, INFO, CRITICAL\n"
        "- Verdict labels: PASS, PASS WITH WARNINGS, FAIL\n"
        "- Emoji symbols (🔴, 🟡, 🔵, ✅, ❌, etc.)\n"
        "- All markdown formatting (headers #, lists -, bold **, code `)\n\n"
        "Translate ONLY the human-readable text descriptions and explanations.\n\n"
        f"--- REPORT START ---\n{text}\n--- REPORT END ---"
    )

    messages = [{"role": "user", "content": prompt}]

    result = await call_llm(
        model=cfg.MODEL_GEMINI_FLASH_LITE,
        messages=messages,
        temperature=0.2,
        max_tokens=min(len(text) * 2, 8000),
    )

    if result and result.strip():
        return result.strip()
    return text