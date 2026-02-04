# app/services/syntax_checker.py
"""
Syntax Checker для Agent Mode.

Проверка синтаксиса и минимальное автоформатирование кода.
Применяет black/autopep8 ТОЛЬКО при критических проблемах с отступами.

Поддерживает:
- Python (через ast.parse)
- JSON (через json.loads)
- Проверка отступов без изменения стиля
"""

from __future__ import annotations

import ast
import json
import re
import logging
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable
from dataclasses import dataclass, field
from app.services import tree_sitter_parser
from enum import Enum

logger = logging.getLogger(__name__)


class SyntaxIssueType(Enum):
    """Типы синтаксических проблем"""
    SYNTAX_ERROR = "syntax_error"
    INDENTATION_ERROR = "indentation_error"
    TAB_SPACE_MIX = "tab_space_mix"
    ENCODING_ERROR = "encoding_error"
    JSON_ERROR = "json_error"


@dataclass
class SyntaxIssue:
    """Описание синтаксической проблемы"""
    issue_type: SyntaxIssueType
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    suggestion: Optional[str] = None
    is_critical: bool = True  # Критические проблемы блокируют выполнение


@dataclass
class SyntaxCheckResult:
    """Результат проверки синтаксиса"""
    is_valid: bool
    issues: List[SyntaxIssue] = field(default_factory=list)
    fixed_content: Optional[str] = None  # Исправленный код (если применялось автоформатирование)
    was_auto_fixed: bool = False
    original_content: str = ""
    applied_fixes: List[str] = field(default_factory=list)  # Список применённых исправлений
    attempted_fixes: List[str] = field(default_factory=list)  # Список опробованных стратегий

    
    @property
    def has_critical_issues(self) -> bool:
        """Есть ли критические проблемы"""
        return any(issue.is_critical for issue in self.issues)
    
    @property
    def error_summary(self) -> str:
        """Краткая сводка ошибок"""
        if not self.issues:
            return "No issues"
        
        critical = [i for i in self.issues if i.is_critical]
        warnings = [i for i in self.issues if not i.is_critical]
        
        parts = []
        if critical:
            parts.append(f"{len(critical)} error(s)")
        if warnings:
            parts.append(f"{len(warnings)} warning(s)")
        
        return ", ".join(parts)


