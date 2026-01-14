#!/usr/bin/env python3
"""
Тестовый скрипт для проверки компонентов Этапа 3 Agent Mode.

Проверяет:
1. Промпты для Agent Mode (Orchestrator, Code Generator, AI Validator)
2. Парсинг CODE_BLOCK формата
3. Применение CODE_BLOCK через FileModifier
4. Интеграция с VirtualFileSystem
5. AI Validator (если API доступен)
6. Полный цикл: Instruction → Generate → Parse → Apply

Запуск:
    python scripts/test_agent_stage3.py
    
Опции:
    --skip-api      Пропустить тесты с реальными API вызовами
    --verbose       Подробный вывод
    --test-dir DIR  Директория для тестовых файлов (по умолчанию: temp)
"""

import sys
import os
import asyncio
import logging
import tempfile
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

# Добавляем корень проекта в путь
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(verbose: bool = False) -> logging.Logger:
    """Настройка логирования с цветным выводом"""
    
    class ColorFormatter(logging.Formatter):
        """Форматтер с цветами для консоли"""
        COLORS = {
            'DEBUG': '\033[36m',     # Cyan
            'INFO': '\033[32m',      # Green
            'WARNING': '\033[33m',   # Yellow
            'ERROR': '\033[31m',     # Red
            'CRITICAL': '\033[35m',  # Magenta
            'RESET': '\033[0m',
        }
        
        def format(self, record):
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            reset = self.COLORS['RESET']
            record.levelname = f"{color}{record.levelname}{reset}"
            return super().format(record)
    
    logger = logging.getLogger("test_stage3")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(handler)
    
    return logger


# ============================================================================
# TEST RESULT TRACKING
# ============================================================================

