# app/services/change_validator.py
"""
Change Validator для Agent Mode.

Многоуровневая валидация изменений кода через VirtualFileSystem.
Проверяет код ДО его реального применения к файлам.

Уровни валидации:
1. SYNTAX - синтаксис всех затронутых файлов
2. IMPORTS - все импорты разрешимы (stdlib, pip, project)
3. TYPES - проверка типов через mypy
4. INTEGRATION - совместимость с зависимыми файлами
5. RUNTIME - import без ошибок в subprocess
6. TESTS - запуск связанных тестов (test_*.py)
"""

from __future__ import annotations

import ast
import sys
import os
import re
import json
import logging
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum
from app.tools.dependency_manager import IMPORT_TO_PACKAGE, get_import_to_package_mapping
from app.services.runtime_tester import RuntimeTester, RuntimeTestSummary, TestStatus, AppType

import time

if TYPE_CHECKING:
    from app.services.virtual_fs import VirtualFileSystem, AffectedFiles




logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS & DATA STRUCTURES
# ============================================================================

class ValidationLevel(Enum):
    """Уровни валидации"""
    SYNTAX = "syntax"
    IMPORTS = "imports"
    TYPES = "types"
    INTEGRATION = "integration"
    RUNTIME = "runtime"
    TESTS = "tests"


