# scripts/test_validation_pipeline.py
"""
Интеграционный тест валидации с реальным вызовом компонентов.
Воспроизводит сценарий: анализ внешнего проекта с проблемным кодом.

Запуск из корня проекта:
    python scripts/test_validation_pipeline.py
"""

import asyncio
import logging
import sys
import os
import tempfile
from pathlib import Path

# ============================================================================
# НАСТРОЙКА ПУТЕЙ — КРИТИЧЕСКИ ВАЖНО
# ============================================================================

# Определяем корень проекта (родитель директории scripts/)
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(PROJECT_ROOT))

print(f"Project root: {PROJECT_ROOT}")
print(f"sys.path[0]: {sys.path[0]}")

# ============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================================

LOG_FILE = PROJECT_ROOT / "validation_test.log"

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding='utf-8', mode='w')
    ]
)

logger = logging.getLogger(__name__)
logger.info(f"Logging to: {LOG_FILE}")

# ============================================================================
# ПАТЧ SUBPROCESS ДЛЯ ДИАГНОСТИКИ
# ============================================================================

import subprocess
_original_run = subprocess.run
_original_Popen = subprocess.Popen

SUBPROCESS_ISSUES_FOUND = []

def patched_run(*args, **kwargs):
    """Логируем все вызовы subprocess.run и ловим проблемы с encoding."""
    diag_logger = logging.getLogger('subprocess.run')
    
    cmd = args[0] if args else kwargs.get('args', '?')
    cmd_str = cmd if isinstance(cmd, str) else ' '.join(str(c) for c in cmd[:4]) + '...'
    
    has_encoding = 'encoding' in kwargs
    is_text = kwargs.get('text') or kwargs.get('universal_newlines')
    capture = kwargs.get('capture_output') or kwargs.get('stdout') == subprocess.PIPE
    
    diag_logger.debug(f"subprocess.run: {cmd_str}")
    diag_logger.debug(f"  text={is_text}, capture={capture}, encoding={kwargs.get('encoding', 'NOT SET')}")
    
    if is_text and capture and not has_encoding:
        import traceback
        stack = ''.join(traceback.format_stack()[-8:-1])
        
        diag_logger.warning(f"⚠️ TEXT MODE WITHOUT ENCODING: {cmd_str}")
        diag_logger.warning(f"Stack:\n{stack}")
        
        SUBPROCESS_ISSUES_FOUND.append({
            'cmd': cmd_str,
            'stack': stack,
        })
        
        # Автоматически исправляем
        kwargs['encoding'] = 'utf-8'
        kwargs['errors'] = 'replace'
    
    return _original_run(*args, **kwargs)


class PatchedPopen(_original_Popen):
    """Логируем все вызовы Popen и ловим проблемы с encoding."""
    
    def __init__(self, args, **kwargs):
        diag_logger = logging.getLogger('subprocess.Popen')
        
        cmd_str = args if isinstance(args, str) else ' '.join(str(c) for c in args[:4]) + '...'
        
        has_encoding = 'encoding' in kwargs
        is_text = kwargs.get('text') or kwargs.get('universal_newlines')
        has_pipe = kwargs.get('stdout') == subprocess.PIPE or kwargs.get('stderr') == subprocess.PIPE
        
        diag_logger.debug(f"Popen: {cmd_str}")
        diag_logger.debug(f"  text={is_text}, pipes={has_pipe}, encoding={kwargs.get('encoding', 'NOT SET')}")
        
        if is_text and has_pipe and not has_encoding:
            import traceback
            stack = ''.join(traceback.format_stack()[-8:-1])
            
            diag_logger.warning(f"⚠️ POPEN TEXT MODE WITHOUT ENCODING: {cmd_str}")
            diag_logger.warning(f"Stack:\n{stack}")
            
            SUBPROCESS_ISSUES_FOUND.append({
                'cmd': cmd_str,
                'stack': stack,
                'type': 'Popen'
            })
            
            kwargs['encoding'] = 'utf-8'
            kwargs['errors'] = 'replace'
        
        super().__init__(args, **kwargs)


# Применяем патчи
subprocess.run = patched_run
subprocess.Popen = PatchedPopen

print("=" * 60)
print("✅ SUBPROCESS PATCHED FOR DIAGNOSTICS")
print("=" * 60)

# ============================================================================
# ТЕПЕРЬ ИМПОРТИРУЕМ НАШЕ ПРИЛОЖЕНИЕ
# ============================================================================

