# scripts/test_agent_foundation.py
"""
Полный тестовый скрипт для проверки компонентов Этапа 1 Agent Mode.

Тестирует:
1. VirtualFileSystem - все методы staging, чтения, зависимостей
2. BackupManager - создание, восстановление, сессии
3. FileModifier - все режимы модификации
4. Интеграцию между компонентами
5. Граничные случаи: большие файлы, Unicode, concurrent access, permissions

Запуск:
    python scripts/test_agent_foundation.py
    python scripts/test_agent_foundation.py --verbose
    python scripts/test_agent_foundation.py --skip-slow  # Пропустить медленные тесты

Автор: AI Code Agent
"""

import asyncio
import tempfile
import shutil
import logging
import traceback
import os
import sys
import stat
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Callable, Any, Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import argparse

# Добавляем корень проекта в path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# CLI ARGUMENTS
# ============================================================================

def parse_args():
    """Парсит аргументы командной строки"""
    parser = argparse.ArgumentParser(description="Test Agent Mode Foundation")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--skip-slow", action="store_true", help="Skip slow tests (large files, etc.)")
    parser.add_argument("--filter", "-f", type=str, help="Run only tests matching pattern")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't cleanup temp files (for debugging)")
    return parser.parse_args()


ARGS = parse_args()


# ============================================================================
# LOGGING SETUP
# ============================================================================

log_level = logging.DEBUG if ARGS.verbose else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('test_agent_foundation.log', mode='w', encoding='utf-8')
    ]
)
logger = logging.getLogger("test_foundation")


# ============================================================================
# TEST RESULT TRACKING
# ============================================================================

@dataclass
class TestResult:
    """Результат одного теста"""
    name: str
    passed: bool
    duration_ms: float
    error: str = ""
    details: str = ""
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class TestStats:
    """Статистика тестов по категориям"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_time_ms: float = 0
    by_group: dict = field(default_factory=dict)


class TestRunner:
    """Запускает тесты и собирает результаты"""
    
    def __init__(self):
        self.results: List[TestResult] = []
        self.current_group = ""
        self.stats = TestStats()
    
    def group(self, name: str):
        """Начинает новую группу тестов"""
        self.current_group = name
        print(f"\n{'='*70}")
        print(f"📦 {name}")
        print('='*70)
        logger.info(f"Starting test group: {name}")
        
        if name not in self.stats.by_group:
            self.stats.by_group[name] = {"passed": 0, "failed": 0, "skipped": 0}
    
    def skip_test(self, name: str, reason: str):
        """Пропускает тест"""
        full_name = f"{self.current_group}::{name}" if self.current_group else name
        
        print(f"\n  ⏭️  {name}... SKIPPED ({reason})")
        logger.info(f"SKIPPED: {full_name} - {reason}")
        
        self.results.append(TestResult(
            name=full_name,
            passed=True,
            duration_ms=0,
            skipped=True,
            skip_reason=reason
        ))
        self.stats.skipped += 1
        self.stats.by_group[self.current_group]["skipped"] += 1
    
    async def run_test(self, name: str, test_func: Callable, *args, 
                       slow: bool = False, **kwargs) -> bool:
        """Запускает один тест"""
        full_name = f"{self.current_group}::{name}" if self.current_group else name
        
        # Фильтрация
        if ARGS.filter and ARGS.filter.lower() not in full_name.lower():
            return True
        
        # Пропуск медленных тестов
        if slow and ARGS.skip_slow:
            self.skip_test(name, "slow test (use --skip-slow to include)")
            return True
        
        print(f"\n  🧪 {name}...", end=" ", flush=True)
        logger.info(f"Running test: {full_name}")
        
        start = datetime.now()
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func(*args, **kwargs)
            else:
                result = test_func(*args, **kwargs)
            
            duration = (datetime.now() - start).total_seconds() * 1000
            
            self.results.append(TestResult(
                name=full_name,
                passed=True,
                duration_ms=duration,
                details=str(result) if result else ""
            ))
            
            status = "✅"
            if duration > 1000:
                status = "✅ (slow)"
            print(f"{status} ({duration:.1f}ms)")
            logger.info(f"PASSED: {full_name} ({duration:.1f}ms)")
            
            self.stats.passed += 1
            self.stats.by_group[self.current_group]["passed"] += 1
            return True
            
        except AssertionError as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            error_msg = str(e) or "Assertion failed"
            
            self.results.append(TestResult(
                name=full_name,
                passed=False,
                duration_ms=duration,
                error=error_msg
            ))
            print(f"❌ ASSERTION: {error_msg}")
            logger.error(f"FAILED: {full_name} - {error_msg}")
            logger.debug(traceback.format_exc())
            
            self.stats.failed += 1
            self.stats.by_group[self.current_group]["failed"] += 1
            return False
            
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            error_msg = f"{type(e).__name__}: {e}"
            
            self.results.append(TestResult(
                name=full_name,
                passed=False,
                duration_ms=duration,
                error=error_msg
            ))
            print(f"❌ ERROR: {error_msg}")
            logger.error(f"ERROR: {full_name} - {error_msg}")
            logger.error(traceback.format_exc())
            
            self.stats.failed += 1
            self.stats.by_group[self.current_group]["failed"] += 1
            return False
        finally:
            self.stats.total += 1
            self.stats.total_time_ms += (datetime.now() - start).total_seconds() * 1000
    
    def summary(self) -> bool:
        """Выводит итоговую сводку"""
        print("\n" + "="*70)
        print("📊 ИТОГОВАЯ СВОДКА")
        print("="*70)
        
        # По группам
        print("\n  📁 ПО ГРУППАМ:")
        for group, stats in self.stats.by_group.items():
            passed = stats["passed"]
            failed = stats["failed"]
            skipped = stats["skipped"]
            total = passed + failed + skipped
            
            if failed > 0:
                status = "❌"
            elif skipped == total:
                status = "⏭️"
            else:
                status = "✅"
            
            print(f"     {status} {group}: {passed}/{total} passed", end="")
            if skipped > 0:
                print(f" ({skipped} skipped)", end="")
            if failed > 0:
                print(f" ({failed} FAILED)", end="")
            print()
        
        # Общая статистика
        print(f"\n  📈 ОБЩАЯ СТАТИСТИКА:")
        print(f"     Всего тестов: {self.stats.total}")
        print(f"     ✅ Пройдено: {self.stats.passed}")
        print(f"     ❌ Провалено: {self.stats.failed}")
        print(f"     ⏭️  Пропущено: {self.stats.skipped}")
        print(f"     ⏱️  Общее время: {self.stats.total_time_ms:.1f}ms")
        
        if self.stats.failed > 0:
            print("\n  ❌ ПРОВАЛЕНЫ:")
            for r in self.results:
                if not r.passed and not r.skipped:
                    print(f"     • {r.name}")
                    print(f"       └─ {r.error}")
        
        print("\n" + "="*70)
        
        if self.stats.failed == 0:
            if self.stats.skipped > 0:
                print(f"🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! ({self.stats.skipped} пропущено)")
            else:
                print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        else:
            print(f"⚠️  {self.stats.failed} ТЕСТ(ОВ) ПРОВАЛЕНО")
        
        print("="*70)
        
        logger.info(f"Test summary: {self.stats.passed}/{self.stats.total} passed, "
                   f"{self.stats.failed} failed, {self.stats.skipped} skipped")
        
        return self.stats.failed == 0


# ============================================================================
# TEST FIXTURES
# ============================================================================

class TestFixture:
    """Создаёт и управляет тестовым окружением"""
    
    def __init__(self):
        self.temp_dir: Optional[Path] = None
        self.project_root: Optional[Path] = None
        self._cleanup_enabled = not ARGS.no_cleanup
    
    def setup(self):
        """Создаёт временную директорию с тестовой структурой проекта"""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="agent_test_"))
        self.project_root = self.temp_dir
        
        logger.info(f"Created test directory: {self.temp_dir}")
        
        self._create_project_structure()
        
        return self
    
    def _create_project_structure(self):
        """Создаёт структуру тестового проекта"""
        
        # app/__init__.py
        (self.project_root / "app").mkdir(parents=True)
        (self.project_root / "app" / "__init__.py").write_text("", encoding='utf-8')
        
        # app/services/__init__.py
        (self.project_root / "app" / "services").mkdir()
        (self.project_root / "app" / "services" / "__init__.py").write_text("", encoding='utf-8')
        
        # app/services/auth.py
        auth_content = '''"""Authentication service"""
from typing import Optional

class AuthService:
    """Handles user authentication"""
    
    def __init__(self, secret_key: str):
        self.secret_key = secret_key
        self.users = {}
    
    def login(self, username: str, password: str) -> bool:
        """Authenticate user"""
        if username in self.users:
            return self.users[username] == password
        return False
    
    def register(self, username: str, password: str) -> bool:
        """Register new user"""
        if username in self.users:
            return False
        self.users[username] = password
        return True
'''
        (self.project_root / "app" / "services" / "auth.py").write_text(auth_content, encoding='utf-8')
        
        # app/services/user.py (импортирует auth)
        user_content = '''"""User management service"""
from app.services.auth import AuthService

class UserService:
    """Manages user operations"""
    
    def __init__(self, auth: AuthService):
        self.auth = auth
    
    def get_user_info(self, username: str) -> dict:
        """Get user information"""
        return {"username": username, "active": True}
'''
        (self.project_root / "app" / "services" / "user.py").write_text(user_content, encoding='utf-8')
        
        # app/utils/__init__.py
        (self.project_root / "app" / "utils").mkdir()
        (self.project_root / "app" / "utils" / "__init__.py").write_text("", encoding='utf-8')
        
        # app/utils/helpers.py
        helpers_content = '''"""Utility helpers"""

def format_name(first: str, last: str) -> str:
    """Format full name"""
    return f"{first} {last}"

def validate_email(email: str) -> bool:
    """Simple email validation"""
    return "@" in email and "." in email
'''
        (self.project_root / "app" / "utils" / "helpers.py").write_text(helpers_content, encoding='utf-8')
        
        # app/main.py (импортирует несколько модулей)
        main_content = '''"""Main application entry point"""
from app.services.auth import AuthService
from app.services.user import UserService
from app.utils.helpers import format_name, validate_email

def main():
    auth = AuthService("secret")
    user_service = UserService(auth)
    
    name = format_name("John", "Doe")
    print(f"Hello, {name}!")

if __name__ == "__main__":
    main()
'''
        (self.project_root / "app" / "main.py").write_text(main_content, encoding='utf-8')
        
        logger.info("Created test project structure")
    
    def cleanup(self):
        """Удаляет временную директорию"""
        if not self._cleanup_enabled:
            logger.info(f"Skipping cleanup (--no-cleanup): {self.temp_dir}")
            return
            
        if self.temp_dir and self.temp_dir.exists():
            # Восстанавливаем права перед удалением (для тестов permissions)
            self._restore_permissions(self.temp_dir)
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up test directory: {self.temp_dir}")
    
    def _restore_permissions(self, path: Path):
        """Восстанавливает права доступа для удаления"""
        try:
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    dir_path = Path(root) / d
                    try:
                        dir_path.chmod(stat.S_IRWXU)
                    except:
                        pass
                for f in files:
                    file_path = Path(root) / f
                    try:
                        file_path.chmod(stat.S_IRWXU)
                    except:
                        pass
        except:
            pass
    
    def read_file(self, rel_path: str) -> str:
        """Читает файл из тестового проекта"""
        return (self.project_root / rel_path).read_text(encoding='utf-8')
    
    def write_file(self, rel_path: str, content: str):
        """Записывает файл в тестовый проект"""
        path = self.project_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    
    def file_exists(self, rel_path: str) -> bool:
        """Проверяет существование файла"""
        return (self.project_root / rel_path).exists()
    
    def make_readonly(self, rel_path: str):
        """Делает файл read-only"""
        path = self.project_root / rel_path
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    
    def make_writable(self, rel_path: str):
        """Восстанавливает права записи"""
        path = self.project_root / rel_path
        path.chmod(stat.S_IRWXU)


# ============================================================================
# VIRTUAL FILE SYSTEM TESTS
# ============================================================================

async def test_vfs_initialization(fixture: TestFixture):
    """Тест инициализации VFS"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    assert vfs.project_root == fixture.project_root
    assert len(vfs._pending_changes) == 0
    assert not vfs.has_pending_changes()
    
    return f"Initialized at {vfs.project_root}"