class IssueSeverity(Enum):
    """Серьёзность проблемы"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """Проблема, обнаруженная при валидации"""
    level: ValidationLevel
    severity: IssueSeverity
    file_path: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    code: Optional[str] = None
    suggestion: Optional[str] = None
    
    def __str__(self) -> str:
        loc = f":{self.line}" if self.line else ""
        col = f":{self.column}" if self.column else ""
        return f"[{self.level.value}] {self.file_path}{loc}{col}: {self.message}"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "severity": self.severity.value,
            "file_path": self.file_path,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "suggestion": self.suggestion,
        }


@dataclass
class TestFileInfo:
    """Информация о найденном тестовом файле"""
    path: str
    related_to: str
    test_type: str
    priority: int


@dataclass
class ValidationResult:
    """Результат валидации"""
    success: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    checked_files: List[str] = field(default_factory=list)
    new_files: List[str] = field(default_factory=list)
    levels_passed: List[ValidationLevel] = field(default_factory=list)
    levels_failed: List[ValidationLevel] = field(default_factory=list)
    levels_skipped: List[ValidationLevel] = field(default_factory=list)
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    
    test_files_found: List[TestFileInfo] = field(default_factory=list)
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    
    # NEW: RUNTIME статистика
    runtime_files_checked: int = 0
    runtime_files_passed: int = 0
    runtime_files_failed: int = 0
    
    runtime_files_skipped: int = 0
    runtime_test_summary: Optional[Dict[str, Any]] = None  # RuntimeTestSummary.to_dict()
    
    auto_format_stats: Dict[str, Any] = field(default_factory=dict)

    
    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]
    
    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]
    
    @property
    def error_count(self) -> int:
        return len(self.errors)
    
    @property
    def warning_count(self) -> int:
        return len(self.warnings)
    
    def summary(self) -> str:
        parts = [f"{'✅ PASSED' if self.success else '❌ FAILED'}"]
        parts.append(f"Files: {len(self.checked_files)}")
        if self.errors:
            parts.append(f"Errors: {self.error_count}")
        if self.warnings:
            parts.append(f"Warnings: {self.warning_count}")
        if self.test_files_found:
            parts.append(f"Tests: {self.tests_passed}/{self.tests_run}")
        parts.append(f"Time: {self.duration_ms:.1f}ms")
        return " | ".join(parts)
    
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "issues": [i.to_dict() for i in self.issues],
            "checked_files": self.checked_files,
            "new_files": self.new_files,
            "levels_passed": [l.value for l in self.levels_passed],
            "levels_failed": [l.value for l in self.levels_failed],
            "levels_skipped": [l.value for l in self.levels_skipped],
            "duration_ms": self.duration_ms,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            # TESTS
            "test_files_found": [
                {"path": t.path, "related_to": t.related_to, "type": t.test_type}
                for t in self.test_files_found
            ],
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            # RUNTIME
            "runtime_files_checked": self.runtime_files_checked,
            "runtime_files_passed": self.runtime_files_passed,
            "runtime_files_failed": self.runtime_files_failed,
            "runtime_files_skipped": self.runtime_files_skipped,  # NEW
            "runtime_test_summary": self.runtime_test_summary,     # NEW
            "auto_format_stats": self.auto_format_stats,
        }


@dataclass
class ValidatorConfig:
    """Конфигурация валидатора"""
    enabled_levels: List[ValidationLevel] = field(default_factory=lambda: [
        ValidationLevel.SYNTAX,
        ValidationLevel.IMPORTS,
        ValidationLevel.TYPES,
        ValidationLevel.INTEGRATION,
        ValidationLevel.RUNTIME,
    ])
    
    # IMPORTS
    check_pip_packages: bool = True
    check_stdlib: bool = True
    check_project_modules: bool = True
    
    # TYPES (mypy)
    mypy_strict: bool = False
    mypy_ignore_missing_imports: bool = True
    
    # RUNTIME
    runtime_timeout_sec: int = 150
    
    # TESTS
    run_related_tests: bool = True
    test_timeout_sec: int = 750
    test_patterns: List[str] = field(default_factory=lambda: [
        "test_{name}.py",
        "{name}_test.py",
        "tests/test_{name}.py",
        "tests/{name}_test.py",
        "test/test_{name}.py",
    ])
    
    max_workers: int = 4
    fail_on_warnings: bool = False
    fail_on_test_failure: bool = True


# ============================================================================
# MAIN VALIDATOR CLASS
# ============================================================================

class ChangeValidator:
    """
    Многоуровневый валидатор изменений кода.
    Работает с VirtualFileSystem.
    """
    
    _STDLIB_MODULES: Optional[Set[str]] = None
    
    PLATFORM_SPECIFIC_MODULES: Set[str] = {
        # Windows
        'win32gui', 'win32process', 'win32api', 'win32con', 'win32com',
        'win32file', 'win32event', 'win32security', 'win32service',
        'win32pipe', 'win32net', 'win32print', 'win32clipboard',
        'pythoncom', 'pywintypes', 'wmi', 'winreg', 'msvcrt',
        'ctypes.wintypes',
        
        # macOS
        'AppKit', 'Foundation', 'Cocoa', 'CoreFoundation', 'CoreGraphics',
        'CoreServices', 'CoreText', 'objc', 'PyObjCTools',
        'Quartz', 'LaunchServices', 'ScriptingBridge',
        
        # Linux
        'Xlib', 'gi', 'dbus', 'apt', 'rpm',
        'xdg', 'pyudev', 'evdev',
        
        # Platform detection (always valid)
        'platform', 'sys',
    }    
    
    
    def __init__(
        self,
        vfs: 'VirtualFileSystem',
        config: Optional[ValidatorConfig] = None,
    ):
        self.vfs = vfs
        self.config = config or ValidatorConfig()
        
        self._pip_packages_cache: Optional[Set[str]] = None
        self._project_modules_cache: Optional[Set[str]] = None
        self._syntax_checker = None
        
        if ChangeValidator._STDLIB_MODULES is None:
            ChangeValidator._STDLIB_MODULES = self._get_stdlib_modules()
        
        logger.debug(f"ChangeValidator initialized with levels: {[l.value for l in self.config.enabled_levels]}")
    
    @property
    def syntax_checker(self):
        if self._syntax_checker is None:
            from app.services.syntax_checker import SyntaxChecker
            # Pass project python path to ensure formatting tools are found in project's venv
            project_python = self.vfs.get_project_python()
            self._syntax_checker = SyntaxChecker(project_python_path=project_python)
        return self._syntax_checker
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    async def validate(
        self,
        levels: Optional[List[ValidationLevel]] = None,
    ) -> ValidationResult:
        """Запускает валидацию по указанным уровням."""
        start_time = time.time()
        
        # NEW: Инвалидируем кэши перед каждой валидацией
        # Это критично для корректной работы с VFS между итерациями
        self._project_modules_cache = None
        self._pip_packages_cache = None
        
        levels_to_check = levels or self.config.enabled_levels
        
        result = ValidationResult(
            success=True,
            checked_files=[],
            levels_passed=[],
            levels_failed=[],
            levels_skipped=[],
        )
        
        # Получаем affected files
        affected = self.vfs.get_affected_files()
        all_files = list(affected.changed_files) + list(affected.new_files) + list(affected.dependent_files)
        result.checked_files = all_files
        result.new_files = list(affected.new_files)  # Сохраняем список новых файлов
        
        # Ищем тестовые файлы
        test_files = self._find_related_test_files(list(affected.changed_files))
        result.test_files_found = test_files
        
        if test_files:
            logger.info(f"Found {len(test_files)} related test files")
        
        if not all_files and not test_files:
            logger.info("No files to validate")
            result.duration_ms = (time.time() - start_time) * 1000
            return result
        
        logger.info(f"Validating {len(all_files)} files")
        
        # Проходим по уровням
        for level in ValidationLevel:
            if level not in levels_to_check:
                result.levels_skipped.append(level)
                continue
            
            if level not in self.config.enabled_levels:
                result.levels_skipped.append(level)
                continue
            
            logger.debug(f"Running {level.value} validation...")
            
            try:
                issues = await self._run_level(level, all_files, result)
                result.issues.extend(issues)
                
                level_errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
                
                if level_errors:
                    result.levels_failed.append(level)
                    result.success = False
                    
                    if level in (ValidationLevel.SYNTAX, ValidationLevel.IMPORTS):
                        logger.warning(f"Stopping validation due to {level.value} errors")
                        break
                else:
                    result.levels_passed.append(level)
                    
            except Exception as e:
                logger.error(f"Error in {level.value} validation: {e}", exc_info=True)
                result.issues.append(ValidationIssue(
                    level=level,
                    severity=IssueSeverity.ERROR,
                    file_path="<validator>",
                    message=f"Validation error: {e}",
                ))
                result.levels_failed.append(level)
                result.success = False
        
        result.duration_ms = (time.time() - start_time) * 1000
        
        if self.config.fail_on_warnings and result.warnings:
            result.success = False
        
        logger.info(f"Validation complete: {result.summary()}")
        
        return result

    
    async def validate_syntax_only(self) -> ValidationResult:
        return await self.validate(levels=[ValidationLevel.SYNTAX])
    
    async def validate_before_commit(self) -> ValidationResult:
        return await self.validate(levels=[
            ValidationLevel.SYNTAX,
            ValidationLevel.IMPORTS,
            ValidationLevel.TYPES,
            ValidationLevel.INTEGRATION,
        ])
    
    async def validate_full(self) -> ValidationResult:
        return await self.validate(levels=list(ValidationLevel))

    async def validate_quick(self) -> ValidationResult:
        """Быстрая валидация (только синтаксис и импорты)."""
        return await self.validate(levels=[
            ValidationLevel.SYNTAX,
            ValidationLevel.IMPORTS,
        ])
    
    # ========================================================================
    # LEVEL DISPATCHER
    # ========================================================================
    
    async def _run_level(
        self,
        level: ValidationLevel,
        files: List[str],
        result: Optional[ValidationResult] = None,
    ) -> List[ValidationIssue]:
        """Run validation for a specific level."""
        if level == ValidationLevel.SYNTAX:
            return await self._check_syntax(files, result)
        elif level == ValidationLevel.IMPORTS:
            return await self._check_imports(files)
        elif level == ValidationLevel.TYPES:
            return await self._check_types(files)
        elif level == ValidationLevel.INTEGRATION:
            return await self._check_integration(files)
        elif level == ValidationLevel.RUNTIME:
            return await self._check_runtime(files, result)
        elif level == ValidationLevel.TESTS:
            if result is None:
                return []
            return await self._check_tests(result)
        else:
            return []
    
    
    
    # ========================================================================
    # LEVEL 1: SYNTAX
    # ========================================================================
    
    async def _check_syntax(self, files: List[str], result: Optional[ValidationResult] = None) -> List[ValidationIssue]:
        """Проверяет синтаксис Python файлов."""
        issues = []
        
        # Initialize stats if result object is provided
        if result is not None and "tools" not in result.auto_format_stats:
            checker = self.syntax_checker
            result.auto_format_stats = {
                "tools": {
                    "black": getattr(checker, "_black_available", False),
                    "autopep8": getattr(checker, "_autopep8_available", False),
                    "isort": getattr(checker, "_isort_available", False),
                    "yapf": getattr(checker, "_yapf_available", False),
                },
                "stats": {
                    "checked": 0,
                    "with_errors": 0,
                    "fixed": 0,
                    "failed": 0
                },
                "fixed_files": [],
                "failed_files": []  # NEW: Track failures
            }
        
        for file_path in files:
            if not file_path.endswith('.py'):
                continue
            
            if result:
                result.auto_format_stats["stats"]["checked"] += 1
            
            content = self.vfs.read_file(file_path)
            if content is None:
                continue
            
            check_result = self.syntax_checker.check_python(content, auto_fix=True)
            
            # === LOGIC FOR STATS ===
            if check_result.was_auto_fixed:
                # Success
                logger.info(f"[VALIDATION] Auto-formatted {file_path}: {check_result.applied_fixes}")
                if result:
                    result.auto_format_stats["stats"]["fixed"] += 1
                    result.auto_format_stats["stats"]["with_errors"] += 1
                    result.auto_format_stats["fixed_files"].append({
                        "file": file_path,
                        "fixes": check_result.applied_fixes
                    })
            elif not check_result.is_valid:
                # Failure
                logger.warning(f"[VALIDATION] Syntax errors in {file_path} could not be auto-fixed.")
                if result:
                    result.auto_format_stats["stats"]["failed"] += 1
                    result.auto_format_stats["stats"]["with_errors"] += 1
                    result.auto_format_stats["failed_files"].append({
                        "file": file_path,
                        "attempts": getattr(check_result, "attempted_fixes", []),
                        "errors": [i.message for i in check_result.issues[:2]]
                    })
            
            # === COLLECT ISSUES ===
            if not check_result.is_valid:
                for issue in check_result.issues:
                    issues.append(ValidationIssue(
                        level=ValidationLevel.SYNTAX,
                        severity=IssueSeverity.ERROR,
                        file_path=file_path,
                        message=issue.message,
                        line=issue.line,
                        column=issue.column,
                        suggestion=issue.suggestion,
                    ))
            
            # === STAGE VALID CHANGES ===
            if check_result.was_auto_fixed and check_result.fixed_content and check_result.is_valid:
                logger.info(f"[VALIDATION] Staging auto-fixed content for {file_path}")
                self.vfs.stage_change(file_path, check_result.fixed_content)
        
        return issues
    
    # ========================================================================
    # LEVEL 2: IMPORTS
    # ========================================================================
    
    async def _check_imports(self, files: List[str]) -> List[ValidationIssue]:
        """Проверяет, что все импорты разрешимы."""
        issues = []
        
        for file_path in files:
            if not file_path.endswith('.py'):
                continue
            
            content = self.vfs.read_file(file_path)
            if content is None:
                continue
            
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            
            imports = self._extract_imports(tree)
            
            for imp in imports:
                is_valid, error = self._validate_import(imp, file_path)
                if not is_valid:
                    issues.append(ValidationIssue(
                        level=ValidationLevel.IMPORTS,
                        severity=IssueSeverity.ERROR,
                        file_path=file_path,
                        message=error,
                        line=imp.get('line'),
                        suggestion=self._suggest_import_fix(imp),
                    ))
        
        return issues
    
    def _extract_imports(self, tree: ast.AST) -> List[Dict[str, Any]]:
        """Извлекает все импорты из AST."""
        imports = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        'type': 'import',
                        'module': alias.name,
                        'name': None,
                        'line': node.lineno,
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    imports.append({
                        'type': 'from',
                        'module': module,
                        'name': alias.name,
                        'level': node.level,
                        'line': node.lineno,
                    })
        
        return imports
    
    def _validate_import(
        self,
        imp: Dict[str, Any],
        source_file: str,
    ) -> Tuple[bool, Optional[str]]:
        """Проверяет валидность одного импорта."""
        module = imp['module']
        name = imp.get('name')
        level = imp.get('level', 0)
        
        if level > 0:
            return self._validate_relative_import(imp, source_file)
        
        if not module:
            return True, None
        
        parts = module.split('.')
        top_level = parts[0]
        
        # =========================================================================
        # Skip platform-specific modules
        # =========================================================================
        if top_level in self.PLATFORM_SPECIFIC_MODULES:
            logger.debug(f"Skipping platform-specific import: {module}")
            return True, None
        
        # Also check for nested platform modules (e.g., ctypes.wintypes)
        if module in self.PLATFORM_SPECIFIC_MODULES:
            logger.debug(f"Skipping platform-specific import: {module}")
            return True, None
        
        # =========================================================================
        # Special handling for imports that are runtime-only or conditional
        # These often fail validation but work at runtime
        # =========================================================================
        runtime_only_patterns = [
            # Dynamic imports
            r'__import__',
            r'importlib\.import_module',
            
            # Conditional imports (common in CLI tools)
            r'click\.',
            r'argparse\.',
            r'typer\.',
            
            # Type checking imports
            r'typing\.TYPE_CHECKING',
            r'if TYPE_CHECKING:',
            
            # Lazy imports
            r'from.*import.*as.*_lazy',
        ]
        
        # Check if this import appears in a conditional context
        # (we can't determine this statically, so we're permissive)
        source_content = self.vfs.read_file(source_file)
        if source_content:
            # Look for patterns indicating runtime/conditional imports
            import_line = imp.get('line')
            if import_line:
                # Get context around the import line
                lines = source_content.split('\n')
                start_line = max(0, import_line - 3)
                end_line = min(len(lines), import_line + 2)
                context = '\n'.join(lines[start_line:end_line])
                
                # Check for conditional patterns
                conditional_patterns = [
                    r'if.*:',
                    r'try:',
                    r'except.*:',
                    r'def.*:.*#.*lazy',
                    r'@.*lazy',
                ]
                
                if any(re.search(pattern, context, re.IGNORECASE) for pattern in conditional_patterns):
                    logger.debug(f"Skipping validation for conditional import: {module} in {source_file}")
                    return True, None
        
        # 1. stdlib
        if self.config.check_stdlib and self._is_stdlib_module(top_level):
            return True, None
        
        # 2. project modules
        if self.config.check_project_modules and self._is_project_module(module):
            if name and name != '*':
                exists = self._check_name_in_module(module, name)
                if not exists:
                    return False, f"Cannot import '{name}' from '{module}'"
            return True, None
        
        # 3. pip packages
        if self.config.check_pip_packages and self._is_pip_package(top_level):
            return True, None
        
        return False, f"Module '{module}' not found"
    
    
    
    def _validate_relative_import(
        self,
        imp: Dict[str, Any],
        source_file: str,
    ) -> Tuple[bool, Optional[str]]:
        """Проверяет relative import с учётом VFS staged files."""
        level = imp.get('level', 0)
        module = imp.get('module', '')
        
        source_path = Path(source_file)
        parent = source_path.parent
        
        # Go up 'level' directories (level=1 means current package)
        for _ in range(level - 1):
            parent = parent.parent
        
        if module:
            module_path = parent / module.replace('.', '/')
        else:
            module_path = parent
        
        # Build possible file paths
        py_file = str(module_path).replace('\\', '/') + '.py'
        package_init = str(module_path / '__init__.py').replace('\\', '/')
        
        # Check 1: VFS file_exists (includes both real FS and staged)
        if self.vfs.file_exists(py_file) or self.vfs.file_exists(package_init):
            return True, None
        
        # Check 2: Explicitly check staged files (belt and suspenders)
        staged_files = set(self.vfs.get_staged_files())
        if py_file in staged_files or package_init in staged_files:
            return True, None
        
        # Check 3: Build full module name and check against scanned modules
        source_module = self._path_to_module(source_file)
        if source_module:
            source_parts = source_module.split('.')
            # Remove 'level' parts from end
            base_parts = source_parts[:-level] if level <= len(source_parts) else []
            
            if module:
                full_module = '.'.join(base_parts + module.split('.'))
            else:
                full_module = '.'.join(base_parts) if base_parts else ''
            
            if full_module and self._is_project_module(full_module):
                return True, None
        
        return False, f"Relative import: module '{module}' not found (from {source_file})"


    def _path_to_module(self, file_path: str) -> Optional[str]:
        """Конвертирует путь файла в имя модуля."""
        if not file_path.endswith('.py'):
            return None
        
        # Normalize path
        normalized = file_path.replace('\\', '/').replace('/', '.')
        
        if normalized.endswith('.__init__.py'):
            return normalized[:-12]  # Remove .__init__.py
        else:
            return normalized[:-3]  # Remove .py



    def _get_staged_modules_set(self) -> Set[str]:
        """Возвращает множество модулей из staged файлов VFS."""
        modules = set()
        
        for file_path in self.vfs.get_staged_files():
            if not file_path.endswith('.py'):
                continue
            
            change = self.vfs.get_change(file_path)
            if change and change.is_deletion:
                continue
            
            module = self._path_to_module_from_path(file_path)
            if module:
                modules.add(module)
                # Добавляем родительские пакеты
                parts = module.split('.')
                for i in range(1, len(parts)):
                    modules.add('.'.join(parts[:i]))
        
        return modules

    def _path_to_module_from_path(self, file_path: str) -> Optional[str]:
        """Конвертирует путь файла в имя модуля (без проверки существования)."""
        if not file_path.endswith('.py'):
            return None
        
        # Нормализуем путь
        normalized = file_path.replace('\\', '/').replace('/', '.')
        
        if normalized.endswith('.__init__.py'):
            module = normalized[:-12]  # Убираем .__init__.py
        else:
            module = normalized[:-3]  # Убираем .py
        
        return module if module else None    
    
    
    def _normalize_path(self, path: str) -> str:
        """Нормализует путь относительно project_root."""
        p = Path(path)
        try:
            return str(p.relative_to(self.vfs.project_root))
        except ValueError:
            return str(p).replace('\\', '/')
    
    def _is_stdlib_module(self, module: str) -> bool:
        return module in ChangeValidator._STDLIB_MODULES
    
    def _is_pip_package(self, package: str) -> bool:
        """
        Проверяет, соответствует ли имя модуля установленному pip-пакету.
        
        Uses dynamic mapping from importlib.metadata for accurate resolution.
        Например: 'docx' → 'python-docx', 'cv2' → 'opencv-python',
                  'googleapiclient' → 'google-api-python-client'
        """
        if self._pip_packages_cache is None:
            self._pip_packages_cache = self._get_pip_packages()
        
        # Нормализуем имя модуля
        normalized = package.lower().replace('-', '_')
        
        # 1. Прямая проверка: модуль совпадает с именем пакета
        if normalized in self._pip_packages_cache:
            return True
        
        if package.lower() in self._pip_packages_cache:
            return True
        
        # 2. Проверка через ДИНАМИЧЕСКИЙ маппинг import → pip
        # Использует importlib.metadata.packages_distributions() для точного маппинга
        dynamic_mapping = get_import_to_package_mapping()
        pip_name = dynamic_mapping.get(package)
        if pip_name:
            pip_normalized = pip_name.lower().replace('-', '_')
            if pip_normalized in self._pip_packages_cache:
                return True
            if pip_name.lower() in self._pip_packages_cache:
                return True
        
        # 3. Проверка вариаций имени
        # Некоторые пакеты: python-xxx, py-xxx, xxx-python
        variations = [
            f"python_{normalized}",
            f"python-{package.lower()}",
            f"py{normalized}",
            f"py_{normalized}",
        ]
        
        for var in variations:
            var_normalized = var.replace('-', '_')
            if var_normalized in self._pip_packages_cache:
                return True
        
        return False
    
    
    
    def _is_project_module(self, module: str) -> bool:
        if self._project_modules_cache is None:
            self._project_modules_cache = self._scan_project_modules()
        
        parts = module.split('.')
        for i in range(len(parts), 0, -1):
            check_module = '.'.join(parts[:i])
            if check_module in self._project_modules_cache:
                return True
        
        return False
    
    def _check_name_in_module(self, module: str, name: str) -> bool:
        """Проверяет, экспортирует ли модуль указанное имя."""
        module_path = module.replace('.', '/')
        candidates = [f"{module_path}.py", f"{module_path}/__init__.py"]
        
        for candidate in candidates:
            # NEW: Используем VFS read_file вместо прямого чтения
            # Это позволяет видеть содержимое staged файлов
            content = self.vfs.read_file(candidate)
            if content is None:
                continue
            
            try:
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name == name:
                            return True
                    if isinstance(node, ast.ClassDef) and node.name == name:
                        return True
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == name:
                                return True
                
            except SyntaxError:
                pass
        
        return True  # Оптимистично

    
    def _suggest_import_fix(self, imp: Dict[str, Any]) -> Optional[str]:
        """
        Предлагает исправление для неразрешённого импорта.
        
        Проверяет:
        1. Известные маппинги import → pip
        2. Похожие модули в проекте
        """
        module = imp['module']
        root_module = module.split('.')[0] if module else ''
        
        suggestions = []
        
        # 1. Проверяем известные маппинги
        if root_module in IMPORT_TO_PACKAGE:
            pip_name = IMPORT_TO_PACKAGE[root_module]
            suggestions.append(f"pip install {pip_name}")
        
        # 2. Проверяем вариации имени пакета
        if not suggestions:
            variations = [
                f"python-{root_module}",
                f"py{root_module}",
                f"{root_module}-python",
            ]
            suggestions.extend([f"pip install {v}" for v in variations[:2]])
        
        # 3. Проверяем похожие модули в проекте
        if self._project_modules_cache:
            similar = [m for m in self._project_modules_cache 
                    if root_module.lower() in m.lower()][:3]
            if similar:
                suggestions.append(f"Did you mean: {', '.join(similar)}")
        
        if suggestions:
            return "; ".join(suggestions[:3])
        
        return None    
    
    
    def _get_stdlib_modules(self) -> Set[str]:
        """Возвращает множество модулей stdlib."""
        stdlib = set()
        
        if hasattr(sys, 'stdlib_module_names'):
            stdlib.update(sys.stdlib_module_names)
        
        # Известные встроенные
        stdlib.update({
            'abc', 'argparse', 'ast', 'asyncio', 'base64', 'collections',
            'concurrent', 'configparser', 'contextlib', 'copy', 'csv',
            'dataclasses', 'datetime', 'decimal', 'difflib', 'email',
            'enum', 'functools', 'glob', 'hashlib', 'heapq', 'html',
            'http', 'importlib', 'inspect', 'io', 'itertools', 'json',
            'logging', 'math', 'multiprocessing', 'os', 'pathlib',
            'pickle', 'platform', 'pprint', 'queue', 'random', 're',
            'shutil', 'signal', 'socket', 'sqlite3', 'ssl', 'string',
            'struct', 'subprocess', 'sys', 'tempfile', 'textwrap',
            'threading', 'time', 'traceback', 'types', 'typing',
            'unittest', 'urllib', 'uuid', 'warnings', 'weakref', 'xml',
            'zipfile', 'zlib', '__future__', 'typing_extensions', 'builtins',
        })
        
        return stdlib
    
    def _get_pip_packages(self) -> Set[str]:
        """
        Возвращает множество установленных pip-пакетов.
        
        CRITICAL: Использует Python интерпретатор ПРОЕКТА, а не агента.
        Это гарантирует, что валидация проверяет пакеты, доступные
        в окружении проекта пользователя.
        """
        packages = set()
        
        # Используем Python проекта, а не sys.executable
        python_path = self.vfs.get_project_python()
        
        try:
            result = subprocess.run(
                [python_path, '-m', 'pip', 'list', '--format=json'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            
            if result.returncode == 0:
                pip_list = json.loads(result.stdout)
                for pkg in pip_list:
                    name = pkg.get('name', '')
                    if not name:
                        continue
                        
                    # Добавляем оригинальное имя
                    packages.add(name.lower())
                    
                    # Добавляем нормализованное имя (- → _)
                    normalized = name.lower().replace('-', '_')
                    packages.add(normalized)
                        
        except Exception as e:
            logger.warning(f"Failed to get pip packages from project venv: {e}")
        
        # Добавляем import-имена через обратный маппинг
        # Использует динамический маппинг для точного разрешения
        dynamic_mapping = get_import_to_package_mapping()
        pip_to_import: Dict[str, str] = {}
        for import_name, pip_name in dynamic_mapping.items():
            pip_normalized = pip_name.lower().replace('-', '_')
            pip_to_import[pip_normalized] = import_name.lower()
            pip_to_import[pip_name.lower()] = import_name.lower()
        
        packages_to_add = set()
        for pip_name in packages:
            if pip_name in pip_to_import:
                import_name = pip_to_import[pip_name]
                packages_to_add.add(import_name)
                packages_to_add.add(import_name.replace('-', '_'))
        
        packages.update(packages_to_add)
        
        return packages
    
    
    def _scan_project_modules(self) -> Set[str]:
        """Сканирует проект и возвращает все модули (включая staged в VFS)."""
        modules = set()
        
        # 1. Реальная файловая система
        for py_file in self.vfs.project_root.rglob('*.py'):
            try:
                rel_path = py_file.relative_to(self.vfs.project_root)
                
                # Пропускаем .venv, __pycache__ и т.д.
                parts = rel_path.parts
                if any(p.startswith('.') or p == '__pycache__' or p == 'venv' 
                    for p in parts):
                    continue
                
                # Конвертируем путь в модуль
                if rel_path.name == '__init__.py':
                    module = '.'.join(parts[:-1])
                else:
                    module = '.'.join(parts)[:-3]  # Убираем .py
                
                if module:
                    modules.add(module)
                    # Добавляем все родительские пакеты
                    module_parts = module.split('.')
                    for i in range(1, len(module_parts)):
                        modules.add('.'.join(module_parts[:i]))
                        
            except ValueError:
                continue
        
        # 2. NEW: Staged files из VFS (файлы, созданные в текущей сессии)
        for file_path in self.vfs.get_staged_files():
            if not file_path.endswith('.py'):
                continue
            
            # Пропускаем удалённые файлы
            change = self.vfs.get_change(file_path)
            if change and change.is_deletion:
                continue
            
            # Пропускаем системные директории
            parts = Path(file_path).parts
            if any(p.startswith('.') or p == '__pycache__' or p == 'venv' 
                for p in parts):
                continue
            
            # Конвертируем путь в модуль
            if file_path.endswith('__init__.py'):
                module = '.'.join(parts[:-1])
            else:
                # Убираем .py и заменяем / на .
                module = file_path[:-3].replace('/', '.').replace('\\', '.')
            
            if module:
                modules.add(module)
                # Добавляем все родительские пакеты
                module_parts = module.split('.')
                for i in range(1, len(module_parts)):
                    modules.add('.'.join(module_parts[:i]))
                
                logger.debug(f"Added VFS staged module: {module}")
        
        return modules
    
    # ========================================================================
    # LEVEL 3: TYPES (mypy)
    # ========================================================================
    
    async def _check_types(self, files: List[str]) -> List[ValidationIssue]:
        """Проверяет типы через mypy."""
        issues = []
        
        py_files = [f for f in files if f.endswith('.py')]
        if not py_files:
            return issues
        
        # Проверяем доступность mypy
        try:
            subprocess.run(['mypy', '--version'], capture_output=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("mypy not available, skipping type check")
            return issues
        
        # Материализуем VFS во временную директорию
        temp_dir = None
        try:
            temp_dir = self._materialize_vfs()
            
            # Запускаем mypy
            cmd = ['mypy', '--no-error-summary']
            
            if self.config.mypy_ignore_missing_imports:
                cmd.append('--ignore-missing-imports')
            
            if self.config.mypy_strict:
                cmd.append('--strict')
            
            # Добавляем файлы
            for f in py_files:
                cmd.append(str(Path(temp_dir) / f))
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',  # ✅ Добавить это
                errors='replace',   # ✅ Опционально: заменять невалидные символы вместо падения
                timeout=120,
                cwd=temp_dir,
            )
            
            # Парсим вывод mypy
            for line in result.stdout.splitlines():
                issue = self._parse_mypy_line(line, py_files)
                if issue:
                    issues.append(issue)
            
        except subprocess.TimeoutExpired:
            logger.warning("mypy timed out")
        except Exception as e:
            logger.error(f"mypy check failed: {e}")
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return issues
    
    def _parse_mypy_line(self, line: str, files: List[str]) -> Optional[ValidationIssue]:
        """Парсит строку вывода mypy."""
        # Формат: file.py:10: error: Message [error-code]
        match = re.match(r'^(.+?):(\d+):\s*(error|warning|note):\s*(.+)$', line)
        if not match:
            return None
        
        file_path, line_num, severity_str, message = match.groups()
        
        # Извлекаем относительный путь
        file_path = Path(file_path).name
        for f in files:
            if f.endswith(file_path) or Path(f).name == file_path:
                file_path = f
                break
        
        # Определяем severity
        severity = IssueSeverity.ERROR
        if severity_str == 'warning':
            severity = IssueSeverity.WARNING
        elif severity_str == 'note':
            severity = IssueSeverity.INFO
        
        # Извлекаем код ошибки [error-code]
        code = None
        bracket_match = re.search(r'\[([^\]\n]+)\]$', message)
        if bracket_match:
            code = bracket_match.group(1)
        
        return ValidationIssue(
            level=ValidationLevel.TYPES,
            severity=severity,
            file_path=file_path,
            message=message,
            line=int(line_num),
            code=code,
        )
    
    
    def _materialize_vfs(self) -> str:
        """
        Материализует VFS во временную директорию.
        
        CRITICAL: Staged файлы из VFS ВСЕГДА перезаписывают файлы из project_root.
        Это гарантирует, что тестируется актуальная версия кода.
        """
        temp_dir = tempfile.mkdtemp(prefix='validator_')
        
        # Получаем список staged файлов ЗАРАНЕЕ
        staged_files = set(self.vfs.get_staged_files())
        
        logger.debug(f"Materializing VFS to {temp_dir}")
        logger.debug(f"Staged files ({len(staged_files)}): {list(staged_files)[:10]}{'...' if len(staged_files) > 10 else ''}")
        
        # 1. Копируем project_root, ИСКЛЮЧАЯ staged файлы
        # (они будут записаны из VFS с актуальным содержимым)
        for item in self.vfs.project_root.iterdir():
            if item.name.startswith('.') or item.name in ('__pycache__', 'venv', '.venv'):
                continue
            
            dest = Path(temp_dir) / item.name
            
            if item.is_dir():
                # Для директорий используем custom copytree с фильтрацией staged файлов
                def ignore_staged(directory, files):
                    ignored = set()
                    for f in files:
                        # Строим относительный путь
                        full_path = Path(directory) / f
                        try:
                            rel_path = str(full_path.relative_to(self.vfs.project_root)).replace('\\', '/')
                        except ValueError:
                            continue
                        
                        # Игнорируем staged файлы и системные директории
                        if rel_path in staged_files:
                            ignored.add(f)
                            logger.debug(f"Skipping staged file from copy: {rel_path}")
                        elif f in ('__pycache__', '.git', '.venv', 'venv') or f.endswith('.pyc'):
                            ignored.add(f)
                    
                    return ignored
                
                shutil.copytree(item, dest, ignore=ignore_staged)
            else:
                # Для файлов в корне проверяем, не staged ли они
                rel_path = item.name
                if rel_path not in staged_files:
                    shutil.copy2(item, dest)
                else:
                    logger.debug(f"Skipping staged root file from copy: {rel_path}")
        
        # 2. Записываем ВСЕ staged файлы из VFS
        # Это гарантирует, что тестируется актуальная версия
        files_written = 0
        for file_path in staged_files:
            # Читаем содержимое из VFS (это вернёт staged версию)
            content = self.vfs.read_file(file_path)
            
            if content is None:
                # Проверяем, может это удаление
                change = self.vfs.get_change(file_path)
                if change and change.is_deletion:
                    logger.debug(f"Skipping deleted file: {file_path}")
                    continue
                else:
                    logger.warning(f"Staged file has no content: {file_path}")
                    continue
            
            dest_path = Path(temp_dir) / file_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(content, encoding='utf-8')
            files_written += 1
            logger.debug(f"Wrote staged file: {file_path} ({len(content)} chars)")
        
        # Log Click-related staged files for debugging
        click_files = []
        for file_path in staged_files:
            if file_path.endswith('.py'):
                try:
                    content = self.vfs.read_file(file_path)
                    if content and ('@click.' in content or 'click.command' in content or 'click.group' in content):
                        click_files.append(file_path)
                except Exception:
                    pass
        
        if click_files:
            logger.debug(f"Materialized VFS contains {len(click_files)} Click-decorated files: {click_files}")
        
        # [REMOVED] Automatic __init__.py creation logic.
        # We now respect the project's original structure (namespace packages support).
        # Imports are handled by proper PYTHONPATH configuration in RuntimeTester.
        
        # Create setup.cfg to declare namespace packages if needed
        setup_cfg_path = Path(temp_dir) / 'setup.cfg'
        if not setup_cfg_path.exists():
            setup_cfg_content = """[metadata]
