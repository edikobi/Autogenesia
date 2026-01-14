# 🤖 AI Code Agent - Интеграционный Тест

**Дата выполнения:** 15.12.2025 00:59:51
**Проект:** `C:\Users\Admin\AI_Assistant_Pro`
**Время выполнения:** 153.32 сек.

---

## 📝 Запрос пользователя

> Проверь файл test_history_manager.py на предмет правильности импортов, если есть ошибка, перепиши весь файл без изменений, только с исправленными импортами.

---

## 🎯 Использованные модели

- **Orchestrator:** Claude Sonnet 4.5 (RouterAI)
- **Code Generator:** deepseek-chat

---

## 🔍 Анализ Оркестратора

[Анализ не выполнен]

---

## 📋 Инструкции для Code Generator

**Task:** Исправить синтаксис типизации для обратной совместимости с Python 3.8

**File:** scripts/test_history_manager.py

**Location:** Строка 147, сигнатура функции test_create_history

**Changes:**
1. В строке 147 заменить `tuple[str, HistoryManager]` на `Tuple[str, HistoryManager]` (использовать импортированный Tuple из typing вместо встроенного tuple для аннотации типов)

**Why:** Синтаксис `tuple[...]` для type hints доступен только с Python 3.9+. Использование `Tuple` из модуля `typing` обеспечивает совместимость с Python 3.8 и более ранними версиями.

---

## 💻 Сгенерированный код

**Файл:** `scripts/test_history_manager.py`

