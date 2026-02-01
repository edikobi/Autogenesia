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
        Пытается исправить синтаксические проблемы (включая отступы).
        Использует Tree-sitter (точечно для отступов), autopep8 и black.
        
        Returns:
            (fixed_code, tool_name) or (None, None)
        """
        def is_valid(c: str) -> bool:
            try:
                ast.parse(c)
                return True
            except SyntaxError:
                return False

        # 1. Tree-sitter Targeted Fix (Conservative, Structural)
        if error and isinstance(error, IndentationError):
            try:
                ts_fixed = self._fix_with_treesitter(code, error)
                if ts_fixed:
                    # A. Direct Success: Tree-sitter fixed it alone
                    if is_valid(ts_fixed):
                        return ts_fixed, "Tree-sitter"
                    
                    # B. "Make Fixable" Strategy: Chain with formatters
                    # Tree-sitter made it parseable-ish, now let Black/Autopep8 finish the job
                    logger.debug("[AUTO-FORMAT] Tree-sitter fix invalid, trying chain with Black/Autopep8")
                    
                    if self._black_available and self.use_black:
                        black_fixed = self._run_black_format(ts_fixed)
                        if black_fixed and is_valid(black_fixed):
                            return black_fixed, "Tree-sitter + Black"
                            
                    if self._autopep8_available and self.use_autopep8:
                        pep8_fixed = self._run_autopep8_indent_only(ts_fixed)
                        if pep8_fixed and is_valid(pep8_fixed):
                            return pep8_fixed, "Tree-sitter + autopep8"
                            
            except Exception as e:
                logger.debug(f"Tree-sitter fix failed: {e}")

        # 2. Autopep8 (Conservative -> Aggressive Indent)
        if self._autopep8_available and self.use_autopep8:
            fixed = self._run_autopep8_indent_only(code)
            if fixed and is_valid(fixed):
                return fixed, "autopep8"
        
        # 3. Black (Reliable but opinionated)
        if self._black_available and self.use_black:
            fixed = self._run_black_check_only(code)
            if fixed and is_valid(fixed):
                return fixed, "black"
        
        # 4. Yapf (Third opinion / Alternative algorithm)
        if self._yapf_available:
            fixed = self._run_yapf_fix(code)
            if fixed and is_valid(fixed):
                return fixed, "yapf"
        
        return None, None
    
    def _fix_with_treesitter(self, code: str, error: IndentationError) -> Optional[str]:
        """
        Structural indentation fix using Tree-sitter AST.
        Strictly deterministic: aligns code with its logical parent or siblings.
        NO textual heuristics.
        """
        if not error.lineno:
            return None

        # Detect indent size at the beginning
        indent_size, _ = self.detect_indent_style(code)

        # 1. Parse Structure
        ts_result = tree_sitter_parser.parse(code)
        if not ts_result.root_node:
            return None

        lines = code.split('\n')
        row = error.lineno - 1  # 0-indexed
        if row >= len(lines):
            return None

        # 2. Locate Node
        # We search at column 0 to capture the indentation/line context
        point = (row, 0)
        node = ts_result.root_node.descendant_for_point_range(point, point)
        if not node:
            return None

        target_indent = None

        # 3. Find Governing Scope (Block or Module)
        curr = node
        while curr:
            # CASE A: Top-level Module
            if curr.type == 'module':
                target_indent = 0
                break
            
            # CASE B: Code Block (suite)
            if curr.type == 'block':
                # Strategy: Match indentation of the first valid sibling
                valid_sibling_indent = None
                for child in curr.children:
                    # Ignore the error line itself, errors, and comments
                    if (child.start_point[0] != row and 
                        child.type != 'ERROR' and 
                        child.type != 'comment'):
                        valid_sibling_indent = child.start_point[1]
                        break
                
                if valid_sibling_indent is not None:
                    target_indent = valid_sibling_indent
                    break
                
                # Strategy: If no siblings, standard indent relative to parent
                # (e.g. parent is function_definition)
                if curr.parent:
                    parent_indent = curr.parent.start_point[1]
                    target_indent = parent_indent + indent_size
                    break

            curr = curr.parent

        # FALLBACK: Heuristic based on previous line
        # If structural analysis failed (e.g. ERROR node), try aligning with previous line
        if target_indent is None:
            prev_line_idx = row - 1
            while prev_line_idx >= 0:
                if lines[prev_line_idx].strip():
                    break
                prev_line_idx -= 1
            
            if prev_line_idx >= 0:
                prev_line = lines[prev_line_idx]
                prev_indent = len(prev_line) - len(prev_line.lstrip())
                stripped_prev = prev_line.strip()
                
                if stripped_prev.endswith(':'):
                    target_indent = prev_indent + indent_size
                else:
                    target_indent = prev_indent

        # 4. Apply Fix (if target found either structurally or heuristically)
        if target_indent is not None:
            line_text = lines[row]
            current_indent_len = len(line_text) - len(line_text.lstrip())
            
            if target_indent != current_indent_len:
                # Detect indent char from line or default to space
                indent_char = line_text[0] if line_text and line_text[0].isspace() else ' '
                if indent_char not in (' ', '\t'): 
                    indent_char = ' '
                
                new_line = (indent_char * target_indent) + line_text.lstrip()
                lines[row] = new_line
                return '\n'.join(lines)

        return None
    
    
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