@dataclass
class TestResult:
    """Результат одного теста"""
    name: str
    passed: bool
    duration_ms: float
    message: str = ""
    details: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TestRunner:
    """Запускает тесты и собирает результаты"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.results: List[TestResult] = []
        self.start_time = datetime.now()
    
    def run_test(self, name: str, test_func, *args, **kwargs) -> TestResult:
        """Запускает один тест и логирует результат"""
        self.logger.info(f"🧪 Запуск теста: {name}")
        
        start = datetime.now()
        try:
            result = test_func(*args, **kwargs)
            duration = (datetime.now() - start).total_seconds() * 1000
            
            if isinstance(result, tuple):
                passed, message, details = result[0], result[1], result[2] if len(result) > 2 else None
            elif isinstance(result, bool):
                passed, message, details = result, "OK" if result else "FAILED", None
            else:
                passed, message, details = True, str(result), None
            
            test_result = TestResult(
                name=name,
                passed=passed,
                duration_ms=duration,
                message=message,
                details=details,
            )
            
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            test_result = TestResult(
                name=name,
                passed=False,
                duration_ms=duration,
                message=f"Exception: {type(e).__name__}",
                error=str(e),
            )
            self.logger.error(f"   ❌ Исключение: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
        
        self.results.append(test_result)
        
        if test_result.passed:
            self.logger.info(f"   ✅ PASSED ({duration:.1f}ms): {test_result.message}")
        else:
            self.logger.error(f"   ❌ FAILED ({duration:.1f}ms): {test_result.message}")
            if test_result.error:
                self.logger.error(f"      Error: {test_result.error}")
        
        return test_result
    
    async def run_test_async(self, name: str, test_func, *args, **kwargs) -> TestResult:
        """Асинхронная версия run_test"""
        self.logger.info(f"🧪 Запуск теста: {name}")
        
        start = datetime.now()
        try:
            result = await test_func(*args, **kwargs)
            duration = (datetime.now() - start).total_seconds() * 1000
            
            if isinstance(result, tuple):
                passed, message, details = result[0], result[1], result[2] if len(result) > 2 else None
            elif isinstance(result, bool):
                passed, message, details = result, "OK" if result else "FAILED", None
            else:
                passed, message, details = True, str(result), None
            
            test_result = TestResult(
                name=name,
                passed=passed,
                duration_ms=duration,
                message=message,
                details=details,
            )
            
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            test_result = TestResult(
                name=name,
                passed=False,
                duration_ms=duration,
                message=f"Exception: {type(e).__name__}",
                error=str(e),
            )
            self.logger.error(f"   ❌ Исключение: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
        
        self.results.append(test_result)
        
        if test_result.passed:
            self.logger.info(f"   ✅ PASSED ({duration:.1f}ms): {test_result.message}")
        else:
            self.logger.error(f"   ❌ FAILED ({duration:.1f}ms): {test_result.message}")
        
        return test_result
    
    def print_summary(self):
        """Выводит итоговую сводку"""
        total_duration = (datetime.now() - self.start_time).total_seconds()
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        
        print("\n" + "=" * 70)
        print("📊 ИТОГОВАЯ СВОДКА")
        print("=" * 70)
        
        for r in self.results:
            status = "✅" if r.passed else "❌"
            print(f"  {status} {r.name}: {r.message} ({r.duration_ms:.1f}ms)")
        
        print("-" * 70)
        print(f"  Всего тестов: {len(self.results)}")
        print(f"  ✅ Успешно: {passed}")
        print(f"  ❌ Провалено: {failed}")
        print(f"  ⏱️  Время: {total_duration:.2f}s")
        print("=" * 70)
        
        return failed == 0


# ============================================================================
# TEST DATA
# ============================================================================

# Тестовый файл Python для модификации
SAMPLE_AUTH_SERVICE = '''"""Authentication service module."""
import hashlib
from typing import Optional


class AuthService:
    """Service for handling authentication."""
    
    def __init__(self, secret_key: str):
        """Initialize with secret key."""
        self.secret_key = secret_key
        self.users = {}
    
    def hash_password(self, password: str) -> str:
        """Hash a password."""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def register_user(self, username: str, password: str) -> bool:
        """Register a new user."""
        if username in self.users:
            return False
        self.users[username] = self.hash_password(password)
        return True
    
    def login(self, username: str, password: str) -> bool:
        """Authenticate a user."""
        if username not in self.users:
            return False
        return self.users[username] == self.hash_password(password)
'''

# Симуляция ответа Code Generator с CODE_BLOCK
MOCK_GENERATOR_RESPONSE_SINGLE = '''
### CODE_BLOCK
FILE: app/services/auth.py
MODE: REPLACE_METHOD
TARGET_CLASS: AuthService
TARGET_METHOD: login

```python
def login(self, username: str, password: str) -> Optional[dict]:
    """
    Authenticate a user and return session info.
    
    Args:
        username: User's username
        password: User's password
        
    Returns:
        Session dict if successful, None otherwise
    """
    if username not in self.users:
        return None
    if self.users[username] != self.hash_password(password):
        return None
    return {
        "username": username,
        "authenticated": True,
        "timestamp": datetime.now().isoformat()
    }
```
### END_CODE_BLOCK
'''

MOCK_GENERATOR_RESPONSE_MULTIPLE = '''
### CODE_BLOCK
FILE: app/services/auth.py
MODE: INSERT_IMPORT

```python
from datetime import datetime
```
### END_CODE_BLOCK

### CODE_BLOCK
FILE: app/services/auth.py
MODE: REPLACE_METHOD
TARGET_CLASS: AuthService
TARGET_METHOD: login

```python
def login(self, username: str, password: str) -> Optional[dict]:
    """Authenticate user and return session."""
    if username not in self.users:
        return None
    if self.users[username] != self.hash_password(password):
        return None
    return {"username": username, "authenticated": True}
```
### END_CODE_BLOCK

### CODE_BLOCK
FILE: app/services/auth.py
MODE: INSERT_CLASS
TARGET_CLASS: AuthService
INSERT_AFTER: login

```python
def logout(self, username: str) -> bool:
    """Log out a user."""
    # In real implementation, invalidate session
    return username in self.users
```
### END_CODE_BLOCK
'''

# Симуляция инструкции от Orchestrator
MOCK_ORCHESTRATOR_INSTRUCTION = '''
## Instruction for Code Generator

**SCOPE:** B

**Task:** Modify login method to return session dict instead of bool, add logout method

---

### FILE: `app/services/auth.py`
**Operation:** MODIFY

**File-level imports to ADD:**
```python
from datetime import datetime
```

**Changes:**

#### ACTION: MODIFY_METHOD
**Target:** `AuthService.login`
**Marker:** `def login(self, username:`

**Current signature:** `def login(self, username: str, password: str) -> bool`
**New signature:** `def login(self, username: str, password: str) -> Optional[dict]`

**Logic:**
1. Check if username exists in self.users
2. Verify password hash matches
3. Return dict with username, authenticated=True, timestamp
4. Return None on failure

#### ACTION: ADD_METHOD
**Target:** `AuthService.logout`
**Location:** Insert after: login

**New signature:** `def logout(self, username: str) -> bool`

**Logic:**
1. Check if username exists
2. Return True if user was logged in
'''


# ============================================================================
# INDIVIDUAL TESTS
# ============================================================================

def test_imports():
    """Проверка, что все необходимые модули импортируются"""
    errors = []
    
    # Core modules
    try:
        from app.services.file_modifier import FileModifier, ModifyMode, ModifyInstruction, ModifyResult
    except ImportError as e:
        errors.append(f"file_modifier base: {e}")
    
    try:
        from app.services.file_modifier import ParsedCodeBlock
    except ImportError as e:
        errors.append(f"ParsedCodeBlock: {e}")
    
    try:
        from app.services.virtual_fs import VirtualFileSystem, ChangeType
    except ImportError as e:
        errors.append(f"virtual_fs: {e}")
    
    try:
        from app.agents.code_generator import parse_agent_code_blocks, generate_code
    except ImportError as e:
        errors.append(f"code_generator: {e}")
    
    # Prompt templates
    try:
        from app.llm.prompt_templates import (
            format_code_generator_prompt_agent,
            CODE_GENERATOR_SYSTEM_PROMPT_AGENT,
        )
    except ImportError as e:
        errors.append(f"prompt_templates agent: {e}")
    
    try:
        from app.llm.prompt_templates import (
            format_ai_validator_prompt,
            AI_VALIDATOR_SYSTEM_PROMPT,
        )
    except ImportError as e:
        errors.append(f"prompt_templates validator: {e}")
    
    # Validator agent
    try:
        from app.agents.validator import AIValidator, ValidationResult
    except ImportError as e:
        errors.append(f"validator: {e}")
    
    if errors:
        return False, f"Import errors: {', '.join(errors)}", {"errors": errors}
    
    return True, f"All {7} import groups successful", None


def test_mode_mapping():
    """Проверка MODE_MAPPING в FileModifier"""
    from app.services.file_modifier import FileModifier, ModifyMode
    
    modifier = FileModifier()
    
    # Проверяем, что MODE_MAPPING существует
    if not hasattr(modifier, 'MODE_MAPPING'):
        return False, "MODE_MAPPING attribute missing", None
    
    mapping = modifier.MODE_MAPPING
    
    # Обязательные режимы
    required_modes = [
        "REPLACE_FILE",
        "REPLACE_METHOD", 
        "REPLACE_FUNCTION",
        "REPLACE_CLASS",
        "INSERT_CLASS",
        "INSERT_FILE",
        "APPEND_FILE",
        "INSERT_IMPORT",
    ]
    
    missing = [m for m in required_modes if m not in mapping]
    if missing:
        return False, f"Missing modes: {missing}", {"missing": missing}
    
    # Проверяем, что все значения - это ModifyMode
    invalid = []
    for key, value in mapping.items():
        if not isinstance(value, ModifyMode):
            invalid.append(f"{key} -> {type(value)}")
    
    if invalid:
        return False, f"Invalid mappings: {invalid}", {"invalid": invalid}
    
    return True, f"MODE_MAPPING has {len(mapping)} valid entries", {"modes": list(mapping.keys())}


def test_parse_code_blocks_single():
    """Тест парсинга одного CODE_BLOCK"""
    from app.agents.code_generator import parse_agent_code_blocks
    
    blocks = parse_agent_code_blocks(MOCK_GENERATOR_RESPONSE_SINGLE)
    
    if len(blocks) != 1:
        return False, f"Expected 1 block, got {len(blocks)}", None
    
    block = blocks[0]
    
    checks = []
    checks.append(("file_path", block.file_path == "app/services/auth.py"))
    checks.append(("mode", block.mode == "REPLACE_METHOD"))
    checks.append(("target_class", block.target_class == "AuthService"))
    checks.append(("target_method", block.target_method == "login"))
    checks.append(("has_code", len(block.code) > 50))
    checks.append(("code_has_def", "def login" in block.code))
    
    failed = [name for name, passed in checks if not passed]
    
    if failed:
        return False, f"Failed checks: {failed}", {"block": block.to_dict()}
    
    return True, "Single CODE_BLOCK parsed correctly", {"code_length": len(block.code)}


def test_parse_code_blocks_multiple():
    """Тест парсинга нескольких CODE_BLOCK"""
    from app.agents.code_generator import parse_agent_code_blocks
    
    blocks = parse_agent_code_blocks(MOCK_GENERATOR_RESPONSE_MULTIPLE)
    
    if len(blocks) != 3:
        return False, f"Expected 3 blocks, got {len(blocks)}", {"count": len(blocks)}
    
    # Проверяем режимы
    modes = [b.mode for b in blocks]
    expected_modes = ["INSERT_IMPORT", "REPLACE_METHOD", "INSERT_CLASS"]
    
    if modes != expected_modes:
        return False, f"Modes mismatch: {modes} vs {expected_modes}", None
    
    # Проверяем INSERT_AFTER для третьего блока
    if blocks[2].insert_after != "login":
        return False, f"INSERT_AFTER wrong: {blocks[2].insert_after}", None
    
    return True, "3 CODE_BLOCKs parsed correctly", {"modes": modes}


def test_apply_code_block_replace_method(test_dir: Path):
    """Тест применения CODE_BLOCK с REPLACE_METHOD"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    modifier = FileModifier()
    
    block = ParsedCodeBlock(
        file_path="app/services/auth.py",
        mode="REPLACE_METHOD",
        code='''def login(self, username: str, password: str) -> Optional[dict]:
    """Authenticate and return session."""
    if username not in self.users:
        return None
    return {"username": username, "authenticated": True}''',
        target_class="AuthService",
        target_method="login",
    )
    
    result = modifier.apply_code_block(SAMPLE_AUTH_SERVICE, block)
    
    if not result.success:
        return False, f"Apply failed: {result.message}", {"warnings": result.warnings}
    
    # Проверяем, что метод заменён
    if "def login(self, username: str, password: str) -> bool:" in result.new_content:
        return False, "Old method signature still present", None
    
    if "def login(self, username: str, password: str) -> Optional[dict]:" not in result.new_content:
        return False, "New method signature not found", None
    
    if '{"username": username, "authenticated": True}' not in result.new_content:
        return False, "New return statement not found", None
    
    # Проверяем, что другие методы сохранились
    if "def hash_password" not in result.new_content:
        return False, "hash_password method was removed", None
    
    if "def register_user" not in result.new_content:
        return False, "register_user method was removed", None
    
    return True, f"Method replaced (+{result.lines_added}/-{result.lines_removed})", {
        "lines_added": result.lines_added,
        "lines_removed": result.lines_removed,
    }