```python
import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple
import traceback

# Project-specific imports
from app.history.manager import HistoryManager
from app.history.storage import HistoryStorage, Thread, Message
from app.history.compressor import compress_history_if_needed, prune_irrelevant_context
from app.utils.token_counter import TokenCounter
from config.settings import Config


def setup_logging() -> None:
    """Configure colored logging with timestamps."""
    # Custom log level for success
    SUCCESS_LEVEL_NUM = 25
    logging.addLevelName(SUCCESS_LEVEL_NUM, "SUCCESS")

    def success(self, message, *args, **kwargs):
        if self.isEnabledFor(SUCCESS_LEVEL_NUM):
            self._log(SUCCESS_LEVEL_NUM, message, args, **kwargs)

    logging.Logger.success = success

    # Color codes
    COLORS = {
        'INFO': '\033[96m',      # Cyan
        'WARNING': '\033[93m',   # Yellow
        'ERROR': '\033[91m',     # Red
        'SUCCESS': '\033[92m',   # Green
        'RESET': '\033[0m',      # Reset
    }

    class ColoredFormatter(logging.Formatter):
        def format(self, record):
            levelname = record.levelname
            if levelname in COLORS:
                levelname_color = f"{COLORS[levelname]}{levelname}{COLORS['RESET']}"
                record.levelname = levelname_color
            return super().format(record)

    # Configure handler
    handler = logging.StreamHandler()
    formatter = ColoredFormatter(
        '[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logging.info("Logging configured successfully")


def test_imports() -> bool:
    """Test all required imports."""
    logger = logging.getLogger(__name__)
    logger.info("Testing imports...")

    imports_to_test = [
        ("app.history.manager", "HistoryManager"),
        ("app.history.storage", "HistoryStorage, Thread, Message"),
        ("app.history.compressor", "compress_history_if_needed, prune_irrelevant_context"),
        ("app.utils.token_counter", "TokenCounter"),
        ("config.settings", "Config"),
    ]

    all_successful = True

    for module_path, import_names in imports_to_test:
        try:
            # This is a simplified check - actual import would be done at module level
            logger.info(f"✓ Import successful: {module_path} -> {import_names}")
        except ImportError as e:
            logger.error(f"✗ Import failed for {module_path}: {e}")
            all_successful = False
        except Exception as e:
            logger.error(f"✗ Unexpected error importing {module_path}: {e}")
            all_successful = False

    if all_successful:
        logger.success("All imports successful")
    else:
        logger.error("Some imports failed")

    return all_successful


async def test_api_connection() -> bool:
    """Test API connection for history compression."""
    logger = logging.getLogger(__name__)
    logger.info("Testing API connection for history compression...")

    try:
        # Import here to avoid circular dependencies
        from app.llm.api_client import call_llm

        # Get model from config
        model = Config().get("compressor_model", "gpt-3.5-turbo")

        # Prepare test message
        messages = [{"role": "user", "content": "test"}]

        logger.debug(f"Calling LLM API with model: {model}")

        # Call API
        response = await call_llm(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=50
        )

        # Extract response content
        if hasattr(response, 'content'):
            content = response.content
        elif isinstance(response, dict) and 'content' in response:
            content = response['content']
        else:
            content = str(response)

        preview = content[:50] + "..." if len(content) > 50 else content
        logger.success(f"API connection successful. Response preview: {preview}")
        return True

    except ImportError as e:
        logger.error(f"Failed to import api_client: {e}")
        return False
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        return False
    except TimeoutError as e:
        logger.error(f"Timeout error: {e}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error during API test: {e}")
        return False


def test_create_history() -> Tuple[str, HistoryManager]:
    """Create a new conversation thread with test messages."""
    logger = logging.getLogger(__name__)
    logger.info("Creating new conversation thread...")

    try:
        # Initialize HistoryManager with test database
        db_path = Path("tests/test_history.db")
        manager = HistoryManager(db_path=str(db_path))

        # Create new thread
        thread = manager.create_thread(
            user_id="test_user",
            project_path="test_project",
            project_name="Test Project"
        )

        thread_id = thread.id
        logger.info(f"Thread created with ID: {thread_id}")

        # Assert thread_id is not None
        assert thread_id is not None, "Thread ID should not be None"
        logger.debug(f"Assertion passed: thread_id = {thread_id}")

        # Add test messages
        test_messages = [
            {"role": "user", "content": "Hello, can you help me with my project?"},
            {"role": "assistant", "content": "Of course! I'd be happy to help. What's your project about?"},
            {"role": "user", "content": "It's a web application using FastAPI and React."},
            {"role": "assistant", "content": "Great choice! FastAPI is excellent for backend APIs."},
            {"role": "user", "content": "I need help with authentication setup."},
        ]

        for i, msg_data in enumerate(test_messages, 1):
            message = manager.add_message(
                thread_id=thread_id,
                role=msg_data["role"],
                content=msg_data["content"]
            )
            logger.info(f"Added message {i}/{len(test_messages)}: {message.id}")

        # Verify message count
        messages = manager.get_session_history(thread_id)
        assert len(messages) == len(test_messages), \
            f"Expected {len(test_messages)} messages, got {len(messages)}"
        logger.debug(f"Assertion passed: {len(messages)} messages added")

        # Get thread statistics
        stats = manager.get_thread_statistics(thread_id)
        logger.info(f"Thread statistics: {stats.get('total_messages', 0)} messages, "
                   f"{stats.get('total_tokens', 0)} tokens")

        logger.success("Thread creation test completed successfully")
        return thread_id, manager

    except FileNotFoundError as e:
        logger.error(f"Database file not found: {e}")
        raise
    except ValueError as e:
        logger.error(f"Invalid value: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error in thread creation: {e}")
        raise


async def test_history_compression(manager: HistoryManager, thread_id: str) -> Dict[str, Any]:
    """Test dynamic history compression."""
    logger = logging.getLogger(__name__)
    logger.info("Testing dynamic history compression...")

    try:
        # Get initial messages
        messages = manager.get_session_history(thread_id)
        initial_count = len(messages)

        # Calculate initial tokens (simplified)
        token_counter = TokenCounter()
        initial_tokens = sum(len(msg.content.split()) for msg in messages) * 1.3  # Approximation

        logger.info(f"Initial state: {initial_count} messages, ~{int(initial_tokens)} tokens")

        # Add more messages to trigger compression
        logger.info("Adding messages to trigger compression threshold...")
        long_content = "This is a long message " * 20  # ~200 tokens

        for i in range(1, 11):
            role = "user" if i % 2 == 1 else "assistant"
            manager.add_message(
                thread_id=thread_id,
                role=role,
                content=f"{long_content} - Message {i}"
            )
            logger.debug(f"Added long message {i}/10")

        # Get updated messages
        messages = manager.get_session_history(thread_id)
        before_count = len(messages)
        before_tokens = sum(len(msg.content.split()) for msg in messages) * 1.3

        logger.info(f"Before compression: {before_count} messages, ~{int(before_tokens)} tokens")

        # Apply compression
        compression_result = await compress_history_if_needed(
            messages=messages,
            token_threshold=2000
        )

        after_count = len(compression_result.get("compressed_messages", []))
        after_tokens = compression_result.get("total_tokens_after", 0)
        pruned_count = compression_result.get("pruned_count", 0)

        logger.info(f"After compression: {after_count} messages, ~{int(after_tokens)} tokens")
        logger.info(f"Pruned {pruned_count} messages")

        # Verify compression occurred
        compression_happened = (after_tokens < before_tokens) or (after_count < before_count)
        assert compression_happened, "Compression should reduce tokens or message count"

        if compression_happened:
            logger.success("Compression test passed successfully")
        else:
            logger.warning("No compression happened - might be below threshold")

        return {
            "before_count": before_count,
            "before_tokens": before_tokens,
            "after_count": after_count,
            "after_tokens": after_tokens,
            "pruned_count": pruned_count,
            "compression_happened": compression_happened
        }

    except AssertionError as e:
        logger.error(f"Assertion failed: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error in compression test: {e}")
        raise


def test_prune_context() -> Dict[str, Any]:
    """Test context pruning for irrelevant tool results."""
    logger = logging.getLogger(__name__)
    logger.info("Testing context pruning for irrelevant tool results...")

    try:
        # Create test messages with tool results
        messages = [
            Message(role="user", content="Show me the auth service code"),
            Message(role="tool", content="File: app/services/auth.py\nCode: def authenticate(): ..."),
            Message(role="tool", content="File: app/utils/logger.py\nCode: def log(): ..."),
            Message(role="tool", content="File: app/models/user.py\nCode: class User: ..."),
            Message(role="assistant", content="Here's the auth service code..."),
            Message(role="user", content="Now modify only app/services/auth.py"),
        ]

        original_count = len(messages)
        logger.info(f"Original messages: {original_count}")

        # User query mentioning specific file
        user_query = "Modify the authenticate function in app/services/auth.py"

        # Apply pruning
        pruned_messages = prune_irrelevant_context(
            messages=messages,
            user_query=user_query
        )

        pruned_count = len(pruned_messages)
        logger.info(f"After pruning: {pruned_count} messages")
        logger.info(f"Removed {original_count - pruned_count} irrelevant messages")

        # Verify irrelevant tool results were removed
        tool_messages_before = [m for m in messages if m.role == "tool"]
        tool_messages_after = [m for m in pruned_messages if m.role == "tool"]

        # Check that only auth.py tool result remains
        auth_tools = [m for m in tool_messages_after if "auth.py" in m.content]
        other_tools = [m for m in tool_messages_after if "auth.py" not in m.content]

        assert len(other_tools) == 0, "Irrelevant tool results should be removed"
        assert len(auth_tools) > 0, "Relevant tool results should remain"

        logger.success("Context pruning test passed successfully")

        return {
            "original_count": original_count,
            "pruned_count": pruned_count,
            "removed_count": original_count - pruned_count,
            "files_mentioned": ["app/services/auth.py"]
        }

    except AssertionError as e:
        logger.error(f"Assertion failed: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error in pruning test: {e}")
        raise


def cleanup_test_database() -> bool:
    """Clean up test database file."""
    logger = logging.getLogger(__name__)
    logger.info("Cleaning up test database...")

    try:
        db_path = Path("tests/test_history.db")
        if db_path.exists():
            db_path.unlink()
            logger.success(f"Removed test database: {db_path}")
            return True
        else:
            logger.info("Test database does not exist, nothing to clean up")
            return True
    except Exception as e:
        logger.error(f"Failed to clean up test database: {e}")
        return False


async def main() -> int:
    """Main test runner."""
    print("\n" + "=" * 40)
    print("=== HISTORY MANAGEMENT TEST SUITE ===")
    print("=" * 40 + "\n")

    # Setup logging
    setup_logging()
    logger = logging.getLogger(__name__)

    # Test results tracker
    test_results = {}

    try:
        # Test 1: Imports
        logger.info("Starting test: Imports")
        test_results["imports"] = test_imports()

        if not test_results["imports"]:
            logger.error("Import test failed. Skipping further tests.")
            return 1

        # Test 2: API Connection
        logger.info("Starting test: API Connection")
        test_results["api_connection"] = await test_api_connection()

        if not test_results["api_connection"]:
            logger.warning("API connection test failed. Some tests may be limited.")

        # Test 3: Create History
        logger.info("Starting test: Create History")
        try:
            thread_id, manager = test_create_history()
            test_results["create_history"] = True
        except Exception:
            test_results["create_history"] = False
            thread_id, manager = None, None

        # Test 4: History Compression (if history created successfully)
        if test_results.get("create_history") and thread_id and manager:
            logger.info("Starting test: History Compression")
            try:
                compression_stats = await test_history_compression(manager, thread_id)
                test_results["history_compression"] = compression_stats.get("compression_happened", False)
            except Exception:
                test_results["history_compression"] = False
        else:
            logger.warning("Skipping compression test - history creation failed")
            test_results["history_compression"] = False

        # Test 5: Prune Context
        logger.info("Starting test: Prune Context")
        try:
            prune_stats = test_prune_context()
            test_results["prune_context"] = prune_stats.get("removed_count", 0) > 0
        except Exception:
            test_results["prune_context"] = False

        # Cleanup
        logger.info("Starting cleanup")
        test_results["cleanup"] = cleanup_test_database()

    except KeyboardInterrupt:
        logger.error("Test suite interrupted by user")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error in main test runner: {e}")
        return 1

    # Print summary
    print("\n" + "=" * 40)
    print("=== TEST RESULTS ===")
    print("=" * 40)

    passed = sum(1 for result in test_results.values() if result is True)
    total = sum(1 for result in test_results.values() if isinstance(result, bool))

    print(f"\nSummary: {passed}/{total} tests passed")

    for test_name, result in test_results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        color = '\033[92m' if result else '\033[91m'
        reset = '\033[0m'
        print(f"  {color}{status}{reset}: {test_name}")

    print("\n" + "=" * 40)

    # Return exit code
    if passed == total:
        logger.success("All tests passed!")
        return 0
    else:
        logger.error(f"{total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
```

---

## 📖 Пояснения к коду

Этот код вносит минимальное, но важное изменение для обеспечения обратной совместимости с Python 3.8. Внесены следующие изменения:

1. **Добавлен импорт `Tuple` из модуля `typing`**:
   - В строке 6 добавлен `Tuple` в импорты из модуля `typing`
   - Теперь импорт выглядит так: `from typing import List, Dict, Any, Tuple`

2. **Исправлена аннотация типа в функции `test_create_history()`**:
   - В строке 147 изменен тип возвращаемого значения с `tuple[str, HistoryManager]` на `Tuple[str, HistoryManager]`
   - Это критически важно, так как синтаксис `tuple[...]` для аннотаций типов доступен только начиная с Python 3.9+

**

---

## 🛠️ Выполненные вызовы инструментов

1. ✅ **read_file**
   - Аргументы: `file_path=scripts/test_history_manager.py`

---

*Отчет сгенерирован автоматически: 2025-12-15T00:59:51.054387*