async def test_vfs_staging_new_file(fixture: TestFixture):
    """Тест стейджинга нового файла"""
    from app.services.virtual_fs import VirtualFileSystem, ChangeType
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    new_code = "# New file\nprint('hello')\n"
    change = vfs.stage_change("app/new_module.py", new_code)
    
    assert change.file_path == "app/new_module.py"
    assert change.is_new_file == True
    assert change.change_type == ChangeType.CREATE
    assert change.new_content == new_code
    assert change.original_content is None
    assert vfs.has_pending_changes()
    
    return f"Staged new file: {change.file_path}"


async def test_vfs_staging_modification(fixture: TestFixture):
    """Тест стейджинга модификации существующего файла"""
    from app.services.virtual_fs import VirtualFileSystem, ChangeType
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    original = fixture.read_file("app/utils/helpers.py")
    modified = original + "\ndef new_function():\n    pass\n"
    
    change = vfs.stage_change("app/utils/helpers.py", modified)
    
    assert change.is_new_file == False
    assert change.change_type == ChangeType.MODIFY
    assert change.original_content == original
    assert change.new_content == modified
    assert change.lines_added > 0
    
    return f"Lines added: {change.lines_added}, removed: {change.lines_removed}"


async def test_vfs_staging_deletion(fixture: TestFixture):
    """Тест стейджинга удаления файла"""
    from app.services.virtual_fs import VirtualFileSystem, ChangeType
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    change = vfs.stage_deletion("app/utils/helpers.py")
    
    assert change.change_type == ChangeType.DELETE
    assert change.new_content == ""
    
    # Файл должен "не существовать" через VFS
    assert not vfs.file_exists("app/utils/helpers.py")
    
    return "Staged for deletion"