def test_apply_code_block_insert_class(test_dir: Path):
    """Тест применения CODE_BLOCK с INSERT_CLASS (добавление метода)"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    modifier = FileModifier()
    
    block = ParsedCodeBlock(
        file_path="app/services/auth.py",
        mode="INSERT_CLASS",
        code='''def logout(self, username: str) -> bool:
    """Log out a user."""
    return username in self.users''',
        target_class="AuthService",
        insert_after="login",
    )
    
    result = modifier.apply_code_block(SAMPLE_AUTH_SERVICE, block)
    
    if not result.success:
        return False, f"Apply failed: {result.message}", {"warnings": result.warnings}
    
    # Проверяем, что метод добавлен
    if "def logout(self, username: str) -> bool:" not in result.new_content:
        return False, "logout method not found", None
    
    # Проверяем, что logout после login
    login_pos = result.new_content.find("def login")
    logout_pos = result.new_content.find("def logout")
    
    if logout_pos < login_pos:
        return False, "logout should be after login", None
    
    return True, f"Method inserted (+{result.lines_added} lines)", {
        "lines_added": result.lines_added,
    }


def test_apply_code_block_insert_import(test_dir: Path):
    """Тест применения CODE_BLOCK с INSERT_IMPORT"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    modifier = FileModifier()
    
    block = ParsedCodeBlock(
        file_path="app/services/auth.py",
        mode="INSERT_IMPORT",
        code="from datetime import datetime",
    )
    
    result = modifier.apply_code_block(SAMPLE_AUTH_SERVICE, block)
    
    if not result.success:
        return False, f"Apply failed: {result.message}", {"warnings": result.warnings}
    
    # Проверяем, что импорт добавлен
    if "from datetime import datetime" not in result.new_content:
        return False, "Import not found in result", None
    
    # Проверяем, что импорт в начале файла (до class)
    import_pos = result.new_content.find("from datetime import datetime")
    class_pos = result.new_content.find("class AuthService")
    
    if import_pos > class_pos:
        return False, "Import should be before class definition", None
    
    return True, "Import added successfully", None


