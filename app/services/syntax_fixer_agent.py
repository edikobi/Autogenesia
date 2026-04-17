from __future__ import annotations
import asyncio
import logging
import concurrent.futures
import re
from typing import Optional, Dict, Any, Tuple, List

logger = logging.getLogger(__name__)

SYNTAX_FIX_PROMPTS: Dict[str, str] = {
    "python": (
        "You are a Python syntax repair expert.\n"
        "REWRITE the CODE TO FIX block to be free of syntax and indentation errors.\n"
        "Return the FULL rewritten block.\n\n"
        "RULES:\n"
        "- PRESERVE logic, variable names, and signatures.\n"
        "- DO NOT add comments, explanations, or docstrings.\n"
        "- WRAP output in ###CODE### markers (e.g., ###CODE###\\nyour_code\\n###CODE###).\n"
        "- NO markdown fences (```)."
    ),
    "javascript": (
        "You are a JavaScript syntax repair expert.\n"
        "REWRITE the CODE TO FIX block to be free of syntax errors.\n"
        "Return the FULL rewritten block.\n\n"
        "RULES:\n"
        "- PRESERVE logic, names, and signatures.\n"
        "- DO NOT add explanations.\n"
        "- WRAP output in ###CODE### markers (e.g., ###CODE###\\nyour_code\\n###CODE###).\n"
        "- NO markdown fences (```)."
    ),
    "typescript": (
        "You are a TypeScript syntax repair expert.\n"
        "REWRITE the CODE TO FIX block to be free of syntax errors.\n"
        "Return the FULL rewritten block.\n\n"
        "RULES:\n"
        "- PRESERVE logic, names, and signatures.\n"
        "- DO NOT add explanations.\n"
        "- WRAP output in ###CODE### markers (e.g., ###CODE###\\nyour_code\\n###CODE###).\n"
        "- NO markdown fences (```)."
    ),
    "java": (
        "You are a Java syntax repair expert.\n"
        "REWRITE the CODE TO FIX block to be free of syntax errors.\n"
        "Return the FULL rewritten block.\n\n"
        "RULES:\n"
        "- PRESERVE logic, names, and signatures.\n"
        "- DO NOT add explanations.\n"
        "- WRAP output in ###CODE### markers (e.g., ###CODE###\\nyour_code\\n###CODE###).\n"
        "- NO markdown fences (```)."
    ),
    "go": (
        "You are a Go syntax repair expert.\n"
        "REWRITE the CODE TO FIX block to be free of syntax errors.\n"
        "Return the FULL rewritten block.\n\n"
        "RULES:\n"
        "- PRESERVE logic, names, and signatures.\n"
        "- DO NOT add explanations.\n"
        "- WRAP output in ###CODE### markers (e.g., ###CODE###\\nyour_code\\n###CODE###).\n"
        "- NO markdown fences (```)."
    )
}

# Generic fallback prompt for unsupported languages
GENERIC_SYNTAX_FIX_PROMPT = (
    "You are a code syntax repair agent.\n\n"
    "YOUR TASK:\n"
    "COMPLETELY REWRITE the CODE TO FIX block so that it is entirely free of syntax errors.\n\n"
    "CRITICAL RULES:\n"
    "- Do NOT change any logic or functionality\n"
    "- Output MUST be wrapped in ###CODE### markers (e.g., ###CODE###\nyour_code_here\n###CODE###)\n"
    "- Do NOT use markdown fences (```)\n"
    "- Do NOT add explanations"
)


def _get_syntax_fix_prompt(language: str) -> str:
    """Returns the system prompt for the given language or a generic one."""
    lang_lower = language.lower()
    if lang_lower in SYNTAX_FIX_PROMPTS:
        return SYNTAX_FIX_PROMPTS[lang_lower]
    return GENERIC_SYNTAX_FIX_PROMPT



