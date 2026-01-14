# app/advice/__init__.py
"""
Advice System for Orchestrator Agent Mode.

Provides methodological thinking frameworks that can be loaded on demand.
Advices teach HOW TO THINK about problem classes, not WHAT to do.

Usage:
    from app.advice import get_catalog_for_prompt, execute_get_advice
    
    # Get catalog for system prompt
    catalog = get_catalog_for_prompt(mode="ask")  # or "new_project"
    
    # Execute tool (called by ToolExecutor)
    result = execute_get_advice("ADV-G01,ADV-E01")
"""

from app.advice.advice_loader import (
    AdviceSystem,
    AdviceSummary,
    AdviceCategory,
    init_advice_system,
    get_advice_system,
    get_catalog_for_prompt,
    execute_get_advice,
)

__all__ = [
    "AdviceSystem",
    "AdviceSummary",
    "AdviceCategory",
    "init_advice_system",
    "get_advice_system",
    "get_catalog_for_prompt",
    "execute_get_advice",
]