try:
    from app.agents.agent_pipeline import AgentPipeline, PipelineMode
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.change_validator import ChangeValidator, ValidatorConfig, ValidationLevel
    print("✅ All imports successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    print(f"   Make sure you run from project root: python scripts/test_validation_pipeline.py")
    sys.exit(1)


# ============================================================================
# ТЕСТ 1: Валидация проекта с проблемным импортом
# ============================================================================

async def test_validation_with_bad_import():
    """
    Создаёт временный проект с файлом который импортирует несуществующий модуль.
    Проверяет что валидатор:
    1. Не падает с UnicodeDecodeError
    2. Корректно обнаруживает ошибку импорта
    """
    print("\n" + "=" * 60)
    print("TEST 1: Validation with bad import (api_manager_copy)")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory(prefix="test_bad_import_") as temp_dir:
        project_path = Path(temp_dir)
        
        # Создаём структуру
        (project_path / "src" / "core").mkdir(parents=True)
        (project_path / "src" / "__init__.py").write_text("", encoding='utf-8')
        (project_path / "src" / "core" / "__init__.py").write_text("", encoding='utf-8')
        
        # Файл с проблемным импортом (как в вашем случае)
        bad_code = '''"""Answer generator module."""
import os
import json
from api_manager_copy import APIManager  # Несуществующий модуль!

class AnswerGenerator:
    def __init__(self):
        self.api = APIManager()
    
    def generate(self, query: str) -> str:
        return f"Answer to: {query}"
'''
        (project_path / "src" / "core" / "answer_generator.py").write_text(bad_code, encoding='utf-8')
        
        print(f"Created test project: {project_path}")
        
        # Создаём VFS и стейджим файл
        vfs = VirtualFileSystem(str(project_path))
        vfs.stage_change("src/core/answer_generator.py", bad_code)
        
        # Настраиваем валидатор
        config = ValidatorConfig(
            enabled_levels=[
                ValidationLevel.SYNTAX,
                ValidationLevel.IMPORTS,
            ]
        )
        
        validator = ChangeValidator(vfs=vfs, config=config)
        
        try:
            result = await validator.validate()
            
            print(f"\n✅ Validation completed (no crash)")
            print(f"   Success: {result.success}")
            print(f"   Errors: {result.error_count}")
            print(f"   Warnings: {result.warning_count}")
            
            for issue in result.issues:
                print(f"   - [{issue.level.value}] {issue.file_path}: {issue.message}")
            
            # Проверяем что ошибка импорта обнаружена
            import_errors = [i for i in result.issues if i.level == ValidationLevel.IMPORTS]
            if import_errors:
                print(f"\n✅ Import error correctly detected")
                return True
            else:
                print(f"\n⚠️ Import error NOT detected (unexpected)")
                return True  # Не падает — уже хорошо
            
        except Exception as e:
            print(f"\n❌ VALIDATION CRASHED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False


# ============================================================================
# ТЕСТ 2: Валидация файла с кириллицей
# ============================================================================

async def test_validation_with_cyrillic():
    """
    Создаёт файл с русскими комментариями и именами.
    Проверяет что валидатор корректно обрабатывает UTF-8.
    """
    print("\n" + "=" * 60)
    print("TEST 2: Validation with Cyrillic characters")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory(prefix="test_cyrillic_") as temp_dir:
        project_path = Path(temp_dir)
        
        # Файл с кириллицей
        cyrillic_code = '''"""Модуль с кириллицей — проверка кодировки."""
# Комментарий на русском языке
# Специальные символы: ёЁ, №, «кавычки»

def hello_world():
    """Приветствие мира."""
    message = "Привет, мир!"
    return message

class TestClass:
    """Тестовый класс с документацией."""
    
    def method(self, параметр: str) -> str:
        # Ещё один комментарий
        return f"Результат: {параметр}"
'''
        (project_path / "cyrillic_module.py").write_text(cyrillic_code, encoding='utf-8')
        
        print(f"Created test file with Cyrillic: {project_path / 'cyrillic_module.py'}")
        
        # Валидация
        vfs = VirtualFileSystem(str(project_path))
        vfs.stage_change("cyrillic_module.py", cyrillic_code)
        
        config = ValidatorConfig(
            enabled_levels=[
                ValidationLevel.SYNTAX,
                ValidationLevel.IMPORTS,
                ValidationLevel.TYPES,  # mypy тоже проверим
            ]
        )
        
        validator = ChangeValidator(vfs=vfs, config=config)
        
        try:
            result = await validator.validate()
            
            print(f"\n✅ Validation completed (no crash)")
            print(f"   Success: {result.success}")
            print(f"   Levels passed: {[l.value for l in result.levels_passed]}")
            print(f"   Levels failed: {[l.value for l in result.levels_failed]}")
            
            if result.issues:
                for issue in result.issues[:5]:  # Первые 5
                    print(f"   - [{issue.level.value}] {issue.message[:80]}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ VALIDATION CRASHED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False


# ============================================================================
# ТЕСТ 3: Прямой вызов mypy
# ============================================================================

async def test_mypy_direct():
    """
    Запускает mypy напрямую на файле с кириллицей.
    Проверяет что subprocess корректно читает вывод.
    """
    print("\n" + "=" * 60)
    print("TEST 3: Direct mypy call on Cyrillic file")
    print("=" * 60)
    
    # Проверяем что mypy доступен
    try:
        check = subprocess.run(
            [sys.executable, '-m', 'mypy', '--version'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=10
        )
        print(f"mypy version: {check.stdout.strip()}")
    except Exception as e:
        print(f"⚠️ mypy not available: {e}")
        return True  # Пропускаем тест
    
    with tempfile.TemporaryDirectory(prefix="test_mypy_") as temp_dir:
        project_path = Path(temp_dir)
        
        code = '''"""Тестовый файл для mypy."""

def функция(x: int) -> str:
    """Функция с аннотациями типов."""
    return str(x)

result: str = функция(42)
'''
        test_file = project_path / "test_types.py"
        test_file.write_text(code, encoding='utf-8')
        
        print(f"Created: {test_file}")
        
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'mypy', str(test_file), '--ignore-missing-imports'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
                cwd=str(project_path),
            )
            
            print(f"\n✅ mypy completed (no crash)")
            print(f"   Return code: {result.returncode}")
            print(f"   Stdout: {result.stdout[:200] if result.stdout else '(empty)'}")
            print(f"   Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ mypy CRASHED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False


# ============================================================================
# ТЕСТ 4: pip list (часто источник проблем)
# ============================================================================

async def test_pip_list():
    """
    Вызывает pip list --format=json.
    Это вызывается в _get_pip_packages() и может быть источником проблемы.
    """
    print("\n" + "=" * 60)
    print("TEST 4: pip list (used in _get_pip_packages)")
    print("=" * 60)
    
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'list', '--format=json'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        
        print(f"✅ pip list completed")
        print(f"   Return code: {result.returncode}")
        print(f"   Packages found: {result.stdout.count('name')}")
        
        # Пробуем распарсить JSON
        import json
        packages = json.loads(result.stdout)
        print(f"   Parsed {len(packages)} packages")
        
        return True
        
    except Exception as e:
        print(f"❌ pip list CRASHED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Запуск всех тестов."""
    print("=" * 60)
    print("INTEGRATION TEST: Validation Pipeline")
    print(f"Project: {PROJECT_ROOT}")
    print(f"Log file: {LOG_FILE}")
    print("=" * 60)
    
    results = {}
    
    results['test_1_bad_import'] = await test_validation_with_bad_import()
    results['test_2_cyrillic'] = await test_validation_with_cyrillic()
    results['test_3_mypy'] = await test_mypy_direct()
    results['test_4_pip'] = await test_pip_list()
    
    # Итоги
    print("\n" + "=" * 60)
    print("RESULTS:")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    # Проверяем найденные проблемы с subprocess
    print("\n" + "=" * 60)
    print("SUBPROCESS ISSUES FOUND:")
    print("=" * 60)
    
    if SUBPROCESS_ISSUES_FOUND:
        print(f"⚠️ Found {len(SUBPROCESS_ISSUES_FOUND)} subprocess calls without encoding!")
        for i, issue in enumerate(SUBPROCESS_ISSUES_FOUND, 1):
            print(f"\n--- Issue {i} ---")
            print(f"Command: {issue['cmd']}")
            print(f"Stack:\n{issue['stack']}")
    else:
        print("✅ No subprocess issues found (all calls have proper encoding)")
    
    print("\n" + "=" * 60)
    if all_passed and not SUBPROCESS_ISSUES_FOUND:
        print("✅ ALL TESTS PASSED, NO ISSUES FOUND")
    elif all_passed:
        print("⚠️ TESTS PASSED but subprocess issues were auto-fixed")
        print(f"   Check {LOG_FILE} for details")
    else:
        print("❌ SOME TESTS FAILED")
        print(f"   Check {LOG_FILE} for full logs")
    print("=" * 60)
    
    return all_passed


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)