class SyntaxChecker:
    """
    Проверяет синтаксис кода и применяет минимальные исправления.
    
    Принципы:
    - Black/autopep8 используются ТОЛЬКО для критических проблем с отступами
    - Стилистические проблемы игнорируются
    - Сохраняем оригинальный стиль кода насколько возможно
    
    Example:
        >>> checker = SyntaxChecker()
        >>> result = checker.check_python("def foo():\n  return 1")
        >>> print(result.is_valid)
        True
    """
    
    def __init__(self, use_black: bool = True, use_autopep8: bool = True, auto_fix_indent_only: bool = True, max_line_length: int = 120, project_python_path: Optional[str] = None):
        """Initialize SyntaxChecker with formatting tool preferences."""
        self.use_black = use_black
        self.use_autopep8 = use_autopep8
        self.auto_fix_indent_only = auto_fix_indent_only
        self.max_line_length = max_line_length
        
        # Store project python path for tool resolution
        self._project_python_path = project_python_path
        
        # Check tool availability
        self._black_available = self._check_tool_available("black")
        self._autopep8_available = self._check_tool_available("autopep8")
        self._isort_available = self._check_tool_available("isort")
        self._yapf_available = self._check_tool_available("yapf")
        self._inserted_block_info: Optional[Dict[str, Any]] = None
        
        logger.debug(
            f"SyntaxChecker initialized: black={self._black_available}, "
            f"autopep8={self._autopep8_available}, isort={self._isort_available}, "
            f"yapf={self._yapf_available}"
        )
    
    def set_project_python_path(self, path: str) -> None:
        """
        Set project Python path and re-check tool availability.
        
        This allows updating the Python path after initialization,
        which is useful when the SyntaxChecker is created before
        the project path is known.
        
        Args:
            path: Path to the project's Python interpreter
        """
        self._project_python_path = path
        
        # Re-check tool availability with new path
        self._black_available = self._check_tool_available("black")
        self._autopep8_available = self._check_tool_available("autopep8")
        self._isort_available = self._check_tool_available("isort")
        self._yapf_available = self._check_tool_available("yapf")
        
        logger.debug(
            f"SyntaxChecker updated with project path: black={self._black_available}, "
            f"autopep8={self._autopep8_available}, isort={self._isort_available}, "
            f"yapf={self._yapf_available}"
        )
    
    
    def set_inserted_block_info(self, block_info: Dict[str, Any]) -> None:
        """
        Set information about the last inserted code block.
        
        This allows targeted fixes on just the inserted block rather than
        the entire method/function.
        
        Args:
            block_info: Dict with keys:
                - start_line: 0-indexed start line of inserted block
                - end_line: 0-indexed end line (exclusive)
                - code: The inserted code
                - target_indent: Expected indentation level
        """
        self._inserted_block_info = block_info
        logger.debug(f"Set inserted block info: lines {block_info.get('start_line')}-{block_info.get('end_line')}")
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    def check_python(self, code: str, auto_fix: bool = False) -> SyntaxCheckResult:
        """
        Проверяет синтаксис Python кода и опционально пытается исправить.
        
        Args:
            code: Python код для проверки
            auto_fix: Если True, пытается автоматически исправить синтаксические ошибки
            
        Returns:
            SyntaxCheckResult с результатами проверки и попытками исправления
        """
        # 1. Попытка парсить исходный код
        try:
            ast.parse(code)
            logger.info("[SYNTAX] Code is valid")
            return SyntaxCheckResult(is_valid=True, original_content=code)
        except SyntaxError as e:
            initial_error = e
            logger.warning(f"[SYNTAX] Syntax error at line {e.lineno}: {e.msg}")
        
        # Если auto_fix отключен, возвращаем ошибку
        if not auto_fix:
            return self._create_error_result(code, initial_error)
        
        # 2. Инициализируем стратегии исправления
        strategies = self._get_fix_strategies(initial_error)
        
        # 3. Try-Revert Loop
        attempted = []
        for name, strategy in strategies:
            attempted.append(name)
            try:
                fixed = strategy(code)
                
                # If tool returned None or didn't change anything, skip
                if not fixed or fixed == code:
                    continue
                    
                # Validate the fix
                ast.parse(fixed)
                
                # If we get here, it's valid!
                logger.info(f"[AUTO-FORMAT] SUCCESS: Fixed syntax using {name}")
                result = SyntaxCheckResult(is_valid=True, original_content=code)
                result.fixed_content = fixed
                result.was_auto_fixed = True
                result.applied_fixes.append(f"Fixed using {name}")
                result.attempted_fixes = attempted  # Record history
                return result
                    
            except SyntaxError:
                # Fix failed to produce valid code, continue to next strategy
                logger.debug(f"[AUTO-FORMAT] {name} failed to fix syntax error")
                continue
            except Exception as ex:
                logger.warning(f"[AUTO-FORMAT] Strategy {name} crashed: {ex}")
                continue

        # 4. All failed
        logger.warning(f"[AUTO-FORMAT] FAILED: Could not fix syntax error. Returning original error.")
        error_result = self._create_error_result(code, initial_error)
        error_result.attempted_fixes = attempted
        return error_result
    
    def _get_fix_strategies(self, error: Optional[SyntaxError] = None) -> List[Tuple[str, Callable[[str], Optional[str]]]]:
            """Returns the ordered list of fix strategies to try."""
            strategies: List[Tuple[str, Callable[[str], Optional[str]]]] = []
            
            # Strategy A: Fix Import Brackets
            strategies.append(("Import Brackets", self._fix_import_brackets))
            
            # Strategy B: Isort (if available)
            if self._isort_available:
                strategies.append(("isort", self._run_isort_fix))
                
            # Strategy C: Tab/Space Mix
            strategies.append(("Tab/Space Fix", self._fix_tab_space_mix))
            
            # Strategy D: Indentation Tools (Black/Autopep8/Yapf)
            # We capture 'error' to help Tree-sitter find the context
            strategies.append(("Indentation Tools", lambda c: self._try_fix_indentation(c, error)[0]))
            
            # Strategy E: Naive Indent Fix
            strategies.append(("Naive Indent Fix", self._simple_indent_fix))
            
            return strategies
    
    def _create_error_result(self, code: str, error: SyntaxError) -> SyntaxCheckResult:
        """Helper to create a failed result from a SyntaxError."""
        result = SyntaxCheckResult(is_valid=False, original_content=code)
        
        # Handle both SyntaxError and IndentationError
        is_indentation = isinstance(error, IndentationError)
        issue_type = SyntaxIssueType.INDENTATION_ERROR if is_indentation else SyntaxIssueType.SYNTAX_ERROR
        
        issue = SyntaxIssue(
            issue_type=issue_type,
            message=str(error.msg) if hasattr(error, 'msg') else str(error),
            line=error.lineno,
            column=error.offset,
            is_critical=True,
        )
        result.issues.append(issue)
        return result
    
    def check_json(self, content: str) -> SyntaxCheckResult:
        """
        Проверяет синтаксис JSON.
        
        Args:
            content: JSON-строка для проверки
            
        Returns:
            SyntaxCheckResult с результатами проверки
        """
        result = SyntaxCheckResult(is_valid=True, original_content=content)
        
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            issue = SyntaxIssue(
                issue_type=SyntaxIssueType.JSON_ERROR,
                message=e.msg,
                line=e.lineno,
                column=e.colno,
                is_critical=True,
            )
            result.issues.append(issue)
            result.is_valid = False
        
        return result
    
    def check_by_extension(self, content: str, file_path: str, 
                          auto_fix: bool = False) -> SyntaxCheckResult:
        """
        Проверяет синтаксис по расширению файла.
        
        Args:
            content: Содержимое файла
            file_path: Путь к файлу (для определения типа)
            auto_fix: Попытаться исправить критические проблемы
            
        Returns:
            SyntaxCheckResult с результатами проверки
        """
        ext = Path(file_path).suffix.lower()
        
        if ext == '.py':
            return self.check_python(content, auto_fix=auto_fix)
        elif ext == '.json':
            return self.check_json(content)
        else:
            # Для неизвестных типов — просто возвращаем "валидно"
            return SyntaxCheckResult(is_valid=True, original_content=content)
    
    def validate_and_fix(self, code: str) -> Tuple[bool, str, List[str]]:
        """
        Комплексная проверка и исправление Python кода.
        
        Returns:
            (is_valid, fixed_code_or_original, list_of_error_messages)
        """
        result = self.check_python(code, auto_fix=True)
        
        errors = [f"Line {i.line}: {i.message}" if i.line else i.message 
                  for i in result.issues if i.is_critical]
        
        final_code = result.fixed_content if result.was_auto_fixed else code
        
        return result.is_valid, final_code, errors
    
    # ========================================================================
    # INDENTATION FIXES
    # ========================================================================
    
    def _check_tab_space_mix(self, code: str) -> Optional[SyntaxIssue]:
        """Проверяет смешение табов и пробелов"""
        lines = code.split('\n')
        
        has_tabs = False
        has_spaces = False
        problem_line = None
        
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            
            leading = line[:len(line) - len(line.lstrip())]
            
            if '\t' in leading:
                has_tabs = True
            if ' ' in leading:
                has_spaces = True
            
            # Проверяем смешение в одной строке
            if '\t' in leading and ' ' in leading:
                problem_line = i
                break
        
        if has_tabs and has_spaces and problem_line:
            return SyntaxIssue(
                issue_type=SyntaxIssueType.TAB_SPACE_MIX,
                message="Mixed tabs and spaces in indentation",
                line=problem_line,
                is_critical=True,
                suggestion="Convert all indentation to spaces (recommended) or tabs",
            )
        
        return None
    
    def _fix_tab_space_mix(self, code: str) -> str:
        """Конвертирует все табы в пробелы (4 пробела на таб)"""
        lines = code.split('\n')
        fixed_lines = []
        
        for line in lines:
            if not line.strip():
                fixed_lines.append(line)
                continue
            
            # Обрабатываем только ведущие отступы
            stripped = line.lstrip()
            leading = line[:len(line) - len(stripped)]
            
            # Заменяем табы на 4 пробела
            fixed_leading = leading.replace('\t', '    ')
            fixed_lines.append(fixed_leading + stripped)
        
        return '\n'.join(fixed_lines)
    
    
    def _fix_import_brackets(self, code: str) -> str:
        """
        Исправляет незакрытые скобки в многострочных импортах.
        
        Обрабатывает случаи типа:
            from module import (
                Class1,
                Class2
            # <- отсутствует )
            
        Returns:
            Исправленный код или оригинал если исправление не требуется
        """
        lines = code.split('\n')
        result_lines = []
        
        in_multiline_import = False
        import_start_line = -1
        paren_depth = 0
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Проверяем начало многострочного импорта
            if not in_multiline_import:
                if (stripped.startswith('from ') or stripped.startswith('import ')) and '(' in stripped:
                    # Считаем скобки в этой строке
                    open_count = stripped.count('(')
                    close_count = stripped.count(')')
                    
                    if open_count > close_count:
                        in_multiline_import = True
                        import_start_line = i
                        paren_depth = open_count - close_count
                
                result_lines.append(line)
                i += 1
                continue
            
            # Внутри многострочного импорта
            open_count = stripped.count('(')
            close_count = stripped.count(')')
            paren_depth += open_count - close_count
            
            # Проверяем, закрылся ли импорт
            if paren_depth <= 0:
                in_multiline_import = False
                result_lines.append(line)
                i += 1
                continue
            
            # Проверяем, не начался ли новый statement (значит импорт не закрыт)
            next_is_statement = False
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].strip()
                # Новый statement начинается с ключевого слова на уровне 0 отступа
                if next_stripped and not next_stripped.startswith('#'):
                    next_line_indent = len(lines[i + 1]) - len(lines[i + 1].lstrip())
                    # Если следующая строка на уровне 0 и это statement
                    if next_line_indent == 0 and (
                        next_stripped.startswith(('def ', 'class ', 'import ', 'from ', '@', 'if ', 'for ', 'while ', 'try:', 'with '))
                        or (next_stripped and next_stripped[0].isupper() and '=' in next_stripped)  # Константа
                        or (next_stripped and not next_stripped.startswith((')', ',', '#')))
                    ):
                        next_is_statement = True
            
            # Если следующая строка — новый statement, а мы всё ещё в импорте
            if next_is_statement and paren_depth > 0:
                # Добавляем закрывающую скобку
                # Определяем отступ для скобки (такой же как у from/import)
                import_line = lines[import_start_line]
                import_indent = len(import_line) - len(import_line.lstrip())
                
                result_lines.append(line)
                result_lines.append(' ' * import_indent + ')')
                
                in_multiline_import = False
                paren_depth = 0
                logger.debug(f"Added missing ')' after line {i + 1}")
                i += 1
                continue
            
            result_lines.append(line)
            i += 1
        
        # Если файл закончился, а импорт не закрыт
        if in_multiline_import and paren_depth > 0:
            import_line = lines[import_start_line]
            import_indent = len(import_line) - len(import_line.lstrip())
            result_lines.append(' ' * import_indent + ')')
            logger.debug(f"Added missing ')' at end of file")
        
        return '\n'.join(result_lines)
    
    
    def _try_fix_indentation(self, code: str, error: Optional[SyntaxError] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Пытается исправить синтаксические проблемы используя все доступные инструменты.
        
        ВАЖНО: Каждый инструмент работает с ОРИГИНАЛЬНЫМ кодом.
        Никаких цепочек - если инструмент не дал валидный результат, его результат отбрасывается.
        
        Порядок попыток:
        1. Tree-sitter (структурное исправление)
        2. autopep8 (консервативное исправление отступов)
        3. Black (форматирование)
        4. yapf (альтернативный форматтер)
        
        Returns:
            (fixed_code, tool_name) or (None, None)
        """
        def is_valid(c: str) -> bool:
            try:
                ast.parse(c)
                return True
            except SyntaxError:
                return False

        # 1. Tree-sitter Targeted Fix (for ANY error, not just IndentationError)
        # Tree-sitter can fix structural issues beyond just indentation
        if error:
            try:
                ts_fixed = self._fix_with_treesitter(code, error)
                if ts_fixed and is_valid(ts_fixed):
                    logger.debug("[AUTO-FORMAT] Tree-sitter fix succeeded")
                    return ts_fixed, "Tree-sitter"
                # If ts_fixed is invalid, discard it completely - no chaining
            except Exception as e:
                logger.debug(f"Tree-sitter fix failed: {e}")

        # 2. Autopep8 (Conservative indent-only fixes) - works on ORIGINAL code
        if self._autopep8_available and self.use_autopep8:
            try:
                fixed = self._run_autopep8_indent_only(code)
                if fixed and is_valid(fixed):
                    logger.debug("[AUTO-FORMAT] autopep8 fix succeeded")
                    return fixed, "autopep8"
                # If invalid, discard - no chaining
            except Exception as e:
                logger.debug(f"autopep8 fix failed: {e}")
        
        # 3. Black (Reliable formatter) - works on ORIGINAL code
        if self._black_available and self.use_black:
            try:
                fixed = self._run_black_check_only(code)
                if fixed and is_valid(fixed):
                    logger.debug("[AUTO-FORMAT] Black fix succeeded")
                    return fixed, "Black"
                # If invalid, discard - no chaining
            except Exception as e:
                logger.debug(f"Black fix failed: {e}")
        
        # 4. Yapf (Alternative formatter) - works on ORIGINAL code
        if self._yapf_available:
            try:
                fixed = self._run_yapf_fix(code)
                if fixed and is_valid(fixed):
                    logger.debug("[AUTO-FORMAT] yapf fix succeeded")
                    return fixed, "yapf"
                # If invalid, discard - no chaining
            except Exception as e:
                logger.debug(f"yapf fix failed: {e}")
        
        return None, None
    
    def _fix_with_treesitter(self, code: str, error: SyntaxError) -> Optional[str]:
        """
        Attempts to fix syntax errors using Tree-sitter strategies.
        
        Strategies (in order):
        0. Shift inserted block to target_indent (if block info available)
        0.1. autopep8 on inserted block
        0.2. black on inserted block
        0.3. yapf on inserted block
        1. Fix missing colons
        2. Fix indentation errors
        3. Fix unclosed brackets
        """
        if not self._ts_parser:
            return None
        
        lines = code.splitlines(keepends=True)
        
        # === STRATEGY 0: Shift inserted block to target_indent ===
        if self._inserted_block_info:
            block_start = self._inserted_block_info.get("start_line")
            block_end = self._inserted_block_info.get("end_line")
            target_indent = self._inserted_block_info.get("target_indent", 0)
            original_block_code = self._inserted_block_info.get("code", "")
            
            if block_start is not None and block_end is not None and 0 <= block_start < len(lines):
                logger.debug(f"Strategy 0: Shifting block [{block_start}, {block_end}) to indent {target_indent}")
                
                # Extract block lines
                block_lines = lines[block_start:block_end]
                
                if block_lines:
                    # Find minimum indentation in block
                    min_indent = float('inf')
                    for line in block_lines:
                        stripped = line.lstrip()
                        if stripped and stripped != '\n':
                            current_indent = len(line) - len(stripped)
                            min_indent = min(min_indent, current_indent)
                    
                    if min_indent == float('inf'):
                        min_indent = 0
                    
                    # Rebuild block with shifted indentation
                    rebuilt_lines = []
                    for line in block_lines:
                        stripped = line.strip()
                        
                        if not stripped:
                            rebuilt_lines.append('\n')
                            continue
                        
                        current_indent = len(line) - len(line.lstrip())
                        relative_indent = current_indent - min_indent
                        new_indent = target_indent + relative_indent
                        
                        rebuilt_lines.append(' ' * new_indent + stripped + '\n')
                    
                    rebuilt_code = ''.join(rebuilt_lines)
                    new_lines = lines[:block_start] + [rebuilt_code] + lines[block_end:]
                    result_code = ''.join(new_lines)
                    
                    # Validate result
                    try:
                        compile(result_code, '<string>', 'exec')
                        logger.info("Strategy 0: Fixed via block shift")
                        self._inserted_block_info = None
                        return result_code
                    except SyntaxError:
                        logger.debug("Strategy 0: Block shift did not fix syntax")
        
        # === STRATEGY 0.1: autopep8 on inserted block ===
        if self._inserted_block_info and self._autopep8_available:
            block_start = self._inserted_block_info.get("start_line")
            block_end = self._inserted_block_info.get("end_line")
            target_indent = self._inserted_block_info.get("target_indent", 0)
            original_block_code = self._inserted_block_info.get("code", "")
            
            if block_start is not None and block_end is not None:
                logger.debug("Strategy 0.1: Trying autopep8 on inserted block")
                
                result = self._strategy_block_autopep8(code, lines, block_start, block_end, target_indent, original_block_code)
                if result:
                    try:
                        compile(result, '<string>', 'exec')
                        logger.info("Strategy 0.1: Fixed via autopep8 on block")
                        self._inserted_block_info = None
                        return result
                    except SyntaxError:
                        logger.debug("Strategy 0.1: autopep8 on block did not fix syntax")
        
        # === STRATEGY 0.2: black on inserted block ===
        if self._inserted_block_info and self._black_available:
            block_start = self._inserted_block_info.get("start_line")
            block_end = self._inserted_block_info.get("end_line")
            target_indent = self._inserted_block_info.get("target_indent", 0)
            original_block_code = self._inserted_block_info.get("code", "")
            
            if block_start is not None and block_end is not None:
                logger.debug("Strategy 0.2: Trying black on inserted block")
                
                result = self._strategy_block_black(code, lines, block_start, block_end, target_indent, original_block_code)
                if result:
                    try:
                        compile(result, '<string>', 'exec')
                        logger.info("Strategy 0.2: Fixed via black on block")
                        self._inserted_block_info = None
                        return result
                    except SyntaxError:
                        logger.debug("Strategy 0.2: black on block did not fix syntax")
        
        # === STRATEGY 0.3: yapf on inserted block ===
        if self._inserted_block_info and self._yapf_available:
            block_start = self._inserted_block_info.get("start_line")
            block_end = self._inserted_block_info.get("end_line")
            target_indent = self._inserted_block_info.get("target_indent", 0)
            original_block_code = self._inserted_block_info.get("code", "")
            
            if block_start is not None and block_end is not None:
                logger.debug("Strategy 0.3: Trying yapf on inserted block")
                
                result = self._strategy_block_yapf(code, lines, block_start, block_end, target_indent, original_block_code)
                if result:
                    try:
                        compile(result, '<string>', 'exec')
                        logger.info("Strategy 0.3: Fixed via yapf on block")
                        self._inserted_block_info = None
                        return result
                    except SyntaxError:
                        logger.debug("Strategy 0.3: yapf on block did not fix syntax")
        
        # Clear block info before falling back to other strategies
        self._inserted_block_info = None
        
        # === STRATEGY 1: Fix missing colons ===
        logger.debug("Strategy 1: Checking for missing colons")
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if stripped and not stripped.startswith('#'):
                # Check if line should end with colon
                if any(stripped.startswith(kw) for kw in ('if ', 'elif ', 'else:', 'for ', 'while ', 'def ', 'class ', 'with ', 'try:', 'except', 'finally:')):
                    if not stripped.endswith(':'):
                        lines[i] = line.rstrip() + ':\n'
                        result_code = ''.join(lines)
                        try:
                            compile(result_code, '<string>', 'exec')
                            logger.info(f"Strategy 1: Fixed missing colon at line {i + 1}")
                            return result_code
                        except SyntaxError:
                            lines[i] = line  # Restore
        
        # === STRATEGY 2: Fix indentation errors ===
        logger.debug("Strategy 2: Checking for indentation errors")
        
        try:
            # Try to detect and fix indentation
            result_code = self._fix_indentation_errors(code)
            if result_code != code:
                compile(result_code, '<string>', 'exec')
                logger.info("Strategy 2: Fixed indentation errors")
                return result_code
        except SyntaxError:
            pass
        
        # === STRATEGY 3: Fix unclosed brackets ===
        logger.debug("Strategy 3: Checking for unclosed brackets")
        
        try:
            result_code = self._fix_unclosed_brackets(code)
            if result_code != code:
                compile(result_code, '<string>', 'exec')
                logger.info("Strategy 3: Fixed unclosed brackets")
                return result_code
        except SyntaxError:
            pass
        
        logger.debug("All Tree-sitter strategies failed")
        return None
    
    
    def _find_containing_element(self, code: str, error_line_idx: int, parser) -> Optional[Tuple[int, int, int, str]]:
        """
        Find the method/function containing the error line.
        
        Returns:
            Tuple of (start_line_idx, end_line_idx, indent, element_name) or None
            Lines are 0-indexed.
        """
        try:
            parse_result = parser.parse(code)
        except Exception as e:
            logger.debug(f"Tree-sitter parse failed: {e}")
            return None
        
        # Check all functions (top-level)
        for func in parse_result.functions:
            # span is 1-indexed, convert to 0-indexed
            start_idx = func.span.start_line - 1
            end_idx = func.span.end_line  # end_line is inclusive, so this is correct for slicing
            
            if start_idx <= error_line_idx < end_idx:
                return (start_idx, end_idx, func.indent, func.name)
        
        # Check all methods in classes
        for cls in parse_result.classes:
            for method in cls.methods:
                start_idx = method.span.start_line - 1
                end_idx = method.span.end_line
                
                if start_idx <= error_line_idx < end_idx:
                    return (start_idx, end_idx, method.indent, f"{cls.name}.{method.name}")
        
        # Fallback: check if error is in a class body (not in a method)
        for cls in parse_result.classes:
            start_idx = cls.span.start_line - 1
            end_idx = cls.span.end_line
            
            if start_idx <= error_line_idx < end_idx:
                return (start_idx, end_idx, cls.indent, cls.name)
        
        return None
    
    def _is_code_line(self, line: str) -> bool:
        """
        Check if line is actual code (not empty, not comment-only).
        
        Returns:
            True if line contains actual code, False if empty or comment-only
        """
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith('#'):
            return False
        return True
    
    def _strategy_simple_shift(self, code: str, lines: List[str], block_start: int, block_end: int, target_indent: int, original_block_code: str) -> Optional[str]:
        """
        Strategy 0: Simple shift of inserted block to target_indent.
        Preserves relative indentation within the block.
        
        Process:
        1. Find minimum indentation in the block (base indent)
        2. Shift all lines so that base indent becomes target_indent
        3. Preserve relative indentation within the block
        """
        block_lines = original_block_code.splitlines(keepends=True)
        
        if not block_lines:
            return None
        
        # Find minimum indentation among non-empty lines
        min_indent = float('inf')
        for line in block_lines:
            stripped = line.lstrip()
            if stripped and stripped != '\n':
                current_indent = len(line) - len(stripped)
                min_indent = min(min_indent, current_indent)
        
        if min_indent == float('inf'):
            min_indent = 0
        
        # Rebuild block with shifted indentation
        rebuilt_lines = []
        for line in block_lines:
            stripped = line.strip()
            
            if not stripped:
                rebuilt_lines.append('\n')
                continue
            
            # Calculate relative indent from minimum
            current_indent = len(line) - len(line.lstrip())
            relative_indent = current_indent - min_indent
            new_indent = target_indent + relative_indent
            
            rebuilt_lines.append(' ' * new_indent + stripped + '\n')
        
        rebuilt_code = ''.join(rebuilt_lines)
        
        # Replace in original code
        new_lines = lines[:block_start] + [rebuilt_code] + lines[block_end:]
        return ''.join(new_lines)
    
    
    def _strategy_block_treesitter_rebuild(self, code: str, lines: List[str], block_start: int, block_end: int, target_indent: int, original_block_code: str) -> Optional[str]:
        """
        Strategy 0.1: Tree-sitter rebuild of inserted block.
        Uses tree-sitter to analyze structure and rebuild with correct indentation.
        """
        block_lines = original_block_code.splitlines(keepends=True)
        
        if not block_lines:
            return None
        
        # Strip all indentation
        stripped_lines = []
        for line in block_lines:
            stripped = line.lstrip()
            stripped_lines.append(stripped if stripped else '\n')
        
        stripped_code = ''.join(stripped_lines)
        
        # Parse with tree-sitter
        try:
            parser = _get_tree_sitter_parser()
            if not parser:
                return None
            
            parse_result = parser.parse(stripped_code)
            if parse_result.has_errors:
                logger.debug("Strategy 0.1: Tree-sitter found errors in stripped block")
                # Continue anyway - we'll try to rebuild
        except Exception as e:
            logger.debug(f"Strategy 0.1: Tree-sitter parse failed: {e}")
            return None
        
        # Rebuild with correct indentation
        rebuilt_lines = []
        current_indent = target_indent
        indent_stack = [target_indent]
        
        for line in stripped_lines:
            stripped = line.strip()
            
            if not stripped:
                rebuilt_lines.append('\n')
                continue
            
            # Handle comments
            if stripped.startswith('#'):
                rebuilt_lines.append(' ' * current_indent + stripped + '\n')
                continue
            
            # Decrease indent for continuation keywords
            if stripped.startswith(('elif ', 'else:', 'except ', 'except:', 'finally:', 'case ')):
                if len(indent_stack) > 1:
                    indent_stack.pop()
                current_indent = indent_stack[-1]
            
            # Add line with current indent
            rebuilt_lines.append(' ' * current_indent + stripped + '\n')
            
            # Increase indent after block openers
            if stripped.endswith(':') and not stripped.startswith('#'):
                block_keywords = ('def ', 'class ', 'if ', 'elif ', 'else:', 'for ', 'while ', 
                                'try:', 'except ', 'except:', 'finally:', 'with ', 'async ', 'match ', 'case ')
                is_block_opener = any(stripped.startswith(kw) or stripped == kw.rstrip() for kw in block_keywords)
                if is_block_opener:
                    current_indent = current_indent + 4
                    indent_stack.append(current_indent)
        
        rebuilt_code = ''.join(rebuilt_lines)
        
        # Replace in original code
        new_lines = lines[:block_start] + [rebuilt_code] + lines[block_end:]
        return ''.join(new_lines)
    
    
    def _strategy_block_autopep8(self, code: str, lines: List[str], block_start: int, block_end: int, target_indent: int, original_block_code: str) -> Optional[str]:
        """
        Strategy 0.2: autopep8 on inserted block.
        Runs autopep8 on the block and then shifts to target_indent.
        """
        if not self._autopep8_available:
            logger.debug("Strategy 0.2: autopep8 not available")
            return None
        
        import subprocess
        
        try:
            if self._project_python_path:
                cmd = [self._project_python_path, '-m', 'autopep8', '-']
            else:
                cmd = ['autopep8', '-']
            
            result = subprocess.run(
                cmd,
                input=original_block_code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0 or not result.stdout:
                logger.debug(f"Strategy 0.2: autopep8 failed: {result.stderr}")
                return None
            
            fixed_block = result.stdout
            
        except subprocess.TimeoutExpired:
            logger.debug("Strategy 0.2: autopep8 timed out")
            return None
        except Exception as e:
            logger.debug(f"Strategy 0.2: autopep8 failed: {e}")
            return None
        
        # Now shift the fixed block to target_indent (same logic as Strategy 0)
        block_lines = fixed_block.splitlines(keepends=True)
        
        if not block_lines:
            return None
        
        # Find minimum indentation
        min_indent = float('inf')
        for line in block_lines:
            stripped = line.lstrip()
            if stripped and stripped != '\n':
                current_indent = len(line) - len(stripped)
                min_indent = min(min_indent, current_indent)
        
        if min_indent == float('inf'):
            min_indent = 0
        
        # Rebuild with shifted indentation
        rebuilt_lines = []
        for line in block_lines:
            stripped = line.strip()
            
            if not stripped:
                rebuilt_lines.append('\n')
                continue
            
            current_indent = len(line) - len(line.lstrip())
            relative_indent = current_indent - min_indent
            new_indent = target_indent + relative_indent
            
            rebuilt_lines.append(' ' * new_indent + stripped + '\n')
        
        rebuilt_code = ''.join(rebuilt_lines)
        
        # Replace in original code
        new_lines = lines[:block_start] + [rebuilt_code] + lines[block_end:]
        return ''.join(new_lines)
    
    def _strategy_block_yapf(self, code: str, lines: List[str], block_start: int, block_end: int, target_indent: int, original_block_code: str) -> Optional[str]:
        """
        Strategy 0.3: yapf on inserted block.
        Runs yapf on the block and then shifts to target_indent.
        """
        if not self._yapf_available:
            logger.debug("Strategy 0.3: yapf not available")
            return None
        
        import subprocess
        
        try:
            if self._project_python_path:
                cmd = [self._project_python_path, '-m', 'yapf', '--style=pep8']
            else:
                cmd = ['yapf', '--style=pep8']
            
            result = subprocess.run(
                cmd,
                input=original_block_code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0 or not result.stdout:
                logger.debug(f"Strategy 0.3: yapf failed: {result.stderr}")
                return None
            
            fixed_block = result.stdout
            
        except subprocess.TimeoutExpired:
            logger.debug("Strategy 0.3: yapf timed out")
            return None
        except Exception as e:
            logger.debug(f"Strategy 0.3: yapf failed: {e}")
            return None
        
        # Now shift the fixed block to target_indent (same logic as Strategy 0)
        block_lines = fixed_block.splitlines(keepends=True)
        
        if not block_lines:
            return None
        
        # Find minimum indentation
        min_indent = float('inf')
        for line in block_lines:
            stripped = line.lstrip()
            if stripped and stripped != '\n':
                current_indent = len(line) - len(stripped)
                min_indent = min(min_indent, current_indent)
        
        if min_indent == float('inf'):
            min_indent = 0
        
        # Rebuild with shifted indentation
        rebuilt_lines = []
        for line in block_lines:
            stripped = line.strip()
            
            if not stripped:
                rebuilt_lines.append('\n')
                continue
            
            current_indent = len(line) - len(line.lstrip())
            relative_indent = current_indent - min_indent
            new_indent = target_indent + relative_indent
            
            rebuilt_lines.append(' ' * new_indent + stripped + '\n')
        
        rebuilt_code = ''.join(rebuilt_lines)
        
        # Replace in original code
        new_lines = lines[:block_start] + [rebuilt_code] + lines[block_end:]
        return ''.join(new_lines)
    
    def _strategy_treesitter_rebuild(self, code: str, lines: List[str], element_start: int, element_end: int, element_indent: int, original_element_code: str) -> Optional[str]:
        """
        Strategy 1: Strip all indentation from element, rebuild line-by-line with tree-sitter.
        
        Process:
        1. Strip all leading whitespace from element lines
        2. Use tree-sitter to analyze structure and determine correct indentation
        3. Rebuild with correct indentation, preserving relative indentation
        4. Replace in original code
        """
        element_lines = original_element_code.splitlines(keepends=True)
        
        if not element_lines:
            return None
        
        # Step 1: Strip all indentation (shift left completely)
        stripped_lines = []
        for line in element_lines:
            stripped = line.lstrip()
            stripped_lines.append(stripped if stripped else '\n')
        
        stripped_code = ''.join(stripped_lines)
        
        # Step 2: Parse stripped code with tree-sitter to understand structure
        try:
            parser = _get_tree_sitter_parser()
            if not parser:
                return None
            
            parse_result = parser.parse(stripped_code)
            if not parse_result.root_node:
                return None
        except Exception:
            return None
        
        # Step 3: Rebuild with correct indentation based on structure
        rebuilt_lines = []
        current_indent = element_indent
        indent_stack = [element_indent]
        
        for line in stripped_lines:
            stripped = line.strip()
            
            # Handle empty lines
            if not stripped:
                rebuilt_lines.append('\n')
                continue
            
            # Handle comment-only lines: preserve relative position but don't affect indent tracking
            if stripped.startswith('#'):
                rebuilt_lines.append(' ' * current_indent + stripped + '\n')
                continue
            
            # Decrease indent for closing keywords (only for actual code, not comments)
            if stripped.startswith(('elif ', 'else:', 'except ', 'except:', 'finally:', 'case ')):
                if len(indent_stack) > 1:
                    indent_stack.pop()
                current_indent = indent_stack[-1]
            
            # Add line with current indent
            rebuilt_lines.append(' ' * current_indent + stripped + '\n')
            
            # Increase indent after block openers (only for actual code ending with :, not comments)
            # Check that line ends with : and is not a comment
            if stripped.endswith(':') and not stripped.startswith('#'):
                # Additional check: ensure it's a real block opener, not a string ending with :
                # Simple heuristic: check for common block keywords
                block_keywords = ('def ', 'class ', 'if ', 'elif ', 'else:', 'for ', 'while ', 
                                'try:', 'except ', 'except:', 'finally:', 'with ', 'async ', 'match ', 'case ')
                is_block_opener = any(stripped.startswith(kw) or stripped == kw.rstrip() for kw in block_keywords)
                if is_block_opener:
                    current_indent = current_indent + 4
                    indent_stack.append(current_indent)
        
        rebuilt_code = ''.join(rebuilt_lines)
        
        # Step 4: Replace in original code
        new_lines = lines[:element_start] + [rebuilt_code] + lines[element_end:]
        return ''.join(new_lines)
    
    
    
    def _strategy_combined_rebuild(self, code: str, lines: List[str], element_start: int, element_end: int, element_indent: int, original_element_code: str) -> Optional[str]:
        """
        Strategy 3: Strip all indentation, rebuild with autopep8 + tree-sitter combined.
        
        Process:
        1. Strip all leading whitespace from element lines
        2. Run autopep8 on stripped code (via subprocess)
        3. Use tree-sitter to verify structure
        4. Re-indent to match original element_indent
        5. Replace in original code
        """
        element_lines = original_element_code.splitlines(keepends=True)
        
        if not element_lines:
            return None
        
        # Step 1: Strip all indentation
        stripped_lines = []
        for line in element_lines:
            stripped = line.lstrip()
            stripped_lines.append(stripped if stripped else '\n')
        
        stripped_code = ''.join(stripped_lines)
        
        # Step 2: Run autopep8 on stripped code via subprocess
        if not self._autopep8_available:
            logger.debug("autopep8 not available for STRATEGY 3")
            return None
        
        try:
            if self._project_python_path:
                cmd = [self._project_python_path, '-m', 'autopep8', '-']
            else:
                cmd = ['autopep8', '-']
            
            result = subprocess.run(
                cmd,
                input=stripped_code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0 or not result.stdout:
                logger.debug(f"autopep8 failed in STRATEGY 3: {result.stderr}")
                return None
            
            fixed_code = result.stdout
            
        except subprocess.TimeoutExpired:
            logger.debug("autopep8 timed out in STRATEGY 3")
            return None
        except Exception as e:
            logger.debug(f"autopep8 failed in STRATEGY 3: {e}")
            return None
        
        # Step 3: Verify with tree-sitter
        try:
            parser = _get_tree_sitter_parser()
            if parser:
                parse_result = parser.parse(fixed_code)
                if parse_result.has_errors:
                    logger.debug("STRATEGY 3: Tree-sitter found errors in autopep8 output")
                    return None
        except Exception as e:
            logger.debug(f"Tree-sitter verification failed in STRATEGY 3: {e}")
            return None
        
        # Step 4: Re-indent to match original element_indent
        fixed_lines = fixed_code.splitlines(keepends=True)
        reindented_lines = []
        
        for line in fixed_lines:
            stripped = line.strip()
            
            # Handle empty lines
            if not stripped:
                reindented_lines.append('\n')
                continue
            
            # For all lines (including comments), calculate new indent
            current_indent = len(line) - len(line.lstrip())
            relative_indent = current_indent
            new_indent = element_indent + relative_indent
            reindented_lines.append(' ' * new_indent + line.lstrip())
        
        reindented_code = ''.join(reindented_lines)
        
        # Step 5: Replace in original code
        new_lines = lines[:element_start] + [reindented_code] + lines[element_end:]
        return ''.join(new_lines)
    
    def _strategy_autopep8_rebuild(self, code: str, lines: List[str], element_start: int, element_end: int, element_indent: int, original_element_code: str) -> Optional[str]:
        """
        Strategy 2: Strip all indentation from ORIGINAL element, rebuild with autopep8.
        
        Process:
        1. Strip all leading whitespace from element lines
        2. Run autopep8 on stripped code to fix indentation (via subprocess)
        3. Re-indent to match original element_indent
        4. Replace in original code
        """
        element_lines = original_element_code.splitlines(keepends=True)
        
        if not element_lines:
            return None
        
        # Step 1: Strip all indentation
        stripped_lines = []
        for line in element_lines:
            stripped = line.lstrip()
            stripped_lines.append(stripped if stripped else '\n')
        
        stripped_code = ''.join(stripped_lines)
        
        # Step 2: Run autopep8 on stripped code via subprocess
        if not self._autopep8_available:
            logger.debug("autopep8 not available for STRATEGY 2")
            return None
        
        try:
            if self._project_python_path:
                cmd = [self._project_python_path, '-m', 'autopep8', '-']
            else:
                cmd = ['autopep8', '-']
            
            result = subprocess.run(
                cmd,
                input=stripped_code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0 or not result.stdout:
                logger.debug(f"autopep8 failed in STRATEGY 2: {result.stderr}")
                return None
            
            fixed_code = result.stdout
            
        except subprocess.TimeoutExpired:
            logger.debug("autopep8 timed out in STRATEGY 2")
            return None
        except Exception as e:
            logger.debug(f"autopep8 failed in STRATEGY 2: {e}")
            return None
        
        # Step 3: Re-indent to match original element_indent
        fixed_lines = fixed_code.splitlines(keepends=True)
        reindented_lines = []
        
        for line in fixed_lines:
            stripped = line.strip()
            
            # Handle empty lines
            if not stripped:
                reindented_lines.append('\n')
                continue
            
            # For all lines (including comments), calculate new indent
            current_indent = len(line) - len(line.lstrip())
            relative_indent = current_indent
            new_indent = element_indent + relative_indent
            reindented_lines.append(' ' * new_indent + line.lstrip())
        
        reindented_code = ''.join(reindented_lines)
        
        # Step 4: Replace in original code
        new_lines = lines[:element_start] + [reindented_code] + lines[element_end:]
        return ''.join(new_lines)
    
    def _run_autopep8_indent_only(self, code: str) -> Optional[str]:
        """Run autopep8 with conservative indent-only fixes."""
        import subprocess
        
        # Build base args based on project_python_path
        if self._project_python_path:
            base_args = [self._project_python_path, '-m', 'autopep8', '--max-line-length', str(self.max_line_length)]
        else:
            base_args = ['autopep8', '--max-line-length', str(self.max_line_length)]
        
        # Conservative indent-only fixes (E1xx = indentation, W1xx = indentation warnings)
        try:
            args = base_args + ['--select=E1,W1', '-']
            result = subprocess.run(
                args,
                input=code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout:
                return result.stdout
            else:
                if result.stderr:
                    logger.debug(f"autopep8 indent-only returned non-zero: {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.warning("autopep8 indent-only timed out")
            return None
        except Exception as e:
            logger.debug(f"autopep8 indent-only failed: {e}")
            return None
    
    
    def _run_black_check_only(self, code: str) -> Optional[str]:
        """Run black formatter in check mode (no changes)."""
        import subprocess
        
        # Build command based on project_python_path
        if self._project_python_path:
            cmd = [self._project_python_path, '-m', 'black', '--quiet', '--line-length', str(self.max_line_length), '-']
        else:
            cmd = ['black', '--quiet', '--line-length', str(self.max_line_length), '-']
        
        try:
            result = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"Black formatting failed: {result.stderr}")
                return None
        
        except subprocess.TimeoutExpired:
            logger.warning("Black formatting timed out")
            return None
        except Exception as e:
            logger.warning(f"Black formatting error: {e}")
            return None
    
    def _run_black_format(self, code: str) -> Optional[str]:
        """Run black formatter (wrapper for chaining)."""
        return self._run_black_check_only(code)
    
    def _run_isort_fix(self, code: str) -> Optional[str]:
        """Run isort to fix import ordering."""
        import subprocess
        
        # Build command based on project_python_path
        if self._project_python_path:
            cmd = [self._project_python_path, '-m', 'isort', '-', '--profile', 'black']
        else:
            cmd = ['isort', '-', '--profile', 'black']
        
        try:
            result = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"isort failed: {result.stderr}")
                return None
        
        except subprocess.TimeoutExpired:
            logger.warning("isort timed out")
            return None
        except Exception as e:
            logger.warning(f"isort error: {e}")
            return None
    
    def _run_yapf_fix(self, code: str) -> Optional[str]:
        """Run yapf formatter."""
        import subprocess
        
        # Build command based on project_python_path
        if self._project_python_path:
            cmd = [self._project_python_path, '-m', 'yapf', '--style', 'pep8']
        else:
            cmd = ['yapf', '--style', 'pep8']
        
        try:
            result = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"yapf failed: {result.stderr}")
                return None
        
        except subprocess.TimeoutExpired:
            logger.warning("yapf timed out")
            return None
        except Exception as e:
            logger.warning(f"yapf error: {e}")
            return None
    
    def _simple_indent_fix(self, code: str) -> Optional[str]:
            """
            Простое исправление отступов без внешних инструментов.
            Пытается нормализовать отступы к сетке (используя определенный размер отступа).
            """
            indent_size, style = self.detect_indent_style(code)
            
            # Поддерживаем только пробелы для наивного исправления
            if style != "spaces":
                return None
                
            lines = code.split('\n')
            fixed_lines = []
            
            for line in lines:
                if not line.strip():
                    fixed_lines.append('')
                    continue
                
                stripped = line.lstrip()
                leading = line[:len(line) - len(stripped)]
                
                # Считаем эффективные пробелы (на случай смешения)
                spaces = leading.count(' ') + leading.count('\t') * indent_size
                
                # Нормализуем к ближайшему кратному indent_size
                if indent_size > 0:
                    # round() позволяет исправить "лишний пробел" (5 -> 4, 7 -> 8)
                    normalized_spaces = round(spaces / indent_size) * indent_size
                else:
                    normalized_spaces = spaces
                
                fixed_lines.append(' ' * normalized_spaces + stripped)
            
            fixed_code = '\n'.join(fixed_lines)
            
            # Проверяем, что исправление корректно
            try:
                ast.parse(fixed_code)
                return fixed_code
            except SyntaxError:
                return None
    
    # ========================================================================
    # UTILITIES
    # ========================================================================
    
    def _check_tool_available(self, tool: str) -> bool:
        """Check if a formatting tool is available."""
        import subprocess
        
        try:
            if self._project_python_path:
                # Use project's Python to check tool
                result = subprocess.run(
                    [self._project_python_path, "-m", tool, "--version"],
                    capture_output=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5
                )
            else:
                # Fallback to system tool
                result = subprocess.run(
                    [tool, '--version'],
                    capture_output=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5
                )
            
            return result.returncode == 0
        
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def get_line_at(self, code: str, line_number: int) -> Optional[str]:
        """Возвращает строку по номеру"""
        lines = code.split('\n')
        if 1 <= line_number <= len(lines):
            return lines[line_number - 1]
        return None
    
    def detect_indent_style(self, code: str) -> Tuple[int, str]:
        """
        Определяет стиль отступов в коде.
        
        Returns:
            (indent_size, "spaces" | "tabs" | "mixed")
        """
        lines = code.split('\n')
        
        space_indents = []
        has_tabs = False
        
        for line in lines:
            if not line.strip():
                continue
            
            leading = line[:len(line) - len(line.lstrip())]
            
            if '\t' in leading:
                has_tabs = True
            
            # Считаем пробелы в начале
            space_count = 0
            for char in leading:
                if char == ' ':
                    space_count += 1
                else:
                    break
            
            if space_count > 0:
                space_indents.append(space_count)
        
        # Определяем стиль
        if has_tabs and space_indents:
            return (4, "mixed")
        elif has_tabs:
            return (1, "tabs")
        elif space_indents:
            # Находим GCD всех отступов
            from math import gcd
            from functools import reduce
            indent_size = reduce(gcd, space_indents) if space_indents else 4
            return (indent_size, "spaces")
        else:
            return (4, "spaces")  # Default
    
    def __repr__(self) -> str:
        return f"SyntaxChecker(black={self._black_available}, autopep8={self._autopep8_available})"