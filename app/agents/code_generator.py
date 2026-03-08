# app/agents/code_generator.py
"""
Code Generator Agent - Generates code based on Orchestrator's instructions.

According to the plan (section 5.4):
1. Receives instruction from Orchestrator (what to do, where, how)
2. Optionally receives existing file code (for context/modification)
3. Generates code with correct indentation and context
4. Returns code and explanation SEPARATELY (for frontend display)

Output format is structured for easy parsing:
- Code section: contains code blocks with filepath comments
- Explanation section: contains human-readable explanation

This separation allows frontend to display code with syntax highlighting
and explanation in a separate panel.
"""

from __future__ import annotations

import logging
import re
from app.services.file_modifier import ParsedCodeBlock
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from config.settings import cfg
from app.llm.api_client import call_llm, get_model_for_role
from app.llm.prompt_templates import format_code_generator_prompt

# После существующих импортов добавить:
import asyncio

# Константы для retry (после других констант)
CODE_GENERATOR_MAX_RETRIES = 3
CODE_GENERATOR_RETRY_DELAY = 5.0  # секунд

# После констант добавить:

def _is_network_error(error: Exception) -> bool:
    """
    Проверяет, является ли ошибка сетевой (можно повторить).
    
    Сетевые ошибки:
    - DNS resolution failed (gaierror)
    - Connection refused/reset
    - Timeout
    - SSL errors
    """
    error_str = str(error).lower()
    
    network_patterns = [
        "getaddrinfo failed",  # DNS ошибка
        "connection refused",
        "connection reset",
        "connect_tcp",
        "timeout",
        "timed out",
        "ssl",
        "network",
        "econnreset",
        "econnrefused",
        "name resolution",
        "temporary failure",
        "unreachable",
        "no route to host",
    ]
    
    for pattern in network_patterns:
        if pattern in error_str:
            return True
    
    # Проверяем тип исключения
    error_type = type(error).__name__.lower()
    if any(t in error_type for t in ["connect", "timeout", "network", "socket", "ssl", "gaierror"]):
        return True
    
    return False

logger = logging.getLogger(__name__)


# ============================================================================
# ASK MODE: AUTOFIX FUNCTIONS
# ============================================================================

def _autofix_response_ask(response: str) -> Tuple[str, List[str]]:
    """
    Автоматически исправляет типичные проблемы в ответе (ASK mode only).
    
    Returns:
        (fixed_response, list_of_issues_fixed)
    """
    issues = []
    fixed = response
    
    # Fix 1: Конвертация ``` в маркеры
    if '# === FILE:' not in fixed and '```' in fixed:
        converted, conv_issues = _convert_markdown_to_markers_ask(fixed)
        if conv_issues:
            fixed = converted
            issues.extend(conv_issues)
    
    # Fix 2: Незакрытый END FILE
    file_opens = len(re.findall(r'^# === FILE:', fixed, re.MULTILINE))
    file_closes = len(re.findall(r'^# === END FILE ===', fixed, re.MULTILINE))
    
    if file_opens > file_closes:
        fixed = fixed.rstrip() + "\n# === END FILE ===\n"
        issues.append("Added missing # === END FILE === marker")
    
    # Fix 3: Незакрытый EXPLANATION
    if '# === EXPLANATION ===' in fixed and '# === END EXPLANATION ===' not in fixed:
        fixed = fixed.rstrip() + "\n# === END EXPLANATION ===\n"
        issues.append("Added missing # === END EXPLANATION === marker")
    
    # Fix 4: LANG маркер
    file_pattern = r'(# === FILE: [^\n]+ ===\n)(?!# === LANG:)'
    
    def add_lang(match):
        issues.append("Added missing # === LANG: python === marker")
        return match.group(1) + "# === LANG: python ===\n"
    
    fixed = re.sub(file_pattern, add_lang, fixed)
    
    # Fix 5: ACTION маркер
    if '# === FILE:' in fixed and '# === ACTION:' not in fixed:
        fixed, action_issues = _infer_and_add_action_ask(fixed)
        issues.extend(action_issues)
    
    return fixed, issues


def _infer_and_add_action_ask(response: str) -> Tuple[str, List[str]]:
    """Пытается определить ACTION из контекста (ASK mode only)."""
    issues = []
    
    file_pattern = r'(# === FILE: ([^\n]+) ===\n# === LANG: \w+ ===\n(?:# === CONTEXT: [^\n]+ ===\n)?)(?!# === ACTION:)'
    
    def add_action(match):
        header = match.group(1)
        filepath = match.group(2).strip()
        
        if '# === CONTEXT:' in header:
            action = "REPLACE_METHOD"
        elif filepath.startswith("CREATE:") or filepath == "unknown_file.py":
            action = "NEW_FILE"
        else:
            action = "REPLACE_FILE"
        
        issues.append(f"Inferred ACTION: {action} for {filepath}")
        return header + f"# === ACTION: {action} ===\n"
    
    fixed = re.sub(file_pattern, add_action, response)
    return fixed, issues


def _convert_markdown_to_markers_ask(response: str) -> Tuple[str, List[str]]:
    """Конвертирует markdown ``` в маркеры (ASK mode fallback)."""
    issues = []
    result_parts = []
    
    fence_pattern = r'```(\w*)\n(.*?)```'
    last_end = 0
    
    for match in re.finditer(fence_pattern, response, re.DOTALL):
        result_parts.append(response[last_end:match.start()])
        
        language = match.group(1) or "python"
        code_content = match.group(2)
        
        filepath = "unknown_file.py"
        filepath_match = re.search(r'^#\s*filepath:\s*(.+?)$', code_content, re.MULTILINE)
        if filepath_match:
            filepath = filepath_match.group(1).strip()
            code_content = re.sub(r'^#\s*filepath:.*$\n?', '', code_content, flags=re.MULTILINE)
        
        context = None
        context_match = re.search(r'^#\s*context:\s*(.+?)$', code_content, re.MULTILINE)
        if context_match:
            context = context_match.group(1).strip()
            code_content = re.sub(r'^#\s*context:.*$\n?', '', code_content, flags=re.MULTILINE)
        
        if context:
            action = "REPLACE_METHOD"
        elif filepath == "unknown_file.py":
            action = "NEW_FILE"
        else:
            action = "REPLACE_FILE"
        
        marker_block = f"\n# === FILE: {filepath} ===\n"
        marker_block += f"# === LANG: {language} ===\n"
        if context:
            marker_block += f"# === CONTEXT: {context} ===\n"
        marker_block += f"# === ACTION: {action} ===\n"
        marker_block += f"\n{code_content.strip()}\n\n"
        marker_block += "# === END FILE ===\n"
        
        result_parts.append(marker_block)
        last_end = match.end()
        issues.append(f"Converted ``` to markers (file: {filepath})")
    
    result_parts.append(response[last_end:])
    return ''.join(result_parts), issues

