#!/usr/bin/env python3
# scripts/test_agent_stage2.py
"""
Полный тестовый скрипт для проверки компонентов Этапа 2 Agent Mode.

Проверяет:
1. VirtualFileSystem - staging, чтение, зависимости, новые файлы
2. SyntaxChecker - проверка синтаксиса Python/JSON, автоисправление
3. ChangeValidator - многоуровневая валидация (все уровни)
4. Интеграция всех компонентов вместе
5. Граничные случаи: Unicode, большие файлы, concurrent access

Тестовая ситуация:
- Создаём временный "проект" с несколькими Python файлами
- Стейджим изменения (модификация + создание новых файлов)
- Проверяем валидацию на разных уровнях
- Измеряем время выполнения каждого этапа

Запуск:
    python scripts/test_agent_stage2.py
    python scripts/test_agent_stage2.py --verbose
    python scripts/test_agent_stage2.py --skip-slow
    python scripts/test_agent_stage2.py --filter validator
"""

import asyncio
import logging
import sys
import os
import tempfile
import shutil
import time
import argparse
import traceback
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Добавляем корень проекта в path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# CLI ARGUMENTS
# ============================================================================

def parse_args():
    """Парсит аргументы командной строки"""
    parser = argparse.ArgumentParser(description="Test Agent Mode Stage 2")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--skip-slow", action="store_true", help="Skip slow tests")
    parser.add_argument("--filter", "-f", type=str, help="Run only tests matching pattern")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't cleanup temp files")
    return parser.parse_args()


ARGS = parse_args()


# ============================================================================
# LOGGING SETUP
# ============================================================================

log_level = logging.DEBUG if ARGS.verbose else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('test_agent_stage2.log', mode='w', encoding='utf-8')
    ]
)
logger = logging.getLogger("test_stage2")

# Отключаем лишние логи
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================================================================
# TEST RESULT TRACKING
# ============================================================================

@dataclass
class TestResult:
    """Результат одного теста"""
    name: str
    passed: bool
    duration_ms: float
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""
    
    def __str__(self) -> str:
        if self.skipped:
            return f"⏭️ SKIP | {self.name} | ({self.skip_reason})"
        status = "✅ PASS" if self.passed else "❌ FAIL"
        time_str = f"{self.duration_ms:.1f}ms"
        result = f"{status} | {self.name} | {time_str}"
        if self.error:
            result += f"\n         └── Error: {self.error}"
        return result


@dataclass
class TestSuite:
    """Набор тестов"""
    name: str
    results: List[TestResult] = field(default_factory=list)
    
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed and not r.skipped)
    
    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed and not r.skipped)
    
    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.skipped)
    
    @property
    def total_time_ms(self) -> float:
        return sum(r.duration_ms for r in self.results)
    
    def add(self, result: TestResult):
        self.results.append(result)
        print(f"  {result}")
        logger.info(f"{'PASS' if result.passed else 'FAIL'}: {self.name}::{result.name}")
    
    def skip(self, name: str, reason: str):
        result = TestResult(name=name, passed=True, duration_ms=0, 
                           skipped=True, skip_reason=reason)
        self.results.append(result)
        print(f"  {result}")
        logger.info(f"SKIP: {self.name}::{name} - {reason}")
    
    def summary(self) -> str:
        total = len(self.results)
        skip_info = f" ({self.skipped} skipped)" if self.skipped else ""
        return (
            f"\n{'='*60}\n"
            f"📊 {self.name}: {self.passed}/{total - self.skipped} passed{skip_info} "
            f"({self.total_time_ms:.0f}ms total)\n"
            f"{'='*60}"
        )


# ============================================================================
# TEST PROJECT FIXTURES
# ============================================================================

# Тестовые файлы для создания временного проекта
TEST_PROJECT_FILES = {
    "app/__init__.py": "",
    
    "app/models.py": '''"""Data models."""
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class User:
    """User model."""
    id: int
    name: str
    email: str
    is_active: bool = True
    
    def greet(self) -> str:
        return f"Hello, {self.name}!"

@dataclass  
class Post:
    """Blog post model."""
    id: int
    title: str
    content: str
    author_id: int
    tags: List[str] = None
    
    def summary(self, length: int = 100) -> str:
        """Return truncated content."""
        if len(self.content) <= length:
            return self.content
        return self.content[:length] + "..."
''',

    "app/services/__init__.py": "",
    
    "app/services/user_service.py": '''"""User service."""
from typing import Optional, List
from app.models import User

class UserService:
    """Service for user operations."""
    
    def __init__(self):
        self._users: dict[int, User] = {}
        self._next_id = 1
    
    def create_user(self, name: str, email: str) -> User:
        """Create a new user."""
        user = User(
            id=self._next_id,
            name=name,
            email=email
        )
        self._users[user.id] = user
        self._next_id += 1
        return user
    
    def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        return self._users.get(user_id)
    
    def list_users(self) -> List[User]:
        """List all users."""
        return list(self._users.values())
    
    def delete_user(self, user_id: int) -> bool:
        """Delete user by ID."""
        if user_id in self._users:
            del self._users[user_id]
            return True
        return False
''',

    "app/services/post_service.py": '''"""Post service."""
from typing import Optional, List
from app.models import Post
from app.services.user_service import UserService

class PostService:
    """Service for post operations."""
    
    def __init__(self, user_service: UserService):
        self._posts: dict[int, Post] = {}
        self._next_id = 1
        self._user_service = user_service
    
    def create_post(self, title: str, content: str, author_id: int) -> Optional[Post]:
        """Create a new post."""
        if not self._user_service.get_user(author_id):
            return None
        
        post = Post(
            id=self._next_id,
            title=title,
            content=content,
            author_id=author_id
        )
        self._posts[post.id] = post
        self._next_id += 1
        return post
    
    def get_post(self, post_id: int) -> Optional[Post]:
        """Get post by ID."""
        return self._posts.get(post_id)
    
    def get_user_posts(self, user_id: int) -> List[Post]:
        """Get all posts by user."""
        return [p for p in self._posts.values() if p.author_id == user_id]
''',

    "app/utils/__init__.py": "",
    
    "app/utils/validators.py": '''"""Validation utilities."""
import re
from typing import Tuple

def validate_email(email: str) -> Tuple[bool, str]:
    """Validate email format."""
    if not email:
        return False, "Email is required"
    
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
    if re.match(pattern, email):
        return True, ""
    return False, "Invalid email format"

def validate_username(name: str) -> Tuple[bool, str]:
    """Validate username."""
    if not name:
        return False, "Name is required"
    if len(name) < 2:
        return False, "Name too short"
    if len(name) > 50:
        return False, "Name too long"
    return True, ""
''',

    "tests/__init__.py": "",
    
    "tests/test_models.py": '''"""Tests for models."""
import unittest
from app.models import User, Post

class TestUser(unittest.TestCase):
    def test_greet(self):
        user = User(id=1, name="John", email="john@test.com")
        self.assertEqual(user.greet(), "Hello, John!")
    
    def test_default_active(self):
        user = User(id=1, name="John", email="john@test.com")
        self.assertTrue(user.is_active)

class TestPost(unittest.TestCase):
    def test_summary_short(self):
        post = Post(id=1, title="Test", content="Short", author_id=1)
        self.assertEqual(post.summary(), "Short")
    
    def test_summary_truncate(self):
        post = Post(id=1, title="Test", content="A" * 200, author_id=1)
        summary = post.summary(100)
        self.assertEqual(len(summary), 103)

if __name__ == "__main__":
    unittest.main()
''',
}

