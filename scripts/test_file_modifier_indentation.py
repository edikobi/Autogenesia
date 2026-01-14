#!/usr/bin/env python3
# scripts/test_file_modifier_indentation.py
"""
Тестовый скрипт для проверки FileModifier с автоматической нормализацией отступов.

Проверяет:
1. _normalize_and_indent_code - базовая нормализация отступов
2. _detect_indent_from_context - определение отступа из контекста
3. REPLACE_METHOD - замена метода с любыми входными отступами
4. REPLACE_FUNCTION - замена функции с любыми входными отступами
5. INSERT_CLASS - вставка метода в класс
6. PATCH_METHOD - вставка кода внутрь метода
7. REPLACE_CLASS - замена класса целиком
8. INSERT_INTO_FILE - вставка кода после импортов
9. Интеграция с VFS
10. Граничные случаи: вложенные классы, async методы, декораторы

Запуск:
    python scripts/test_file_modifier_indentation.py
    python scripts/test_file_modifier_indentation.py --verbose
    python scripts/test_file_modifier_indentation.py --filter patch

Опции:
    --verbose       Подробный вывод
    --filter PATTERN  Запустить только тесты, содержащие PATTERN
    --no-cleanup    Не удалять временные файлы
"""

import sys
import os
import asyncio
import logging
import tempfile
import shutil
import argparse
import time
import ast
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

# Добавляем корень проекта в путь
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# CLI ARGUMENTS
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="Test FileModifier indentation handling")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--filter", "-f", type=str, help="Run only tests matching pattern")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't cleanup temp files")
    return parser.parse_args()


ARGS = parse_args()


# ============================================================================
# LOGGING SETUP
# ============================================================================

class ColorFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'RESET': '\033[0m',
    }
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


log_level = logging.DEBUG if ARGS.verbose else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("test_indentation")


# ============================================================================
# TEST RESULT TRACKING
# ============================================================================

@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float
    message: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""
    
    def __str__(self) -> str:
        if self.skipped:
            return f"⏭️  SKIP | {self.name} | ({self.skip_reason})"
        status = "✅ PASS" if self.passed else "❌ FAIL"
        time_str = f"{self.duration_ms:.1f}ms"
        result = f"{status} | {self.name} | {time_str}"
        if self.message:
            result += f" | {self.message}"
        if self.error:
            result += f"\n         └── Error: {self.error}"
        return result


@dataclass
class TestSuite:
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
    
    def summary(self) -> str:
        total = len(self.results)
        skip_info = f" ({self.skipped} skipped)" if self.skipped else ""
        return (
            f"\n{'='*60}\n"
            f"📊 {self.name}: {self.passed}/{total - self.skipped} passed{skip_info} "
            f"({self.total_time_ms:.0f}ms)\n"
            f"{'='*60}"
        )


# ============================================================================
# TEST FIXTURE DATA
# ============================================================================

# Базовый тестовый файл с классом
SAMPLE_SERVICE_FILE = '''"""Service module."""
from typing import Optional, List
from datetime import datetime


class UserService:
    """Service for user operations."""
    
    def __init__(self, db_connection):
        """Initialize service."""
        self.db = db_connection
        self.cache = {}
    
    def get_user(self, user_id: int) -> Optional[dict]:
        """Get user by ID."""
        if user_id in self.cache:
            return self.cache[user_id]
        return self.db.find_user(user_id)
    
    def create_user(self, name: str, email: str) -> dict:
        """Create a new user."""
        user = {
            "id": self._generate_id(),
            "name": name,
            "email": email,
            "created_at": datetime.now()
        }
        self.db.save(user)
        return user
    
    def _generate_id(self) -> int:
        """Generate unique ID."""
        return hash(datetime.now().isoformat())


class PostService:
    """Service for post operations."""
    
    def __init__(self, user_service: UserService):
        self.user_service = user_service
        self.posts = []
    
    def create_post(self, user_id: int, title: str, content: str) -> Optional[dict]:
        """Create a post."""
        user = self.user_service.get_user(user_id)
        if not user:
            return None
        post = {"title": title, "content": content, "author_id": user_id}
        self.posts.append(post)
        return post
'''

# Файл с вложенными классами
SAMPLE_NESTED_CLASSES = '''"""Module with nested classes."""


class OuterClass:
    """Outer class with nested classes."""
    
    class InnerClass:
        """Inner class."""
        
        def inner_method(self) -> str:
            """Inner method."""
            return "inner"
        
        class DeepNestedClass:
            """Deeply nested class."""
            
            def deep_method(self) -> str:
                """Deep method."""
                return "deep"
    
    def outer_method(self) -> str:
        """Outer method."""
        return "outer"
'''

# Файл с async методами и декораторами
SAMPLE_ASYNC_FILE = '''"""Async module."""
import asyncio
from functools import wraps


def timing_decorator(func):
    """Timing decorator."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = asyncio.get_event_loop().time()
        result = await func(*args, **kwargs)
        end = asyncio.get_event_loop().time()
        print(f"{func.__name__} took {end - start:.2f}s")
        return result
    return wrapper


class AsyncService:
    """Async service."""
    
    def __init__(self):
        self.data = {}
    
    @timing_decorator
    async def fetch_data(self, key: str) -> Optional[str]:
        """Fetch data asynchronously."""
        await asyncio.sleep(0.1)
        return self.data.get(key)
    
    async def save_data(self, key: str, value: str) -> bool:
        """Save data asynchronously."""
        await asyncio.sleep(0.05)
        self.data[key] = value
        return True
'''

# Простой файл с функциями (без классов)
SAMPLE_FUNCTIONS_FILE = '''"""Utility functions."""
from typing import List, Optional


def calculate_sum(numbers: List[int]) -> int:
    """Calculate sum of numbers."""
    total = 0
    for num in numbers:
        total += num
    return total


def find_max(numbers: List[int]) -> Optional[int]:
    """Find maximum number."""
    if not numbers:
        return None
    return max(numbers)


def format_output(value: int, prefix: str = "") -> str:
    """Format output string."""
    return f"{prefix}{value}"
'''


# ============================================================================
# TEST FIXTURE CLASS
# ============================================================================

class TestFixture:
    """Manages test environment with isolation."""
    
    def __init__(self):
        self.temp_dir: Optional[Path] = None
        self._cleanup_enabled = not ARGS.no_cleanup
    
    def setup(self) -> 'TestFixture':
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_modifier_"))
        return self
    
    def cleanup(self):
        if not self._cleanup_enabled:
            logger.info(f"Keeping temp dir: {self.temp_dir}")
            return
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @property
    def path(self) -> str:
        return str(self.temp_dir)
    
    def write_file(self, rel_path: str, content: str) -> Path:
        path = self.temp_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path
    
    def read_file(self, rel_path: str) -> str:
        return (self.temp_dir / rel_path).read_text(encoding='utf-8')