name = ai-assistant-temp-project
version = 0.0.1

[options]
packages = find:
zip_safe = False

[options.packages.find]
exclude = 
    tests*
    test*
    *test
    *tests
    """
            setup_cfg_path.write_text(setup_cfg_content, encoding='utf-8')
            logger.debug(f"Created setup.cfg at {temp_dir}")
        
        logger.info(f"Materialized VFS: {files_written} files, {temp_dir}")
        
        return temp_dir

    
    # ========================================================================
    # LEVEL 4: INTEGRATION
    # ========================================================================
    
    async def _check_integration(self, files: List[str]) -> List[ValidationIssue]:
        """Проверяет интеграционную совместимость."""
        issues = []
        
        affected = self.vfs.get_affected_files()
        
        for changed_file in affected.changed_files:
            if not changed_file.endswith('.py'):
                continue
            
            # Анализируем что изменилось
            changes = self._analyze_changes(changed_file)
            
            if not changes:
                continue
            
            # Проверяем зависимые файлы
            for dep_file in affected.dependent_files:
                dep_issues = self._check_dependent_compatibility(
                    changed_file, dep_file, changes
                )
                issues.extend(dep_issues)
        
        return issues
    
    def _analyze_changes(self, file_path: str) -> Dict[str, Any]:
        """Анализирует изменения в файле."""
        changes = {
            'removed_functions': [],
            'removed_classes': [],
            'changed_signatures': [],
            'added_functions': [],
            'added_classes': [],
        }
        
        old_content = self.vfs.read_file_original(file_path)
        new_content = self.vfs.read_file(file_path)
        
        if old_content is None or new_content is None:
            return changes
        
        try:
            old_tree = ast.parse(old_content)
            new_tree = ast.parse(new_content)
        except SyntaxError:
            return changes
        
        # Извлекаем определения
        old_defs = self._extract_definitions(old_tree)
        new_defs = self._extract_definitions(new_tree)
        
        # Находим удалённые
        for name, info in old_defs.items():
            if name not in new_defs:
                if info['type'] == 'function':
                    changes['removed_functions'].append(name)
                elif info['type'] == 'class':
                    changes['removed_classes'].append(name)
            else:
                # Проверяем изменение сигнатуры
                if info['type'] == 'function':
                    old_sig = info.get('signature', '')
                    new_sig = new_defs[name].get('signature', '')
                    if old_sig != new_sig:
                        changes['changed_signatures'].append({
                            'name': name,
                            'old': old_sig,
                            'new': new_sig,
                        })
        
        # Находим добавленные
        for name, info in new_defs.items():
            if name not in old_defs:
                if info['type'] == 'function':
                    changes['added_functions'].append(name)
                elif info['type'] == 'class':
                    changes['added_classes'].append(name)
        
        return changes
    
    def _extract_definitions(self, tree: ast.AST) -> Dict[str, Dict]:
        """Извлекает определения функций и классов."""
        defs = {}
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._get_signature(node)
                defs[node.name] = {
                    'type': 'function',
                    'signature': sig,
                    'line': node.lineno,
                }
            elif isinstance(node, ast.ClassDef):
                defs[node.name] = {
                    'type': 'class',
                    'line': node.lineno,
                    'methods': [n.name for n in node.body 
                               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
                }
        
        return defs
    
    def _get_signature(self, node: ast.FunctionDef) -> str:
        """Извлекает сигнатуру функции."""
        args = []
        
        for arg in node.args.args:
            args.append(arg.arg)
        
        for arg in node.args.kwonlyargs:
            args.append(f"{arg.arg}=")
        
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        
        return f"({', '.join(args)})"
    
    def _check_dependent_compatibility(
        self,
        changed_file: str,
        dependent_file: str,
        changes: Dict[str, Any],
    ) -> List[ValidationIssue]:
        """Проверяет совместимость зависимого файла."""
        issues = []
        
        content = self.vfs.read_file(dependent_file)
        if content is None:
            return issues
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return issues
        
        # Ищем использование удалённых функций/классов
        removed = set(changes['removed_functions'] + changes['removed_classes'])
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in removed:
                issues.append(ValidationIssue(
                    level=ValidationLevel.INTEGRATION,
                    severity=IssueSeverity.ERROR,
                    file_path=dependent_file,
                    message=f"Uses '{node.id}' which was removed from {changed_file}",
                    line=node.lineno,
                    suggestion=f"Update {dependent_file} to handle removal of '{node.id}'",
                ))
            
            if isinstance(node, ast.Attribute) and node.attr in removed:
                issues.append(ValidationIssue(
                    level=ValidationLevel.INTEGRATION,
                    severity=IssueSeverity.WARNING,
                    file_path=dependent_file,
                    message=f"May use '{node.attr}' which was removed from {changed_file}",
                    line=node.lineno,
                ))
        
        return issues
    
    
    # ========================================================================
    # LEVEL 5: RUNTIME
    # ========================================================================
    
    
    async def _check_runtime(self, files: List[str], result: ValidationResult = None) -> List[ValidationIssue]:
        """
        Проверяет runtime — выполняет каждый файл с учётом его типа.
        
        Использует RuntimeTester для умного тестирования:
        - Standard Python: runpy.run_path()
        - GUI/Game: headless import test
        - SQL: temp database validation
        - API: network check + graceful handling
        - Web: skip with INFO
        
        Args:
            files: List of file paths to check
            result: ValidationResult to update statistics
            
        Returns:
            List of ValidationIssues
        """
        issues = []
        
        py_files = [f for f in files if f.endswith('.py')]
        if not py_files:
            logger.info("RUNTIME: No Python files to check")
            return issues
        
        logger.info(f"RUNTIME: Checking {len(py_files)} files with RuntimeTester")
        
        temp_dir = None
        try:
            # Materialize VFS to temp directory
            temp_dir = self._materialize_vfs()
            
            # Create RuntimeTester
            tester = RuntimeTester(self.vfs)
            
            # Analyze project
            analysis = await tester.analyze_project(py_files)
            
            logger.info(
                f"RUNTIME: Project analysis - {analysis.total_tokens} tokens, "
                f"category={analysis.size_category.value}, "
                f"timeout={analysis.total_timeout_seconds}s, "
                f"frameworks={list(analysis.detected_frameworks.keys())}"
            )
            
            # Run all tests
            summary = await tester.run_all_tests(
                files=py_files,
                temp_dir=temp_dir,
                total_timeout=analysis.total_timeout_seconds,
                analysis=analysis,
            )
            
            # Process results
            for test_result in summary.results:
                if test_result.status == TestStatus.PASSED:
                    # Success — no issue, just log
                    logger.info(f"RUNTIME PASSED: {test_result.file_path} ({test_result.app_type.value})")
                    
                elif test_result.status == TestStatus.SKIPPED:
                    # Skipped — INFO level issue
                    issues.append(ValidationIssue(
                        level=ValidationLevel.RUNTIME,
                        severity=IssueSeverity.INFO,
                        file_path=test_result.file_path,
                        message=test_result.message,
                        suggestion=test_result.suggestion,
                    ))
                    logger.info(f"RUNTIME SKIPPED: {test_result.file_path} - {test_result.message}")
                    
                elif test_result.status == TestStatus.FAILED:
                    # Failed — ERROR
                    full_message = test_result.message
                    if test_result.details:
                        # Извлекаем последние строки traceback (наиболее информативные)
                        details_lines = test_result.details.strip().split('\n')
                        # Берём последние 10 строк или меньше
                        relevant_lines = details_lines[-10:] if len(details_lines) > 10 else details_lines
                        details_preview = '\n'.join(relevant_lines)
                        full_message = f"{test_result.message}\n\nTraceback (last 10 lines):\n{details_preview}"
                    
                    issues.append(ValidationIssue(
                        level=ValidationLevel.RUNTIME,
                        severity=IssueSeverity.ERROR,
                        file_path=test_result.file_path,
                        message=full_message,
                        suggestion=test_result.suggestion,
                    ))
                    logger.warning(f"RUNTIME FAILED: {test_result.file_path} - {test_result.message}")
                  
                  
                    
                elif test_result.status == TestStatus.TIMEOUT:
                    # Timeout — ERROR
                    issues.append(ValidationIssue(
                        level=ValidationLevel.RUNTIME,
                        severity=IssueSeverity.ERROR,
                        file_path=test_result.file_path,
                        message=test_result.message,
                        suggestion=test_result.suggestion or "Check for infinite loops or blocking operations",
                    ))
                    logger.warning(f"RUNTIME TIMEOUT: {test_result.file_path} - {test_result.message}")
                    
                elif test_result.status == TestStatus.ERROR:
                    # Error — ERROR
                    full_message = test_result.message
                    if test_result.details:
                        # Извлекаем последние строки traceback (наиболее информативные)
                        details_lines = test_result.details.strip().split('\n')
                        # Берём последние 10 строк или меньше
                        relevant_lines = details_lines[-10:] if len(details_lines) > 10 else details_lines
                        details_preview = '\n'.join(relevant_lines)
                        full_message = f"{test_result.message}\n\nTraceback (last 10 lines):\n{details_preview}"
                    
                    issues.append(ValidationIssue(
                        level=ValidationLevel.RUNTIME,
                        severity=IssueSeverity.ERROR,
                        file_path=test_result.file_path,
                        message=full_message,
                        suggestion=test_result.suggestion,
                    ))
                    logger.error(f"RUNTIME ERROR: {test_result.file_path} - {test_result.message}")
            
            # Update result statistics
            if result is not None:
                result.runtime_files_checked = summary.total_files
                result.runtime_files_passed = summary.passed
                result.runtime_files_failed = summary.failed + summary.errors + summary.timeouts
                result.runtime_files_skipped = summary.skipped
                result.runtime_test_summary = summary.to_dict()
            
            logger.info(
                f"RUNTIME SUMMARY: {summary.passed}/{summary.total_files} passed, "
                f"{summary.failed} failed, {summary.skipped} skipped, "
                f"{summary.timeouts} timeouts, {summary.errors} errors"
            )
            
        except Exception as e:
            logger.error(f"Runtime check failed: {e}", exc_info=True)
            issues.append(ValidationIssue(
                level=ValidationLevel.RUNTIME,
                severity=IssueSeverity.ERROR,
                file_path="<runtime>",
                message=f"Runtime validation error: {e}",
            ))
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return issues    
    
    
    
    # ========================================================================
    # LEVEL 6: TESTS
    # ========================================================================
    
    async def _check_tests(self, result: ValidationResult) -> List[ValidationIssue]:
        """Запускает связанные тесты."""
        issues = []
        
        if not self.config.run_related_tests:
            return issues
        
        if not result.test_files_found:
            return issues
        
        temp_dir = None
        try:
            temp_dir = self._materialize_vfs()
            
            for test_info in result.test_files_found:
                test_path = Path(temp_dir) / test_info.path
                
                if not test_path.exists():
                    # Пробуем найти в project_root
                    test_path = self.vfs.project_root / test_info.path
                    if not test_path.exists():
                        continue
                
                result.tests_run += 1
                
                # Запускаем pytest
                proc_result = subprocess.run(
                    [sys.executable, '-m', 'pytest', str(test_path), '-v', '--tb=short'],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',  # ✅ Добавить это
                    errors='replace',   # ✅ Опционально: заменять невалидные символы вместо падения
                    timeout=self.config.test_timeout_sec,
                    cwd=temp_dir if temp_dir else str(self.vfs.project_root),
                )
                
                if proc_result.returncode == 0:
                    result.tests_passed += 1
                else:
                    result.tests_failed += 1
                    
                    # Извлекаем информацию о падении
                    error_lines = proc_result.stdout.split('\n')
                    failed_tests = [l for l in error_lines if 'FAILED' in l]
                    
                    for failed in failed_tests[:3]:  # Максимум 3 ошибки
                        issues.append(ValidationIssue(
                            level=ValidationLevel.TESTS,
                            severity=IssueSeverity.ERROR if self.config.fail_on_test_failure else IssueSeverity.WARNING,
                            file_path=test_info.path,
                            message=f"Test failed: {failed.strip()}",
                        ))
                    
        except subprocess.TimeoutExpired:
            logger.warning("Test execution timed out")
            issues.append(ValidationIssue(
                level=ValidationLevel.TESTS,
                severity=IssueSeverity.WARNING,
                file_path="<tests>",
                message="Test execution timed out",
            ))
        except Exception as e:
            logger.error(f"Test execution failed: {e}")
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return issues
    
    def _find_related_test_files(self, changed_files: List[str]) -> List[TestFileInfo]:
        """Находит тестовые файлы для изменённых модулей."""
        test_files = []
        seen = set()
        
        for file_path in changed_files:
            if not file_path.endswith('.py'):
                continue
            
            # Извлекаем имя модуля
            module_name = Path(file_path).stem
            
            # Пробуем разные паттерны
            for pattern in self.config.test_patterns:
                test_path = pattern.format(name=module_name)
                
                # Проверяем существование
                if self.vfs.file_exists(test_path):
                    if test_path not in seen:
                        seen.add(test_path)
                        test_files.append(TestFileInfo(
                            path=test_path,
                            related_to=file_path,
                            test_type=self._detect_test_type(test_path),
                            priority=1,
                        ))
            
            # Также ищем по директории
            file_dir = Path(file_path).parent
            test_dirs = ['tests', 'test', str(file_dir / 'tests')]
            
            for test_dir in test_dirs:
                test_path = f"{test_dir}/test_{module_name}.py"
                if self.vfs.file_exists(test_path) and test_path not in seen:
                    seen.add(test_path)
                    test_files.append(TestFileInfo(
                        path=test_path,
                        related_to=file_path,
                        test_type='unit',
                        priority=1,
                    ))
        
        # Сортируем по приоритету
        test_files.sort(key=lambda t: t.priority)
        
        return test_files
    

    def _normalize_test_path(self, path: str) -> str:
        """Нормализует путь к тестовому файлу."""
        # Убираем лишние слэши и точки
        path = str(Path(path)).replace('\\', '/')
        # Убираем начальный ./
        if path.startswith('./'):
            path = path[2:]
        return path

    
    def _detect_test_type(self, test_path: str) -> str:
        """Определяет тип теста по пути."""
        path_lower = test_path.lower()
        
        if 'integration' in path_lower:
            return 'integration'
        elif 'e2e' in path_lower or 'end_to_end' in path_lower:
            return 'e2e'
        else:
            return 'unit'
    
    def __repr__(self) -> str:
        return f"ChangeValidator(levels={[l.value for l in self.config.enabled_levels]})"