async def test_vfs_read_staged(fixture: TestFixture):
    """Тест чтения файла с pending changes"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Читаем оригинал
    original = vfs.read_file("app/utils/helpers.py")
    assert "format_name" in original
    
    # Стейджим изменение
    modified = "# Completely different content\n"
    vfs.stage_change("app/utils/helpers.py", modified)
    
    # Читаем через VFS - должен вернуть staged версию
    content = vfs.read_file("app/utils/helpers.py")
    assert content == modified
    assert "Completely different" in content
    assert "format_name" not in content
    
    return "Read returns staged content"


async def test_vfs_read_original(fixture: TestFixture):
    """Тест чтения оригинального файла (без pending changes)"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    original = fixture.read_file("app/utils/helpers.py")
    
    # Стейджим изменение
    vfs.stage_change("app/utils/helpers.py", "# Modified")
    
    # read_file_original должен вернуть оригинал
    read_original = vfs.read_file_original("app/utils/helpers.py")
    assert read_original == original
    assert "format_name" in read_original
    
    return "read_file_original works correctly"


async def test_vfs_unstage(fixture: TestFixture):
    """Тест отмены стейджинга"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    vfs.stage_change("app/test1.py", "content1")
    vfs.stage_change("app/test2.py", "content2")
    
    assert len(vfs._pending_changes) == 2
    
    # Отменяем один
    result = vfs.unstage("app/test1.py")
    assert result == True
    assert len(vfs._pending_changes) == 1
    assert "app/test1.py" not in vfs._pending_changes
    
    # Повторная отмена
    result = vfs.unstage("app/test1.py")
    assert result == False
    
    return "Unstage works correctly"


async def test_vfs_discard_all(fixture: TestFixture):
    """Тест отмены всех изменений"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    vfs.stage_change("app/test1.py", "content1")
    vfs.stage_change("app/test2.py", "content2")
    vfs.stage_change("app/test3.py", "content3")
    
    count = vfs.discard_all()
    
    assert count == 3
    assert len(vfs._pending_changes) == 0
    assert not vfs.has_pending_changes()
    
    return f"Discarded {count} changes"


async def test_vfs_affected_files_dependents(fixture: TestFixture):
    """Тест поиска зависимых файлов (кто импортирует изменённый)"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Стейджим изменение auth.py
    original = fixture.read_file("app/services/auth.py")
    vfs.stage_change("app/services/auth.py", original + "\n# Modified")
    
    affected = vfs.get_affected_files()
    
    # user.py и main.py импортируют auth.py
    assert "app/services/auth.py" in affected.changed_files
    dependents = affected.dependent_files
    assert "app/services/user.py" in dependents or "app/main.py" in dependents
    
    return f"Dependents: {list(affected.dependent_files)}"


async def test_vfs_affected_files_dependencies(fixture: TestFixture):
    """Тест поиска зависимостей (кого импортирует изменённый)"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # main.py импортирует auth, user, helpers
    original = fixture.read_file("app/main.py")
    vfs.stage_change("app/main.py", original + "\n# Modified")
    
    affected = vfs.get_affected_files()
    
    # main.py зависит от auth.py, user.py, helpers.py
    deps = affected.dependency_files
    assert any("auth" in d for d in deps) or any("user" in d for d in deps)
    
    return f"Dependencies: {list(deps)}"


async def test_vfs_diff_generation(fixture: TestFixture):
    """Тест генерации diff"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    original = fixture.read_file("app/utils/helpers.py")
    modified = original.replace("format_name", "format_full_name")
    
    vfs.stage_change("app/utils/helpers.py", modified)
    
    diff = vfs.get_diff("app/utils/helpers.py")
    
    assert diff is not None
    assert "-" in diff and "+" in diff  # Должны быть удалённые и добавленные строки
    assert "format_name" in diff or "format_full_name" in diff
    
    return f"Diff length: {len(diff)} chars"


async def test_vfs_get_all_diffs(fixture: TestFixture):
    """Тест получения diff для всех файлов"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    vfs.stage_change("app/test1.py", "# Test 1")
    vfs.stage_change("app/test2.py", "# Test 2")
    
    diffs = vfs.get_all_diffs()
    
    assert len(diffs) == 2
    assert "app/test1.py" in diffs
    assert "app/test2.py" in diffs
    
    return f"Got diffs for {len(diffs)} files"


async def test_vfs_status(fixture: TestFixture):
    """Тест получения статуса"""
    from app.services.virtual_fs import VirtualFileSystem, ChangeType
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    vfs.stage_change("app/new1.py", "# new1")
    vfs.stage_change("app/utils/helpers.py", fixture.read_file("app/utils/helpers.py") + "\n# mod")
    
    status = vfs.get_status()
    
    assert status["has_changes"] == True
    assert status["staged_count"] == 2
    assert len(status["staged_files"]) == 2
    
    # Проверяем типы
    types = [f["type"] for f in status["staged_files"]]
    assert "create" in types
    assert "modify" in types
    
    return f"Status: {status['staged_count']} files staged"


async def test_vfs_format_status_message(fixture: TestFixture):
    """Тест форматирования статуса для пользователя"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    vfs.stage_change("app/new_file.py", "# New file\nprint('hello')\n")
    
    status_msg = vfs.format_status_message()
    
    assert "Staging Area" in status_msg
    assert "app/new_file.py" in status_msg
    
    return "Status message formatted"


async def test_vfs_commit_sync(fixture: TestFixture):
    """Тест синхронного коммита"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Создаём новый файл
    new_content = "# New module\nprint('created')\n"
    vfs.stage_change("app/created.py", new_content)
    
    # Модифицируем существующий
    modified = fixture.read_file("app/utils/helpers.py") + "\n# MODIFIED\n"
    vfs.stage_change("app/utils/helpers.py", modified)
    
    # Коммитим
    result = vfs.commit_all_sync()
    
    assert result.success == True
    assert "app/created.py" in result.created_files
    assert "app/utils/helpers.py" in result.applied_files
    assert len(result.errors) == 0
    
    # Проверяем, что файлы реально изменились
    assert fixture.file_exists("app/created.py")
    assert "# MODIFIED" in fixture.read_file("app/utils/helpers.py")
    
    # Staging должен быть пустым
    assert not vfs.has_pending_changes()
    
    return f"Committed: {result.total_files} files"


async def test_vfs_commit_with_backup(fixture: TestFixture):
    """Тест коммита с бэкапом"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.backup_manager import BackupManager
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    backup = BackupManager(str(fixture.project_root))
    
    # Запоминаем оригинал
    original = fixture.read_file("app/services/auth.py")
    
    # Модифицируем
    modified = original.replace("AuthService", "AuthenticationService")
    vfs.stage_change("app/services/auth.py", modified)
    
    # Коммитим с бэкапом
    result = vfs.commit_all_sync(backup_manager=backup)
    
    assert result.success == True
    assert "app/services/auth.py" in result.backups
    
    # Проверяем, что файл изменился
    assert "AuthenticationService" in fixture.read_file("app/services/auth.py")
    
    # Восстанавливаем из бэкапа
    sessions = backup.list_sessions()
    assert len(sessions) > 0
    
    backup.restore_file("app/services/auth.py", sessions[0].session_id)
    assert "AuthService" in fixture.read_file("app/services/auth.py")
    assert "AuthenticationService" not in fixture.read_file("app/services/auth.py")
    
    return "Backup created and restored"


async def test_vfs_multiple_modifications_same_file(fixture: TestFixture):
    """Тест множественных изменений одного файла"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Первое изменение
    vfs.stage_change("app/utils/helpers.py", "# Version 1")
    assert vfs.read_file("app/utils/helpers.py") == "# Version 1"
    
    # Второе изменение перезаписывает первое
    vfs.stage_change("app/utils/helpers.py", "# Version 2")
    assert vfs.read_file("app/utils/helpers.py") == "# Version 2"
    
    # Должен быть только один pending change
    assert len(vfs._pending_changes) == 1
    
    return "Multiple modifications handled correctly"


