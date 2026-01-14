# app/agents/__init__.py
"""
AI Code Agent - Agent modules

Agents:
- Router: Classifies task complexity
- PreFilter: Selects relevant code chunks
- Orchestrator: Analyzes code and creates instructions
- CodeGenerator: Generates code from instructions
"""

from app.agents.code_generator import (
    generate_code,
    generate_code_agent_mode,  # Новый экспорт
    parse_agent_code_blocks,   # Новый экспорт
    CodeGeneratorResult,
    CodeBlock,
)
__all__ = [
    "route_request",
    "RouteResult",
    "pre_filter_chunks",
    "PreFilterResult",
    "orchestrate",
    "OrchestratorResult",
    "generate_code",
    "CodeGeneratorResult",
]