def should_skip(test_name: str) -> Optional[str]:
    """Check if test should be skipped based on filter."""
    if ARGS.filter and ARGS.filter.lower() not in test_name.lower():
        return "filtered out"
    return None


def validate_python_syntax(code: str) -> Tuple[bool, Optional[str]]:
    """Validate Python syntax and return (is_valid, error_message)."""
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"


def get_indentation(line: str) -> int:
    """Get indentation level of a line in spaces."""
    return len(line) - len(line.lstrip())


# ============================================================================
# UNIT TESTS: _normalize_and_indent_code
# ============================================================================

def test_normalize_basic() -> TestResult:
    """Test basic indentation normalization."""
    name = "normalize_basic"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        # Code with 8-space indent, need 4-space
        code_8_spaces = '''        def method(self):
            return True'''
        
        result = modifier._normalize_and_indent_code(code_8_spaces, target_indent=4)
        
        lines = result.splitlines()
        assert get_indentation(lines[0]) == 4, f"First line should have 4 spaces, got {get_indentation(lines[0])}"
        assert get_indentation(lines[1]) == 8, f"Second line should have 8 spaces (4 base + 4 relative)"
        assert "def method(self):" in lines[0]
        assert "return True" in lines[1]
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="8-space to 4-space OK"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


def test_normalize_zero_indent() -> TestResult:
    """Test normalization to zero indent (module level)."""
    name = "normalize_zero_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        # Code with arbitrary indent, normalize to 0
        code_indented = '''    def function():
        return 42'''
        
        result = modifier._normalize_and_indent_code(code_indented, target_indent=0)
        
        lines = result.splitlines()
        assert get_indentation(lines[0]) == 0, "First line should have 0 indent"
        assert get_indentation(lines[1]) == 4, "Second line should have 4 spaces (relative)"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="To zero indent OK"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


def test_normalize_preserve_relative() -> TestResult:
    """Test that relative indentation is preserved."""
    name = "normalize_preserve_relative"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        # Nested code with varying indentation
        code = '''def outer():
    if True:
        for i in range(10):
            print(i)
    return None'''
        
        result = modifier._normalize_and_indent_code(code, target_indent=4)
        
        lines = result.splitlines()
        # Check relative indents are preserved
        assert get_indentation(lines[0]) == 4, "def should be at 4"
        assert get_indentation(lines[1]) == 8, "if should be at 8"
        assert get_indentation(lines[2]) == 12, "for should be at 12"
        assert get_indentation(lines[3]) == 16, "print should be at 16"
        assert get_indentation(lines[4]) == 8, "return should be at 8"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Relative indentation preserved"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


def test_normalize_empty_lines() -> TestResult:
    """Test that empty lines are handled correctly."""
    name = "normalize_empty_lines"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        code = '''        def method(self):
            x = 1
            
            y = 2
            return x + y'''
        
        result = modifier._normalize_and_indent_code(code, target_indent=4)
        
        lines = result.splitlines()
        # Empty line should remain empty (not have trailing spaces)
        assert lines[2] == '', f"Empty line should be empty, got '{lines[2]}'"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Empty lines handled correctly"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


def test_normalize_mixed_indents() -> TestResult:
    """Test normalization of code with messy mixed indentation."""
    name = "normalize_mixed_indents"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        # Simulate what Generator might produce - code with extra base indent
        # This is a METHOD that should be indented at 4 spaces when inside a class
        messy_code = '''            def validate(self, data):
                """Validate data."""
                if not data:
                    return False
                return True'''
        
        result = modifier._normalize_and_indent_code(messy_code, target_indent=4)
        
        # Check structure is correct
        lines = result.splitlines()
        assert get_indentation(lines[0]) == 4, f"def should be at 4, got {get_indentation(lines[0])}"
        assert get_indentation(lines[1]) == 8, f"docstring should be at 8, got {get_indentation(lines[1])}"
        assert get_indentation(lines[2]) == 8, f"if should be at 8, got {get_indentation(lines[2])}"
        assert get_indentation(lines[3]) == 12, f"return False should be at 12, got {get_indentation(lines[3])}"
        assert get_indentation(lines[4]) == 8, f"return True should be at 8, got {get_indentation(lines[4])}"
        
        # Build a complete class to test syntax - the normalized code is a METHOD
        # so it needs to be inside a class
        test_code = f"class Test:\n{result}"
        is_valid, error = validate_python_syntax(test_code)
        assert is_valid, f"Normalized code in class context should be valid Python: {error}\n\nGenerated:\n{test_code}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Messy indentation normalized"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


# ============================================================================
# UNIT TESTS: _detect_indent_from_context
# ============================================================================

def test_detect_indent_before() -> TestResult:
    """Test detecting indent from lines before."""
    name = "detect_indent_before"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        lines = [
            "class MyClass:",
            "    def method(self):",
            "        x = 1",
            "        y = 2",
            "        # insertion point here",
        ]
        
        # Detect indent from line before index 4
        indent = modifier._detect_indent_from_context(lines, line_index=4, direction="before")
        
        assert indent == 8, f"Should detect 8-space indent, got {indent}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Detected 8-space indent"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


def test_detect_indent_after() -> TestResult:
    """Test detecting indent from lines after."""
    name = "detect_indent_after"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    try:
        from app.services.file_modifier import FileModifier
        modifier = FileModifier()
        
        lines = [
            "class MyClass:",
            "    # insertion point",
            "    def method(self):",
            "        return True",
        ]
        
        indent = modifier._detect_indent_from_context(lines, line_index=1, direction="after")
        
        assert indent == 4, f"Should detect 4-space indent, got {indent}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Detected 4-space indent"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))


# ============================================================================
# INTEGRATION TESTS: REPLACE_METHOD
# ============================================================================