async def test_vfs_empty_file(fixture: TestFixture):
    """Тест работы с пустыми файлами"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Создаём пустой файл
    vfs.stage_change("app/empty.py", "")
    
    change = vfs._pending_changes.get("app/empty.py")
    assert change is not None
    assert change.new_content == ""
    assert change.lines_added == 0
    
    # Коммитим
    result = vfs.commit_all_sync()
    assert result.success
    
    # Проверяем, что пустой файл создан
    assert fixture.file_exists("app/empty.py")
    assert fixture.read_file("app/empty.py") == ""
    
    return "Empty file handled correctly"


async def test_vfs_get_all_python_files(fixture: TestFixture):
    """Тест получения списка всех Python файлов"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    py_files = vfs.get_all_python_files()
    
    assert len(py_files) > 0
    assert any("auth.py" in f for f in py_files)
    assert any("helpers.py" in f for f in py_files)
    
    return f"Found {len(py_files)} Python files"


async def test_vfs_find_test_files(fixture: TestFixture):
    """Тест поиска тестовых файлов"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Создаём тестовый файл
    fixture.write_file("tests/test_auth.py", "# Test for auth")
    
    tests = vfs.find_test_files("app/services/auth.py")
    
    # Должен найти test_auth.py
    assert any("test_auth" in t for t in tests)
    
    return f"Found test files: {tests}"


async def test_vfs_invalidate_cache(fixture: TestFixture):
    """Тест инвалидации кэша"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Заполняем кэш
    _ = vfs.get_affected_files()
    _ = vfs._iter_python_files()
    
    assert vfs._affected_cache is not None or vfs._python_files_cache is not None
    
    # Инвалидируем
    vfs.invalidate_cache()
    
    assert vfs._affected_cache is None
    assert vfs._python_files_cache is None
    
    return "Cache invalidated"


async def test_vfs_pending_changes_property(fixture: TestFixture):
    """Тест свойства pending_changes"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    vfs.stage_change("app/test.py", "# Test")
    
    # Проверяем доступ к _pending_changes
    assert "app/test.py" in vfs._pending_changes
    change = vfs._pending_changes["app/test.py"]
    assert change.new_content == "# Test"
    
    return "pending_changes accessible"


# ============================================================================
# BACKUP MANAGER TESTS
# ============================================================================

async def test_backup_initialization(fixture: TestFixture):
    """Тест инициализации BackupManager"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    assert backup.project_root == fixture.project_root
    assert backup.backup_root.exists()
    
    return f"Backup root: {backup.backup_root}"


async def test_backup_session_lifecycle(fixture: TestFixture):
    """Тест жизненного цикла сессии"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    # Начинаем сессию
    session = backup.start_session("Test session")
    
    assert session.session_id is not None
    assert session.description == "Test session"
    assert backup.get_current_session() is not None
    
    # Создаём бэкапы
    path1 = backup.create_backup("app/services/auth.py")
    path2 = backup.create_backup("app/utils/helpers.py")
    
    assert path1 is not None
    assert path2 is not None
    assert len(session.backups) == 2
    
    # Завершаем сессию
    ended = backup.end_session()
    
    assert ended.session_id == session.session_id
    assert backup.get_current_session() is None
    
    return f"Session {session.session_id} with {len(session.backups)} backups"


async def test_backup_restore_file(fixture: TestFixture):
    """Тест восстановления отдельного файла"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    # Запоминаем оригинал
    original = fixture.read_file("app/utils/helpers.py")
    
    # Создаём бэкап
    session = backup.start_session("Restore test")
    backup.create_backup("app/utils/helpers.py")
    backup.end_session()
    
    # Портим файл
    fixture.write_file("app/utils/helpers.py", "# CORRUPTED FILE")
    assert "CORRUPTED" in fixture.read_file("app/utils/helpers.py")
    
    # Восстанавливаем
    success = backup.restore_file("app/utils/helpers.py", session.session_id)
    
    assert success == True
    assert fixture.read_file("app/utils/helpers.py") == original
    
    return "File restored successfully"


async def test_backup_restore_session(fixture: TestFixture):
    """Тест восстановления всей сессии"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    # Запоминаем оригиналы
    originals = {
        "app/services/auth.py": fixture.read_file("app/services/auth.py"),
        "app/utils/helpers.py": fixture.read_file("app/utils/helpers.py"),
    }
    
    # Создаём сессию с несколькими бэкапами
    session = backup.start_session("Multi-file restore")
    for path in originals:
        backup.create_backup(path)
    backup.end_session()
    
    # Портим все файлы
    for path in originals:
        fixture.write_file(path, f"# CORRUPTED: {path}")
    
    # Восстанавливаем сессию
    results = backup.restore_session(session.session_id)
    
    assert len(results) == 2
    assert all(results.values())
    
    # Проверяем содержимое
    for path, original in originals.items():
        assert fixture.read_file(path) == original
    
    return f"Restored {len(results)} files"


async def test_backup_list_sessions(fixture: TestFixture):
    """Тест списка сессий"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    # Создаём несколько сессий
    for i in range(3):
        session = backup.start_session(f"Session {i}")
        backup.create_backup("app/services/auth.py")
        backup.end_session()
    
    sessions = backup.list_sessions()
    
    assert len(sessions) >= 3
    assert sessions[0].description == "Session 2"  # Последняя первой
    
    return f"Listed {len(sessions)} sessions"


async def test_backup_nonexistent_file(fixture: TestFixture):
    """Тест бэкапа несуществующего файла"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    session = backup.start_session("Test")
    result = backup.create_backup("nonexistent/file.py")
    backup.end_session()
    
    assert result is None
    
    return "Correctly handles nonexistent files"


async def test_backup_duplicate_file_in_session(fixture: TestFixture):
    """Тест повторного бэкапа того же файла в сессии"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    session = backup.start_session("Duplicate test")
    
    # Первый бэкап
    path1 = backup.create_backup("app/services/auth.py")
    assert path1 is not None
    
    # Повторный бэкап того же файла
    path2 = backup.create_backup("app/services/auth.py")
    
    # Должен вернуть тот же путь или создать новый (зависит от реализации)
    # Главное - не должен падать
    
    backup.end_session()
    
    return "Duplicate backup handled"