# ============================================================================
# ASK MODE: VALIDATION FUNCTIONS
# ============================================================================

def _validate_parsed_blocks_ask(
    blocks: List[CodeBlock],
    instruction: str
) -> ValidationResult:
    """Проверяет результат парсинга (ASK mode only)."""
    if not blocks:
        return ValidationResult(
            is_ok=False,
            error="No code blocks found. Expected # === FILE: ... === markers."
        )
    
    empty_blocks = [b for b in blocks if not b.code.strip()]
    if empty_blocks:
        empty_files = [b.filepath or "unknown" for b in empty_blocks]
        return ValidationResult(
            is_ok=False,
            error=f"Empty code in: {', '.join(empty_files)}"
        )
    
    for block in blocks:
        if _looks_truncated_ask(block.code):
            return ValidationResult(
                is_ok=False,
                error=f"Code in {block.filepath or 'block'} appears truncated"
            )
    
    blocks_without_action = [b for b in blocks if not b.action]
    if blocks_without_action:
        return ValidationResult(
            is_ok=True,
            warning=f"{len(blocks_without_action)} block(s) without ACTION marker"
        )
    
    return ValidationResult(is_ok=True)


def _looks_truncated_ask(code: str) -> bool:
    """Эвристика: код обрезан? (ASK mode)"""
    lines = code.strip().split('\n')
    if not lines:
        return True
    
    last_line = lines[-1].strip()
    
    truncation_signs = [
        last_line.endswith(',') and not last_line.endswith("',") and not last_line.endswith('",'),
        last_line.endswith('(') and '(' not in last_line[:-1],
        last_line.endswith('[') and '[' not in last_line[:-1],
        last_line.endswith('{') and '{' not in last_line[:-1],
        bool(re.match(r'^\s*(def|class|if|for|while|try|with|async\s+def)\s+\S+.*:$', last_line) 
             and len(lines) == 1),
    ]
    
    return any(truncation_signs)


def _build_retry_prompt_ask(
    original_instruction: str,
    error: str,
    raw_response: str
) -> str:
    """Формирует retry prompt (ASK mode only)."""
    return f"""
{original_instruction}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ YOUR PREVIOUS RESPONSE WAS REJECTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Reason:** {error}

**Your previous response (first 500 chars):**
{raw_response[:500]}{'...' if len(raw_response) > 500 else ''}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 REQUIRED FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# === FILE: path/to/file.ext ===
# === LANG: python ===
# === ACTION: REPLACE_METHOD ===
# === TARGET: def method_name ===

<your complete code here>

# === END FILE ===

DO NOT USE ``` markdown fences!
"""


# ============================================================================
# ASK MODE: MARKER FORMAT PARSER
# ============================================================================

def parse_marker_format_ask(
    response: str,
    default_filepath: Optional[str] = None
) -> Tuple[List[CodeBlock], str]:
    """
    Парсит ответ в формате маркеров # === FILE: ... === (ASK mode only).
    """
    blocks: List[CodeBlock] = []
    explanation = ""
    
    file_pattern = r'# === FILE:\s*(.+?)\s*===\s*\n(.*?)# === END FILE ==='
    
    for match in re.finditer(file_pattern, response, re.DOTALL):
        filepath = match.group(1).strip()
        block_content = match.group(2)
        
        lang_match = re.search(r'# === LANG:\s*(\w+)\s*===', block_content)
        language = lang_match.group(1) if lang_match else "python"
        
        context_match = re.search(r'# === CONTEXT:\s*(.+?)\s*===', block_content)
        context = context_match.group(1).strip() if context_match else None
        
        action_match = re.search(r'# === ACTION:\s*(.+?)\s*===', block_content)
        action = action_match.group(1).strip() if action_match else None
        
        target_match = re.search(r'# === TARGET:\s*(.+?)\s*===', block_content)
        target = target_match.group(1).strip() if target_match else None
        
        code = block_content
        code = re.sub(r'# === LANG:.*===\s*\n?', '', code)
        code = re.sub(r'# === CONTEXT:.*===\s*\n?', '', code)
        code = re.sub(r'# === ACTION:.*===\s*\n?', '', code)
        code = re.sub(r'# === TARGET:.*===\s*\n?', '', code)
        code = code.strip()
        
        if code:
            blocks.append(CodeBlock(
                code=code,
                filepath=filepath,
                language=language,
                context=context,
                action=action,
                target=target,
                start_marker=f"# === FILE: {filepath} ===",
            ))
    
    explanation_match = re.search(
        r'# === EXPLANATION ===\s*\n(.*?)(?:# === END EXPLANATION ===|$)',
        response,
        re.DOTALL
    )
    if explanation_match:
        explanation = explanation_match.group(1).strip()
    
    return blocks, explanation


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class CodeBlock:
    """
    Represents a single code block with placement metadata.
    
    Used by ASK mode. Agent mode uses ParsedCodeBlock from file_modifier.
    """
    code: str
    filepath: Optional[str] = None
    language: str = "python"
    context: Optional[str] = None
    action: Optional[str] = None  # NEW: только для ASK mode
    target: Optional[str] = None  # NEW: только для ASK mode
    start_marker: str = "```python"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "code": self.code,
            "filepath": self.filepath,
            "language": self.language,
            "context": self.context,
            "action": self.action,
            "target": self.target,
        }
    
    def get_placement_hint(self) -> str:
        """
        Возвращает человекочитаемую подсказку о размещении кода.
        
        Используется только в ASK режиме для отображения пользователю.
        """
        if not self.action:
            return ""
        
        action = self.action.upper()
        target = self.target or ""
        context = self.context or ""
        
        action_hints = {
            "NEW_FILE": "📄 Создать новый файл",
            "REPLACE_FILE": "📄 Заменить содержимое файла полностью",
            "REPLACE_CLASS": "🔄 Заменить класс",
            "REPLACE_METHOD": "🔄 Заменить метод",
            "REPLACE_FUNCTION": "🔄 Заменить функцию",
            "INSERT_AFTER": "➕ Вставить ПОСЛЕ",
            "INSERT_BEFORE": "➕ Вставить ПЕРЕД",
            "INSERT_AT_END": "➕ Вставить в конец",
            "ADD_IMPORT": "📥 Добавить в секцию импортов",
        }
        
        hint = action_hints.get(action, f"📝 {action}")
        
        if target and action not in ("NEW_FILE", "REPLACE_FILE", "ADD_IMPORT"):
            hint += f" `{target}`"
        
        if context and action in ("REPLACE_METHOD", "INSERT_AFTER", "INSERT_BEFORE", "INSERT_AT_END"):
            hint += f" в классе `{context}`"
        
        line_match = re.search(r'\(line[s]?\s*(\d+(?:-\d+)?)\)', target)
        if line_match:
            hint += f"\n   📍 Строка: {line_match.group(1)}"
        
        return hint 