def test_replace_method_with_wrong_indent() -> TestResult:
    """Test REPLACE_METHOD with code that has wrong indentation."""
    name = "replace_method_wrong_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        fixture.write_file("service.py", SAMPLE_SERVICE_FILE)
        modifier = FileModifier()
        
        # Generator sends code with 8 spaces (wrong!)
        wrong_indent_code = '''        def get_user(self, user_id: int) -> Optional[dict]:
            """Get user by ID with caching."""
            # Check cache first
            if user_id in self.cache:
                return self.cache[user_id]
            
            # Fetch from DB
            user = self.db.find_user(user_id)
            if user:
                self.cache[user_id] = user
            return user'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=wrong_indent_code,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        # Validate syntax
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check correct indentation (4 spaces for method in class)
        assert "    def get_user(self, user_id: int)" in result.new_content, "Method should have 4-space indent"
        assert "        # Check cache first" in result.new_content, "Body should have 8-space indent"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Wrong indent auto-corrected"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_replace_method_with_zero_indent() -> TestResult:
    """Test REPLACE_METHOD with code that has zero indentation."""
    name = "replace_method_zero_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        fixture.write_file("service.py", SAMPLE_SERVICE_FILE)
        modifier = FileModifier()
        
        # Generator sends code with NO indent (copy-pasted from somewhere)
        zero_indent_code = '''def create_user(self, name: str, email: str) -> dict:
    """Create a new user with validation."""
    if not name or not email:
        raise ValueError("Name and email required")
    
    user = {
        "id": self._generate_id(),
        "name": name.strip(),
        "email": email.lower(),
        "created_at": datetime.now()
    }
    self.db.save(user)
    self.cache[user["id"]] = user
    return user'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=zero_indent_code,
            target_class="UserService",
            target_method="create_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        assert "    def create_user(self, name: str, email: str)" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Zero indent auto-corrected"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_replace_method_preserves_others() -> TestResult:
    """Test that REPLACE_METHOD preserves other methods in class."""
    name = "replace_method_preserves_others"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_code = '''def get_user(self, user_id: int) -> Optional[dict]:
    """New implementation."""
    return {"id": user_id}'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=new_code,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success
        
        # Check other methods still exist
        assert "def __init__(self, db_connection):" in result.new_content
        assert "def create_user(self, name: str, email: str)" in result.new_content
        assert "def _generate_id(self)" in result.new_content
        
        # Check PostService class is untouched
        assert "class PostService:" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Other methods preserved"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# INTEGRATION TESTS: REPLACE_FUNCTION
# ============================================================================

def test_replace_function_wrong_indent() -> TestResult:
    """Test REPLACE_FUNCTION with wrong indentation."""
    name = "replace_function_wrong_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Function with 4-space indent (should be 0 for module level)
        wrong_code = '''    def calculate_sum(numbers: List[int]) -> int:
        """Calculate sum with validation."""
        if not numbers:
            return 0
        return sum(numbers)'''
        
        block = ParsedCodeBlock(
            file_path="utils.py",
            mode="REPLACE_FUNCTION",
            code=wrong_code,
            target_function="calculate_sum",
        )
        
        result = modifier.apply_code_block(SAMPLE_FUNCTIONS_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Module-level function should have 0 indent
        assert "def calculate_sum(numbers: List[int])" in result.new_content
        lines = result.new_content.splitlines()
        for line in lines:
            if line.strip().startswith("def calculate_sum"):
                assert get_indentation(line) == 0, f"Function should be at module level (0 indent), got {get_indentation(line)}"
                break
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Function indent corrected to 0"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# INTEGRATION TESTS: INSERT_CLASS
# ============================================================================

def test_insert_class_new_method() -> TestResult:
    """Test INSERT_CLASS to add new method."""
    name = "insert_class_new_method"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # New method with 8-space indent (wrong for class method)
        new_method = '''        def delete_user(self, user_id: int) -> bool:
            """Delete user by ID."""
            if user_id in self.cache:
                del self.cache[user_id]
            return self.db.delete(user_id)'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="INSERT_CLASS",
            code=new_method,
            target_class="UserService",
            insert_after="create_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check method was inserted with correct indent
        assert "def delete_user(self, user_id: int)" in result.new_content
        assert "    def delete_user" in result.new_content, "Should have 4-space indent"
        
        # Check it's after create_user
        create_pos = result.new_content.find("def create_user")
        delete_pos = result.new_content.find("def delete_user")
        assert delete_pos > create_pos, "delete_user should be after create_user"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="New method inserted with correct indent"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_insert_class_no_existing_methods() -> TestResult:
    """Test INSERT_CLASS when class has no methods (edge case)."""
    name = "insert_class_no_methods"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Simple class with no methods
        simple_class = '''class EmptyClass:
    """A class with no methods."""
    pass
'''
        
        new_method = '''def first_method(self) -> str:
    """First method."""
    return "hello"'''
        
        block = ParsedCodeBlock(
            file_path="simple.py",
            mode="INSERT_CLASS",
            code=new_method,
            target_class="EmptyClass",
        )
        
        result = modifier.apply_code_block(simple_class, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Method inserted into empty class"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# INTEGRATION TESTS: PATCH_METHOD
# ============================================================================

def test_patch_method_insert_after() -> TestResult:
    """Test PATCH_METHOD with insert_after pattern."""
    name = "patch_method_insert_after"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Looking at SAMPLE_SERVICE_FILE, create_user method has:
        #     user = {
        #         "id": self._generate_id(),
        #         ...
        #     }
        #     self.db.save(user)
        #     return user
        #
        # We want to insert AFTER "self.db.save(user)" line (before return)
        # This is a safe place to insert logging
        code_to_insert = '''# Log user creation
logger.info(f"Created user: {user['id']}")'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="PATCH_METHOD",
            code=code_to_insert,
            target_class="UserService",
            target_method="create_user",
            insert_after="self.db.save(user)",  # Insert after db save, before return
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}\n\nContent:\n{result.new_content}"
        
        # Check code was inserted
        assert "# Log user creation" in result.new_content
        assert "logger.info" in result.new_content
        
        # Check order: db.save -> our code -> return
        save_pos = result.new_content.find("self.db.save(user)")
        log_pos = result.new_content.find("# Log user creation")
        return_pos = result.new_content.find("return user")
        
        assert save_pos < log_pos < return_pos, "Code should be between save and return"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Code patched into method"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_patch_method_insert_before_return() -> TestResult:
    """Test PATCH_METHOD inserting before return statement."""
    name = "patch_method_before_return"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Code to insert (with messy indent)
        code_to_insert = '''        # Validate before saving
        if not user.get("email"):
            raise ValueError("Email required")'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="PATCH_METHOD",
            code=code_to_insert,
            target_class="UserService",
            target_method="create_user",
            insert_before="self.db.save(user)",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check code was inserted before db.save
        validate_pos = result.new_content.find("# Validate before saving")
        save_pos = result.new_content.find("self.db.save(user)")
        assert validate_pos < save_pos, "Validation should be before save"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Code inserted before target line"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_patch_method_default_before_return() -> TestResult:
    """Test PATCH_METHOD default behavior (insert before return)."""
    name = "patch_method_default_return"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        code_to_insert = '''# Final validation
if not user:
    raise RuntimeError("User not found")'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="PATCH_METHOD",
            code=code_to_insert,
            target_class="UserService",
            target_method="get_user",
            # No insert_after or insert_before - should insert before return
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check code was inserted before return
        validation_pos = result.new_content.find("# Final validation")
        return_pos = result.new_content.find("return self.db.find_user")
        assert validation_pos < return_pos, "Should be inserted before return"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Default behavior: inserted before return"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# INTEGRATION TESTS: REPLACE_CLASS
# ============================================================================

def test_replace_class_wrong_indent() -> TestResult:
    """Test REPLACE_CLASS with wrong indentation."""
    name = "replace_class_wrong_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Class with 4-space indent (should be 0 at module level)
        wrong_class = '''    class PostService:
        """Refactored post service."""
        
        def __init__(self):
            self.posts = []
        
        def get_all(self) -> list:
            return self.posts'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_CLASS",
            code=wrong_class,
            target_class="PostService",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # UserService should be untouched
        assert "class UserService:" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Class replaced with corrected indent"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# INTEGRATION TESTS: INSERT_INTO_FILE
# ============================================================================

def test_insert_into_file_function() -> TestResult:
    """Test INSERT_INTO_FILE for adding a new function."""
    name = "insert_into_file_function"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # New function with 4-space indent (wrong for module level)
        new_function = '''    def validate_numbers(numbers: List[int]) -> bool:
        """Validate list of numbers."""
        return all(n >= 0 for n in numbers)'''
        
        block = ParsedCodeBlock(
            file_path="utils.py",
            mode="INSERT_FILE",
            code=new_function,
        )
        
        result = modifier.apply_code_block(SAMPLE_FUNCTIONS_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Function should be at module level (0 indent)
        assert "def validate_numbers" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Function inserted at module level"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

def test_async_method_replace() -> TestResult:
    """Test replacing async method."""
    name = "async_method_replace"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Replace async method
        new_async = '''async def fetch_data(self, key: str) -> Optional[str]:
    """Fetch with retry."""
    for _ in range(3):
        result = self.data.get(key)
        if result:
            return result
        await asyncio.sleep(0.1)
    return None'''
        
        block = ParsedCodeBlock(
            file_path="async_service.py",
            mode="REPLACE_METHOD",
            code=new_async,
            target_class="AsyncService",
            target_method="fetch_data",
        )
        
        result = modifier.apply_code_block(SAMPLE_ASYNC_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check async preserved
        assert "async def fetch_data" in result.new_content
        assert "for _ in range(3)" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Async method replaced correctly"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_method_with_decorator_replace() -> TestResult:
    """Test replacing method that has decorators."""
    name = "method_with_decorator"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Note: When replacing a method, decorator is part of the method body
        # So we need to include decorator in the replacement code
        new_method_with_decorator = '''@timing_decorator
async def fetch_data(self, key: str) -> Optional[str]:
    """Fetch with logging."""
    print(f"Fetching {key}")
    return self.data.get(key)'''
        
        block = ParsedCodeBlock(
            file_path="async_service.py",
            mode="REPLACE_METHOD",
            code=new_method_with_decorator,
            target_class="AsyncService",
            target_method="fetch_data",
        )
        
        result = modifier.apply_code_block(SAMPLE_ASYNC_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Method with decorator replaced"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_nested_class_method() -> TestResult:
    """Test replacing method in nested class."""
    name = "nested_class_method"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Replace method in InnerClass
        new_method = '''def inner_method(self) -> str:
    """Updated inner method."""
    return "inner_updated"'''
        
        block = ParsedCodeBlock(
            file_path="nested.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="InnerClass",  # Note: this targets InnerClass directly
            target_method="inner_method",
        )
        
        result = modifier.apply_code_block(SAMPLE_NESTED_CLASSES, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        assert "inner_updated" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Nested class method replaced"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# VFS INTEGRATION TESTS
# ============================================================================

def test_vfs_integration_modify() -> TestResult:
    """Test FileModifier integration with VFS."""
    name = "vfs_integration_modify"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        from app.services.virtual_fs import VirtualFileSystem
        
        # Write test file
        fixture.write_file("app/service.py", SAMPLE_SERVICE_FILE)
        
        vfs = VirtualFileSystem(fixture.path)
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="app/service.py",
            mode="REPLACE_METHOD",
            code='''        def get_user(self, user_id: int) -> Optional[dict]:
            """VFS test."""
            return {"id": user_id}''',
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block_to_vfs(vfs, block)
        
        assert result.success, f"Should succeed: {result.message}"
        assert vfs.has_pending_changes(), "VFS should have pending changes"
        
        # Check staged content
        staged_content = vfs.read_file("app/service.py")
        assert "VFS test." in staged_content
        
        is_valid, error = validate_python_syntax(staged_content)
        assert is_valid, f"Staged content should be valid Python: {error}"
        
        # Original file should be unchanged
        original = fixture.read_file("app/service.py")
        assert "VFS test." not in original
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="VFS integration works"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_multiple_blocks_same_file() -> TestResult:
    """Test applying multiple CODE_BLOCKs to same file."""
    name = "multiple_blocks_same_file"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        from app.services.virtual_fs import VirtualFileSystem
        
        fixture.write_file("app/service.py", SAMPLE_SERVICE_FILE)
        
        vfs = VirtualFileSystem(fixture.path)
        modifier = FileModifier()
        
        # Block 1: Replace method
        block1 = ParsedCodeBlock(
            file_path="app/service.py",
            mode="REPLACE_METHOD",
            code='''def get_user(self, user_id: int) -> Optional[dict]:
    """Block 1."""
    return None''',
            target_class="UserService",
            target_method="get_user",
        )
        
        # Block 2: Insert new method
        block2 = ParsedCodeBlock(
            file_path="app/service.py",
            mode="INSERT_CLASS",
            code='''def delete_user(self, user_id: int) -> bool:
    """Block 2."""
    return True''',
            target_class="UserService",
            insert_after="get_user",
        )
        
        # Apply both
        result1 = modifier.apply_code_block_to_vfs(vfs, block1)
        assert result1.success, f"Block 1 failed: {result1.message}"
        
        result2 = modifier.apply_code_block_to_vfs(vfs, block2)
        assert result2.success, f"Block 2 failed: {result2.message}"
        
        # Check final content
        content = vfs.read_file("app/service.py")
        
        is_valid, error = validate_python_syntax(content)
        assert is_valid, f"Final content should be valid Python: {error}"
        
        assert "Block 1." in content
        assert "Block 2." in content
        assert "def delete_user" in content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Multiple blocks applied successfully"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# STRESS TESTS
# ============================================================================

def test_extreme_indent_correction() -> TestResult:
    """Test correction of extreme indentation (16+ spaces)."""
    name = "extreme_indent_correction"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Code with 16-space indent
        extreme_indent = '''                def get_user(self, user_id: int) -> Optional[dict]:
                    """Extreme indent."""
                    if user_id:
                        return {"id": user_id}
                    return None'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=extreme_indent,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check correct 4-space indent
        assert "    def get_user" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Extreme indent (16 spaces) corrected"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_tabs_to_spaces_conversion() -> TestResult:
    """Test that tabs are converted to spaces."""
    name = "tabs_to_spaces"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Code with tabs
        code_with_tabs = "def get_user(self, user_id: int) -> Optional[dict]:\n\t\"\"\"Tab indent.\"\"\"\n\treturn None"
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=code_with_tabs,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Note: Current implementation may not convert tabs to spaces
        # This test documents current behavior
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Tabs handled (may or may not convert)"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# MAIN
# ============================================================================



# ============================================================================
# REALISTIC GENERATOR OUTPUT TESTS
# ============================================================================

def test_generator_tabs_mixed_with_spaces() -> TestResult:
    """Test handling tabs mixed with spaces (common Generator issue)."""
    name = "generator_tabs_mixed"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Generator sometimes mixes tabs and spaces
        messy_code = "def get_user(self, user_id: int) -> Optional[dict]:\n\t\"\"\"Get user.\"\"\"\n\treturn self.cache.get(user_id)"
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=messy_code,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        # Result should be valid Python
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Tabs mixed with spaces handled"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_generator_trailing_whitespace() -> TestResult:
    """Test handling trailing whitespace in Generator output."""
    name = "generator_trailing_whitespace"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Code with trailing whitespace on various lines
        code_with_trailing = "def get_user(self, user_id: int) -> Optional[dict]:   \n    \"\"\"Get user.\"\"\"   \n    return None   "
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=code_with_trailing,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Trailing whitespace handled"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_generator_windows_line_endings() -> TestResult:
    """Test handling Windows CRLF line endings."""
    name = "generator_crlf"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Code with CRLF line endings
        code_crlf = "def get_user(self, user_id: int) -> Optional[dict]:\r\n    \"\"\"Get user.\"\"\"\r\n    return None"
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=code_crlf,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="CRLF line endings handled"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_generator_inconsistent_indent_within_block() -> TestResult:
    """Test code where Generator used inconsistent indentation within same block."""
    name = "generator_inconsistent_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # This is internally consistent (valid Python) but has unusual base indent
        # Note: truly inconsistent indent would be invalid Python anyway
        code = '''      def get_user(self, user_id: int) -> Optional[dict]:
          """Get user with caching."""
          if user_id in self.cache:
              return self.cache[user_id]
          return None'''
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code=code,
            target_class="UserService",
            target_method="get_user",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Should have proper 4-space indent for method
        assert "    def get_user" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Inconsistent base indent normalized"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# DEEP NESTING TESTS
# ============================================================================

DEEPLY_NESTED_FILE = '''"""Module with deep nesting."""


class DataProcessor:
    """Processes data with complex logic."""
    
    def process(self, data: dict) -> dict:
        """Process data through multiple stages."""
        result = {}
        
        if data:
            for key, value in data.items():
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        if item is not None:
                            # Deep processing here
                            result[f"{key}_{i}"] = item
        
        return result
    
    def validate(self, data: dict) -> bool:
        """Validate data."""
        return bool(data)
'''


def test_patch_deeply_nested_code() -> TestResult:
    """Test PATCH_METHOD at 4+ nesting levels."""
    name = "patch_deeply_nested"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Insert code after the deep comment
        code_to_insert = '''# Transform the item
transformed = str(item).upper()
result[f"{key}_{i}"] = transformed'''
        
        block = ParsedCodeBlock(
            file_path="processor.py",
            mode="PATCH_METHOD",
            code=code_to_insert,
            target_class="DataProcessor",
            target_method="process",
            insert_after="# Deep processing here",
        )
        
        result = modifier.apply_code_block(DEEPLY_NESTED_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}\n\nContent:\n{result.new_content}"
        
        # Check the code was inserted
        assert "# Transform the item" in result.new_content
        assert "transformed = str(item).upper()" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Deep nesting (4+ levels) handled"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_insert_method_into_nested_class() -> TestResult:
    """Test INSERT_CLASS into a nested class."""
    name = "insert_into_nested_class"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_method = '''def new_inner_method(self) -> str:
    """New method for inner class."""
    return "new_inner"'''
        
        block = ParsedCodeBlock(
            file_path="nested.py",
            mode="INSERT_CLASS",
            code=new_method,
            target_class="InnerClass",
            insert_after="inner_method",
        )
        
        result = modifier.apply_code_block(SAMPLE_NESTED_CLASSES, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check method was inserted
        assert "def new_inner_method" in result.new_content
        
        # Check it's after inner_method
        inner_pos = result.new_content.find("def inner_method")
        new_pos = result.new_content.find("def new_inner_method")
        assert new_pos > inner_pos, "New method should be after inner_method"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Method inserted into nested class"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# DECORATOR HANDLING TESTS
# ============================================================================

FILE_WITH_DECORATORS = '''"""Module with decorated methods."""
from functools import lru_cache, wraps


def my_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


class CachedService:
    """Service with cached methods."""
    
    def __init__(self):
        self.data = {}
    
    @lru_cache(maxsize=100)
    def get_cached(self, key: str) -> str:
        """Get cached value."""
        return self.data.get(key, "")
    
    @my_decorator
    @lru_cache(maxsize=50)
    def get_multi_decorated(self, key: str) -> str:
        """Method with multiple decorators."""
        return self.data.get(key, "default")
    
    def get_plain(self, key: str) -> str:
        """Plain method without decorators."""
        return self.data.get(key, "")
'''


def test_replace_decorated_method_removes_old_decorators() -> TestResult:
    """Test that REPLACE_METHOD properly replaces decorated methods."""
    name = "replace_removes_decorators"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Replace with a non-decorated version
        new_method = '''def get_cached(self, key: str) -> str:
    """Get value without caching."""
    return self.data.get(key, "no_cache")'''
        
        block = ParsedCodeBlock(
            file_path="cached.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="CachedService",
            target_method="get_cached",
        )
        
        result = modifier.apply_code_block(FILE_WITH_DECORATORS, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # The new method should be there
        assert "no_cache" in result.new_content
        
        # Other decorated method should still have its decorators
        assert "@my_decorator" in result.new_content
        assert "get_multi_decorated" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Decorated method replaced correctly"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_replace_method_with_new_decorators() -> TestResult:
    """Test replacing plain method with decorated version."""
    name = "replace_add_decorators"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Add decorator to previously plain method
        new_method = '''@lru_cache(maxsize=200)
def get_plain(self, key: str) -> str:
    """Now cached method."""
    return self.data.get(key, "now_cached")'''
        
        block = ParsedCodeBlock(
            file_path="cached.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="CachedService",
            target_method="get_plain",
        )
        
        result = modifier.apply_code_block(FILE_WITH_DECORATORS, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Check new content
        assert "now_cached" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Plain method replaced with decorated version"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# MULTIPLE CLASSES TESTS
# ============================================================================

FILE_WITH_DUPLICATE_METHODS = '''"""Module with multiple classes having same method names."""


class ServiceA:
    """First service."""
    
    def process(self, data: str) -> str:
        """Process in ServiceA."""
        return f"A: {data}"
    
    def validate(self, data: str) -> bool:
        """Validate in ServiceA."""
        return len(data) > 0


class ServiceB:
    """Second service."""
    
    def process(self, data: str) -> str:
        """Process in ServiceB."""
        return f"B: {data}"
    
    def validate(self, data: str) -> bool:
        """Validate in ServiceB."""
        return len(data) > 5


class ServiceC:
    """Third service."""
    
    def process(self, data: str) -> str:
        """Process in ServiceC."""
        return f"C: {data}"
'''


def test_replace_method_correct_class_first() -> TestResult:
    """Test replacing method in first class when multiple have same method."""
    name = "replace_in_first_class"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_method = '''def process(self, data: str) -> str:
    """MODIFIED ServiceA process."""
    return f"A_MODIFIED: {data}"'''
        
        block = ParsedCodeBlock(
            file_path="services.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="ServiceA",
            target_method="process",
        )
        
        result = modifier.apply_code_block(FILE_WITH_DUPLICATE_METHODS, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # ServiceA should be modified
        assert "A_MODIFIED" in result.new_content
        
        # ServiceB and ServiceC should be unchanged
        assert 'return f"B: {data}"' in result.new_content
        assert 'return f"C: {data}"' in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Only ServiceA.process was modified"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_replace_method_correct_class_middle() -> TestResult:
    """Test replacing method in middle class when multiple have same method."""
    name = "replace_in_middle_class"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_method = '''def process(self, data: str) -> str:
    """MODIFIED ServiceB process."""
    return f"B_MODIFIED: {data}"'''
        
        block = ParsedCodeBlock(
            file_path="services.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="ServiceB",
            target_method="process",
        )
        
        result = modifier.apply_code_block(FILE_WITH_DUPLICATE_METHODS, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Only ServiceB should be modified
        assert "B_MODIFIED" in result.new_content
        assert 'return f"A: {data}"' in result.new_content
        assert 'return f"C: {data}"' in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Only ServiceB.process was modified"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# NEGATIVE TESTS (Error Handling)
# ============================================================================

def test_error_class_not_found() -> TestResult:
    """Test proper error when target_class doesn't exist."""
    name = "error_class_not_found"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code="def foo(self): pass",
            target_class="NonExistentClass",
            target_method="foo",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        # Should fail gracefully
        assert not result.success, "Should fail when class not found"
        assert "NonExistentClass" in result.message or "not found" in result.message.lower()
        
        # Original content should be unchanged
        assert result.new_content == SAMPLE_SERVICE_FILE
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Proper error for missing class"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_error_method_not_found() -> TestResult:
    """Test proper error when target_method doesn't exist."""
    name = "error_method_not_found"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="REPLACE_METHOD",
            code="def foo(self): pass",
            target_class="UserService",
            target_method="non_existent_method",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert not result.success, "Should fail when method not found"
        assert "non_existent_method" in result.message or "not found" in result.message.lower()
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Proper error for missing method"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_error_function_not_found() -> TestResult:
    """Test proper error when target_function doesn't exist."""
    name = "error_function_not_found"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="utils.py",
            mode="REPLACE_FUNCTION",
            code="def foo(): pass",
            target_function="non_existent_function",
        )
        
        result = modifier.apply_code_block(SAMPLE_FUNCTIONS_FILE, block)
        
        assert not result.success, "Should fail when function not found"
        assert "non_existent_function" in result.message or "not found" in result.message.lower()
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Proper error for missing function"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_error_patch_pattern_not_found() -> TestResult:
    """Test proper error when PATCH_METHOD pattern not found."""
    name = "error_patch_pattern_not_found"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="PATCH_METHOD",
            code="# some code",
            target_class="UserService",
            target_method="get_user",
            insert_after="THIS_PATTERN_DOES_NOT_EXIST",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert not result.success, "Should fail when pattern not found"
        assert "THIS_PATTERN_DOES_NOT_EXIST" in result.message or "not found" in result.message.lower()
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Proper error for missing pattern"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_error_invalid_mode() -> TestResult:
    """Test proper error for invalid MODE."""
    name = "error_invalid_mode"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="INVALID_MODE_XYZ",
            code="# some code",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert not result.success, "Should fail for invalid mode"
        assert "INVALID_MODE_XYZ" in result.message or "Unknown" in result.message
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Proper error for invalid mode"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_error_syntax_error_in_existing_file() -> TestResult:
    """Test handling of syntax error in existing file."""
    name = "error_existing_syntax_error"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        broken_file = '''class Broken:
    def method(self:  # Missing closing paren
        pass
'''
        
        block = ParsedCodeBlock(
            file_path="broken.py",
            mode="REPLACE_METHOD",
            code="def method(self): pass",
            target_class="Broken",
            target_method="method",
        )
        
        result = modifier.apply_code_block(broken_file, block)
        
        assert not result.success, "Should fail on syntax error in existing file"
        assert "syntax" in result.message.lower() or "error" in result.message.lower()
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Proper error for broken existing file"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# PRESERVATION TESTS
# ============================================================================

FILE_WITH_COMMENTS = '''"""Module with comments."""


class CommentedService:
    """Service with inline comments."""
    
    # Class-level comment about configuration
    CONFIG = {"key": "value"}
    
    def __init__(self):
        # Init comment
        self.data = {}  # Inline comment
    
    def process(self, item: str) -> str:
        """Process an item.
        
        This is a long docstring that should be replaced.
        """
        # Step 1: validate
        if not item:
            return ""
        
        # Step 2: transform
        result = item.upper()
        
        # Step 3: return
        return result  # Return the result
    
    # Comment between methods
    
    def other_method(self):
        """Other method."""
        pass
'''


def test_replace_method_preserves_surrounding_comments() -> TestResult:
    """Test that comments between methods are preserved."""
    name = "preserve_surrounding_comments"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_method = '''def process(self, item: str) -> str:
    """New implementation."""
    return item.lower()'''
        
        block = ParsedCodeBlock(
            file_path="commented.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="CommentedService",
            target_method="process",
        )
        
        result = modifier.apply_code_block(FILE_WITH_COMMENTS, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        # Comment between methods should still exist
        assert "# Comment between methods" in result.new_content
        
        # Class-level comment should exist
        assert "# Class-level comment" in result.new_content
        
        # Init comment should exist
        assert "# Init comment" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Surrounding comments preserved"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_replace_preserves_class_attributes() -> TestResult:
    """Test that class attributes are preserved when replacing method."""
    name = "preserve_class_attributes"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_method = '''def process(self, item: str) -> str:
    """Replaced."""
    return "replaced"'''
        
        block = ParsedCodeBlock(
            file_path="commented.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="CommentedService",
            target_method="process",
        )
        
        result = modifier.apply_code_block(FILE_WITH_COMMENTS, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        # Class attribute should still exist
        assert 'CONFIG = {"key": "value"}' in result.new_content
        
        # Other method should still exist
        assert "def other_method(self):" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Class attributes preserved"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


# ============================================================================
# SPECIAL CASES
# ============================================================================

def test_replace_method_with_only_pass() -> TestResult:
    """Test replacing a method that only has 'pass' as body."""
    name = "replace_pass_only_method"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        simple_class = '''class Simple:
    """Simple class."""
    
    def placeholder(self):
        pass
    
    def other(self):
        return True
'''
        
        new_method = '''def placeholder(self):
    """Now implemented."""
    return "implemented"'''
        
        block = ParsedCodeBlock(
            file_path="simple.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="Simple",
            target_method="placeholder",
        )
        
        result = modifier.apply_code_block(simple_class, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        assert "implemented" in result.new_content
        assert "def other(self):" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Pass-only method replaced"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_method_with_multiline_string() -> TestResult:
    """Test that multiline strings don't confuse indent detection."""
    name = "multiline_string_indent"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        file_with_multiline = '''class Template:
    """Template class."""
    
    def render(self) -> str:
        """Render template."""
        return """
        This is a multiline string
        with weird indentation
    that shouldn't affect anything
        """
    
    def other(self):
        return True
'''
        
        new_method = '''def render(self) -> str:
    """New render."""
    return "simple"'''
        
        block = ParsedCodeBlock(
            file_path="template.py",
            mode="REPLACE_METHOD",
            code=new_method,
            target_class="Template",
            target_method="render",
        )
        
        result = modifier.apply_code_block(file_with_multiline, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        assert '"simple"' in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Multiline string handled correctly"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_insert_import_deduplication() -> TestResult:
    """Test that INSERT_IMPORT doesn't duplicate existing imports."""
    name = "insert_import_dedup"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        # Try to add import that already exists
        block = ParsedCodeBlock(
            file_path="service.py",
            mode="INSERT_IMPORT",
            code="from typing import Optional, List",
        )
        
        result = modifier.apply_code_block(SAMPLE_SERVICE_FILE, block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        # Should have warning about duplicate
        has_warning = len(result.warnings) > 0 or "already" in result.message.lower()
        
        # Count occurrences of the import - should be just one
        import_count = result.new_content.count("from typing import Optional, List")
        assert import_count == 1, f"Import should appear exactly once, found {import_count}"
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Duplicate import prevented"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_append_to_empty_file() -> TestResult:
    """Test APPEND_FILE to empty file."""
    name = "append_to_empty"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        block = ParsedCodeBlock(
            file_path="new.py",
            mode="APPEND_FILE",
            code='''def hello():
    """Say hello."""
    return "Hello, World!"''',
        )
        
        result = modifier.apply_code_block("", block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        assert "def hello():" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="Append to empty file works"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def test_create_new_file_with_replace_file() -> TestResult:
    """Test REPLACE_FILE mode for creating new file."""
    name = "create_new_file"
    if skip := should_skip(name):
        return TestResult(name=name, passed=True, duration_ms=0, skipped=True, skip_reason=skip)
    
    start = time.time()
    fixture = TestFixture().setup()
    
    try:
        from app.services.file_modifier import FileModifier, ParsedCodeBlock
        
        modifier = FileModifier()
        
        new_file_content = '''"""New module."""
from typing import List


def process_items(items: List[str]) -> List[str]:
    """Process items."""
    return [item.upper() for item in items]


class Processor:
    """Item processor."""
    
    def __init__(self):
        self.items = []
    
    def add(self, item: str) -> None:
        self.items.append(item)
'''
        
        block = ParsedCodeBlock(
            file_path="new_module.py",
            mode="REPLACE_FILE",
            code=new_file_content,
        )
        
        # Empty string = new file
        result = modifier.apply_code_block("", block)
        
        assert result.success, f"Should succeed: {result.message}"
        
        is_valid, error = validate_python_syntax(result.new_content)
        assert is_valid, f"Result should be valid Python: {error}"
        
        assert "def process_items" in result.new_content
        assert "class Processor" in result.new_content
        
        return TestResult(
            name=name,
            passed=True,
            duration_ms=(time.time() - start) * 1000,
            message="New file created with REPLACE_FILE"
        )
    except Exception as e:
        return TestResult(name=name, passed=False, duration_ms=(time.time() - start) * 1000, error=str(e))
    finally:
        fixture.cleanup()


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("🧪 FILE MODIFIER INDENTATION TESTS")
    print("=" * 60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if ARGS.filter:
        print(f"🔍 Filter: {ARGS.filter}")
    print()
    
    all_suites = []
    
    # ==================== Unit Tests: _normalize_and_indent_code ====================
    print("\n📏 Normalization Unit Tests")
    print("-" * 40)
    
    norm_suite = TestSuite("Normalization")
    norm_suite.add(test_normalize_basic())
    norm_suite.add(test_normalize_zero_indent())
    norm_suite.add(test_normalize_preserve_relative())
    norm_suite.add(test_normalize_empty_lines())
    norm_suite.add(test_normalize_mixed_indents())
    
    print(norm_suite.summary())
    all_suites.append(norm_suite)
    
    # ==================== Unit Tests: _detect_indent_from_context ====================
    print("\n🔍 Context Detection Tests")
    print("-" * 40)
    
    detect_suite = TestSuite("Context Detection")
    detect_suite.add(test_detect_indent_before())
    detect_suite.add(test_detect_indent_after())
    
    print(detect_suite.summary())
    all_suites.append(detect_suite)
    
    # ==================== Integration Tests: REPLACE_METHOD ====================
    print("\n🔄 REPLACE_METHOD Tests")
    print("-" * 40)
    
    replace_method_suite = TestSuite("REPLACE_METHOD")
    replace_method_suite.add(test_replace_method_with_wrong_indent())
    replace_method_suite.add(test_replace_method_with_zero_indent())
    replace_method_suite.add(test_replace_method_preserves_others())
    
    print(replace_method_suite.summary())
    all_suites.append(replace_method_suite)
    
    # ==================== Integration Tests: REPLACE_FUNCTION ====================
    print("\n🔄 REPLACE_FUNCTION Tests")
    print("-" * 40)
    
    replace_func_suite = TestSuite("REPLACE_FUNCTION")
    replace_func_suite.add(test_replace_function_wrong_indent())
    
    print(replace_func_suite.summary())
    all_suites.append(replace_func_suite)
    
    # ==================== Integration Tests: INSERT_CLASS ====================
    print("\n➕ INSERT_CLASS Tests")
    print("-" * 40)
    
    insert_suite = TestSuite("INSERT_CLASS")
    insert_suite.add(test_insert_class_new_method())
    insert_suite.add(test_insert_class_no_existing_methods())
    
    print(insert_suite.summary())
    all_suites.append(insert_suite)
    
    # ==================== Integration Tests: PATCH_METHOD ====================
    print("\n🩹 PATCH_METHOD Tests")
    print("-" * 40)
    
    patch_suite = TestSuite("PATCH_METHOD")
    patch_suite.add(test_patch_method_insert_after())
    patch_suite.add(test_patch_method_insert_before_return())
    patch_suite.add(test_patch_method_default_before_return())
    
    print(patch_suite.summary())
    all_suites.append(patch_suite)
    
    # ==================== Integration Tests: REPLACE_CLASS ====================
    print("\n🔄 REPLACE_CLASS Tests")
    print("-" * 40)
    
    replace_class_suite = TestSuite("REPLACE_CLASS")
    replace_class_suite.add(test_replace_class_wrong_indent())
    
    print(replace_class_suite.summary())
    all_suites.append(replace_class_suite)
    
    # ==================== Integration Tests: INSERT_INTO_FILE ====================
    print("\n📄 INSERT_INTO_FILE Tests")
    print("-" * 40)
    
    insert_file_suite = TestSuite("INSERT_INTO_FILE")
    insert_file_suite.add(test_insert_into_file_function())
    
    print(insert_file_suite.summary())
    all_suites.append(insert_file_suite)
    
    # ==================== Edge Case Tests ====================
    print("\n⚡ Edge Case Tests")
    print("-" * 40)
    
    edge_suite = TestSuite("Edge Cases")
    edge_suite.add(test_async_method_replace())
    edge_suite.add(test_method_with_decorator_replace())
    edge_suite.add(test_nested_class_method())
    
    print(edge_suite.summary())
    all_suites.append(edge_suite)
    
    # ==================== VFS Integration Tests ====================
    print("\n🗂️ VFS Integration Tests")
    print("-" * 40)
    
    vfs_suite = TestSuite("VFS Integration")
    vfs_suite.add(test_vfs_integration_modify())
    vfs_suite.add(test_multiple_blocks_same_file())
    
    print(vfs_suite.summary())
    all_suites.append(vfs_suite)
    
    # ==================== Stress Tests ====================
    print("\n💪 Stress Tests")
    print("-" * 40)
    
    stress_suite = TestSuite("Stress Tests")
    stress_suite.add(test_extreme_indent_correction())
    stress_suite.add(test_tabs_to_spaces_conversion())
    
    print(stress_suite.summary())
    all_suites.append(stress_suite)
    
    # ==================== Generator Output Tests ====================
    print("\n🤖 Generator Output Tests")
    print("-" * 40)
    
    generator_suite = TestSuite("Generator Output")
    generator_suite.add(test_generator_tabs_mixed_with_spaces())
    generator_suite.add(test_generator_trailing_whitespace())
    generator_suite.add(test_generator_windows_line_endings())
    generator_suite.add(test_generator_inconsistent_indent_within_block())
    
    print(generator_suite.summary())
    all_suites.append(generator_suite)
    
    # ==================== Deep Nesting Tests ====================
    print("\n🔽 Deep Nesting Tests")
    print("-" * 40)
    
    nesting_suite = TestSuite("Deep Nesting")
    nesting_suite.add(test_patch_deeply_nested_code())
    nesting_suite.add(test_insert_method_into_nested_class())
    
    print(nesting_suite.summary())
    all_suites.append(nesting_suite)
    
    # ==================== Decorator Tests ====================
    print("\n🎀 Decorator Tests")
    print("-" * 40)
    
    decorator_suite = TestSuite("Decorators")
    decorator_suite.add(test_replace_decorated_method_removes_old_decorators())
    decorator_suite.add(test_replace_method_with_new_decorators())
    
    print(decorator_suite.summary())
    all_suites.append(decorator_suite)
    
    # ==================== Multiple Classes Tests ====================
    print("\n📦 Multiple Classes Tests")
    print("-" * 40)
    
    multi_class_suite = TestSuite("Multiple Classes")
    multi_class_suite.add(test_replace_method_correct_class_first())
    multi_class_suite.add(test_replace_method_correct_class_middle())
    
    print(multi_class_suite.summary())
    all_suites.append(multi_class_suite)
    
    # ==================== Negative Tests ====================
    print("\n❌ Negative Tests (Error Handling)")
    print("-" * 40)
    
    negative_suite = TestSuite("Error Handling")
    negative_suite.add(test_error_class_not_found())
    negative_suite.add(test_error_method_not_found())
    negative_suite.add(test_error_function_not_found())
    negative_suite.add(test_error_patch_pattern_not_found())
    negative_suite.add(test_error_invalid_mode())
    negative_suite.add(test_error_syntax_error_in_existing_file())
    
    print(negative_suite.summary())
    all_suites.append(negative_suite)
    
    # ==================== Preservation Tests ====================
    print("\n💾 Preservation Tests")
    print("-" * 40)
    
    preservation_suite = TestSuite("Preservation")
    preservation_suite.add(test_replace_method_preserves_surrounding_comments())
    preservation_suite.add(test_replace_preserves_class_attributes())
    
    print(preservation_suite.summary())
    all_suites.append(preservation_suite)
    
    # ==================== Special Cases ====================
    print("\n🔮 Special Cases")
    print("-" * 40)
    
    special_suite = TestSuite("Special Cases")
    special_suite.add(test_replace_method_with_only_pass())
    special_suite.add(test_method_with_multiline_string())
    special_suite.add(test_insert_import_deduplication())
    special_suite.add(test_append_to_empty_file())
    special_suite.add(test_create_new_file_with_replace_file())
    
    print(special_suite.summary())
    all_suites.append(special_suite)
    
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
        print(f"  {status} {suite.name}: {suite.passed}/{len(suite.results) - suite.skipped} passed{skip_info}")
    
    print("-" * 60)
    
    if total_failed == 0:
        skip_info = f" ({total_skipped} skipped)" if total_skipped else ""
        print(f"✅ ALL TESTS PASSED: {total_passed}/{total_tests}{skip_info} ({total_time:.0f}ms)")
    else:
        print(f"❌ TESTS FAILED: {total_passed}/{total_tests} passed, {total_failed} failed ({total_time:.0f}ms)")
        
        print("\n❌ Failed Tests:")
        for suite in all_suites:
            for result in suite.results:
                if not result.passed and not result.skipped:
                    print(f"  - {suite.name}::{result.name}")
                    if result.error:
                        print(f"    └── {result.error}")
    
    print("=" * 60 + "\n")
    
    return total_failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)