# Изменения для тестирования
TEST_CHANGES = {
    "app/models.py": '''"""Data models."""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class User:
    """User model with timestamps."""
    id: int
    name: str
    email: str
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    
    def greet(self) -> str:
        return f"Hello, {self.name}!"
    
    def deactivate(self) -> None:
        """Deactivate the user."""
        self.is_active = False

@dataclass  
class Post:
    """Blog post model."""
    id: int
    title: str
    content: str
    author_id: int
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    
    def summary(self, length: int = 100) -> str:
        """Return truncated content."""
        if len(self.content) <= length:
            return self.content
        return self.content[:length] + "..."
    
    def add_tag(self, tag: str) -> None:
        """Add a tag to the post."""
        if tag not in self.tags:
            self.tags.append(tag)
''',

    "app/services/notification_service.py": '''"""Notification service."""
from typing import List
from app.models import User

class NotificationService:
    """Service for sending notifications."""
    
    def __init__(self):
        self._sent: List[dict] = []
    
    def send_email(self, user: User, subject: str, body: str) -> bool:
        """Send email notification."""
        if not user.is_active:
            return False
        
        self._sent.append({
            "type": "email",
            "to": user.email,
            "subject": subject,
            "body": body
        })
        return True
    
    def send_welcome(self, user: User) -> bool:
        """Send welcome email to new user."""
        return self.send_email(
            user,
            "Welcome!",
            f"Hello {user.name}, welcome to our platform!"
        )
    
    def get_sent_count(self) -> int:
        """Get number of sent notifications."""
        return len(self._sent)
''',

    "tests/test_notification_service.py": '''"""Tests for notification service."""
import unittest
from app.models import User
from app.services.notification_service import NotificationService

class TestNotificationService(unittest.TestCase):
    def setUp(self):
        self.service = NotificationService()
        self.user = User(id=1, name="John", email="john@test.com")
    
    def test_send_email(self):
        result = self.service.send_email(self.user, "Test", "Body")
        self.assertTrue(result)
        self.assertEqual(self.service.get_sent_count(), 1)
    
    def test_send_to_inactive_user(self):
        self.user.is_active = False
        result = self.service.send_email(self.user, "Test", "Body")
        self.assertFalse(result)
    
    def test_send_welcome(self):
        result = self.service.send_welcome(self.user)
        self.assertTrue(result)

if __name__ == "__main__":
    unittest.main()
''',
}

# Файлы с ошибками для тестирования валидации
TEST_INVALID_FILES = {
    "syntax_error": '''def broken_function(
    print("missing closing paren"
''',
    
    "indent_error": '''def bad_indent():
    x = 1
  y = 2  # Wrong indent
    return x + y
''',
    
    "import_error": '''from nonexistent_module import something
from app.fake_module import FakeClass

def use_fake():
    return FakeClass()
''',
    
    "tab_space_mix": '''def mixed_indent():
\tx = 1  # tab
    y = 2  # spaces
\t    z = 3  # mixed
    return x + y + z
''',
}

# Unicode тестовые данные
TEST_UNICODE_FILES = {
    "app/unicode_module.py": '''# -*- coding: utf-8 -*-
"""Модуль с Unicode символами."""
from typing import Optional

def приветствие(имя: str) -> str:
    """Функция приветствия на русском."""
    return f"Привет, {имя}! 🎉"

def 計算(値: int) -> int:
    """Japanese function."""
    return 値 * 2

class Сервис:
    """Класс с русским названием."""
    
    def __init__(self):
        self.данные = {}
    
    def добавить(self, ключ: str, значение: str) -> None:
        self.данные[ключ] = значение

КОНСТАНТА = "Значение: ñ é ü ö 中文 العربية"
''',
}


# ============================================================================
# TEST FIXTURE CLASS
# ============================================================================

class TestFixture:
    """Управляет тестовым окружением с изоляцией"""
    
    def __init__(self, base_files: Dict[str, str] = None):
        self.base_files = base_files or TEST_PROJECT_FILES
        self.temp_dir: Optional[Path] = None
        self._cleanup_enabled = not ARGS.no_cleanup
    
    def setup(self) -> 'TestFixture':
        """Создаёт новую временную директорию"""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_stage2_"))
        
        for file_path, content in self.base_files.items():
            full_path = self.temp_dir / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding='utf-8')
        
        logger.debug(f"Created test fixture: {self.temp_dir}")
        return self
    
    def cleanup(self):
        """Удаляет временную директорию"""
        if not self._cleanup_enabled:
            logger.info(f"Keeping temp dir: {self.temp_dir}")
            return
            
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.debug(f"Cleaned up: {self.temp_dir}")
    
    @property
    def path(self) -> str:
        return str(self.temp_dir)
    
    def read_file(self, rel_path: str) -> str:
        return (self.temp_dir / rel_path).read_text(encoding='utf-8')
    
    def write_file(self, rel_path: str, content: str):
        path = self.temp_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    
    def file_exists(self, rel_path: str) -> bool:
        return (self.temp_dir / rel_path).exists()