@dataclass
class ValidationResult:
    """Result of ASK mode response validation."""
    is_ok: bool
    error: Optional[str] = None
    warning: Optional[str] = None
    autofix_applied: List[str] = field(default_factory=list)


@dataclass
class CodeGeneratorResult:
    """
    Result from Code Generator.
    
    Designed for frontend consumption:
    - code_blocks: List of code blocks (can display each separately)
    - explanation: Plain text explanation (separate from code)
    - raw_response: Original LLM response (for debugging)
    
    Frontend can:
    - Display code blocks with syntax highlighting
    - Show filepath as header/tab for each block
    - Display explanation in separate panel
    - Use combined_code for copy-all functionality
    """
    code_blocks: List[CodeBlock] = field(default_factory=list)
    explanation: str = ""
    raw_response: str = ""
    success: bool = True
    error: Optional[str] = None
    model_used: str = ""
    tokens_used: int = 0
    
    @property
    def combined_code(self) -> str:
        """
        Get all code blocks combined into single string.
        Useful for copy-to-clipboard functionality.
        """
        if not self.code_blocks:
            return ""
        
        parts = []
        for block in self.code_blocks:
            header = []
            if block.filepath:
                header.append(f"# filepath: {block.filepath}")
            if block.context:
                header.append(f"# context: {block.context}")
            
            if header:
                parts.append("\n".join(header) + "\n\n" + block.code)
            else:
                parts.append(block.code)
        
        return "\n\n# " + "=" * 50 + "\n\n".join(parts)
    
    @property
    def primary_code(self) -> str:
        """Get code from first (primary) code block"""
        if self.code_blocks:
            return self.code_blocks[0].code
        return ""
    
    @property
    def primary_filepath(self) -> Optional[str]:
        """Get filepath from first code block"""
        if self.code_blocks:
            return self.code_blocks[0].filepath
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for JSON/API response.
        
        This is the format that frontend will receive.
        """
        return {
            "success": self.success,
            "code_blocks": [block.to_dict() for block in self.code_blocks],
            "combined_code": self.combined_code,
            "explanation": self.explanation,
            "error": self.error,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
        }


    def format_for_user(self) -> str:
        """
        Форматирует результат для отображения пользователю (ASK mode).
        
        Включает подсказки о размещении кода.
        """
        if not self.success:
            return f"❌ **Ошибка:** {self.error}"
        
        parts = []
        
        for i, block in enumerate(self.code_blocks, 1):
            if len(self.code_blocks) > 1:
                parts.append(f"### Блок {i}")
            
            if block.filepath:
                parts.append(f"📁 **Файл:** `{block.filepath}`")
            
            hint = block.get_placement_hint()
            if hint:
                parts.append(f"📍 **Действие:** {hint}")
            
            parts.append("")
            parts.append(f"```{block.language}")
            parts.append(block.code)
            parts.append("```")
            parts.append("")
        
        if self.explanation:
            parts.append("---")
            parts.append("### Объяснение")
            parts.append("")
            parts.append(self.explanation)
        
        return "\n".join(parts)

# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def generate_code(
    instruction: str,
    file_code: Optional[str] = None,
    target_file: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 34000,
) -> CodeGeneratorResult:
    """
    Generate code for ASK mode with validation and retry.
    
    This is the ASK mode entry point. Agent mode uses generate_code_agent_mode().
    """
    VALIDATION_RETRIES = 2
    
    if model is None:
        model = get_model_for_role("code_generator")
    
    logger.info(f"Code Generator (ASK): using {cfg.get_model_display_name(model)}")
    
    # =========================================================================
    # FIX: Adjust max_tokens for DeepSeek Chat (API limit 8192)
    # =========================================================================
    if model == "deepseek-chat" and max_tokens > 7000:
        max_tokens = 7000
        logger.info(f"Code Generator (ASK): adjusted max_tokens to {max_tokens} for DeepSeek Chat")
    
    if not instruction or not instruction.strip():
        return CodeGeneratorResult(
            success=False,
            error="Empty instruction provided",
            model_used=model,
        )
    
    current_instruction = instruction
    
    for attempt in range(VALIDATION_RETRIES + 1):
        try:
            prompts = format_code_generator_prompt(
                orchestrator_instruction=current_instruction,
                file_code=file_code or "",
            )
            
            messages = [
                {"role": "system", "content": prompts["system"]},
                {"role": "user", "content": prompts["user"]},
            ]
            
            response = await call_llm(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            logger.info(f"Code Generator (ASK): received {len(response)} chars")
            
            # ASK mode autofix
            fixed_response, fixes = _autofix_response_ask(response)
            if fixes:
                logger.info(f"Code Generator (ASK): autofix: {fixes}")
            
            # ASK mode parse
            blocks, log_summary = parse_ask_mode_code_blocks(fixed_response, target_file)
            explanation = extract_explanation_from_response(fixed_response)
            
            # ASK mode validate
            validation = _validate_parsed_blocks_ask(blocks, instruction)
            
            if validation.is_ok:
                if validation.warning:
                    logger.warning(f"Code Generator (ASK): {validation.warning}")
                
                return CodeGeneratorResult(
                    code_blocks=blocks,
                    explanation=explanation,
                    raw_response=response,
                    success=True,
                    model_used=model,
                )
            
            # Retry
            if attempt < VALIDATION_RETRIES:
                logger.warning(f"Code Generator (ASK): validation failed (attempt {attempt + 1}): {validation.error}")
                current_instruction = _build_retry_prompt_ask(instruction, validation.error, response)
                continue
            
            logger.error(f"Code Generator (ASK): validation failed: {validation.error}")
            
            if blocks:
                return CodeGeneratorResult(
                    code_blocks=blocks,
                    explanation=explanation,
                    raw_response=response,
                    success=True,
                    error=f"Warning: {validation.error}",
                    model_used=model,
                )
            
            return CodeGeneratorResult(
                success=False,
                error=validation.error,
                raw_response=response,
                model_used=model,
            )
            
        except Exception as e:
            logger.error(f"Code Generator (ASK) error: {e}")
            return CodeGeneratorResult(
                success=False,
                error=str(e),
                model_used=model,
            )
    
    return CodeGeneratorResult(success=False, error="Unexpected error", model_used=model)


# ============================================================================
# AGENT MODE GENERATION
# ============================================================================

async def generate_code_agent_mode(
    instruction: str,
    file_contents: Dict[str, str],
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 38500,
) -> tuple[List[ParsedCodeBlock], str]:
    """
    Generate code in Agent Mode with CODE_BLOCK output.
    
    This is the main entry point for Agent Mode code generation.
    Returns ParsedCodeBlock objects ready for FileModifier.
    
    Includes automatic retry for network errors (DNS, connection issues).
    
    Args:
        instruction: Instruction from Orchestrator (what to do, where, how)
        file_contents: Dict mapping file_path -> current content
                      (allows generating code for multiple files)
        model: Model to use (defaults to code_generator model)
        temperature: Sampling temperature (0.2 for deterministic code)
        max_tokens: Maximum tokens in response
        
    Returns:
        Tuple of:
        - List[ParsedCodeBlock]: Parsed blocks ready for FileModifier
        - str: Raw response (for debugging/logging)
        
    Example:
        >>> blocks, raw = await generate_code_agent_mode(
        ...     instruction="Add login method to AuthService",
        ...     file_contents={"app/services/auth.py": existing_code}
        ... )
        >>> for block in blocks:
        ...     result = modifier.apply_code_block(existing, block)
    """
    from app.llm.prompt_templates import format_code_generator_prompt_agent
    
    # Determine model
    if model is None:
        model = get_model_for_role("code_generator")
    
    logger.info(f"Code Generator (Agent Mode): using {cfg.get_model_display_name(model)}")
    
    # =========================================================================
    # FIX: Adjust max_tokens for DeepSeek Chat (API limit 8192)
    # =========================================================================
    if model == "deepseek-chat" and max_tokens > 7000:
        max_tokens = 7000
        logger.info(f"Code Generator (Agent Mode): adjusted max_tokens to {max_tokens} for DeepSeek Chat")
    
    # Validate input
    if not instruction or not instruction.strip():
        logger.error("Empty instruction provided")
        return [], ""
    
    # Build file context string
    file_context_parts = []
    for file_path, content in file_contents.items():
        if content:
            file_context_parts.append(f"=== FILE: {file_path} ===")
            file_context_parts.append(content)
            file_context_parts.append("")
    
    file_code = "\n".join(file_context_parts) if file_context_parts else ""
    
    # Build prompts
    prompts = format_code_generator_prompt_agent(
        orchestrator_instruction=instruction,
        file_code=file_code,
        file_contents=file_contents,
    )
    
    messages = [
        {"role": "system", "content": prompts["system"]},
        {"role": "user", "content": prompts["user"]},
    ]
    
    # Execute with retry for network errors
    last_error = None
    
    for attempt in range(CODE_GENERATOR_MAX_RETRIES):
        try:
            # Call LLM
            response = await call_llm(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            logger.info(f"Code Generator (Agent Mode): received response ({len(response)} chars)")
            
            # Parse CODE_BLOCK sections
            blocks = parse_agent_code_blocks(response)
            
            if not blocks:
                logger.warning("Code Generator (Agent Mode): no CODE_BLOCK sections found")
                # Try fallback parsing
                fallback_code = _extract_any_code(response)
                if fallback_code:
                    # Try to determine file from instruction
                    file_match = re.search(r'FILE:\s*[`"]?([^`"\n]+)[`"]?', instruction)
                    file_path = file_match.group(1) if file_match else "unknown.py"
                    
                    blocks = [ParsedCodeBlock(
                        file_path=file_path,
                        mode="REPLACE_FILE",
                        code=fallback_code,
                    )]
                    logger.info("Used fallback parsing for code extraction")
            
            # Success - return results
            return blocks, response
            
        except Exception as e:
            last_error = e
            
            # Check if it's a network error (retryable)
            if _is_network_error(e):
                attempts_left = CODE_GENERATOR_MAX_RETRIES - attempt - 1
                
                if attempts_left > 0:
                    # Calculate delay with exponential backoff
                    delay = CODE_GENERATOR_RETRY_DELAY * (2 ** attempt)
                    
                    logger.warning(
                        f"Code Generator network error (attempt {attempt + 1}/{CODE_GENERATOR_MAX_RETRIES}): {e}. "
                        f"Retrying in {delay:.0f}s... ({attempts_left} attempts left)"
                    )
                    
                    await asyncio.sleep(delay)
                    continue
                else:
                    # All retries exhausted for network error
                    logger.error(
                        f"Code Generator network error: all {CODE_GENERATOR_MAX_RETRIES} retries exhausted. "
                        f"Last error: {e}"
                    )
            else:
                # Not a network error - don't retry
                logger.error(f"Code Generator (Agent Mode) error (non-retryable): {e}")
                break
    
    # All attempts exhausted or non-retryable error
    logger.error(f"Code Generator (Agent Mode) failed after {CODE_GENERATOR_MAX_RETRIES} attempts: {last_error}")
    return [], ""
# ============================================================================
# PARSING FUNCTIONS
# ============================================================================

def _parse_code_generator_response(
    response: str,
    default_filepath: Optional[str] = None
) -> CodeGeneratorResult:
    """
    Parse Code Generator response into structured result.
    
    Expected format (from prompt):
    
    ### Code
    
    ```python
    # filepath: path/to/file.py
    # context: ClassName
    
    <code here>
    ```
    
    ### Explanation
    
    <explanation here>
    
    Args:
        response: Raw LLM response
        default_filepath: Fallback filepath if not specified in code
        
    Returns:
        CodeGeneratorResult with parsed code_blocks and explanation
    """
    result = CodeGeneratorResult()
    
    # Extract code section
    code_blocks = _extract_code_blocks(response, default_filepath)
    result.code_blocks = code_blocks
    
    # Extract explanation section
    explanation = _extract_explanation(response)
    result.explanation = explanation
    
    # If no explanation found but code exists, mark as partial success
    if code_blocks and not explanation:
        result.explanation = "[No separate explanation provided]"
    
    return result


def _extract_code_blocks(
    response: str,
    default_filepath: Optional[str] = None
) -> List[CodeBlock]:
    """
    Extract all code blocks from response.
    
    Handles:
    - Multiple code blocks
    - Different languages (python, javascript, etc.)
    - Filepath and context comments
    """
    blocks: List[CodeBlock] = []
    
    # Pattern to match code fences with optional language
    # Matches: ```python, ```js, ```, etc.
    code_fence_pattern = r'```(\w*)\n(.*?)```'
    
    matches = re.findall(code_fence_pattern, response, re.DOTALL)
    
    for language, code_content in matches:
        if not code_content.strip():
            continue
        
        # Default to python if no language specified
        language = language or "python"
        
        filepath_patterns = [
            r'^#\s*filepath:\s*(.+?)$',           # Python style (#)
            r'^//\s*filepath:\s*(.+?)$',          # C-style (//)
            r'^--\s*filepath:\s*(.+?)$',          # SQL style (--)
            r'^<!--\s*filepath:\s*(.+?)\s*-->$',  # HTML style (<!-- -->)
            r'^/\*\s*filepath:\s*(.+?)\s*\*/$'    # CSS style (/* */)
        ]

        found_path = False
        for pattern in filepath_patterns:
            path_match = re.search(pattern, code_content, re.MULTILINE)
            if path_match:
                filepath = path_match.group(1).strip()
                found_path = True
                break
        
        # Если не нашли по специфичным, пробуем "голый" filepath (на всякий случай, если модель забыла коммент)
        if not found_path:
             fallback_match = re.search(r'^filepath:\s*(.+?)$', code_content, re.MULTILINE)
             if fallback_match:
                 filepath = fallback_match.group(1).strip()        
        
        # Extract context from comment
        context = None
        context_match = re.search(
            r'^#\s*context:\s*(.+?)$',
            code_content,
            re.MULTILINE
        )
        if context_match:
            context = context_match.group(1).strip()
        
        # Clean code: remove filepath/context comments for cleaner output
        clean_code = code_content
        clean_code = re.sub(r'^#\s*filepath:.*$\n?', '', clean_code, flags=re.MULTILINE)
        clean_code = re.sub(r'^#\s*context:.*$\n?', '', clean_code, flags=re.MULTILINE)
        clean_code = clean_code.strip()
        
        if clean_code:
            blocks.append(CodeBlock(
                code=clean_code,
                filepath=filepath,
                language=language,
                context=context,
                start_marker=f"```{language}",
            ))
    
    return blocks


def _extract_explanation(response: str) -> str:
    """
    Extract explanation section from response.
    
    Looks for:
    - ### Explanation header
    - Text after code blocks
    - Bullet points with explanations
    """
    # Try to find explicit Explanation section
    explanation_patterns = [
        r'###\s*Explanation\s*\n(.*?)(?=###|$)',
        r'##\s*Explanation\s*\n(.*?)(?=##|$)',
        r'\*\*Explanation[:\*]*\*\*\s*\n(.*?)(?=\*\*|$)',
    ]
    
    for pattern in explanation_patterns:
        match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if match:
            explanation = match.group(1).strip()
            # Remove any code blocks from explanation
            explanation = re.sub(r'```.*?```', '', explanation, flags=re.DOTALL)
            if explanation.strip():
                return explanation.strip()
    
    # Fallback: get text after last code block
    last_code_end = 0
    for match in re.finditer(r'```.*?```', response, re.DOTALL):
        last_code_end = match.end()
    
    if last_code_end > 0:
        remaining = response[last_code_end:].strip()
        # Clean up remaining text
        remaining = re.sub(r'^[\s\-\*]+', '', remaining)
        if remaining and len(remaining) > 20:  # Meaningful content
            return remaining
    
    return ""


def _extract_any_code(response: str) -> Optional[str]:
    """
    Fallback: try to extract any code-like content from response.
    
    Used when standard parsing fails.
    """
    # Try to find indented code blocks (4+ spaces)
    indented_pattern = r'^((?:    .+\n)+)'
    match = re.search(indented_pattern, response, re.MULTILINE)
    if match:
        return match.group(1).strip()
    
    # Try to find code between markers
    markers = [
        (r'```\w*\n', r'\n```'),
        (r'<code>', r'</code>'),
    ]
    
    for start, end in markers:
        pattern = f'{start}(.*?){end}'
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()
    
    return None


# ============================================================================
# ASK MODE PARSING (SEPARATE FROM AGENT MODE)
# ============================================================================

# ============================================================================
# ASK MODE PARSING (SEPARATE FROM AGENT MODE)
# ============================================================================

def parse_ask_mode_code_blocks(
    response: str,
    default_filepath: Optional[str] = None,
    log_file: Optional[str] = None
) -> Tuple[List[CodeBlock], str]:
    """
    Унифицированный парсер для ASK режима.
    
    НЕ влияет на Agent mode (который использует parse_agent_code_blocks).
    """
    import os
    from datetime import datetime
    
    log_lines: List[str] = []
    
    def log(msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_lines.append(f"[{timestamp}] {msg}")
    
    log("=" * 60)
    log("ASK MODE CODE BLOCK PARSER")
    log("=" * 60)
    log(f"Response length: {len(response)} chars")
    
    blocks: List[CodeBlock] = []
    explanation = ""
    
    # Пробуем маркерный формат (приоритет)
    if '# === FILE:' in response:
        log("Detected MARKER format")
        blocks, explanation = parse_marker_format_ask(response, default_filepath)
        log(f"Parsed {len(blocks)} block(s)")
        
        for i, b in enumerate(blocks, 1):
            log(f"  {i}. {b.filepath} [{b.language}]")
            log(f"     ACTION: {b.action or '(not set)'}, TARGET: {b.target or '(not set)'}")
    
    # Fallback на markdown
    if not blocks and '```' in response:
        log("Fallback to MARKDOWN format")
        code_blocks_raw = _extract_code_fences_smart(response, log)
        
        for language, code_content, _, _ in code_blocks_raw:
            if not code_content.strip():
                continue
            
            language = language or "python"
            filepath = default_filepath
            
            for pattern in [r'^#\s*filepath:\s*(.+?)$', r'^//\s*filepath:\s*(.+?)$']:
                path_match = re.search(pattern, code_content, re.MULTILINE | re.IGNORECASE)
                if path_match:
                    filepath = path_match.group(1).strip().strip('`"\' ')
                    break
            
            context = None
            context_match = re.search(r'^#\s*context:\s*(.+?)$', code_content, re.MULTILINE)
            if context_match:
                context = context_match.group(1).strip()
            
            clean_code = code_content
            clean_code = re.sub(r'^#\s*filepath:.*$\n?', '', clean_code, flags=re.MULTILINE)
            clean_code = re.sub(r'^#\s*context:.*$\n?', '', clean_code, flags=re.MULTILINE)
            clean_code = clean_code.strip()
            
            # Эвристика для action
            if context:
                action = "REPLACE_METHOD"
            elif not filepath:
                action = "NEW_FILE"
            else:
                action = "REPLACE_FILE"
            
            if clean_code:
                blocks.append(CodeBlock(
                    code=clean_code,
                    filepath=filepath,
                    language=language,
                    context=context,
                    action=action,
                    target=None,
                ))
        
        explanation = _extract_explanation(response)
        log(f"Parsed {len(blocks)} block(s) from markdown")
    
    log(f"\nTotal: {len(blocks)} blocks")
    log_summary = f"Parsed {len(blocks)} code block(s)"
    
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(log_lines))
        except Exception as e:
            logger.warning(f"Failed to write log: {e}")
    
    return blocks, log_summary


def _extract_code_fences_smart(response: str, log_func) -> List[tuple]:
    """
    Умное извлечение code fences с учётом вложенных ``` в строках.
    
    Улучшенный алгоритм:
    1. Ищем открывающие ``` в начале строки
    2. Определяем язык (если не указан — смотрим на содержимое)
    3. Ищем закрывающий ``` который:
       - Находится в начале строки
       - НЕ внутри Python-строки (для Python файлов)
    
    Returns:
        List of tuples: (language, content, start_pos, end_pos)
    """
    results = []
    
    # Паттерн для открывающего fence: ``` с опциональным языком
    # Должен быть в начале строки (после \n или начало текста)
    open_pattern = re.compile(r'(?:^|\n)([ \t]*```(\w*))[ \t]*\n', re.MULTILINE)
    
    pos = 0
    iteration_guard = 0
    max_iterations = 100  # Защита от бесконечного цикла
    
    while pos < len(response) and iteration_guard < max_iterations:
        iteration_guard += 1
        
        # Ищем следующий открывающий fence
        match = open_pattern.search(response, pos)
        if not match:
            break
        
        fence_start = match.start()
        if response[fence_start] == '\n':
            fence_start += 1  # Пропускаем \n, fence начинается с ```
        
        language = match.group(2) or ""
        content_start = match.end()
        
        log_func(f"  Found opening fence at pos {fence_start}, language='{language}'")
        
        # Определяем, нужно ли отслеживать Python-строки
        # Проверяем первые строки контента на наличие # filepath: *.py
        is_python = language.lower() in ('python', 'py', '')
        if not language:
            # Попробуем определить по содержимому
            preview_end = min(content_start + 500, len(response))
            preview = response[content_start:preview_end]
            if re.search(r'#\s*filepath:.*\.py', preview, re.IGNORECASE):
                is_python = True
                log_func(f"    Detected Python from filepath comment")
            elif re.search(r'^(def |class |import |from |async def )', preview, re.MULTILINE):
                is_python = True
                log_func(f"    Detected Python from code patterns")
        
        # Ищем закрывающий fence
        close_pos = _find_closing_fence_improved(
            response, 
            content_start, 
            is_python=is_python,
            log_func=log_func
        )
        
        if close_pos == -1:
            log_func(f"  WARNING: No closing fence found! Taking rest of response.")
            content = response[content_start:].rstrip()
            results.append((language or "python", content, fence_start, len(response)))
            break
        
        # Извлекаем контент
        content = response[content_start:close_pos]
        
        # Убираем trailing newline из контента
        content = content.rstrip('\n')
        
        # Находим конец закрывающего fence
        fence_end = close_pos + 3
        # Пропускаем пробелы и перенос строки после ```
        while fence_end < len(response) and response[fence_end] in ' \t':
            fence_end += 1
        if fence_end < len(response) and response[fence_end] == '\n':
            fence_end += 1
        
        log_func(f"  Found closing fence at pos {close_pos}, content length={len(content)}")
        
        # Если язык не указан, используем python по умолчанию
        final_language = language or "python"
        
        results.append((final_language, content, fence_start, fence_end))
        
        # Продолжаем поиск после этого блока
        pos = fence_end
    
    return results