async def test_backup_empty_session(fixture: TestFixture):
    """Тест пустой сессии (без бэкапов)"""
    from app.services.backup_manager import BackupManager
    
    backup = BackupManager(str(fixture.project_root))
    
    session = backup.start_session("Empty session")
    ended = backup.end_session()
    
    assert ended is not None
    assert len(ended.backups) == 0
    
    return "Empty session handled"


# ============================================================================
# FILE MODIFIER TESTS
# ============================================================================

async def test_modifier_replace_file(fixture: TestFixture):
    """Тест замены всего файла"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = "# Old content\nold_var = 1\n"
    new_code = "# New content\nnew_var = 2\n"
    
    result = modifier.apply(
        existing,
        ModifyInstruction(mode=ModifyMode.REPLACE_FILE, code=new_code)
    )
    
    assert result.success == True
    assert result.new_content == new_code
    assert "New content" in result.new_content
    
    return result.message


async def test_modifier_insert_into_class(fixture: TestFixture):
    """Тест вставки метода в класс"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''class MyClass:
    def __init__(self):
        self.value = 0
    
    def existing_method(self):
        pass
'''
    
    new_method = '''def new_method(self):
    return self.value * 2'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="MyClass",
            insert_after="__init__"
        )
    )
    
    assert result.success == True
    assert "def new_method(self):" in result.new_content
    assert result.lines_added > 0
    
    # Проверяем отступы (должны быть 4 пробела внутри класса)
    lines = result.new_content.splitlines()
    method_line = next(l for l in lines if "def new_method" in l)
    indent = len(method_line) - len(method_line.lstrip())
    assert indent == 4, f"Expected indent 4, got {indent}"
    
    return f"Inserted with {indent} spaces indent"


async def test_modifier_replace_method(fixture: TestFixture):
    """Тест замены метода в классе"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''class Calculator:
    def add(self, a, b):
        return a + b
    
    def subtract(self, a, b):
        return a - b
'''
    
    new_add = '''def add(self, a, b):
    """Enhanced add with logging"""
    result = a + b
    print(f"Adding {a} + {b} = {result}")
    return result'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.REPLACE_METHOD,
            code=new_add,
            target_class="Calculator",
            target_method="add"
        )
    )
    
    assert result.success == True
    assert "Enhanced add with logging" in result.new_content
    assert "def subtract" in result.new_content  # Другой метод не затронут
    
    return result.message


async def test_modifier_replace_function(fixture: TestFixture):
    """Тест замены функции на уровне модуля"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''def greet(name):
    return f"Hello, {name}"

def farewell(name):
    return f"Goodbye, {name}"
'''
    
    new_greet = '''def greet(name):
    """Enhanced greeting"""
    return f"Hello, dear {name}! Welcome!"'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.REPLACE_FUNCTION,
            code=new_greet,
            target_function="greet"
        )
    )
    
    assert result.success == True
    assert "Enhanced greeting" in result.new_content
    assert "dear {name}" in result.new_content
    assert "def farewell" in result.new_content
    
    return result.message


async def test_modifier_replace_class(fixture: TestFixture):
    """Тест замены класса"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''class OldClass:
    def method(self):
        pass

class OtherClass:
    pass
'''
    
    new_class = '''class OldClass:
    """Completely rewritten class"""
    
    def __init__(self):
        self.initialized = True
    
    def new_method(self):
        return "new"'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.REPLACE_CLASS,
            code=new_class,
            target_class="OldClass"
        )
    )
    
    assert result.success == True
    assert "Completely rewritten" in result.new_content
    assert "class OtherClass" in result.new_content
    
    return result.message


async def test_modifier_insert_import(fixture: TestFixture):
    """Тест добавления импорта"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''import os
from typing import List

def main():
    pass
'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.INSERT_IMPORT,
            code="from pathlib import Path"
        )
    )
    
    assert result.success == True
    assert "from pathlib import Path" in result.new_content
    assert result.lines_added == 1
    
    # Проверяем, что импорт не дублируется
    result2 = modifier.apply(
        result.new_content,
        ModifyInstruction(
            mode=ModifyMode.INSERT_IMPORT,
            code="from pathlib import Path"
        )
    )
    
    assert "Import already exists" in result2.message
    
    return "Import added correctly"


async def test_modifier_append_to_file(fixture: TestFixture):
    """Тест добавления в конец файла"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''# Header
def existing():
    pass'''
    
    new_code = '''def appended():
    print("I was appended!")'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.APPEND_TO_FILE,
            code=new_code
        )
    )
    
    assert result.success == True
    assert "def appended():" in result.new_content
    assert result.new_content.index("def existing") < result.new_content.index("def appended")
    
    return result.message


async def test_modifier_smart_apply(fixture: TestFixture):
    """Тест умного применения кода"""
    from app.services.file_modifier import FileModifier
    
    modifier = FileModifier()
    
    existing = '''class Service:
    def __init__(self):
        pass
'''
    
    # Умное добавление метода в класс
    new_method = '''def process(self):
    return "processed"'''
    
    result = modifier.smart_apply(
        existing_content=existing,
        code=new_method,
        context="Service"
    )
    
    assert result.success == True
    assert "def process(self):" in result.new_content
    
    return result.message


async def test_modifier_class_not_found(fixture: TestFixture):
    """Тест обработки несуществующего класса"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''class RealClass:
    pass
'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code="def method(self): pass",
            target_class="NonExistentClass"
        )
    )
    
    assert result.success == False
    assert "not found" in result.message.lower()
    
    return f"Correctly failed: {result.message}"


async def test_modifier_syntax_error_handling(fixture: TestFixture):
    """Тест обработки синтаксически неверного кода"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    # Синтаксически неверный existing code
    existing = '''def broken(
    # Missing closing parenthesis
'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code="def method(self): pass",
            target_class="SomeClass"
        )
    )
    
    assert result.success == False
    assert "syntax" in result.message.lower() or "error" in result.message.lower()
    
    return "Correctly handled syntax error"


async def test_modifier_indent_detection(fixture: TestFixture):
    """Тест определения стиля отступов"""
    from app.services.file_modifier import FileModifier
    
    modifier = FileModifier()
    
    # 2 пробела
    code_2_spaces = '''class A:
  def method(self):
    pass'''
    
    indent, style = modifier.detect_indent_style(code_2_spaces)
    assert style == "spaces"
    
    # Табы
    code_tabs = '''class A:
\tdef method(self):
\t\tpass'''
    
    indent, style = modifier.detect_indent_style(code_tabs)
    assert style == "tabs"
    
    return "Detected indent styles correctly"


async def test_modifier_apply_to_vfs(fixture: TestFixture):
    """Тест интеграции FileModifier с VFS"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    modifier = FileModifier()
    
    # Применяем через VFS
    result = modifier.apply_to_vfs(
        vfs,
        "app/services/auth.py",
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code="def logout(self): pass",
            target_class="AuthService"
        )
    )
    
    assert result.success == True
    assert vfs.has_pending_changes()
    assert "def logout(self):" in vfs.read_file("app/services/auth.py")
    
    return "VFS integration works"