def _build_user_message(code_block: str, language: str, context_info: Dict[str, Any]) -> str:
    """Builds a structured, NEUTRAL user message for the LLM. Pure Generator mode."""
    mode = context_info.get("mode", "unknown")
    target = context_info.get("target_name", "unknown")
    errors = context_info.get("error_details", "")
    surrounding = context_info.get("surrounding_context", "")
    original_code = context_info.get("original_code", "")
    target_indent = context_info.get("target_indent", None)
    is_method_or_function = context_info.get("is_method_or_function_insert", False)
    replace_pattern = context_info.get("replace_pattern", None)
    line_indent = context_info.get("line_indent", None)
    indent_anchor = context_info.get("indent_anchor", None)

    parts: List[str] = []

    # Header
    parts.append(f"### LANGUAGE: {language.upper()}")
    parts.append(f"### MODIFICATION MODE: {mode}")
    parts.append(f"### TARGET ELEMENT: {target}")

    if replace_pattern:
        parts.append(f"### REPLACE PATTERN: `{replace_pattern}`")

    # Indentation guidance
    effective_indent = line_indent if line_indent is not None else target_indent
    if effective_indent is not None:
        parts.append(f"\n### INDENTATION REQUIREMENT:")
        parts.append(f"Base Indentation Level: {effective_indent} spaces.")
        if indent_anchor:
            parts.append(f"Indentation Anchor String: \"{indent_anchor}\"")
        parts.append(f"Example: Every line at the root of this block must start with exactly {effective_indent} spaces.")

    if is_method_or_function:
        parts.append(f"\n### NOTE: This block is a class/method/function definition.")
    else:
        parts.append(f"\n### NOTE: This block is being inserted INSIDE a method body.")

    # Structural constraints (NEUTRAL phrasing)
    if errors:
        parts.append(f"\n### DETECTED STRUCTURAL CONSTRAINTS:\n{errors}")

    # Surrounding context
    if surrounding and surrounding.strip():
        parts.append(f"\n### SURROUNDING CONTEXT (with line numbers):\n```\n{surrounding}\n```")

    # Original & Current code
    if original_code and original_code.strip():
        parts.append(f"\n### ORIGINAL CODE BLOCK:\n```{language}\n{original_code}\n```")
    parts.append(f"\n### CURRENT CODE BLOCK STATE:\n```{language}\n{code_block}\n```")

    # Final instruction (Pure Generator directive)
    parts.append(
        "\n### FINAL INSTRUCTION:\n"
        "1. Analyze STRUCTURAL CONSTRAINTS and CURRENT STATE.\n"
        "2. REWRITE the CODE BLOCK entirely to fix all syntax/indentation issues.\n"
        "3. Ensure consistency with SURROUNDING CONTEXT style and indentation.\n"
        "4. DO NOT include comments, explanations, or markdown fences.\n"
        "5. WRAP YOUR CODE IN ###CODE### markers.\n\n"
        "START YOUR RESPONSE IMMEDIATELY WITH ###CODE###."
    )
    return "\n".join(parts)

