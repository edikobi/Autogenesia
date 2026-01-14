# 🤖 AI Code Agent - Интеграционный Тест

**Дата выполнения:** 20.12.2025 06:12:16
**Проект:** `C:\Users\Admin\AI_Assistant_Pro`
**Время выполнения:** 212.73 сек.

---

## 📝 Запрос пользователя

> У меня возникает проблема при пользовании General Chat, а именно выходит ошибка. Проанализируй и напиши код решения  File "c:\Users\Admin\AI_Assistant_Pro\scripts\test_general_chat.py", line 128, in main    result: GeneralChatResult = await orchestrator.orchestrate_general(                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  File "c:\Users\Admin\AI_Assistant_Pro\app\agents\orchestrator.py", line 850, in orchestrate_general    prompts = format_orchestrator_prompt_general(              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^TypeError: format_orchestrator_prompt_general() got an unexpected keyword argument 'user_query'

---

## 🎯 Использованные модели

- **Orchestrator:** GPT-5.1 Codex Max (RouterAI)
- **Code Generator:** deepseek-chat

---

## 🔍 Анализ Оркестратора

ROOT CAUSE: The General Chat orchestrator calls `format_orchestrator_prompt_general` with `user_query`, `user_files`, and `remaining_web_searches`, expecting a dict with `"system"` and `"user"` prompts, but the function currently accepts only `is_legal_mode` and returns a single string. This signature mismatch causes `TypeError: unexpected keyword argument 'user_query'`.

---

## 📋 Инструкции для Code Generator

SCOPE: A (single function in one file)

**Task:** Update the General Chat prompt formatter to accept the parameters used by `orchestrate_general` and return both system and user prompts as a dict.

**File:** `app/llm/prompt_templates.py`

**Target Location:**
- Function: `format_orchestrator_prompt_general`
- Lines: 2111-2192 (per current file)
- Identifying pattern: `def format_orchestrator_prompt_general(is_legal_mode: bool = False) -> str:`

**Current Code (for reference):**
```
def format_orchestrator_prompt_general(is_legal_mode: bool = False) -> str:
    """
    Строит системный промпт для режима General Chat.
    Args:
        is_legal_mode: Если True, добавляет юридическую специфику и строгость.
    """
    prompt_parts = []
    ...
    return "\n".join(prompt_parts)
```

**Required Changes:**
1. Change the signature to accept `user_query`, optional `user_files`, `is_legal_mode`, and `remaining_web_searches` with default `MAX_WEB_SEARCH_CALLS`.
2. Return a `Dict[str, str]` with keys `"system"` and `"user"` instead of a single string.
3. Preserve the existing system prompt content, but include a line showing remaining web search calls (`remaining_web_searches` out of `MAX_WEB_SEARCH_CALLS`).
4. Build a user prompt that includes the user query and, if provided, a compact list of attached files (filename and content). Default to an empty list if `user_files` is None.
5. Update typing imports to include `List` alongside existing `Dict` if not already imported.

**New/Modified Code:**
```
from typing import Dict, List  # ensure List is included with existing typing imports

def format_orchestrator_prompt_general(
    user_query: str,
    user_files: List[Dict[str, str]] = None,
    is_legal_mode: bool = False,
    remaining_web_searches: int = MAX_WEB_SEARCH_CALLS,
) -> Dict[str, str]:
    """
    Строит системный и пользовательский промпт для режима General Chat.
    Args:
        user_query: Запрос пользователя
        user_files: Список файлов вида {"filename": ..., "content": ...}
        is_legal_mode: Если True, добавляет юридическую специфику и строгость.
        remaining_web_searches: Сколько web_search еще можно вызвать
    """
    user_files = user_files or []

    prompt_parts = []
    # --- ROLE DEFINITION ---
    if is_legal_mode:
        prompt_parts.append("Ты — профессиональный юридический консультант и аналитик высшей квалификации.")
        prompt_parts.append("Твоя специализация: законодательство РФ, международное право и судебная практика.")
        prompt_parts.append("Твоя цель: давать точные, обоснованные и юридически грамотные ответы, опираясь на факты.")
    else:
        prompt_parts.append("Ты — интеллектуальный AI-аналитик и универсальный ассистент.")
        prompt_parts.append("Твоя цель: глубоко анализировать запросы пользователя и предоставлять исчерпывающие, структурированные ответы.")
        prompt_parts.append("Ты умеешь работать с текстами, документами, строить графики и объяснять сложные концепции.")

    prompt_parts.append("")
    prompt_parts.append(f"Осталось вызовов web_search: {remaining_web_searches} из {MAX_WEB_SEARCH_CALLS}.")
    prompt_parts.append("")

    # --- AVAILABLE TOOLS & PHILOSOPHY ---
    prompt_parts.append("ДОСТУПНЫЕ ИНСТРУМЕНТЫ")
    prompt_parts.append("- general_web_search(query, time_limit, max_results): Поиск в интернете (Google/DDG).")
    prompt_parts.append("  Используй 'time_limit'='w' (неделя) или 'm' (месяц) для новостей и свежих законов.")
    prompt_parts.append("")

    # =========================================================================
    # CRITICAL FIX: Явное требование финального ответа
    # =========================================================================
    prompt_parts.append("ОБЯЗАТЕЛЬНЫЙ WORKFLOW (ВАЖНО!)")
    prompt_parts.append("Твоя работа состоит из двух этапов:")
    prompt_parts.append("")
    prompt_parts.append("ЭТАП 1: ПОИСК ИНФОРМАЦИИ (если нужно)")
    prompt_parts.append("Используй инструменты для получения актуальной информации.")
    prompt_parts.append("")
    prompt_parts.append("ЭТАП 2: ФИНАЛЬНЫЙ ОТВЕТ ПОЛЬЗОВАТЕЛЮ (ОБЯЗАТЕЛЬНО!)")
    prompt_parts.append("После получения результатов от инструментов ты ДОЛЖЕН:")
    prompt_parts.append("• Проанализировать полученную информацию")
    prompt_parts.append("• Сформулировать полный, структурированный ответ на РУССКОМ языке")
    prompt_parts.append("• Предоставить этот ответ пользователю в финальном сообщении")
    prompt_parts.append("")
    prompt_parts.append("⚠️ НЕ ОСТАНАВЛИВАЙСЯ после использования инструмента.")
    prompt_parts.append("⚠️ ВСЕГДА предоставляй итоговый ответ на основе найденной информации.")
    prompt_parts.append("")

    prompt_parts.append("ФИЛОСОФИЯ ИСПОЛЬЗОВАНИЯ ПОИСКА (TOOL USAGE STRATEGY)")
    prompt_parts.append("Ты обладаешь огромной базой знаний, но мир меняется каждую секунду.")
    prompt_parts.append("1. ПРИНЦИП НЕОБХОДИМОСТИ: Не ищи информацию, которая является общеизвестным фактом (например, 'столица Франции'). Ищи только то, что может устареть (курсы валют, законы) или чего ты не знаешь (свежие новости, специфика узкой документации).")
    prompt_parts.append("2. ПРИНЦИП ТОЧНОСТИ ЗАПРОСА: Твой запрос в поиск должен быть не вопросом пользователя, а ключевыми словами для поискового движка.")
    prompt_parts.append("   - Плохо: 'Скажи мне, какие налоги платит ИП в 2025 году?'")
    prompt_parts.append("   - Хорошо: 'налоговые ставки ИП 2025 изменения РФ'")
    if is_legal_mode:
        prompt_parts.append("3. ЮРИДИЧЕСКАЯ СПЕЦИФИКА: При поиске законов обязательно указывай номер статьи или название кодекса, если известно. Если ищешь практику — добавляй 'судебная практика' или 'пленум ВС РФ'.")
    else:
        prompt_parts.append("3. ИТЕРАТИВНОСТЬ: Если первый поиск не дал результата, переформулируй запрос. Попробуй синонимы или более широкие термины.")
    prompt_parts.append("")

    # --- OUTPUT FORMATTING ---
    prompt_parts.append("ФОРМАТ ОТВЕТА")
    prompt_parts.append("Ты должен отвечать на РУССКОМ языке (если пользователь не попросил иное).")
    prompt_parts.append("Используй Markdown для структуры:")
    prompt_parts.append("- Используй заголовки (

---

## 💻 Сгенерированный код

**Файл:** `app/llm/prompt_templates.py`

```python
"""
Prompt templates for AI Code Agent roles.

Each role has:
- SYSTEM prompt (defines behavior and output format)
- USER prompt template (with placeholders for variables)

NEW: Adaptive blocks for different model cognitive types:
- GPT-5.1 Codex Max (executor): Standard prompts, no modifications
- Claude Sonnet 4.5 / Opus 4.5 (deep_thinker): Additional guidance for concrete instructions
- DeepSeek V3.2 Reasoning (reasoner): Leverages reasoning capabilities

Prompts are in English for better model performance.
Uses prompt_parts.append pattern for clean multi-line prompts.

CENTRALIZED PROMPT STORAGE:
- Router prompts: stored in app/agents/router.py (co-located with routing logic)
- All other prompts: stored here
"""

from typing import Dict, List
from config.settings import Config
# ============================================================================
# CONSTANTS (shared across prompts)
# ============================================================================

MAX_WEB_SEARCH_CALLS = 3  # Maximum web_search calls per session


# ============================================================================
# MODEL COGNITIVE TYPES
# ============================================================================

# Exact model IDs from config/settings.py for reference:
# - Claude Opus 4.5:    "anthropic/claude-opus-4.5"
# - Claude Sonnet 4.5:  "anthropic/claude-sonnet-4.5"
# - GPT-5.1 Codex Max:  "openai/gpt-5.1-codex-max"
# - Gemini 3.0 Pro:     "google/gemini-3-pro-preview"
# - Gemini 2.0 Flash:   "google/gemini-2.0-flash-001"
# - DeepSeek Reasoner:  "deepseek-reasoner"
# - DeepSeek Chat:      "deepseek-chat"

# Mapping of EXACT model IDs to their cognitive types
# Used as fallback after fuzzy matching
MODEL_COGNITIVE_TYPES: Dict[str, str] = {
    # Deep Thinker - склонны к глубокому анализу и абстракции
    # Нуждаются в напоминании о конкретных, выполнимых инструкциях
    Config.MODEL_OPUS_4_5: "deep_thinker",      # "anthropic/claude-opus-4.5"
    Config.MODEL_SONNET_4_5: "deep_thinker",    # "anthropic/claude-sonnet-4.5"
    
    # Executor - ориентированы на выполнение задач
    # Стандартные промпты работают хорошо, дополнения не нужны
    Config.MODEL_GPT_5_1_Codex_MAX: "executor", # "openai/gpt-5.1-codex-max"
    Config.MODEL_GEMINI_3_PRO: "executor", # "google/gemini-3-pro-preview"
    
    # Reasoner - модели с цепочкой рассуждений
    # Могут понимать менее детальные инструкции, фокус на "почему"
    Config.MODEL_DEEPSEEK_REASONER: "reasoner", # "deepseek-reasoner"
}

def get_model_cognitive_type(model_id: str) -> str:
    """
    Determine the cognitive type of a model.
    
    Uses FUZZY MATCHING to handle variations in model IDs from different
    providers (RouterAI, OpenRouter, direct API). This is critical because
    the same model can have different IDs:
    - "anthropic/claude-sonnet-4.5" (OpenRouter style)
    - "claude-sonnet-4.5" (short form)
    - "Claude Sonnet 4.5 (RouterAI)" (display name - should not happen but safe)
    
    Args:
        model_id: Model identifier (e.g., "anthropic/claude-opus-4.5")
        
    Returns:
        Cognitive type: "deep_thinker", "executor", "reasoner", or "general"
    """
    if not model_id:
        return "general"
    
    # Normalize for comparison
    model_lower = model_id.lower()
    
    # === CLAUDE FAMILY → deep_thinker ===
    # Matches: claude-opus-4.5, claude-sonnet-4.5, anthropic/claude-3.5-sonnet, etc.
    if "claude" in model_lower:
        # Opus and Sonnet variants are deep thinkers
        if any(variant in model_lower for variant in ["opus", "sonnet"]):
            return "deep_thinker"
    
    # === GEMINI FAMILY ===
    if "gemini" in model_lower:
        # Gemini Pro models (3.0, 2.5, etc.) are deep thinkers
        if "pro" in model_lower or "ultra" in model_lower:
            return "deep_thinker"
        # Gemini Flash models are executors (fast, less reasoning)
        if "flash" in model_lower:
            return "executor"
    
    # === GPT FAMILY → executor ===
    # Matches: gpt-5.1-codex-max, openai/gpt-5.1-codex-max, etc.
    if "gpt" in model_lower:
        # GPT-5.x and Codex models are executors
        if "5" in model_lower or "codex" in model_lower:
            return "executor"
    
    # === DEEPSEEK FAMILY ===
    if "deepseek" in model_lower:
        # DeepSeek Reasoner (R1) is a reasoner
        if "reason" in model_lower or "r1" in model_lower:
            return "reasoner"
        # DeepSeek Chat is an executor
        if "chat" in model_lower:
            return "executor"
    
    # === FALLBACK: Exact match from dictionary ===
    if model_id in MODEL_COGNITIVE_TYPES:
        return MODEL_COGNITIVE_TYPES[model_id]
    
    # === DEFAULT ===
    return "general"

# ============================================================================
# ADAPTIVE BLOCKS FOR ORCHESTRATOR
# ============================================================================


def _build_adaptive_block_ask_deep_thinker() -> str:
    """Build adaptive block for deep_thinker models (Claude Opus/Sonnet) in ASK mode"""
    prompt_parts: List[str] = []
    
    prompt_parts.append("")
    prompt_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    prompt_parts.append("🤝 ORCHESTRATOR-WORKER COLLABORATION PROTOCOL")
    prompt_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    prompt_parts.append("")
    prompt_parts.append("<delegation_context>")
    prompt_parts.append("You are the LEAD AGENT in a multi-agent system.")
    prompt_parts.append("Your output will be consumed by a WORKER AGENT (Code Generator) that:")
    prompt_parts.append("• Operates with an isolated context window")
    prompt_parts.append("• Has no access to your analysis, tool results, or conversation history")
    prompt_parts.append("• Receives ONLY the 'Instruction for Code Generator' section")
    prompt_parts.append("")
    prompt_parts.append("Your delegation must include:")
    prompt_parts.append("")
    prompt_parts.append("1. OBJECTIVE: What should the worker achieve?")
    prompt_parts.append("   Example: 'Add input validation to the login function'")
    prompt_parts.append("")
    prompt_parts.append("2. OUTPUT FORMAT: What deliverable should the worker produce?")
    prompt_parts.append("   Example: 'Modified function with try-except block catching ValueError'")
    prompt_parts.append("")
    prompt_parts.append("3. TOOL GUIDANCE: What code patterns/imports should the worker use?")
    prompt_parts.append("   Example: 'Import: from typing import Optional; Pattern: return None on error'")
    prompt_parts.append("")
    prompt_parts.append("4. TASK BOUNDARIES: What should the worker NOT modify?")
    prompt_parts.append("   Example: 'Do not change the function signature or return type'")
    prompt_parts.append("")
    prompt_parts.append("</delegation_context>")
    prompt_parts.append("")
    prompt_parts.append("<division_of_labor>")
    prompt_parts.append("")
    prompt_parts.append("YOUR ROLE (Orchestrator):")
    prompt_parts.append("• Analyze the problem and identify root cause")
    prompt_parts.append("• Use tools to gather necessary code context")
    prompt_parts.append("• Decide WHAT needs to change and WHY")
    prompt_parts.append("")
    prompt_parts.append("WORKER'S ROLE (Code Generator):")
    prompt_parts.append("• Receive your instruction with complete context")
    prompt_parts.append("• Write/modify code based on your specification")
    prompt_parts.append("• Execute the HOW based on your WHAT/WHY")
    prompt_parts.append("")
    prompt_parts.append("HANDOFF QUALITY CHECK:")
    prompt_parts.append("Before submitting, verify your instruction contains:")
    prompt_parts.append("✓ Sufficient context (worker can understand the problem)")
    prompt_parts.append("✓ Precise location (file path + method/class + insertion point)")
    prompt_parts.append("✓ Actual code snippets (not descriptions like 'add validation')")
    prompt_parts.append("✓ All necessary imports explicitly listed")
    prompt_parts.append("")
    prompt_parts.append("</division_of_labor>")
    
# =========================================================================
# INSTRUCTION COMPLETENESS (following Anthropic delegation framework)
# =========================================================================
    prompt_parts.append("<instruction_completeness>")
    prompt_parts.append("")
    prompt_parts.append("After using tools to analyze the problem, compose a complete instruction")
    prompt_parts.append("for the Code Generator that follows this delegation framework:")
    prompt_parts.append("")
    prompt_parts.append("1. OBJECTIVE (What should be achieved):")
    prompt_parts.append("   State the goal in one clear sentence.")
    prompt_parts.append("   Template: 'Modify {component_name} to {desired_behavior}'")
    prompt_parts.append("")
    prompt_parts.append("2. OUTPUT FORMAT (What the worker should produce):")
    prompt_parts.append("   Specify the deliverable with actual code blocks.")
    prompt_parts.append("   ")
    prompt_parts.append("   Structure:")
    prompt_parts.append("   FILE: {full_file_path}")
    prompt_parts.append("   LOCATION: {where_to_apply_change}")
    prompt_parts.append("   ACTION: INSERT | REPLACE | DELETE")
    prompt_parts.append("   ")
    prompt_parts.append("   CODE:")
    prompt_parts.append("
```

---

## 📖 Пояснения к коду

")
    prompt_parts.append("   {complete_runnable_code}")
    prompt_parts.append("   ```")
    prompt_parts.append("")
    prompt_parts.append("   Include:")
    prompt_parts.append("   • All necessary imports at the top")
    prompt_parts.append("   • Complete function/method signatures with type hints")
    prompt_parts.append("   • Exact variable names and parameter lists")
    prompt_parts.append("")
    prompt_parts.append("3. TOOL GUIDANCE (How to implement):")
    prompt_parts.append("   Provide implementation context the worker needs:")
    prompt_parts.append("   • Which design patterns to follow (if project has conventions)")
    prompt_parts.append("   • What error handling strategy to use")
    prompt_parts.append("   • Any project-specific utilities to leverage")
    prompt_parts.append("")
    prompt_parts.append("4. TASK BOUNDARIES (What NOT to change):")
    prompt_parts.append("   Explicitly state constraints:")
    prompt_parts.append("   • Which parts of the code should remain untouched")
    prompt_parts.append("   • Which APIs/interfaces must stay compatible")
    prompt_parts.append("   • What scope limits apply (single file vs. multi-file)")
    prompt_parts.append("")
    prompt_parts.append("5. CONTEXT BRIEFING (Why this matters):")
    prompt_parts.append("   Explain the reasoning so the worker understands:")
    prompt_parts.append("   • ROOT CAUSE: One sentence explaining the fundamental issue")
    prompt_parts.append("   • EXPECTED BEHAVIOR: What should happen after the change")
    prompt_parts.append("   • DEPENDENCIES: Other components that might be affected")
    prompt_parts.append("")
    prompt_parts.append("</instruction_completeness>")
    prompt_parts.append("")
    prompt_parts.append("<quality_checklist>")
    prompt_parts.append("")
    prompt_parts.append("Before submitting your instruction, verify:")
    prompt_parts.append("✓ Code blocks contain implementations (not descriptions like 'add validation')")
    prompt_parts.append("✓ Location markers use patterns from the actual file you read")
    prompt_parts.append("✓ All imports are explicitly listed with full module paths")
    prompt_parts.append("✓ The worker could execute this without asking follow-up questions")
    prompt_parts.append("✓ You copied relevant existing code patterns from tool results")
    prompt_parts.append("")
    prompt_parts.append("</quality_checklist>")
        
    # =========================================================================
    # HOLISTIC FIXING (positive framing)
    # =========================================================================
    prompt_parts.append("<holistic_fixing>")
    prompt_parts.append("")
    prompt_parts.append("When you identify a bug, scan the entire file for similar patterns.")
    prompt_parts.append("Batch all related fixes into a single instruction block.")
    prompt_parts.append("Focus on critical bugs (crashes, logic errors) and skip style changes.")
    prompt_parts.append("")
    prompt_parts.append("</holistic_fixing>")
    prompt_parts.append("")
    
    # =========================================================================
    # VERIFICATION STEP
    # =========================================================================
    prompt_parts.append("<self_verification>")
    prompt_parts.append("")
    prompt_parts.append("Before submitting your instruction, verify:")
    prompt_parts.append("✓ Code blocks contain actual implementations (not pseudocode)")
    prompt_parts.append("✓ All imports are listed explicitly")
    prompt_parts.append("✓ File paths are complete and accurate")
    prompt_parts.append("✓ Location markers are precise enough to find the spot")
    prompt_parts.append("")
    prompt_parts.append("</self_verification>")
    
    return "\n".join(prompt_parts)


def _build_adaptive_block_ask_reasoner() -> str:
    """Build adaptive block for reasoner models (DeepSeek V3.2) in ASK mode"""
    prompt_parts: List[str] = []
    
    prompt_parts.append("")
    prompt_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    prompt_parts.append("🧠 REASONING-FIRST ORCHESTRATION PROTOCOL")
    prompt_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    prompt_parts.append("")
    
    # =========================================================================
    # LEVERAGE YOUR REASONING STRENGTHS
    # =========================================================================
    prompt_parts.append("<reasoning_strengths>")
    prompt_parts.append("")
    prompt_parts.append("Your reasoning model excels at:")
    prompt_parts.append("• Multi-step logical inference")
    prompt_parts.append("• Pattern identification across large codebases")
    prompt_parts.append("• Tracing consequence chains through dependencies")
    prompt_parts.append("• Comprehensive code analysis")
    prompt_parts.append("")
    prompt_parts.append("Apply these strengths to orchestration:")
    prompt_parts.append("")
    prompt_parts.append("DEPENDENCY REASONING PATTERN:")
    prompt_parts.append("When analyzing a change, reason through:")
    prompt_parts.append("1. IF we modify component X in module M,")
    prompt_parts.append("2. THEN which components import from M? (upstream impact)")
    prompt_parts.append("3. AND which components does M import? (downstream dependencies)")
    prompt_parts.append("4. THEREFORE, what is the ripple effect scope?")
    prompt_parts.append("")
    prompt_parts.append("Use this chain to predict:")
    prompt_parts.append("• Breaking changes (API modifications)")
    prompt_parts.append("• Hidden circular dependency risks")
    prompt_parts.append("• Integration points that need updates")
    prompt_parts.append("")
    prompt_parts.append("</reasoning_strengths>")
    prompt_parts.append("")
    
    # =========================================================================
    # COMPENSATE FOR SPARSE ATTENTION (FILE DETECTION)
    # =========================================================================
    prompt_parts.append("<file_detection_strategy>")
    prompt_parts.append("")
    prompt_parts.append("IMPORTANT: Your sparse attention mechanism optimizes for efficiency,")
    prompt_parts.append("but may filter out relevant files if they don't match initial patterns.")
    prompt_parts.append("")
    prompt_parts.append("MANDATORY FILE DISCOVERY PROTOCOL:")
    prompt_parts.append("")
    prompt_parts.append("1. EXPLICIT SEARCH BEFORE REASONING:")
    prompt_parts.append("   Before reasoning about the problem, actively SEARCH for files.")
    prompt_parts.append("   ")
    prompt_parts.append("   Use tools to force file visibility:")
    prompt_parts.append("   • search_code({keyword_from_user_query}) → Find ALL mentions")
    prompt_parts.append("   • read_file({config_path}) → Check configuration for related modules")
    prompt_parts.append("   • search_code({function_name}) → Locate definitions and usages")

---

## 🛠️ Выполненные вызовы инструментов

1. ✅ **read_code_chunk**
   - Аргументы: `file_path=app/llm/prompt_templates.py, chunk_name=format_orchestrator_prompt_general`

2. ✅ **read_code_chunk**
   - Аргументы: `file_path=app/agents/orchestrator.py, chunk_name=orchestrate_general`

3. ✅ **search_code**
   - Аргументы: `query=format_orchestrator_prompt_general(, search_type=function`

4. ✅ **read_code_chunk**
   - Аргументы: `file_path=app/llm/prompt_templates.py, chunk_name=format_orchestrator_prompt_ask`

5. ✅ **read_code_chunk**
   - Аргументы: `file_path=app/llm/prompt_templates.py, chunk_name=format_orchestrator_prompt`

---

*Отчет сгенерирован автоматически: 2025-12-20T06:12:16.379309*