async def test_modifier_preserve_trailing_newline(fixture: TestFixture):
    """Тест сохранения trailing newline"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    # Файл с trailing newline
    existing = "def func():\n    pass\n"
    new_code = "def func():\n    return 42\n"
    
    result = modifier.apply(
        existing,
        ModifyInstruction(mode=ModifyMode.REPLACE_FILE, code=new_code)
    )
    
    assert result.new_content.endswith("\n")
    
    return "Trailing newline preserved"


async def test_modifier_method_with_decorators(fixture: TestFixture):
    """Тест замены метода с декораторами"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''class Service:
    @property
    def value(self):
        return self._value
    
    @staticmethod
    def helper():
        pass
'''
    
    new_method = '''@property
def value(self):
    """Updated property"""
    return self._value * 2'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.REPLACE_METHOD,
            code=new_method,
            target_class="Service",
            target_method="value"
        )
    )
    
    assert result.success == True
    assert "Updated property" in result.new_content
    assert "@staticmethod" in result.new_content  # Другой метод не затронут
    
    return "Method with decorators replaced"


async def test_modifier_nested_class(fixture: TestFixture):
    """Тест работы с вложенными классами"""
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    modifier = FileModifier()
    
    existing = '''class Outer:
    class Inner:
        def inner_method(self):
            pass
    
    def outer_method(self):
        pass
'''
    
    # Добавляем метод во внешний класс
    new_method = '''def new_outer(self):
    return "outer"'''
    
    result = modifier.apply(
        existing,
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="Outer"
        )
    )
    
    assert result.success == True
    assert "def new_outer(self):" in result.new_content
    assert "class Inner:" in result.new_content
    
    return "Nested class handled"


async def test_modifier_mode_mapping(fixture: TestFixture):
    """Тест маппинга MODE_MAPPING"""
    from app.services.file_modifier import FileModifier, ModifyMode
    
    modifier = FileModifier()
    
    # Проверяем, что все ключевые режимы есть
    assert "REPLACE_FILE" in modifier.MODE_MAPPING
    assert "INSERT_CLASS" in modifier.MODE_MAPPING
    assert "REPLACE_METHOD" in modifier.MODE_MAPPING
    assert "INSERT_IMPORT" in modifier.MODE_MAPPING
    
    # Проверяем алиасы
    assert "CREATE_FILE" in modifier.MODE_MAPPING
    assert "ADD_METHOD" in modifier.MODE_MAPPING
    
    # Проверяем значения
    assert modifier.MODE_MAPPING["REPLACE_FILE"] == ModifyMode.REPLACE_FILE
    assert modifier.MODE_MAPPING["INSERT_CLASS"] == ModifyMode.INSERT_INTO_CLASS
    
    return f"MODE_MAPPING has {len(modifier.MODE_MAPPING)} entries"


async def test_modifier_parsed_code_block(fixture: TestFixture):
    """Тест ParsedCodeBlock и apply_code_block"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    modifier = FileModifier()
    
    existing = '''class MyService:
    def __init__(self):
        pass
'''
    
    block = ParsedCodeBlock(
        file_path="app/service.py",
        mode="INSERT_CLASS",
        code="def new_method(self): return 42",
        target_class="MyService"
    )
    
    result = modifier.apply_code_block(existing, block)
    
    assert result.success == True
    assert "def new_method(self):" in result.new_content
    assert "Applied CODE_BLOCK" in result.changes_made[0]
    
    return "ParsedCodeBlock applied successfully"


async def test_modifier_apply_code_block_to_vfs(fixture: TestFixture):
    """Тест apply_code_block_to_vfs"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    modifier = FileModifier()
    
    block = ParsedCodeBlock(
        file_path="app/services/auth.py",
        mode="INSERT_CLASS",
        code="def verify(self): return True",
        target_class="AuthService"
    )
    
    result = modifier.apply_code_block_to_vfs(vfs, block)
    
    assert result.success == True
    assert vfs.has_pending_changes()
    assert "def verify(self):" in vfs.read_file("app/services/auth.py")
    
    return "apply_code_block_to_vfs works"


async def test_modifier_invalid_mode(fixture: TestFixture):
    """Тест обработки неизвестного MODE"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    modifier = FileModifier()
    
    existing = "# Some code"
    
    block = ParsedCodeBlock(
        file_path="test.py",
        mode="UNKNOWN_MODE",
        code="print('test')"
    )
    
    result = modifier.apply_code_block(existing, block)
    
    assert result.success == False
    assert "Unknown MODE" in result.message
    
    return "Invalid mode handled correctly"


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

async def test_large_file_handling(fixture: TestFixture):
    """Тест работы с большими файлами (1MB+)"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Генерируем большой файл (~1MB)
    lines = []
    for i in range(20000):
        lines.append(f"def function_{i}():")
        lines.append(f"    '''Function number {i}'''")
        lines.append(f"    return {i}")
        lines.append("")
    
    large_content = "\n".join(lines)
    size_mb = len(large_content) / (1024 * 1024)
    
    # Стейджим
    start = time.time()
    change = vfs.stage_change("app/large_file.py", large_content)
    stage_time = time.time() - start
    
    assert change is not None
    assert change.lines_added > 10000
    
    # Коммитим
    start = time.time()
    result = vfs.commit_all_sync()
    commit_time = time.time() - start
    
    assert result.success
    assert fixture.file_exists("app/large_file.py")
    
    return f"Handled {size_mb:.2f}MB file (stage: {stage_time:.2f}s, commit: {commit_time:.2f}s)"


async def test_unicode_content(fixture: TestFixture):
    """Тест работы с Unicode контентом"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Контент с Unicode
    unicode_content = '''# -*- coding: utf-8 -*-
"""
Модуль с Unicode символами
Поддержка: 日本語, 中文, العربية, עברית, Ελληνικά
Эмодзи: 🚀 🎉 ✅ ❌ 🔥
"""

def приветствие(имя: str) -> str:
    """Функция приветствия на русском"""
    return f"Привет, {имя}! 👋"

def 計算(値: int) -> int:
    """Japanese function name"""
    return 値 * 2

# Комментарий: αβγδ, ΑΒΓΔ
КОНСТАНТА = "Значение с юникодом: ñ é ü ö"
'''
    
    # Стейджим и коммитим
    vfs.stage_change("app/unicode_module.py", unicode_content)
    result = vfs.commit_all_sync()
    
    assert result.success
    
    # Читаем обратно и проверяем
    read_content = fixture.read_file("app/unicode_module.py")
    assert "Привет" in read_content
    assert "日本語" in read_content
    assert "🚀" in read_content
    assert "приветствие" in read_content
    
    return "Unicode content handled correctly"


async def test_unicode_file_path(fixture: TestFixture):
    """Тест работы с Unicode в путях файлов"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Создаём директорию с Unicode именем
    unicode_dir = fixture.project_root / "модули"
    unicode_dir.mkdir(exist_ok=True)
    
    # Создаём файл с Unicode именем
    unicode_path = "модули/сервис.py"
    content = "# Unicode path test\ndef функция(): pass\n"
    
    vfs.stage_change(unicode_path, content)
    result = vfs.commit_all_sync()
    
    assert result.success
    assert fixture.file_exists(unicode_path)
    assert fixture.read_file(unicode_path) == content
    
    return "Unicode file path handled"