def _find_closing_fence_improved(
    text: str, 
    start: int, 
    is_python: bool,
    log_func
) -> int:
    """
    Находит позицию закрывающего ``` с улучшенной логикой.
    
    Ключевые улучшения:
    1. Закрывающий ``` должен быть на ОТДЕЛЬНОЙ строке
    2. Для Python: отслеживаем строковые литералы
    3. Для не-Python: просто ищем ``` в начале строки
    
    Returns:
        Позиция начала закрывающего ```, или -1 если не найден
    """
    pos = start
    length = len(text)
    
    # Состояние парсера (только для Python)
    in_string = False
    string_char = None  # '"' или "'"
    is_triple = False   # Тройные кавычки?
    
    while pos < length:
        char = text[pos]
        
        # ========================================
        # Проверяем, может это закрывающий fence?
        # ========================================
        if char == '`':
            # Проверяем ```
            if pos + 2 < length and text[pos:pos+3] == '```':
                # Если мы внутри Python-строки — это НЕ закрывающий fence
                if is_python and in_string:
                    pos += 3
                    continue
                
                # Проверяем, что ``` в начале строки
                line_start = text.rfind('\n', 0, pos)
                if line_start == -1:
                    line_start = 0
                else:
                    line_start += 1
                
                # Между началом строки и ``` должны быть только пробелы/табы
                prefix = text[line_start:pos]
                if prefix.strip() == '':
                    # Проверяем, что после ``` нет букв (не открывающий fence)
                    after_pos = pos + 3
                    if after_pos >= length:
                        return pos  # Конец текста — это закрывающий
                    
                    after_char = text[after_pos]
                    # Закрывающий fence: после ``` пробелы/табы/перенос или конец
                    if after_char in ' \t\n' or after_pos >= length:
                        # Дополнительная проверка: вся строка после ``` пустая?
                        nl_pos = text.find('\n', after_pos)
                        if nl_pos == -1:
                            rest = text[after_pos:]
                        else:
                            rest = text[after_pos:nl_pos]
                        
                        if rest.strip() == '':
                            return pos
        
        # ========================================
        # Отслеживание Python-строк
        # ========================================
        if is_python:
            if not in_string:
                # Проверяем начало строки
                if char in ('"', "'"):
                    # Тройные кавычки?
                    if pos + 2 < length and text[pos:pos+3] in ('"""', "'''"):
                        in_string = True
                        string_char = char
                        is_triple = True
                        pos += 3
                        continue
                    else:
                        in_string = True
                        string_char = char
                        is_triple = False
                        pos += 1
                        continue
            else:
                # Внутри строки
                if char == '\\' and pos + 1 < length:
                    # Escape — пропускаем следующий символ
                    pos += 2
                    continue
                
                if is_triple:
                    # Закрывающие тройные кавычки
                    if pos + 2 < length and text[pos:pos+3] == string_char * 3:
                        in_string = False
                        string_char = None
                        is_triple = False
                        pos += 3
                        continue
                else:
                    # Закрывающая одинарная кавычка
                    if char == string_char:
                        in_string = False
                        string_char = None
                        is_triple = False
        
        pos += 1
    
    return -1


