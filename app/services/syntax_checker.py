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
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass, field
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
    
    def __init__(
        self,
        use_black: bool = True,
        use_autopep8: bool = True,
        auto_fix_indent_only: bool = True,  # Исправлять ТОЛЬКО отступы
        max_line_length: int = 120,  # Не ограничиваем жёстко
    ):
        self.use_black = use_black
        self.use_autopep8 = use_autopep8
        self.auto_fix_indent_only = auto_fix_indent_only
        self.max_line_length = max_line_length
        
        # Проверяем доступность инструментов
        self._black_available = self._check_tool_available("black")
        self._autopep8_available = self._check_tool_available("autopep8")
        
        logger.debug(f"SyntaxChecker initialized: black={self._black_available}, autopep8={self._autopep8_available}")
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    def check_python(self, code: str, auto_fix: bool = False) -> SyntaxCheckResult:
        """
        Проверяет синтаксис Python кода.
        
        Args:
            code: Код для проверки
            auto_fix: Попытаться автоматически исправить критические проблемы
            
        Returns:
            SyntaxCheckResult с результатами проверки
        """
        result = SyntaxCheckResult(is_valid=True, original_content=code)
        
        # 0. Пытаемся исправить незакрытые скобки в импортах (до любых других проверок)
        if auto_fix:
            try:
                fixed_imports = self._fix_import_brackets(code)
                if fixed_imports != code:
                    code = fixed_imports
                    result.was_auto_fixed = True
                    result.fixed_content = fixed_imports
                    logger.info("Auto-fixed unclosed brackets in imports")
            except Exception as e:
                logger.warning(f"Failed to fix import brackets: {e}")
        
        # 1. Проверяем смешение табов и пробелов (до ast.parse)
        tab_space_issue = self._check_tab_space_mix(code)
        if tab_space_issue:
            result.issues.append(tab_space_issue)
            
            # Пытаемся исправить
            if auto_fix:
                fixed_code = self._fix_tab_space_mix(code)
                if fixed_code != code:
                    code = fixed_code
                    result.was_auto_fixed = True
                    result.fixed_content = fixed_code
                    logger.info("Auto-fixed tab/space mixing")
        
        # 2. Проверяем через ast.parse
        try:
            ast.parse(code)
            
        except IndentationError as e:
            issue = SyntaxIssue(
                issue_type=SyntaxIssueType.INDENTATION_ERROR,
                message=str(e.msg) if hasattr(e, 'msg') else str(e),
                line=e.lineno,
                column=e.offset,
                is_critical=True,
            )
            result.issues.append(issue)
            result.is_valid = False
            
            # Пытаемся исправить отступы
            if auto_fix:
                fixed_code = self._try_fix_indentation(code)
                if fixed_code:
                    # Проверяем, что исправление помогло
                    try:
                        ast.parse(fixed_code)
                        result.fixed_content = fixed_code
                        result.was_auto_fixed = True
                        result.is_valid = True
                        # Убираем issue, так как исправили
                        result.issues = [i for i in result.issues 
                                        if i.issue_type != SyntaxIssueType.INDENTATION_ERROR]
                        logger.info("Auto-fixed indentation error")
                    except SyntaxError:
                        pass  # Исправление не помогло
        
        except SyntaxError as e:
            issue = SyntaxIssue(
                issue_type=SyntaxIssueType.SYNTAX_ERROR,
                message=str(e.msg) if hasattr(e, 'msg') else str(e),
                line=e.lineno,
                column=e.offset,
                is_critical=True,
            )
            result.issues.append(issue)
            result.is_valid = False
        
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
    
    
    def _try_fix_indentation(self, code: str) -> Optional[str]:
        """
        Пытается исправить проблемы с отступами.
        Использует autopep8 с минимальными настройками.
        """
        # Сначала пробуем autopep8 (более консервативный)
        if self._autopep8_available and self.use_autopep8:
            fixed = self._run_autopep8_indent_only(code)
            if fixed:
                return fixed
        
        # Затем пробуем black (более агрессивный, но надёжный)
        if self._black_available and self.use_black:
            fixed = self._run_black_check_only(code)
            if fixed:
                return fixed
        
        # Пробуем простое исправление вручную
        return self._simple_indent_fix(code)
    
    def _run_autopep8_indent_only(self, code: str) -> Optional[str]:
        """
        Запускает autopep8 ТОЛЬКО для исправления отступов.
        
        autopep8 --select=E1 исправляет только:
        - E101: indentation contains mixed spaces and tabs
        - E111: indentation is not a multiple of four
        - E112: expected an indented block
        - E113: unexpected indentation
        - E114-E131: continuation line issues
        """
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', 
                                            delete=False, encoding='utf-8') as f:
                f.write(code)
                temp_path = f.name
            
            # --select=E1 — только ошибки индентации
            result = subprocess.run(
                ['autopep8', '--select=E1', '--max-line-length', str(self.max_line_length), temp_path],
                capture_output=True,
                text=True,
                encoding='utf-8',      # ✅ ДОБАВИТЬ
                errors='replace',      # ✅ ДОБАВИТЬ (опционально, для надёжности)
                timeout=10,
            )
            
            if result.returncode == 0:
                fixed = Path(temp_path).read_text(encoding='utf-8')
                return fixed if fixed != code else None
            
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"autopep8 failed: {e}")
        finally:
            try:
                Path(temp_path).unlink()
            except:
                pass
        
        return None
    
    def _run_black_check_only(self, code: str) -> Optional[str]:
        """
        Запускает black для проверки и исправления.
        Black более агрессивен, поэтому используем его как fallback.
        """
        try:
            result = subprocess.run(
                ['black', '--quiet', '--line-length', str(self.max_line_length), '-'],
                input=code,
                capture_output=True,
                text=True,
                encoding='utf-8',      # ✅ ДОБАВИТЬ
                errors='replace',      # ✅ ДОБАВИТЬ (опционально, для надёжности)
                timeout=10,
            )
            
            if result.returncode == 0 and result.stdout:
                return result.stdout
            
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"black failed: {e}")
        
        return None
    
    def _simple_indent_fix(self, code: str) -> Optional[str]:
        """
        Простое исправление отступов без внешних инструментов.
        Пытается нормализовать отступы к 4 пробелам.
        """
        lines = code.split('\n')
        fixed_lines = []
        
        for line in lines:
            if not line.strip():
                fixed_lines.append('')
                continue
            
            # Считаем ведущие пробелы/табы
            stripped = line.lstrip()
            leading = line[:len(line) - len(stripped)]
            
            # Конвертируем в пробелы
            spaces = 0
            for char in leading:
                if char == '\t':
                    spaces += 4
                elif char == ' ':
                    spaces += 1
            
            # Нормализуем к кратному 4
            normalized_spaces = (spaces // 4) * 4
            
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
        """Проверяет доступность внешнего инструмента"""
        try:
            result = subprocess.run(
                [tool, '--version'],
                capture_output=True,
                encoding='utf-8',      # ✅ ДОБАВИТЬ
                errors='replace',      # ✅ ДОБАВИТЬ (опционально, для надёжности)
                timeout=5,
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