async def test_concurrent_staging(fixture: TestFixture):
    """Тест конкурентного стейджинга"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    results = []
    errors = []
    
    def stage_file(i: int):
        try:
            content = f"# File {i}\ndef func_{i}(): pass\n"
            vfs.stage_change(f"app/concurrent_{i}.py", content)
            results.append(i)
        except Exception as e:
            errors.append((i, str(e)))
    
    # Запускаем 20 потоков
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(stage_file, i) for i in range(20)]
        for f in futures:
            f.result()
    
    assert len(errors) == 0, f"Errors: {errors}"
    assert len(vfs._pending_changes) == 20
    
    # Коммитим
    result = vfs.commit_all_sync()
    assert result.success
    assert result.total_files == 20
    
    return f"Concurrent staging: {len(results)} files, {len(errors)} errors"


async def test_readonly_file_handling(fixture: TestFixture):
    """Тест обработки read-only файлов"""
    from app.services.virtual_fs import VirtualFileSystem
    
    # Пропускаем на Windows (другая модель прав)
    if sys.platform == "win32":
        return "Skipped on Windows"
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Делаем файл read-only
    fixture.make_readonly("app/utils/helpers.py")
    
    try:
        # Стейджим изменение
        vfs.stage_change("app/utils/helpers.py", "# Modified")
        
        # Коммит должен либо fallback'нуть, либо выдать понятную ошибку
        result = vfs.commit_all_sync()
        
        # Если коммит прошёл - файл был разблокирован
        # Если нет - проверяем, что ошибка понятная
        if not result.success:
            assert len(result.errors) > 0
            assert any("permission" in str(e).lower() or "readonly" in str(e).lower() 
                      for e in result.errors)
    finally:
        # Восстанавливаем права
        fixture.make_writable("app/utils/helpers.py")
    
    return "Read-only file handled"


async def test_deeply_nested_path(fixture: TestFixture):
    """Тест работы с глубоко вложенными путями"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Создаём глубокий путь
    deep_path = "a/b/c/d/e/f/g/h/i/j/deep_module.py"
    content = "# Deeply nested\ndef deep(): pass\n"
    
    vfs.stage_change(deep_path, content)
    result = vfs.commit_all_sync()
    
    assert result.success
    assert fixture.file_exists(deep_path)
    
    return "Created file at depth 10"


async def test_special_characters_in_content(fixture: TestFixture):
    """Тест специальных символов в контенте"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Контент со спецсимволами
    special_content = '''# Special characters test
import re

REGEX = r"^[a-z]+$"
ESCAPE_CHARS = "\\n\\t\\r"
QUOTES = '"single\\'s" and "double\\""'
RAW = r'raw \\string'
MULTILINE = """
Line 1
Line 2
"""

def escape_test():
    return f"Tab:\\tNewline:\\n"
'''
    
    vfs.stage_change("app/special.py", special_content)
    result = vfs.commit_all_sync()
    
    assert result.success
    
    # Читаем и проверяем
    read_content = fixture.read_file("app/special.py")
    assert "\\n\\t\\r" in read_content
    assert 'r"^[a-z]+$"' in read_content
    
    return "Special characters preserved"


async def test_empty_directory_structure(fixture: TestFixture):
    """Тест создания файла в несуществующей директории"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Директория не существует
    new_path = "new_package/subpackage/module.py"
    assert not (fixture.project_root / "new_package").exists()
    
    vfs.stage_change(new_path, "# New module\n")
    result = vfs.commit_all_sync()
    
    assert result.success
    assert fixture.file_exists(new_path)
    assert (fixture.project_root / "new_package" / "subpackage").is_dir()
    
    return "Directory structure created"


async def test_binary_like_content(fixture: TestFixture):
    """Тест контента, похожего на бинарный"""
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Python файл с байтовыми литералами
    binary_like = '''# Binary-like content
DATA = b"\\x00\\x01\\x02\\xff\\xfe"
HEX = "0x1234ABCD"
BYTES = bytes([0, 1, 2, 255, 254])
'''
    
    vfs.stage_change("app/binary_like.py", binary_like)
    result = vfs.commit_all_sync()
    
    assert result.success
    
    read_content = fixture.read_file("app/binary_like.py")
    assert "\\x00\\x01" in read_content
    
    return "Binary-like content handled"


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

async def test_full_workflow(fixture: TestFixture):
    """Тест полного рабочего процесса"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.backup_manager import BackupManager
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    backup = BackupManager(str(fixture.project_root))
    modifier = FileModifier()
    
    # 1. Модифицируем через FileModifier + VFS
    result = modifier.apply_to_vfs(
        vfs,
        "app/services/auth.py",
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code="def verify_token(self, token: str) -> bool:\n    return len(token) > 10",
            target_class="AuthService"
        )
    )
    assert result.success
    
    # 2. Проверяем статус
    status = vfs.get_status()
    assert status["staged_count"] == 1
    
    # 3. Проверяем affected files
    affected = vfs.get_affected_files()
    assert len(affected.changed_files) == 1
    
    # 4. Генерируем diff
    diff = vfs.get_diff("app/services/auth.py")
    assert "verify_token" in diff
    
    # 5. Коммитим с бэкапом
    commit_result = vfs.commit_all_sync(backup_manager=backup)
    assert commit_result.success
    assert len(commit_result.backups) == 1
    
    # 6. Проверяем, что файл изменился
    content = fixture.read_file("app/services/auth.py")
    assert "def verify_token" in content
    
    # 7. Восстанавливаем из бэкапа
    sessions = backup.list_sessions()
    backup.restore_file("app/services/auth.py", sessions[0].session_id)
    
    # 8. Проверяем, что файл восстановлен
    content = fixture.read_file("app/services/auth.py")
    assert "def verify_token" not in content
    
    return "Full workflow completed successfully"


async def test_multiple_files_workflow(fixture: TestFixture):
    """Тест работы с несколькими файлами"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.backup_manager import BackupManager
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    backup = BackupManager(str(fixture.project_root))
    modifier = FileModifier()
    
    # Модифицируем несколько файлов
    files_to_modify = [
        ("app/services/auth.py", "AuthService", "def check(self): return True"),
        ("app/services/user.py", "UserService", "def info(self): return {}"),
    ]
    
    for file_path, class_name, code in files_to_modify:
        result = modifier.apply_to_vfs(
            vfs,
            file_path,
            ModifyInstruction(
                mode=ModifyMode.INSERT_INTO_CLASS,
                code=code,
                target_class=class_name
            )
        )
        assert result.success, f"Failed to modify {file_path}"
    
    # Проверяем статус
    status = vfs.get_status()
    assert status["staged_count"] == 2
    
    # Коммитим
    commit_result = vfs.commit_all_sync(backup_manager=backup)
    assert commit_result.success
    assert len(commit_result.applied_files) == 2
    
    return f"Modified {len(files_to_modify)} files"