def _find_closing_fence(text: str, start: int, log_func) -> int:
    """
    Находит позицию закрывающего ``` с учётом строковых литералов.
    
    Отслеживает:
    - Одинарные и двойные кавычки
    - Тройные кавычки (docstrings)
    - Escape-последовательности
    - f-строки (сами f-строки не особенные, но {} внутри — да)
    
    Returns:
        Позиция начала закрывающего ```, или -1 если не найден
    """
    pos = start
    length = len(text)
    
    # Состояние парсера
    in_string = False
    string_char = None  # '"' или "'"
    is_triple = False   # Тройные кавычки?
    
    while pos < length:
        char = text[pos]
        
        # ========================================
        # Обработка строковых литералов
        # ========================================
        if not in_string:
            # Проверяем начало строки
            if char in ('"', "'"):
                # Проверяем тройные кавычки
                if pos + 2 < length and text[pos:pos+3] in ('"""', "'''"):
                    in_string = True
                    string_char = char
                    is_triple = True
                    pos += 3
                    continue
                else:
                    in_string = True
                    string_char = char
                    is_triple = False
                    pos += 1
                    continue
            
            # Проверяем закрывающий fence
            # Он должен быть в начале строки (или после пробелов)
            if char == '`' and pos + 2 < length and text[pos:pos+3] == '```':
                # Проверяем, что это начало строки
                line_start = text.rfind('\n', 0, pos)
                if line_start == -1:
                    line_start = 0
                else:
                    line_start += 1
                
                # Проверяем, что между началом строки и ``` только пробелы
                prefix = text[line_start:pos]
                if prefix.strip() == '':
                    # Проверяем, что после ``` конец строки или пробелы+конец
                    after_fence = pos + 3
                    valid_close = False
                    
                    if after_fence >= length:
                        valid_close = True
                    else:
                        rest_of_line = ""
                        nl_pos = text.find('\n', after_fence)
                        if nl_pos == -1:
                            rest_of_line = text[after_fence:]
                        else:
                            rest_of_line = text[after_fence:nl_pos]
                        
                        if rest_of_line.strip() == '':
                            valid_close = True
                    
                    if valid_close:
                        return pos
        
        else:
            # Мы внутри строки
            if char == '\\' and pos + 1 < length:
                # Escape-последовательность — пропускаем следующий символ
                pos += 2
                continue
            
            if is_triple:
                # Ищем закрывающие тройные кавычки
                if char == string_char and pos + 2 < length and text[pos:pos+3] == string_char * 3:
                    in_string = False
                    string_char = None
                    is_triple = False
                    pos += 3
                    continue
            else:
                # Ищем закрывающую одинарную кавычку
                if char == string_char:
                    in_string = False
                    string_char = None
                    is_triple = False
        
        pos += 1
    
    return -1