def test_apply_code_block_replace_file(test_dir: Path):
    """Тест применения CODE_BLOCK с REPLACE_FILE (создание нового файла)"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    
    modifier = FileModifier()
    
    new_file_code = '''"""Validators module."""
from typing import Optional
import re


def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$'
    return bool(re.match(pattern, email))


def validate_password(password: str) -> tuple[bool, str]:
    """Validate password strength."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    return True, "OK"
'''
    
    block = ParsedCodeBlock(
        file_path="app/utils/validators.py",
        mode="REPLACE_FILE",
        code=new_file_code,
    )
    
    # Для нового файла existing_content пустой
    result = modifier.apply_code_block("", block)
    
    if not result.success:
        return False, f"Apply failed: {result.message}", None
    
    if "def validate_email" not in result.new_content:
        return False, "validate_email not in result", None
    
    if "def validate_password" not in result.new_content:
        return False, "validate_password not in result", None
    
    return True, f"New file created ({result.lines_added} lines)", {
        "lines_added": result.lines_added,
    }


def test_vfs_integration(test_dir: Path):
    """Тест интеграции FileModifier с VirtualFileSystem"""
    from app.services.file_modifier import FileModifier, ParsedCodeBlock
    from app.services.virtual_fs import VirtualFileSystem
    
    # Создаём тестовую структуру
    app_dir = test_dir / "app" / "services"
    app_dir.mkdir(parents=True, exist_ok=True)
    
    auth_file = app_dir / "auth.py"
    auth_file.write_text(SAMPLE_AUTH_SERVICE)
    
    # Инициализируем VFS и Modifier
    vfs = VirtualFileSystem(str(test_dir))
    modifier = FileModifier()
    
    # Создаём CODE_BLOCK
    block = ParsedCodeBlock(
        file_path="app/services/auth.py",
        mode="REPLACE_METHOD",
        code='''def login(self, username: str, password: str) -> dict:
    """Login with session."""
    return {"user": username}''',
        target_class="AuthService",
        target_method="login",
    )
    
    # Применяем через apply_code_block_to_vfs
    result = modifier.apply_code_block_to_vfs(vfs, block)
    
    if not result.success:
        return False, f"VFS apply failed: {result.message}", None
    
    # Проверяем, что изменения в staging
    if not vfs.has_pending_changes():
        return False, "No pending changes in VFS", None
    
    staged = vfs.get_staged_files()
    if "app/services/auth.py" not in staged:
        return False, f"File not staged: {staged}", None
    
    # Проверяем, что read_file возвращает изменённое содержимое
    content = vfs.read_file("app/services/auth.py")
    if '{"user": username}' not in content:
        return False, "VFS read doesn't return staged content", None
    
    # Проверяем, что реальный файл НЕ изменился
    real_content = auth_file.read_text()
    if '{"user": username}' in real_content:
        return False, "Real file was modified (should not be)", None
    
    return True, "VFS integration works correctly", {
        "staged_files": staged,
    }


def test_prompt_templates_agent_mode():
    """Тест промптов для Agent Mode"""
    from app.llm.prompt_templates import (
        format_code_generator_prompt_agent,
        CODE_GENERATOR_SYSTEM_PROMPT_AGENT,
        CODE_GENERATOR_USER_PROMPT_AGENT,
    )
    
    errors = []
    
    # Проверяем system prompt
    if not CODE_GENERATOR_SYSTEM_PROMPT_AGENT:
        errors.append("CODE_GENERATOR_SYSTEM_PROMPT_AGENT is empty")
    
    if "CODE_BLOCK" not in CODE_GENERATOR_SYSTEM_PROMPT_AGENT:
        errors.append("System prompt doesn't mention CODE_BLOCK format")
    
    if "MODE" not in CODE_GENERATOR_SYSTEM_PROMPT_AGENT:
        errors.append("System prompt doesn't mention MODE")
    
    # Проверяем форматирование
    prompts = format_code_generator_prompt_agent(
        orchestrator_instruction="Test instruction",
        file_code="def test(): pass",
    )
    
    if "system" not in prompts or "user" not in prompts:
        errors.append("format_code_generator_prompt_agent doesn't return system/user")
    
    if "Test instruction" not in prompts.get("user", ""):
        errors.append("Instruction not included in user prompt")
    
    if errors:
        return False, f"Prompt errors: {errors}", None
    
    return True, "Agent Mode prompts configured correctly", {
        "system_length": len(CODE_GENERATOR_SYSTEM_PROMPT_AGENT),
    }


def test_prompt_templates_validator():
    """Тест промптов для AI Validator"""
    from app.llm.prompt_templates import (
        format_ai_validator_prompt,
        AI_VALIDATOR_SYSTEM_PROMPT,
    )
    
    errors = []
    
    if not AI_VALIDATOR_SYSTEM_PROMPT:
        errors.append("AI_VALIDATOR_SYSTEM_PROMPT is empty")
    
    # Проверяем ключевые элементы
    keywords = ["approved", "confidence", "JSON", "critical_issues"]
    for kw in keywords:
        if kw.lower() not in AI_VALIDATOR_SYSTEM_PROMPT.lower():
            errors.append(f"Missing keyword: {kw}")
    
    # Проверяем форматирование
    prompts = format_ai_validator_prompt(
        user_request="Add login method",
        orchestrator_instruction="Modify AuthService",
        original_content="class AuthService: pass",
        proposed_code="def login(): return True",
        file_path="auth.py",
    )
    
    if "system" not in prompts or "user" not in prompts:
        errors.append("format_ai_validator_prompt doesn't return system/user")
    
    if errors:
        return False, f"Validator prompt errors: {errors}", None
    
    return True, "Validator prompts configured correctly", {
        "system_length": len(AI_VALIDATOR_SYSTEM_PROMPT),
    }


def test_full_parsing_and_apply_cycle(test_dir: Path):
    """Полный цикл: парсинг ответа → применение → проверка"""
    from app.agents.code_generator import parse_agent_code_blocks
    from app.services.file_modifier import FileModifier
    from app.services.virtual_fs import VirtualFileSystem
    
    # Создаём тестовую структуру
    app_dir = test_dir / "app" / "services"
    app_dir.mkdir(parents=True, exist_ok=True)
    
    auth_file = app_dir / "auth.py"
    auth_file.write_text(SAMPLE_AUTH_SERVICE)
    
    # Инициализируем компоненты
    vfs = VirtualFileSystem(str(test_dir))
    modifier = FileModifier()
    
    # Парсим ответ с несколькими блоками
    blocks = parse_agent_code_blocks(MOCK_GENERATOR_RESPONSE_MULTIPLE)
    
    if len(blocks) != 3:
        return False, f"Parsing failed: expected 3, got {len(blocks)}", None
    
    # Применяем все блоки
    results = []
    for block in blocks:
        result = modifier.apply_code_block_to_vfs(vfs, block)
        results.append((block.mode, result.success, result.message))
        
        if not result.success:
            return False, f"Apply {block.mode} failed: {result.message}", None
    
    # Проверяем финальное состояние
    content = vfs.read_file("app/services/auth.py")
    
    checks = []
    checks.append(("has_datetime_import", "from datetime import datetime" in content))
    checks.append(("login_returns_dict", "Optional[dict]" in content or '{"username"' in content))
    checks.append(("has_logout", "def logout" in content))
    checks.append(("preserved_hash_password", "def hash_password" in content))
    checks.append(("preserved_register", "def register_user" in content))
    
    failed = [name for name, passed in checks if not passed]
    
    if failed:
        return False, f"Final content checks failed: {failed}", {
            "content_preview": content[:500],
        }
    
    # Проверяем статус VFS
    status = vfs.get_status()
    
    return True, f"Full cycle completed: 3 blocks applied", {
        "applied_modes": [r[0] for r in results],
        "staged_files": status["staged_files"],
    }


async def test_ai_validator_basic():
    """Базовый тест AI Validator (без реального API вызова)"""
    from app.agents.validator import AIValidator, ValidationResult
    
    validator = AIValidator()
    
    if not hasattr(validator, 'validate'):
        return False, "AIValidator missing validate method", None
    
    if hasattr(validator, '_parse_response'):
        mock_json = '{"approved": true, "confidence": 0.9, "verdict": "OK", "critical_issues": [], "core_request": "test"}'
        
        # Передаём все 3 обязательных аргумента!
        result = validator._parse_response(
            response=mock_json,
            model="test-model",
            duration_ms=100.0
        )
        
        if not result.approved:
            return False, "Failed to parse approved=true", None
        
        if abs(result.confidence - 0.9) > 0.01:
            return False, f"Confidence mismatch: {result.confidence}", None
    
    return True, "AIValidator basic structure OK", None



async def test_ai_validator_with_api(skip_api: bool):
    """Тест AI Validator с реальным API вызовом"""
    if skip_api:
        return True, "Skipped (--skip-api)", None
    
    from app.agents.validator import AIValidator
    
    validator = AIValidator()
    
    try:
        result = await validator.validate(
            user_request="Add a method to validate email format",
            orchestrator_instruction="Add validate_email method to UserService",
            original_content="class UserService:\n    pass",
            proposed_code='''def validate_email(self, email: str) -> bool:
    """Validate email format."""
    import re
    pattern = r'^[\\w.-]+@[\\w.-]+\\.\\w+$'
    return bool(re.match(pattern, email))''',
            file_path="user_service.py",
        )
        
        # Проверяем структуру результата
        if not hasattr(result, 'approved'):
            return False, "Result missing 'approved' field", None
        
        if not hasattr(result, 'confidence'):
            return False, "Result missing 'confidence' field", None
        
        return True, f"Validator returned: approved={result.approved}, confidence={result.confidence}", {
            "approved": result.approved,
            "confidence": result.confidence,
            "verdict": result.verdict if hasattr(result, 'verdict') else "N/A",
        }
        
    except Exception as e:
        return False, f"API call failed: {e}", None


# ============================================================================
# MAIN
# ============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Test Agent Mode Stage 3")
    parser.add_argument("--skip-api", action="store_true", help="Skip tests requiring API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--test-dir", type=str, help="Directory for test files")
    args = parser.parse_args()
    
    logger = setup_logging(args.verbose)
    runner = TestRunner(logger)
    
    print("\n" + "=" * 70)
    print("🧪 ТЕСТИРОВАНИЕ ЭТАПА 3: Agent Mode Code Generation")
    print("=" * 70 + "\n")
    
    # Создаём временную директорию для тестов
    if args.test_dir:
        test_dir = Path(args.test_dir)
        test_dir.mkdir(parents=True, exist_ok=True)
        cleanup_dir = False
    else:
        test_dir = Path(tempfile.mkdtemp(prefix="agent_test_"))
        cleanup_dir = True
    
    logger.info(f"📁 Test directory: {test_dir}")
    
    try:
        # ===== ГРУППА 1: Импорты =====
        print("\n📦 ГРУППА 1: Проверка импортов\n")
        runner.run_test("Import all modules", test_imports)
        
        # ===== ГРУППА 2: MODE_MAPPING =====
        print("\n🔧 ГРУППА 2: MODE_MAPPING\n")
        runner.run_test("MODE_MAPPING configuration", test_mode_mapping)
        
        # ===== ГРУППА 3: Парсинг CODE_BLOCK =====
        print("\n📝 ГРУППА 3: Парсинг CODE_BLOCK\n")
        runner.run_test("Parse single CODE_BLOCK", test_parse_code_blocks_single)
        runner.run_test("Parse multiple CODE_BLOCKs", test_parse_code_blocks_multiple)
        
        # ===== ГРУППА 4: Применение CODE_BLOCK =====
        print("\n⚙️ ГРУППА 4: Применение CODE_BLOCK\n")
        runner.run_test("Apply REPLACE_METHOD", test_apply_code_block_replace_method, test_dir)
        runner.run_test("Apply INSERT_CLASS", test_apply_code_block_insert_class, test_dir)
        runner.run_test("Apply INSERT_IMPORT", test_apply_code_block_insert_import, test_dir)
        runner.run_test("Apply REPLACE_FILE", test_apply_code_block_replace_file, test_dir)
        
        # ===== ГРУППА 5: Интеграция с VFS =====
        print("\n🗂️ ГРУППА 5: Интеграция с VFS\n")
        # Создаём свежую директорию для этого теста
        vfs_test_dir = test_dir / "vfs_test"
        if vfs_test_dir.exists():
            shutil.rmtree(vfs_test_dir)
        vfs_test_dir.mkdir()
        runner.run_test("VFS integration", test_vfs_integration, vfs_test_dir)
        
        # ===== ГРУППА 6: Промпты =====
        print("\n📋 ГРУППА 6: Промпты Agent Mode\n")
        runner.run_test("Code Generator Agent prompts", test_prompt_templates_agent_mode)
        runner.run_test("AI Validator prompts", test_prompt_templates_validator)
        
        # ===== ГРУППА 7: Полный цикл =====
        print("\n🔄 ГРУППА 7: Полный цикл\n")
        full_cycle_dir = test_dir / "full_cycle"
        if full_cycle_dir.exists():
            shutil.rmtree(full_cycle_dir)
        full_cycle_dir.mkdir()
        runner.run_test("Full parsing and apply cycle", test_full_parsing_and_apply_cycle, full_cycle_dir)
        
        # ===== ГРУППА 8: AI Validator =====
        print("\n🤖 ГРУППА 8: AI Validator\n")
        await runner.run_test_async("AI Validator basic", test_ai_validator_basic)
        await runner.run_test_async("AI Validator with API", test_ai_validator_with_api, args.skip_api)
        
    finally:
        # Cleanup
        if cleanup_dir and test_dir.exists():
            shutil.rmtree(test_dir)
            logger.debug(f"Cleaned up test directory: {test_dir}")
    
    # Итоговая сводка
    success = runner.print_summary()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