async def test_rollback_on_partial_failure(fixture: TestFixture):
    """Тест отката при частичной ошибке"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.backup_manager import BackupManager
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    backup = BackupManager(str(fixture.project_root))
    
    # Запоминаем оригиналы
    original_auth = fixture.read_file("app/services/auth.py")
    
    # Стейджим несколько изменений
    vfs.stage_change("app/services/auth.py", original_auth + "\n# Modified")
    vfs.stage_change("app/new_file.py", "# New file")
    
    # Коммитим с бэкапом
    result = vfs.commit_all_sync(backup_manager=backup)
    
    # Проверяем, что файлы изменились
    assert fixture.file_exists("app/new_file.py")
    
    # Восстанавливаем всю сессию
    sessions = backup.list_sessions()
    if sessions:
        backup.restore_session(sessions[0].session_id)
        # После восстановления auth.py должен быть как был
        assert fixture.read_file("app/services/auth.py") == original_auth
    
    return "Rollback scenario tested"


async def test_chain_modifications(fixture: TestFixture):
    """Тест цепочки модификаций одного файла"""
    from app.services.virtual_fs import VirtualFileSystem
    from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    modifier = FileModifier()
    
    # Читаем исходный файл
    original = fixture.read_file("app/services/auth.py")
    
    # Цепочка модификаций
    modifications = [
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code="def method1(self): pass",
            target_class="AuthService"
        ),
        ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code="def method2(self): pass",
            target_class="AuthService"
        ),
        ModifyInstruction(
            mode=ModifyMode.INSERT_IMPORT,
            code="from typing import Dict"
        ),
    ]
    
    # Применяем последовательно
    for mod in modifications:
        result = modifier.apply_to_vfs(vfs, "app/services/auth.py", mod)
        assert result.success, f"Failed: {result.message}"
    
    # Коммитим
    commit_result = vfs.commit_all_sync()
    assert commit_result.success
    
    # Проверяем результат
    final_content = fixture.read_file("app/services/auth.py")
    assert "def method1(self):" in final_content
    assert "def method2(self):" in final_content
    assert "from typing import Dict" in final_content
    
    return f"Applied {len(modifications)} modifications in chain"


async def test_vfs_with_change_type_enum(fixture: TestFixture):
    """Тест работы с ChangeType enum напрямую"""
    from app.services.virtual_fs import VirtualFileSystem, ChangeType
    
    vfs = VirtualFileSystem(str(fixture.project_root))
    
    # Явное указание ChangeType
    change = vfs.stage_change("app/explicit_create.py", "# Created", ChangeType.CREATE)
    assert change.change_type == ChangeType.CREATE
    
    change2 = vfs.stage_change(
        "app/utils/helpers.py",
        fixture.read_file("app/utils/helpers.py") + "\n# Modified",
        ChangeType.MODIFY
    )
    assert change2.change_type == ChangeType.MODIFY
    
    return "ChangeType enum works correctly"


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Главная функция запуска тестов"""
    
    print("\n" + "="*70)
    print("🚀 ТЕСТИРОВАНИЕ ЭТАПА 1: Agent Mode Foundation")
    print("="*70)
    print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Лог: test_agent_foundation.log")
    if ARGS.skip_slow:
        print("⏭️  Медленные тесты пропускаются (--skip-slow)")
    if ARGS.filter:
        print(f"🔍 Фильтр: {ARGS.filter}")
    if ARGS.no_cleanup:
        print("🗂️  Временные файлы НЕ удаляются (--no-cleanup)")
    
    runner = TestRunner()
    fixture = TestFixture()
    
    try:
        fixture.setup()
        
        # ==================== VirtualFileSystem Tests ====================
        runner.group("VirtualFileSystem")
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Initialization", test_vfs_initialization, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Stage new file", test_vfs_staging_new_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Stage modification", test_vfs_staging_modification, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Stage deletion", test_vfs_staging_deletion, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Read staged content", test_vfs_read_staged, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Read original content", test_vfs_read_original, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Unstage", test_vfs_unstage, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Discard all", test_vfs_discard_all, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Find dependents", test_vfs_affected_files_dependents, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Find dependencies", test_vfs_affected_files_dependencies, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Diff generation", test_vfs_diff_generation, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Get all diffs", test_vfs_get_all_diffs, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Status", test_vfs_status, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Format status message", test_vfs_format_status_message, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Commit sync", test_vfs_commit_sync, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Commit with backup", test_vfs_commit_with_backup, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Multiple modifications same file", 
                             test_vfs_multiple_modifications_same_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Empty file", test_vfs_empty_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Get all Python files", test_vfs_get_all_python_files, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Find test files", test_vfs_find_test_files, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Invalidate cache", test_vfs_invalidate_cache, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Pending changes property", test_vfs_pending_changes_property, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("ChangeType enum usage", test_vfs_with_change_type_enum, fixture)
        
        # ==================== BackupManager Tests ====================
        runner.group("BackupManager")
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Initialization", test_backup_initialization, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Session lifecycle", test_backup_session_lifecycle, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Restore file", test_backup_restore_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Restore session", test_backup_restore_session, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("List sessions", test_backup_list_sessions, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Nonexistent file", test_backup_nonexistent_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Duplicate file in session", 
                             test_backup_duplicate_file_in_session, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Empty session", test_backup_empty_session, fixture)
        
        # ==================== FileModifier Tests ====================
        runner.group("FileModifier")
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Replace file", test_modifier_replace_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Insert into class", test_modifier_insert_into_class, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Replace method", test_modifier_replace_method, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Replace function", test_modifier_replace_function, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Replace class", test_modifier_replace_class, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Insert import", test_modifier_insert_import, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Append to file", test_modifier_append_to_file, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Smart apply", test_modifier_smart_apply, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Class not found", test_modifier_class_not_found, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Syntax error handling", test_modifier_syntax_error_handling, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Indent detection", test_modifier_indent_detection, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Apply to VFS", test_modifier_apply_to_vfs, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Preserve trailing newline", 
                             test_modifier_preserve_trailing_newline, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Method with decorators", 
                             test_modifier_method_with_decorators, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Nested class", test_modifier_nested_class, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("MODE_MAPPING", test_modifier_mode_mapping, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("ParsedCodeBlock", test_modifier_parsed_code_block, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("apply_code_block_to_vfs", test_modifier_apply_code_block_to_vfs, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Invalid mode handling", test_modifier_invalid_mode, fixture)
        
        # ==================== Edge Case Tests ====================
        runner.group("Edge Cases")
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Large file handling (1MB+)", 
                             test_large_file_handling, fixture, slow=True)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Unicode content", test_unicode_content, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Unicode file path", test_unicode_file_path, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Concurrent staging", test_concurrent_staging, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Read-only file handling", 
                             test_readonly_file_handling, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Deeply nested path", test_deeply_nested_path, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Special characters in content", 
                             test_special_characters_in_content, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Empty directory structure", 
                             test_empty_directory_structure, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Binary-like content", test_binary_like_content, fixture)
        
        # ==================== Integration Tests ====================
        runner.group("Integration")
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Full workflow", test_full_workflow, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Multiple files", test_multiple_files_workflow, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Rollback on partial failure", 
                             test_rollback_on_partial_failure, fixture)
        
        fixture.cleanup()
        fixture.setup()
        await runner.run_test("Chain modifications", test_chain_modifications, fixture)
        
    finally:
        if not ARGS.no_cleanup:
            fixture.cleanup()
        else:
            print(f"\n📁 Temp files preserved at: {fixture.temp_dir}")
    
    # Итоговая сводка
    success = runner.summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)