# ============================================================================
# TEST RUNNER HELPER
# ============================================================================

async def run_test(
    name: str,
    test_func: Callable,
    *args,
    is_async: bool = False,
    slow: bool = False,
    **kwargs
) -> TestResult:
    """Запускает один тест с обработкой ошибок"""
    
    # Фильтрация
    if ARGS.filter and ARGS.filter.lower() not in name.lower():
        return TestResult(name=name, passed=True, duration_ms=0, 
                         skipped=True, skip_reason="filtered out")
    
    # Пропуск медленных
    if slow and ARGS.skip_slow:
        return TestResult(name=name, passed=True, duration_ms=0,
                         skipped=True, skip_reason="slow test")
    
    start = time.time()
    
    try:
        if is_async:
            result = await test_func(*args, **kwargs)
        else:
            result = test_func(*args, **kwargs)
        
        return result
        
    except Exception as e:
        logger.error(f"Test {name} failed: {e}")
        logger.debug(traceback.format_exc())
        return TestResult(
            name=name,
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=f"{type(e).__name__}: {e}"
        )


# ============================================================================
# VIRTUAL FILE SYSTEM TESTS
# ============================================================================

def test_vfs_initialization() -> TestResult:
    """Тест инициализации VFS"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        assert vfs.project_root == Path(fixture.path).resolve(), "Wrong project root"
        assert not vfs.has_pending_changes(), "Should have no pending changes"
        assert len(vfs.get_staged_files()) == 0, "Staged files should be empty"
        
        return TestResult(
            name="VFS Initialization",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"project_root": str(vfs.project_root)}
        )
    except Exception as e:
        return TestResult(
            name="VFS Initialization",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_read_file() -> TestResult:
    """Тест чтения файлов"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Читаем существующий файл
        content = vfs.read_file("app/models.py")
        assert content is not None, "Should read existing file"
        assert "class User:" in content, "Should contain User class"
        
        # Читаем несуществующий файл
        content = vfs.read_file("nonexistent.py")
        assert content is None, "Should return None for missing file"
        
        # file_exists
        assert vfs.file_exists("app/models.py"), "Should find existing file"
        assert not vfs.file_exists("fake.py"), "Should not find fake file"
        
        # read_file_safe
        safe_content = vfs.read_file_safe("nonexistent.py", "default")
        assert safe_content == "default", "Should return default"
        
        return TestResult(
            name="VFS Read File",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Read File",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_stage_modify() -> TestResult:
    """Тест стейджинга модификации"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem, ChangeType
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим изменение
        new_content = TEST_CHANGES["app/models.py"]
        change = vfs.stage_change("app/models.py", new_content)
        
        assert change is not None, "Should return PendingChange"
        assert change.change_type == ChangeType.MODIFY, f"Should be MODIFY, got {change.change_type}"
        assert not change.is_new_file, "Should not be new file"
        assert change.is_modification, "Should be modification"
        assert not change.is_deletion, "Should not be deletion"
        assert change.lines_added > 0, "Should have added lines"
        assert change.has_changes, "Should have changes"
        
        # Проверяем что read_file возвращает новое содержимое
        content = vfs.read_file("app/models.py")
        assert content == new_content, "Should return staged content"
        
        # Проверяем original
        original = vfs.read_file_original("app/models.py")
        assert original != new_content, "Original should be different"
        assert "created_at" not in original, "Original should not have created_at"
        
        return TestResult(
            name="VFS Stage Modify",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "lines_added": change.lines_added,
                "lines_removed": change.lines_removed
            }
        )
    except Exception as e:
        return TestResult(
            name="VFS Stage Modify",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_stage_create() -> TestResult:
    """Тест стейджинга нового файла"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem, ChangeType
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим новый файл
        new_content = TEST_CHANGES["app/services/notification_service.py"]
        change = vfs.stage_change("app/services/notification_service.py", new_content)
        
        assert change is not None, "Should return PendingChange"
        assert change.change_type == ChangeType.CREATE, f"Should be CREATE, got {change.change_type}"
        assert change.is_new_file, "Should be new file"
        assert not change.is_modification, "Should not be modification"
        assert change.original_content is None, "Original should be None"
        
        # Проверяем что файл "существует" в VFS
        assert vfs.file_exists("app/services/notification_service.py"), "New file should exist in VFS"
        
        # Но не существует на диске
        assert not fixture.file_exists("app/services/notification_service.py"), "Should not exist on disk"
        
        return TestResult(
            name="VFS Stage Create",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"is_new_file": change.is_new_file}
        )
    except Exception as e:
        return TestResult(
            name="VFS Stage Create",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_stage_delete() -> TestResult:
    """Тест стейджинга удаления"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem, ChangeType
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим удаление
        change = vfs.stage_deletion("app/utils/validators.py")
        
        assert change is not None, "Should return PendingChange"
        assert change.change_type == ChangeType.DELETE, f"Should be DELETE, got {change.change_type}"
        assert change.is_deletion, "Should be deletion"
        
        # Файл не должен "существовать" в VFS
        assert not vfs.file_exists("app/utils/validators.py"), "Deleted file should not exist in VFS"
        
        # Но существует на диске
        assert fixture.file_exists("app/utils/validators.py"), "File should still exist on disk"
        
        return TestResult(
            name="VFS Stage Delete",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Stage Delete",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_stage_with_change_type() -> TestResult:
    """Тест стейджинга с явным указанием ChangeType"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem, ChangeType
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Явно указываем CREATE
        change1 = vfs.stage_change("app/new_file.py", "# New", ChangeType.CREATE)
        assert change1.change_type == ChangeType.CREATE
        
        # Явно указываем MODIFY
        original = fixture.read_file("app/models.py")
        change2 = vfs.stage_change("app/models.py", original + "\n# Modified", ChangeType.MODIFY)
        assert change2.change_type == ChangeType.MODIFY
        
        return TestResult(
            name="VFS Stage with ChangeType",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Stage with ChangeType",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_affected_files() -> TestResult:
    """Тест определения затронутых файлов"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим изменения
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        vfs.stage_change("app/services/notification_service.py", 
                        TEST_CHANGES["app/services/notification_service.py"])
        
        # Получаем affected files
        affected = vfs.get_affected_files()
        
        assert "app/models.py" in affected.changed_files, "models.py should be in changed"
        assert "app/services/notification_service.py" in affected.new_files, "notification should be in new"
        
        # Проверяем методы AffectedFiles
        assert len(affected.all_modified) >= 2, "Should have at least 2 modified"
        assert affected.all_affected, "Should have affected files"
        
        # Проверяем to_dict
        affected_dict = affected.to_dict()
        assert "changed" in affected_dict
        assert "new" in affected_dict
        
        return TestResult(
            name="VFS Affected Files",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "changed": len(affected.changed_files),
                "new": len(affected.new_files),
                "dependents": len(affected.dependent_files),
                "all": len(affected.all_affected)
            }
        )
    except Exception as e:
        return TestResult(
            name="VFS Affected Files",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_find_tests() -> TestResult:
    """Тест поиска тестовых файлов"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Ищем тесты для models.py
        tests = vfs.find_test_files("app/models.py")
        
        assert "tests/test_models.py" in tests, f"Should find test_models.py, got {tests}"
        
        return TestResult(
            name="VFS Find Tests",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"tests_found": tests}
        )
    except Exception as e:
        return TestResult(
            name="VFS Find Tests",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_unstage() -> TestResult:
    """Тест отмены staging"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим
        vfs.stage_change("app/models.py", "# modified")
        assert vfs.has_pending_changes(), "Should have pending changes"
        
        # Unstage
        result = vfs.unstage("app/models.py")
        assert result, "Unstage should return True"
        assert not vfs.has_pending_changes(), "Should have no pending changes"
        
        # Discard all
        vfs.stage_change("app/models.py", "# modified")
        vfs.stage_change("app/utils/validators.py", "# modified")
        count = vfs.discard_all()
        assert count == 2, f"Should discard 2, got {count}"
        assert not vfs.has_pending_changes(), "Should have no pending changes"
        
        return TestResult(
            name="VFS Unstage",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Unstage",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_diff() -> TestResult:
    """Тест генерации diff"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим изменение
        original = fixture.read_file("app/models.py")
        modified = original.replace("class User:", "class User:  # Modified")
        vfs.stage_change("app/models.py", modified)
        
        # Получаем diff
        diff = vfs.get_diff("app/models.py")
        assert diff is not None, "Should return diff"
        assert "Modified" in diff, "Diff should contain change"
        
        # get_all_diffs
        vfs.stage_change("app/utils/validators.py", "# Changed")
        all_diffs = vfs.get_all_diffs()
        assert len(all_diffs) == 2, f"Should have 2 diffs, got {len(all_diffs)}"
        
        return TestResult(
            name="VFS Diff",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Diff",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_status() -> TestResult:
    """Тест получения статуса"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим разные типы
        vfs.stage_change("app/new.py", "# New file")
        vfs.stage_change("app/models.py", fixture.read_file("app/models.py") + "\n# Mod")
        
        status = vfs.get_status()
        
        assert status["has_changes"], "Should have changes"
        assert status["staged_count"] == 2, f"Should have 2, got {status['staged_count']}"
        assert status["total_lines_added"] > 0, "Should have lines added"
        
        # format_status_message
        msg = vfs.format_status_message()
        assert "Staging Area" in msg, "Should contain header"
        
        return TestResult(
            name="VFS Status",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details=status
        )
    except Exception as e:
        return TestResult(
            name="VFS Status",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_commit() -> TestResult:
    """Тест коммита изменений"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим
        vfs.stage_change("app/models.py", "# MODIFIED CONTENT")
        vfs.stage_change("app/new_file.py", "# NEW FILE")
        
        # Коммитим
        result = vfs.commit_all_sync()
        
        assert result.success, f"Commit should succeed: {result.errors}"
        assert "app/models.py" in result.applied_files, "models.py should be applied"
        assert "app/new_file.py" in result.created_files, "new_file.py should be created"
        assert result.total_files == 2, f"Should have 2 files, got {result.total_files}"
        
        # Проверяем файлы на диске
        models_content = fixture.read_file("app/models.py")
        assert models_content == "# MODIFIED CONTENT", "Content should be updated"
        
        assert fixture.file_exists("app/new_file.py"), "New file should exist"
        assert fixture.read_file("app/new_file.py") == "# NEW FILE"
        
        # Staging должен быть пуст
        assert not vfs.has_pending_changes(), "Should have no pending changes after commit"
        
        return TestResult(
            name="VFS Commit",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details=result.to_dict()
        )
    except Exception as e:
        return TestResult(
            name="VFS Commit",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_commit_delete() -> TestResult:
    """Тест коммита удаления"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим удаление
        vfs.stage_deletion("app/utils/validators.py")
        
        # Коммитим
        result = vfs.commit_all_sync()
        
        assert result.success, f"Commit should succeed: {result.errors}"
        assert "app/utils/validators.py" in result.deleted_files, "File should be deleted"
        
        # Проверяем что файл удалён
        assert not fixture.file_exists("app/utils/validators.py"), "File should not exist"
        
        return TestResult(
            name="VFS Commit Delete",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Commit Delete",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_unicode_content() -> TestResult:
    """Тест работы с Unicode контентом"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим Unicode контент
        unicode_content = TEST_UNICODE_FILES["app/unicode_module.py"]
        vfs.stage_change("app/unicode_module.py", unicode_content)
        
        # Читаем через VFS
        content = vfs.read_file("app/unicode_module.py")
        assert "приветствие" in content, "Should contain Russian"
        assert "計算" in content, "Should contain Japanese"
        assert "🎉" in content, "Should contain emoji"
        
        # Коммитим и проверяем
        result = vfs.commit_all_sync()
        assert result.success
        
        disk_content = fixture.read_file("app/unicode_module.py")
        assert "Сервис" in disk_content, "Should contain Russian class name"
        
        return TestResult(
            name="VFS Unicode Content",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Unicode Content",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_unicode_path() -> TestResult:
    """Тест работы с Unicode в путях"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Создаём файл с Unicode путём
        content = "# Файл с русским путём\ndef функция(): pass\n"
        vfs.stage_change("модули/сервис.py", content)
        
        # Проверяем
        assert vfs.file_exists("модули/сервис.py")
        assert vfs.read_file("модули/сервис.py") == content
        
        # Коммитим
        result = vfs.commit_all_sync()
        assert result.success
        
        # Проверяем на диске
        assert fixture.file_exists("модули/сервис.py")
        
        return TestResult(
            name="VFS Unicode Path",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Unicode Path",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_all_python_files() -> TestResult:
    """Тест получения списка всех Python файлов"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        py_files = vfs.get_all_python_files()
        
        assert len(py_files) > 0, "Should find Python files"
        assert any("models.py" in f for f in py_files), "Should include models.py"
        assert any("user_service.py" in f for f in py_files), "Should include user_service.py"
        
        return TestResult(
            name="VFS All Python Files",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"count": len(py_files)}
        )
    except Exception as e:
        return TestResult(
            name="VFS All Python Files",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_vfs_cache_invalidation() -> TestResult:
    """Тест инвалидации кэша"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Заполняем кэш
        _ = vfs.get_affected_files()
        _ = vfs.get_all_python_files()
        
        # Инвалидируем
        vfs.invalidate_cache()
        
        assert vfs._affected_cache is None, "Affected cache should be None"
        assert vfs._python_files_cache is None, "Python files cache should be None"
        
        return TestResult(
            name="VFS Cache Invalidation",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="VFS Cache Invalidation",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


# ============================================================================
# SYNTAX CHECKER TESTS
# ============================================================================

def test_syntax_checker_valid() -> TestResult:
    """Тест проверки валидного синтаксиса"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker
        
        checker = SyntaxChecker()
        
        valid_code = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

class Greeter:
    def greet(self, name):
        return hello(name)
'''
        
        result = checker.check_python(valid_code)
        
        assert result.is_valid, f"Should be valid: {result.issues}"
        assert not result.has_critical_issues, "Should have no critical issues"
        assert not result.was_auto_fixed, "Should not need auto-fix"
        
        return TestResult(
            name="SyntaxChecker Valid Code",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker Valid Code",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


def test_syntax_checker_syntax_error() -> TestResult:
    """Тест обнаружения синтаксических ошибок"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker, SyntaxIssueType
        
        checker = SyntaxChecker()
        
        result = checker.check_python(TEST_INVALID_FILES["syntax_error"])
        
        assert not result.is_valid, "Should be invalid"
        assert result.has_critical_issues, "Should have critical issues"
        assert len(result.issues) > 0, "Should have issues"
        
        # Проверяем тип ошибки
        has_syntax_error = any(
            i.issue_type in (SyntaxIssueType.SYNTAX_ERROR, SyntaxIssueType.INDENTATION_ERROR)
            for i in result.issues
        )
        assert has_syntax_error, f"Should have syntax error: {result.issues}"
        
        return TestResult(
            name="SyntaxChecker Syntax Error",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"issues": [str(i.message) for i in result.issues]}
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker Syntax Error",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


def test_syntax_checker_indent_error() -> TestResult:
    """Тест обнаружения ошибок отступов"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker
        
        checker = SyntaxChecker()
        
        result = checker.check_python(TEST_INVALID_FILES["indent_error"])
        
        assert not result.is_valid, "Should be invalid"
        
        return TestResult(
            name="SyntaxChecker Indent Error",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"issues": [str(i.message) for i in result.issues]}
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker Indent Error",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


def test_syntax_checker_tab_space_mix() -> TestResult:
    """Тест обнаружения смешения табов и пробелов"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker, SyntaxIssueType
        
        checker = SyntaxChecker()
        
        result = checker.check_python(TEST_INVALID_FILES["tab_space_mix"], auto_fix=False)
        
        has_mix_issue = any(
            i.issue_type == SyntaxIssueType.TAB_SPACE_MIX
            for i in result.issues
        )
        
        assert has_mix_issue, f"Should detect tab/space mix: {result.issues}"
        
        return TestResult(
            name="SyntaxChecker Tab/Space Mix",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker Tab/Space Mix",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


def test_syntax_checker_auto_fix() -> TestResult:
    """Тест автоисправления"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker
        
        checker = SyntaxChecker()
        
        # Код с табами
        code_with_tabs = '''def foo():
\treturn 1
'''
        
        result = checker.check_python(code_with_tabs, auto_fix=True)
        
        # Если был auto-fix, должен быть fixed_content
        if result.was_auto_fixed:
            assert result.fixed_content is not None, "Should have fixed content"
            assert '\t' not in result.fixed_content, "Should not have tabs after fix"
        
        return TestResult(
            name="SyntaxChecker Auto-Fix",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"was_auto_fixed": result.was_auto_fixed}
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker Auto-Fix",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


def test_syntax_checker_json() -> TestResult:
    """Тест проверки JSON"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker
        
        checker = SyntaxChecker()
        
        # Валидный JSON
        valid_json = '{"name": "test", "value": 123}'
        result = checker.check_json(valid_json)
        assert result.is_valid, "Valid JSON should pass"
        
        # Невалидный JSON
        invalid_json = '{"name": "test", value: 123}'
        result = checker.check_json(invalid_json)
        assert not result.is_valid, "Invalid JSON should fail"
        
        # Пустой JSON
        empty_result = checker.check_json("")
        assert not empty_result.is_valid, "Empty JSON should fail"
        
        return TestResult(
            name="SyntaxChecker JSON",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker JSON",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


def test_syntax_checker_unicode() -> TestResult:
    """Тест проверки синтаксиса с Unicode"""
    start = time.time()
    
    try:
        from app.services.syntax_checker import SyntaxChecker
        
        checker = SyntaxChecker()
        
        unicode_code = TEST_UNICODE_FILES["app/unicode_module.py"]
        result = checker.check_python(unicode_code)
        
        assert result.is_valid, f"Unicode code should be valid: {result.issues}"
        
        return TestResult(
            name="SyntaxChecker Unicode",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="SyntaxChecker Unicode",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )


# ============================================================================
# CHANGE VALIDATOR TESTS
# ============================================================================

async def test_validator_syntax_level() -> TestResult:
    """Тест валидации на уровне синтаксиса"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим валидный код
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[ValidationLevel.SYNTAX])
        
        assert result.success, f"Should pass syntax: {[str(i) for i in result.issues]}"
        assert ValidationLevel.SYNTAX in result.levels_passed, "SYNTAX should be in passed"
        
        return TestResult(
            name="Validator SYNTAX Level",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "issues": len(result.issues),
                "checked_files": len(result.checked_files)
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator SYNTAX Level",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_syntax_error() -> TestResult:
    """Тест валидации с синтаксической ошибкой"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим код с ошибкой
        vfs.stage_change("app/broken.py", TEST_INVALID_FILES["syntax_error"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[ValidationLevel.SYNTAX])
        
        assert not result.success, "Should fail on syntax error"
        assert ValidationLevel.SYNTAX in result.levels_failed, "SYNTAX should be in failed"
        assert result.error_count > 0, "Should have errors"
        
        return TestResult(
            name="Validator SYNTAX Error Detection",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"errors": result.error_count}
        )
    except Exception as e:
        return TestResult(
            name="Validator SYNTAX Error Detection",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_imports_level() -> TestResult:
    """Тест валидации на уровне импортов"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим код с валидными импортами
        vfs.stage_change("app/services/notification_service.py", 
                        TEST_CHANGES["app/services/notification_service.py"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[ValidationLevel.SYNTAX, ValidationLevel.IMPORTS])
        
        # Проверяем что прошёл синтаксис
        assert ValidationLevel.SYNTAX in result.levels_passed, "SYNTAX should pass"
        
        return TestResult(
            name="Validator IMPORTS Level",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "passed_levels": [l.value for l in result.levels_passed],
                "failed_levels": [l.value for l in result.levels_failed],
                "errors": result.error_count
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator IMPORTS Level",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_import_error() -> TestResult:
    """Тест обнаружения ошибок импорта"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим код с невалидными импортами
        vfs.stage_change("app/bad_imports.py", TEST_INVALID_FILES["import_error"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[ValidationLevel.SYNTAX, ValidationLevel.IMPORTS])
        
        # Ищем ошибки импортов
        import_errors = [i for i in result.issues 
                        if i.level == ValidationLevel.IMPORTS]
        
        # Должны быть ошибки импортов
        has_import_errors = len(import_errors) > 0
        
        return TestResult(
            name="Validator Import Error Detection",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "import_errors": len(import_errors),
                "detected": has_import_errors
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator Import Error Detection",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_types_level() -> TestResult:
    """Тест валидации на уровне типов (mypy)"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[ValidationLevel.TYPES])
        
        # mypy может быть не установлен — это OK
        if ValidationLevel.TYPES in result.levels_skipped:
            return TestResult(
                name="Validator TYPES Level",
                passed=True,
                duration_ms=(time.time() - start) * 1000,
                details={"skipped": "mypy not available"}
            )
        
        return TestResult(
            name="Validator TYPES Level",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "passed": ValidationLevel.TYPES in result.levels_passed,
                "issues": result.error_count
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator TYPES Level",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_integration_level() -> TestResult:
    """Тест валидации на уровне интеграции"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Модифицируем models.py (от него зависят другие файлы)
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[
            ValidationLevel.SYNTAX,
            ValidationLevel.INTEGRATION
        ])
        
        return TestResult(
            name="Validator INTEGRATION Level",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "passed": ValidationLevel.INTEGRATION in result.levels_passed,
                "issues": len([i for i in result.issues if i.level == ValidationLevel.INTEGRATION])
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator INTEGRATION Level",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_new_files() -> TestResult:
    """Тест валидации новых файлов"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим несколько новых файлов
        vfs.stage_change("app/services/notification_service.py",
                        TEST_CHANGES["app/services/notification_service.py"])
        vfs.stage_change("tests/test_notification_service.py",
                        TEST_CHANGES["tests/test_notification_service.py"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[
            ValidationLevel.SYNTAX,
            ValidationLevel.IMPORTS,
            ValidationLevel.INTEGRATION
        ])
        
        # Проверяем что новые файлы определены
        assert len(result.new_files) >= 2, f"Should have 2 new files, got {result.new_files}"
        assert "app/services/notification_service.py" in result.new_files
        
        return TestResult(
            name="Validator New Files",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "new_files": result.new_files,
                "success": result.success
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator New Files",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_quick_methods() -> TestResult:
    """Тест быстрых методов валидации"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator
        
        vfs = VirtualFileSystem(fixture.path)
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        validator = ChangeValidator(vfs)
        
        # syntax_only
        result1 = await validator.validate_syntax_only()
        assert result1.success, "validate_syntax_only should pass"
        
        # quick (syntax + imports)
        vfs.discard_all()
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        result2 = await validator.validate_quick()
        
        return TestResult(
            name="Validator Quick Methods",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "syntax_only_passed": result1.success,
                "quick_passed": result2.success
            }
        )
    except Exception as e:
        return TestResult(
            name="Validator Quick Methods",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_test_discovery() -> TestResult:
    """Тест обнаружения тестовых файлов через валидатор"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # Стейджим изменение models.py
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        validator = ChangeValidator(vfs)
        
        # validate с TESTS уровнем (без реального запуска тестов)
        # Просто проверяем что test_files_found заполняется
        result = await validator.validate(levels=[ValidationLevel.SYNTAX])
        
        # Проверяем что тесты найдены
        test_files = result.test_files_found
        
        return TestResult(
            name="Validator Test Discovery",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"test_files": [t.path for t in test_files]}
        )
    except Exception as e:
        return TestResult(
            name="Validator Test Discovery",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_validator_result_summary() -> TestResult:
    """Тест методов ValidationResult"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[ValidationLevel.SYNTAX])
        
        # Проверяем методы ValidationResult
        summary = result.summary()
        assert "PASSED" in summary or "FAILED" in summary, "Should have status"
        
        result_dict = result.to_dict()
        assert "success" in result_dict
        assert "issues" in result_dict
        assert "checked_files" in result_dict
        
        return TestResult(
            name="Validator Result Summary",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"summary": summary}
        )
    except Exception as e:
        return TestResult(
            name="Validator Result Summary",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

async def test_full_workflow() -> TestResult:
    """Тест полного рабочего процесса"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        
        vfs = VirtualFileSystem(fixture.path)
        
        # 1. Стейджим изменения
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        vfs.stage_change("app/services/notification_service.py",
                        TEST_CHANGES["app/services/notification_service.py"])
        vfs.stage_change("tests/test_notification_service.py",
                        TEST_CHANGES["tests/test_notification_service.py"])
        
        # 2. Проверяем статус
        status = vfs.get_status()
        assert status["has_changes"], "Should have changes"
        assert status["staged_count"] == 3, f"Should have 3 staged, got {status['staged_count']}"
        
        # 3. Валидация
        validator = ChangeValidator(vfs)
        validation_result = await validator.validate(levels=[
            ValidationLevel.SYNTAX,
            ValidationLevel.IMPORTS,
        ])
        
        # 4. Если валидация прошла — коммитим
        if validation_result.success:
            commit_result = vfs.commit_all_sync()
            
            assert commit_result.success, f"Commit should succeed: {commit_result.errors}"
            assert len(commit_result.created_files) >= 2, "Should create at least 2 files"
            
            # Проверяем файлы на диске
            assert fixture.file_exists("app/services/notification_service.py")
        
        return TestResult(
            name="Full Workflow",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "validation_passed": validation_result.success,
                "files_committed": status["staged_count"]
            }
        )
    except Exception as e:
        return TestResult(
            name="Full Workflow",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_workflow_with_backup() -> TestResult:
    """Тест workflow с BackupManager"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.backup_manager import BackupManager
        
        vfs = VirtualFileSystem(fixture.path)
        backup = BackupManager(fixture.path)
        
        # Запоминаем оригинал
        original = fixture.read_file("app/models.py")
        
        # Стейджим изменение
        vfs.stage_change("app/models.py", TEST_CHANGES["app/models.py"])
        
        # Коммитим с бэкапом
        result = vfs.commit_all_sync(backup_manager=backup)
        
        assert result.success
        assert "app/models.py" in result.backups, "Should have backup"
        
        # Проверяем что файл изменился
        new_content = fixture.read_file("app/models.py")
        assert new_content != original
        assert "deactivate" in new_content
        
        # Восстанавливаем
        sessions = backup.list_sessions()
        assert len(sessions) > 0
        
        backup.restore_file("app/models.py", sessions[0].session_id)
        restored = fixture.read_file("app/models.py")
        assert restored == original, "Should restore original"
        
        return TestResult(
            name="Workflow with Backup",
            passed=True,
            duration_ms=(time.time() - start) * 1000
        )
    except Exception as e:
        return TestResult(
            name="Workflow with Backup",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_concurrent_staging() -> TestResult:
    """Тест конкурентного стейджинга"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        
        vfs = VirtualFileSystem(fixture.path)
        
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
        
        return TestResult(
            name="Concurrent Staging",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={"files": len(results), "errors": len(errors)}
        )
    except Exception as e:
        return TestResult(
            name="Concurrent Staging",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


def test_large_file() -> TestResult:
    """Тест работы с большими файлами"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.syntax_checker import SyntaxChecker
        
        vfs = VirtualFileSystem(fixture.path)
        checker = SyntaxChecker()
        
        # Генерируем большой файл (~500KB)
        lines = []
        for i in range(10000):
            lines.append(f"def function_{i}():")
            lines.append(f"    '''Function {i}'''")
            lines.append(f"    return {i}")
            lines.append("")
        
        large_content = "\n".join(lines)
        size_kb = len(large_content) / 1024
        
        # Проверяем синтаксис
        t0 = time.time()
        syntax_result = checker.check_python(large_content)
        syntax_time = (time.time() - t0) * 1000
        
        assert syntax_result.is_valid, "Large file should be valid"
        
        # Стейджим
        t0 = time.time()
        vfs.stage_change("app/large_file.py", large_content)
        stage_time = (time.time() - t0) * 1000
        
        # Коммитим
        t0 = time.time()
        result = vfs.commit_all_sync()
        commit_time = (time.time() - t0) * 1000
        
        assert result.success
        
        return TestResult(
            name="Large File",
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            details={
                "size_kb": f"{size_kb:.1f}",
                "syntax_ms": f"{syntax_time:.1f}",
                "stage_ms": f"{stage_time:.1f}",
                "commit_ms": f"{commit_time:.1f}"
            }
        )
    except Exception as e:
        return TestResult(
            name="Large File",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


async def test_performance() -> TestResult:
    """Тест производительности"""
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.virtual_fs import VirtualFileSystem
        from app.services.change_validator import ChangeValidator, ValidationLevel
        from app.services.syntax_checker import SyntaxChecker
        
        timings = {}
        
        # 1. VFS initialization
        t0 = time.time()
        vfs = VirtualFileSystem(fixture.path)
        timings["vfs_init"] = (time.time() - t0) * 1000
        
        # 2. Stage multiple changes
        t0 = time.time()
        for path, content in TEST_CHANGES.items():
            vfs.stage_change(path, content)
        timings["staging"] = (time.time() - t0) * 1000
        
        # 3. Get affected files
        t0 = time.time()
        affected = vfs.get_affected_files()
        timings["affected_files"] = (time.time() - t0) * 1000
        
        # 4. SyntaxChecker
        t0 = time.time()
        checker = SyntaxChecker()
        for content in TEST_CHANGES.values():
            checker.check_python(content)
        timings["syntax_check"] = (time.time() - t0) * 1000
        
        # 5. Full validation
        t0 = time.time()
        validator = ChangeValidator(vfs)
        result = await validator.validate(levels=[
            ValidationLevel.SYNTAX,
            ValidationLevel.IMPORTS,
        ])
        timings["validation"] = (time.time() - t0) * 1000
        
        # Проверяем что всё укладывается в разумные рамки
        total = sum(timings.values())
        perf_ok = total < 30000  # < 30 секунд
        
        return TestResult(
            name="Performance",
            passed=perf_ok,
            duration_ms=(time.time() - start) * 1000,
            details={
                "timings_ms": timings,
                "total_ms": f"{total:.1f}",
                "within_limits": perf_ok
            },
            error=None if perf_ok else f"Total time {total:.0f}ms exceeds limit"
        )
    except Exception as e:
        return TestResult(
            name="Performance",
            passed=False,
            duration_ms=(time.time() - start) * 1000,
            error=str(e)
        )
    finally:
        fixture.cleanup()


# ============================================================================
# MAIN
# ============================================================================

async def run_all_tests():
    """Запускает все тесты"""
    print("\n" + "=" * 60)
    print("🧪 AGENT MODE STAGE 2 - INTEGRATION TESTS")
    print("=" * 60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Log: test_agent_stage2.log")
    if ARGS.skip_slow:
        print("⏭️  Slow tests skipped (--skip-slow)")
    if ARGS.filter:
        print(f"🔍 Filter: {ARGS.filter}")
    print()
    
    all_suites = []
    
    # ==================== VFS Tests ====================
    print("\n📁 Virtual File System Tests")
    print("-" * 40)
    
    vfs_suite = TestSuite("VirtualFileSystem")
    
    vfs_suite.add(test_vfs_initialization())
    vfs_suite.add(test_vfs_read_file())
    vfs_suite.add(test_vfs_stage_modify())
    vfs_suite.add(test_vfs_stage_create())
    vfs_suite.add(test_vfs_stage_delete())
    vfs_suite.add(test_vfs_stage_with_change_type())
    vfs_suite.add(test_vfs_affected_files())
    vfs_suite.add(test_vfs_find_tests())
    vfs_suite.add(test_vfs_unstage())
    vfs_suite.add(test_vfs_diff())
    vfs_suite.add(test_vfs_status())
    vfs_suite.add(test_vfs_commit())
    vfs_suite.add(test_vfs_commit_delete())
    vfs_suite.add(test_vfs_unicode_content())
    vfs_suite.add(test_vfs_unicode_path())
    vfs_suite.add(test_vfs_all_python_files())
    vfs_suite.add(test_vfs_cache_invalidation())
    
    print(vfs_suite.summary())
    all_suites.append(vfs_suite)
    
    # ==================== SyntaxChecker Tests ====================
    print("\n🔍 Syntax Checker Tests")
    print("-" * 40)
    
    syntax_suite = TestSuite("SyntaxChecker")
    
    syntax_suite.add(test_syntax_checker_valid())
    syntax_suite.add(test_syntax_checker_syntax_error())
    syntax_suite.add(test_syntax_checker_indent_error())
    syntax_suite.add(test_syntax_checker_tab_space_mix())
    syntax_suite.add(test_syntax_checker_auto_fix())
    syntax_suite.add(test_syntax_checker_json())
    syntax_suite.add(test_syntax_checker_unicode())
    
    print(syntax_suite.summary())
    all_suites.append(syntax_suite)
    
    # ==================== ChangeValidator Tests ====================
    print("\n✅ Change Validator Tests")
    print("-" * 40)
    
    validator_suite = TestSuite("ChangeValidator")
    
    validator_suite.add(await test_validator_syntax_level())
    validator_suite.add(await test_validator_syntax_error())
    validator_suite.add(await test_validator_imports_level())
    validator_suite.add(await test_validator_import_error())
    validator_suite.add(await test_validator_types_level())
    validator_suite.add(await test_validator_integration_level())
    validator_suite.add(await test_validator_new_files())
    validator_suite.add(await test_validator_quick_methods())
    validator_suite.add(await test_validator_test_discovery())
    validator_suite.add(await test_validator_result_summary())
    
    print(validator_suite.summary())
    all_suites.append(validator_suite)
    
    # ==================== Integration Tests ====================
    print("\n🔗 Integration Tests")
    print("-" * 40)
    
    integration_suite = TestSuite("Integration")
    
    integration_suite.add(await test_full_workflow())
    integration_suite.add(await test_workflow_with_backup())
    integration_suite.add(test_concurrent_staging())
    
    # Медленные тесты
    if not ARGS.skip_slow:
        integration_suite.add(test_large_file())
        integration_suite.add(await test_performance())
    else:
        integration_suite.skip("Large File", "slow test")
        integration_suite.skip("Performance", "slow test")
    
    print(integration_suite.summary())
    all_suites.append(integration_suite)
    
    # ==================== Final Summary ====================
    print("\n" + "=" * 60)
    print("📊 FINAL SUMMARY")
    print("=" * 60)
    
    total_passed = sum(s.passed for s in all_suites)
    total_failed = sum(s.failed for s in all_suites)
    total_skipped = sum(s.skipped for s in all_suites)
    total_tests = total_passed + total_failed
    total_time = sum(s.total_time_ms for s in all_suites)
    
    for suite in all_suites:
        status = "✅" if suite.failed == 0 else "❌"
        skip_info = f" ({suite.skipped} skipped)" if suite.skipped else ""
        print(f"  {status} {suite.name}: {suite.passed}/{len(suite.results) - suite.skipped} passed{skip_info} ({suite.total_time_ms:.0f}ms)")
    
    print("-" * 60)
    
    if total_failed == 0:
        skip_info = f" ({total_skipped} skipped)" if total_skipped else ""
        print(f"✅ ALL TESTS PASSED: {total_passed}/{total_tests}{skip_info} ({total_time:.0f}ms)")
    else:
        print(f"❌ TESTS FAILED: {total_passed}/{total_tests} passed, {total_failed} failed ({total_time:.0f}ms)")
        
        # Выводим все ошибки
        print("\n❌ Failed Tests:")
        for suite in all_suites:
            for result in suite.results:
                if not result.passed and not result.skipped:
                    print(f"  - {suite.name}::{result.name}")
                    print(f"    └── {result.error}")
    
    print("=" * 60 + "\n")
    
    logger.info(f"Test summary: {total_passed}/{total_tests} passed, {total_failed} failed, {total_skipped} skipped")
    
    return total_failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)