def extract_explanation_from_response(response: str) -> str:
    """Извлекает explanation (ASK mode, оба формата)."""
    # Маркерный формат
    if '# === EXPLANATION ===' in response:
        match = re.search(
            r'# === EXPLANATION ===\s*\n(.*?)(?:# === END EXPLANATION ===|$)',
            response,
            re.DOTALL
        )
        if match:
            return match.group(1).strip()
    
    # Fallback на legacy
    return _extract_explanation(response)


# ============================================================================
# AGENT MODE CODE_BLOCK PARSING
# ============================================================================

def parse_agent_code_blocks(response: str) -> List[ParsedCodeBlock]:
    """
    Парсит ответ Генератора в Agent Mode.
    
    Извлекает все CODE_BLOCK секции из ответа и преобразует
    в список ParsedCodeBlock для применения через FileModifier.
    
    Expected format:
        ### CODE_BLOCK
        FILE: path/to/file.py
        MODE: REPLACE_METHOD
        TARGET_CLASS: ClassName (optional)
        TARGET_METHOD: method_name (optional)
        TARGET_FUNCTION: function_name (optional)
        TARGET_ATTRIBUTE: attribute_name (optional)
        INSERT_AFTER: element_name (optional)
        INSERT_BEFORE: element_name (optional)
        REPLACE_PATTERN: pattern_to_find (optional)
        
        <code here>
        ### END_CODE_BLOCK
    
    Args:
        response: Raw response from Code Generator (Agent Mode)
        
    Returns:
        List of ParsedCodeBlock objects ready for FileModifier
        
    Example:
        >>> response = '''
        ... ### CODE_BLOCK
        ... FILE: app/auth.py
        ... MODE: REPLACE_METHOD
        ... TARGET_CLASS: AuthService
        ... TARGET_METHOD: login
        ...
        ... ```python
        ... def login(self, user, password):
        ...     return self.verify(user, password)
        ... ```
        ... ### END_CODE_BLOCK
        ... '''
        >>> blocks = parse_agent_code_blocks(response)
        >>> len(blocks)
        1
        >>> blocks[0].mode
        'REPLACE_METHOD'
    """
    blocks: List[ParsedCodeBlock] = []
    
    # Паттерн для извлечения CODE_BLOCK секций
    # Поддерживает оба варианта: с END_CODE_BLOCK и без (до следующего ### CODE_BLOCK или конца)
    pattern = r'###\s*CODE_BLOCK\s*\n(.*?)(?:###\s*END_CODE_BLOCK|(?=###\s*CODE_BLOCK)|$)'
    
    for match in re.finditer(pattern, response, re.DOTALL | re.IGNORECASE):
        block_content = match.group(1).strip()
        
        if not block_content:
            continue
        
        # Извлекаем метаданные
        file_path = _extract_field(block_content, "FILE")
        mode = _extract_field(block_content, "MODE")
        target_class = _extract_field(block_content, "TARGET_CLASS")
        target_method = _extract_field(block_content, "TARGET_METHOD")
        target_function = _extract_field(block_content, "TARGET_FUNCTION")
        target_attribute = _extract_field(block_content, "TARGET_ATTRIBUTE")
        insert_after = _extract_field(block_content, "INSERT_AFTER")
        insert_before = _extract_field(block_content, "INSERT_BEFORE")
        replace_pattern = _extract_field(block_content, "REPLACE_PATTERN")
        
        # Извлекаем код из code fence
        code, language = _extract_code_from_block(block_content)
        
        # Валидация обязательных полей
        if not file_path:
            logger.warning(f"CODE_BLOCK missing FILE field, skipping")
            continue
        
        if not mode:
            logger.warning(f"CODE_BLOCK for {file_path} missing MODE field, skipping")
            continue
        
        if not code:
            logger.warning(f"CODE_BLOCK for {file_path} missing code, skipping")
            continue
        
        blocks.append(ParsedCodeBlock(
            file_path=file_path,
            mode=mode,
            code=code,
            target_class=target_class,
            target_method=target_method,
            target_function=target_function,
            target_attribute=target_attribute,
            insert_after=insert_after,
            insert_before=insert_before,
            replace_pattern=replace_pattern,
            language=language,
        ))
        
        logger.debug(f"Parsed CODE_BLOCK: {file_path} [{mode}]")
    
    logger.info(f"Parsed {len(blocks)} CODE_BLOCK(s) from response")
    return blocks