def extract_surrounding_context(
    file_content: str,
    target_class: Optional[str],
    target_method: Optional[str],
    target_function: Optional[str],
    language: str,
    mode: Optional[str] = None,
    insertion_line: Optional[int] = None
) -> Dict[str, Any]:
    """
    Extracts the surrounding method/function/class context for better AI understanding.
    Uses Tree-sitter parsers for all supported languages.
    
    Returns a dict with:
      - "surrounding_context": str — the relevant code context
      - "target_indent": Optional[int] — the indentation level of the target element (in spaces)
    
    Context strategy depends on the modification mode:
      - "inside" modes (PATCH_METHOD, INSERT_IN_FUNCTION, DIFF_INSERT, etc.):
        Show the ENTIRE enclosing method/function body so AI sees full structure.
      - "replace" modes (REPLACE_METHOD, REPLACE_FUNCTION, DIFF_REPLACE, etc.):
        Show the OLD method/function being replaced.
      - "insert new" modes (INSERT_CLASS, ADD_METHOD, ADD_NEW_FUNCTION, etc.):
        Return brief explanatory context only.
    
    For non-Python DIFF modes, if no structural target is found, falls back to
    showing up to 200 lines above and below the insertion_line.
    
    This function is fault-tolerant: if extraction fails, it returns defaults
    rather than raising an exception.
    
    Args:
        file_content: Full file content
        target_class: Target class name (if applicable)
        target_method: Target method name (if applicable)
        target_function: Target function name (if applicable)
        language: Programming language
        mode: CODE_BLOCK mode string (e.g. "REPLACE_METHOD", "DIFF_REPLACE")
        insertion_line: 0-indexed line number of the insertion/replacement point (for fallback context)
        
    Returns:
        Dict with "surrounding_context" (str) and "target_indent" (Optional[int])
    """
    empty_result: Dict[str, Any] = {"surrounding_context": "", "target_indent": None}
    
    if not file_content:
        return empty_result
    
    lang_lower = language.lower()
    lines = file_content.splitlines()
    mode_upper = (mode or "").upper()
    
    # === Classify the mode into a context strategy ===
    INSIDE_MODES = {
        "PATCH_METHOD", "INSERT_INTO_METHOD", "ADD_LINES",
        "REPLACE_IN_METHOD", "MODIFY_METHOD_LINE",
        "INSERT_IN_FUNCTION", "PATCH_FUNCTION",
        "REPLACE_IN_FUNCTION",
        "DIFF_INSERT",
    }
    REPLACE_MODES = {
        "REPLACE_METHOD", "MODIFY_METHOD",
        "REPLACE_FUNCTION", "MODIFY_FUNCTION",
        "DIFF_REPLACE",
    }
    INSERT_NEW_MODES = {
        "INSERT_CLASS", "ADD_METHOD",
        "ADD_NEW_FUNCTION", "CREATE_FUNCTION",
        "INSERT_FILE", "ADD_FUNCTION",
        "APPEND_FILE", "APPEND",
    }
    
    is_inside_mode = mode_upper in INSIDE_MODES
    is_replace_mode = mode_upper in REPLACE_MODES
    is_insert_new_mode = mode_upper in INSERT_NEW_MODES
    
    # === PYTHON: Use FaultTolerantParser ===
    if lang_lower == "python":
        try:
            from app.services.tree_sitter_parser import FaultTolerantParser
            parser = FaultTolerantParser()
            parse_result = parser.parse(file_content)
            
            context_text = ""
            target_indent_value = None
            
            if target_method and target_class:
                target_indent_value = parser.get_element_indent(file_content, target_method, parent_class=target_class)
            elif target_function:
                target_indent_value = parser.get_element_indent(file_content, target_function)
            elif target_class:
                target_indent_value = parser.get_element_indent(file_content, target_class)
            
            if is_inside_mode:
                if target_class and target_method:
                    method_info = parse_result.get_method(target_class, target_method)
                    if method_info:
                        start = max(0, method_info.span.start_line - 1)
                        end = min(len(lines), method_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
                elif target_function:
                    func_info = parse_result.get_function(target_function)
                    if func_info:
                        start = max(0, func_info.span.start_line - 1)
                        end = min(len(lines), func_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
                elif target_class:
                    class_info = parse_result.get_class(target_class)
                    if class_info:
                        start = max(0, class_info.span.start_line - 1)
                        end = min(len(lines), class_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
            
            elif is_replace_mode:
                if target_class and target_method:
                    method_info = parse_result.get_method(target_class, target_method)
                    if method_info:
                        start = max(0, method_info.span.start_line - 1)
                        end = min(len(lines), method_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
                elif target_function:
                    func_info = parse_result.get_function(target_function)
                    if func_info:
                        start = max(0, func_info.span.start_line - 1)
                        end = min(len(lines), func_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
            
            elif is_insert_new_mode:
                if target_class:
                    class_info = parse_result.get_class(target_class)
                    if class_info:
                        start = max(0, class_info.span.start_line - 1)
                        end = min(len(lines), class_info.span.end_line, start + 10)
                        context_text = "\n".join(lines[start:end])
                        if end < class_info.span.end_line:
                            context_text += "\n    # ... (rest of class)"
            
            else:
                if target_class and target_method:
                    method_info = parse_result.get_method(target_class, target_method)
                    if method_info:
                        start = max(0, method_info.span.start_line - 1)
                        end = min(len(lines), method_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
                
                if not context_text and target_function:
                    func_info = parse_result.get_function(target_function)
                    if func_info:
                        start = max(0, func_info.span.start_line - 1)
                        end = min(len(lines), func_info.span.end_line)
                        context_text = "\n".join(lines[start:end])
                
                if not context_text and target_class:
                    class_info = parse_result.get_class(target_class)
                    if class_info:
                        start = max(0, class_info.span.start_line - 1)
                        end = min(len(lines), class_info.span.end_line, start + 50)
                        context_text = "\n".join(lines[start:end])
            
            return {
                "surrounding_context": context_text,
                "target_indent": target_indent_value
            }
                    
        except Exception as e:
            logger.debug(f"Failed to extract Python context: {e}")
            return empty_result
    
    # === JAVASCRIPT, TYPESCRIPT, GO, JAVA: Use MultiLanguageParser ===
    elif lang_lower in ("javascript", "typescript", "go", "java"):
        try:
            from app.services.tree_sitter_parser import MultiLanguageParser
            parser = MultiLanguageParser()
            
            context_text = ""
            target_indent_value = None
            
            # --- Determine target_indent ---
            if target_class and target_method:
                element_info = parser.find_element(
                    source_code=file_content,
                    language=lang_lower,
                    element_name=target_method,
                    element_type='method',
                    parent_name=target_class
                )
                if element_info:
                    target_indent_value = element_info.get('indent', None)
            elif target_function:
                element_info = parser.find_element(
                    source_code=file_content,
                    language=lang_lower,
                    element_name=target_function,
                    element_type='function'
                )
                if element_info:
                    target_indent_value = element_info.get('indent', None)
            elif target_class:
                element_info = parser.find_element(
                    source_code=file_content,
                    language=lang_lower,
                    element_name=target_class,
                    element_type='class'
                )
                if element_info:
                    target_indent_value = element_info.get('indent', None)
            
            # --- Context extraction based on mode ---
            
            if is_inside_mode:
                # Show the ENTIRE enclosing method/function body
                if target_class and target_method:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_method,
                        element_type='method',
                        parent_name=target_class
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
                elif target_function:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_function,
                        element_type='function'
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
                elif target_class:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_class,
                        element_type='class'
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
            
            elif is_replace_mode:
                # Show the OLD method/function being replaced
                if target_class and target_method:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_method,
                        element_type='method',
                        parent_name=target_class
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
                elif target_function:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_function,
                        element_type='function'
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
            
            elif is_insert_new_mode:
                # Inserting a new method/function — show brief class signature
                if target_class:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_class,
                        element_type='class'
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'], start + 10)
                        context_text = "\n".join(lines[start:end])
                        if end < element_info['end_line']:
                            context_text += "\n    // ... (rest of class)"
            
            else:
                # Unknown mode — use original behavior
                if target_class and target_method:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_method,
                        element_type='method',
                        parent_name=target_class
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
                
                if not context_text and target_function:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_function,
                        element_type='function'
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'])
                        context_text = "\n".join(lines[start:end])
                
                if not context_text and target_class:
                    element_info = parser.find_element(
                        source_code=file_content,
                        language=lang_lower,
                        element_name=target_class,
                        element_type='class'
                    )
                    if element_info:
                        start = max(0, element_info['start_line'] - 1)
                        end = min(len(lines), element_info['end_line'], start + 50)
                        context_text = "\n".join(lines[start:end])
            
            # === FALLBACK FOR NON-PYTHON: 200-line context around insertion_line ===
            if not context_text and insertion_line is not None:
                # Fall back to 200 lines above and below insertion_line
                start = max(0, insertion_line - 200)
                end = min(len(lines), insertion_line + 200)
                context_text = "\n".join(lines[start:end])
            
            # === FINAL FALLBACK: First 30 lines if no structural target and no insertion_line ===
            if not context_text and not target_class and not target_method and not target_function:
                if len(lines) > 30:
                    context_text = "\n".join(lines[:30]) + "\n... (truncated)"
                else:
                    context_text = file_content
            
            return {
                "surrounding_context": context_text,
                "target_indent": target_indent_value
            }
                    
        except Exception as e:
            logger.debug(f"Failed to extract {lang_lower} context with MultiLanguageParser: {e}")
            return empty_result
    
    # === FALLBACK: Return first 30 lines as context ===
    try:
        context_text = ""
        if len(lines) > 30:
            context_text = "\n".join(lines[:30]) + "\n... (truncated)"
        else:
            context_text = file_content
        
        return {
            "surrounding_context": context_text,
            "target_indent": None
        }
    except Exception:
        return empty_result

async def _call_syntax_fixer(code_block: str, language: str, context_info: Dict[str, Any], target_model: Optional[str] = None) -> Optional[str]:
    """Calls LLM to fix syntax. Supports direct model call or internal A->A->B cascade."""
    try:
        from app.llm.api_client import call_llm
        from config.settings import Config
    except ImportError:
        logger.error("LLM client or Config not available")
        return None

    system_prompt = _get_syntax_fix_prompt(language)
    user_message = _build_user_message(code_block, language, context_info)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    response = None

    # === 1. ПРЯМОЙ ВЫЗОВ (если target_model передан из Pipeline) ===
    if target_model:
        print(f"🤖 [AI SYNTAX FIXER] Direct call to {target_model}...")
        try:
            response = await call_llm(
                model=target_model,
                messages=messages,
                temperature=0.0,
                max_tokens=45000
            )
        except Exception as e:
            logger.warning(f"Direct call to {target_model} failed: {e}")
            response = None

    # === 2. ВНУТРЕННИЙ КАСКАД A -> A -> B ===
    else:
        MODEL_A = Config.MODEL_QWEN3_Coder_Next
        MODEL_B = Config.MODEL_NORMAL

        # Попытка А
        print(f"🤖 [AI SYNTAX FIXER] Calling primary model ({MODEL_A}) for syntax fix...")
        try:
            response = await call_llm(
                model=MODEL_A,
                messages=messages,
                temperature=0.0,
                max_tokens=45000
            )
        except Exception as e:
            logger.warning(f"Primary model A failed: {e}. Trying retry...")
            response = None

        # Если пусто — повтор А
        if not response or not response.strip():
            print(f"🤖 [AI SYNTAX FIXER] Model A returned empty/failed. Retrying ({MODEL_A})...")
            try:
                response = await call_llm(
                    model=MODEL_A,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=45000
                )
            except Exception as e:
                logger.warning(f"Model A retry failed: {e}. Switching to fallback...")
                response = None

        # Если все еще пусто — Модель Б
        if not response or not response.strip():
            print(f"⚠️ [AI SYNTAX FIXER] Switching to fallback ({MODEL_B})...")
            try:
                response = await call_llm(
                    model=MODEL_B,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=8192
                )
            except Exception as fe:
                logger.error(f"Fallback syntax fix model also failed: {fe}")
                print(f"❌ [AI SYNTAX FIXER] Fallback model also failed: {fe}. Syntax fix aborted.")
                return None

    # === НИЖЕ ВАША ИСХОДНАЯ ЛОГИКА БЕЗ ИЗМЕНЕНИЙ ===
    if not response:
        print(f"❌ [AI SYNTAX FIXER] Model returned empty response. Syntax fix aborted.")
        return None

    print(f"✅ [AI SYNTAX FIXER] Model returned a response, processing...")

    # Robust extraction using ###CODE### markers
    # Support for missing closing marker: if closing tag not found, take till end.
    start_tag = "###CODE###"
    end_tag = "###CODE###"

    start_idx = response.find(start_tag)
    if start_idx != -1:
        # Move index to after the start tag and skip optional newline
        content_start = start_idx + len(start_tag)
        if  content_start  < len(response) and response[content_start] ==  "\n":
            content_start += 1
            
        end_idx = response.find(end_tag, content_start)
        if end_idx != -1:
            cleaned = response[content_start:end_idx]
        else:
             # Missing closing tag: take everything from start_tag to EOF
            logger.warning( "AI SYNTAX FIXER: Missing closing ###CODE### tag, taking content to EOF. ")
            cleaned = response[content_start:]
    else:
        # Fallback: take raw response if markers not found
        cleaned = response

    return cleaned if cleaned else None


def attempt_ai_syntax_fix(code_block: str, language: str, context_info: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """Synchronous entry point for AI syntax correction."""
    try:
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if loop and loop.is_running():
            # Run in a separate thread to avoid "loop already running".
            # IMPORTANT: the coroutine must be created INSIDE the worker thread,
            # not in the calling thread, to avoid "Task attached to a different event loop".
            def _thread_runner():
                return asyncio.run(_call_syntax_fixer(code_block, language, context_info))
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_thread_runner)
                result = future.result()
        else:
            result = asyncio.run(_call_syntax_fixer(code_block, language, context_info))

        if result:
            return result, True
        return None, False
    except Exception as e:
        logger.error(f"Unexpected error in attempt_ai_syntax_fix: {e}")
        return None, False
    
    
def attempt_ai_syntax_fix_with_retries(
    code_block: str,
    language: str,
    context_info: Dict[str, Any],
    max_attempts: int = 3
) -> Tuple[Optional[str], bool]:
    """
    Retry wrapper for AI syntax fix with anti-cascade protection.

    Always passes the ORIGINAL code to each attempt (never the result of a
    previous failed attempt). Tracks failed versions to avoid accepting a
    fix that is identical to a previously failed one.

    Args:
        code_block: Original code block with syntax errors
        language: Programming language (python, javascript, etc.)
        context_info: Dict with mode, target_name, error_details, etc.
        max_attempts: Maximum number of fix attempts (default 3)

    Returns:
        Tuple of (fixed_code_or_None, success_bool)
    """
    try:
        original_code = code_block
        failed_versions: List[str] = []
        
        context_info["original_code"] = original_code
        
        for attempt in range(1, max_attempts + 1):
            context_info["attempt_number"] = attempt
            context_info["original_code"] = original_code
            
            fixed_code, success = attempt_ai_syntax_fix(original_code, language, context_info)
            
            if not success or not fixed_code:
                logger.debug(f"AI syntax fix attempt {attempt}/{max_attempts} returned empty/failed")
                continue
            
            if fixed_code in failed_versions:
                logger.warning(f"AI syntax fix attempt {attempt}/{max_attempts} produced same result as a previous failed attempt (anti-cascade skip)")
                continue
            
            if fixed_code == original_code:
                logger.debug(f"AI syntax fix attempt {attempt}/{max_attempts} returned unchanged code, skipping")
                failed_versions.append(fixed_code)
                continue
            
            logger.info(f"AI syntax fix succeeded on attempt {attempt}/{max_attempts}")
            return fixed_code, True
        
        logger.warning(f"AI syntax fix exhausted all {max_attempts} attempts")
        return None, False
        
    except Exception as e:
        logger.error(f"Unexpected error in attempt_ai_syntax_fix_with_retries: {e}")
        return None, False