def _extract_field(content: str, field_name: str) -> Optional[str]:
    """
    Извлекает значение поля из CODE_BLOCK.
    
    Поддерживает форматы:
    - FIELD: value
    - FIELD:value
    - **FIELD:** value (markdown bold)
    """
    patterns = [
        rf'^{field_name}:\s*(.+?)$',
        rf'^\*\*{field_name}:\*\*\s*(.+?)$',
        rf'^{field_name}\s*:\s*(.+?)$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # Убираем backticks если есть
            value = value.strip('`')
            return value
    
    return None


def _extract_code_from_block(content: str) -> Tuple[Optional[str], str]:
    """
    Извлекает код из CODE_BLOCK.
    
    Ищет код внутри code fence (```language ... ```)
    Корректно обрабатывает случаи, когда ``` встречается внутри кода.
    
    Поддерживает языки: python, javascript, typescript, go, java и другие.
    
    Returns:
        Tuple of (code_content, language)
    """
    # Ищем открывающий fence
    open_match = re.search(r'^```(\w*)\s*$', content, re.MULTILINE)
    if not open_match:
        # Fallback: ищем ``` в любом месте
        open_match = re.search(r'```(\w*)\n', content)
        if not open_match:
            return (None, "python")
    
    code_start = open_match.end()
    language = open_match.group(1).lower() if open_match.group(1) else "python"
    
    # Normalize language aliases
    language_map = {
        'py': 'python',
        'js': 'javascript',
        'ts': 'typescript',
        'golang': 'go',
    }
    language = language_map.get(language, language)
    
    # Ищем закрывающий fence
    remaining_content = content[code_start:]
    lines = remaining_content.split('\n')
    code_lines = []
    
    for line in lines:
        if re.match(r'^\s*```\s*$', line):
            break
        code_lines.append(line)
    
    code = '\n'.join(code_lines).strip()
    return (code, language) if code else (None, language)



def format_code_blocks_summary(blocks: List[ParsedCodeBlock]) -> str:
    """
    Форматирует список CODE_BLOCK для отображения.
    
    Args:
        blocks: Список распарсенных блоков
        
    Returns:
        Форматированная строка для логов/UI
    """
    if not blocks:
        return "No CODE_BLOCK sections found"
    
    lines = [f"📦 **{len(blocks)} CODE_BLOCK(s) parsed:**", ""]
    
    for i, block in enumerate(blocks, 1):
        lines.append(f"**Block {i}:**")
        lines.append(f"  📁 File: `{block.file_path}`")
        lines.append(f"  🔧 Mode: `{block.mode}`")
        
        if block.target_class:
            lines.append(f"  🎯 Class: `{block.target_class}`")
        if block.target_method:
            lines.append(f"  🎯 Method: `{block.target_method}`")
        if block.target_function:
            lines.append(f"  🎯 Function: `{block.target_function}`")
        if block.insert_after:
            lines.append(f"  ➡️ After: `{block.insert_after}`")
        
        lines.append(f"  📝 Code: {len(block.code)} chars, {len(block.code.splitlines())} lines")
        lines.append("")
    
    return "\n".join(lines)


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def validate_indentation(code: str, context_code: Optional[str] = None) -> str:
    """
    Validate and fix code indentation.
    
    Checks:
    - Consistent indentation (spaces vs tabs)
    - Proper nesting levels
    - Class method indentation (4 spaces)
    
    Args:
        code: Code to validate
        context_code: Existing code for context matching
        
    Returns:
        Code with fixed indentation (if needed)
    """
    lines = code.split('\n')
    
    # Detect indentation style from context or code
    indent_char = ' '
    indent_size = 4
    
    if context_code:
        # Try to detect from context
        for line in context_code.split('\n'):
            if line.startswith('    '):
                indent_char = ' '
                indent_size = 4
                break
            elif line.startswith('\t'):
                indent_char = '\t'
                indent_size = 1
                break
    
    # Check for mixed indentation
    has_tabs = any('\t' in line for line in lines)
    has_spaces = any(line.startswith(' ') for line in lines)
    
    if has_tabs and has_spaces:
        # Convert all to spaces
        lines = [line.replace('\t', '    ') for line in lines]
        logger.warning("Code Generator: fixed mixed tabs/spaces indentation")
    
    return '\n'.join(lines)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

async def generate_code_simple(
    instruction: str,
    file_code: Optional[str] = None,
) -> tuple[str, str]:
    """
    Simplified interface - returns just (code, explanation) tuple.
    
    For quick usage when you don't need full CodeGeneratorResult.
    
    Args:
        instruction: What to generate
        file_code: Existing code context
        
    Returns:
        Tuple of (code_string, explanation_string)
    """
    result = await generate_code(instruction, file_code)
    return result.primary_code, result.explanation


def format_result_for_display(result: CodeGeneratorResult) -> str:
    """Format result for display (ASK mode)."""
    return result.format_for_user()