import asyncio
import logging
import sys
import gc
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import traceback

# === DEBUG: CHECK STARTUP ===
print("DEBUG: Script started...", file=sys.stderr)

# Add project root to sys.path for imports
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Project-specific imports
try:
    from app.history.manager import HistoryManager
    from app.history.storage import HistoryStorage, Thread, Message
    # Import FUNCTIONS, not class
    from app.history.compressor import compress_history_if_needed, prune_irrelevant_context
    from app.utils.token_counter import TokenCounter
    from config.settings import Config
except ImportError as e:
    print(f"CRITICAL IMPORT ERROR: {e}", file=sys.stderr)
    sys.exit(1)

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
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',  # Добавили %(name)s
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers to avoid duplicates
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    
    logger.addHandler(handler)
    
    # Устанавливаем настройки для всех используемых логгеров
    loggers_to_configure = [
        'app', 'app.history', 'app.history.manager', 'app.history.compressor',
        'app.history.storage', 'app.llm', 'app.utils', 'config', '__main__'
    ]
    
    for logger_name in loggers_to_configure:
        log = logging.getLogger(logger_name)
        log.setLevel(logging.DEBUG)
        log.handlers = []  # Очищаем старые обработчики
        log.addHandler(handler)
        log.propagate = True  # Позволяем сообщениям передаваться родительским логгерам
    
    logging.getLogger(__name__).info("Logging configured successfully")


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
            __import__(module_path)
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
        model = getattr(Config, 'HISTORY_COMPRESSOR_MODEL', 'deepseek-chat')
        if not model:
             model = 'deepseek-chat' # Fallback

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


async def test_create_history() -> tuple[Optional[str], Optional[HistoryManager]]:
    """Create a new conversation thread with test messages."""
    logger = logging.getLogger(__name__)
    logger.info("Creating new conversation thread...")

    try:
        # Ensure tests directory exists
        db_path = Path("tests/test_history.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize HistoryManager with test database
        manager = HistoryManager(db_path=str(db_path))
        
        # Initialize token counter
        token_counter = TokenCounter()

        # Create new thread (CORRECT METHOD NAME: create_thread)
        thread = await manager.create_thread(
            user_id="test_user",
            project_path="test_project",
            title="Test Project"
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
            message = await manager.add_message(
                thread_id=thread_id,
                role=msg_data["role"],
                content=msg_data["content"],
                tokens=token_counter.count(msg_data["content"])
            )
            logger.info(f"Added message {i}/{len(test_messages)}: {message.id}")

        # Verify message count
        messages = await manager.get_session_history(
            thread_id=thread_id,
            current_query="I need help with authentication setup."
        )
        
        assert len(messages) == len(test_messages), \
            f"Expected {len(test_messages)} messages, got {len(messages)}"
        logger.debug(f"Assertion passed: {len(messages)} messages added")

        # Get thread statistics (CORRECT METHOD NAME: get_thread_stats)
        stats = await manager.get_thread_stats(thread_id)
        
        logger.info(f"Thread statistics: {stats.get('message_count', 0)} messages, "
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
        return None, None


async def test_history_compression(manager: HistoryManager, thread_id: str, test_query: str = "Тестовый запрос для проверки сжатия") -> Dict[str, Any]:
    """Test dynamic history compression using compress_history_if_needed."""
    logger = logging.getLogger(__name__)
    logger.info("TEST_COMPRESSION: Starting compression test...")
    logger.info(f"TEST_COMPRESSION: thread_id={thread_id}, manager={type(manager).__name__}")

    try:
        # Get initial messages with test_query
        logger.info("TEST_COMPRESSION: Getting session history...")
        messages = await manager.get_session_history(thread_id, test_query)
        initial_count = len(messages)
        logger.info(f"TEST_COMPRESSION: Got {initial_count} initial messages")

        # Calculate initial tokens
        token_counter = TokenCounter()
        initial_tokens = sum(token_counter.count(msg.content) for msg in messages)

        logger.info(f"TEST_COMPRESSION: Initial state: {initial_count} messages, ~{int(initial_tokens)} tokens")

        # Add more messages to trigger compression
        logger.info("TEST_COMPRESSION: Adding messages to trigger compression threshold...")
        long_content = "This is a long message " * 20  # ~200 tokens

        for i in range(1, 11):
            role = "user" if i % 2 == 1 else "assistant"
            logger.debug(f"TEST_COMPRESSION: Adding long message {i}/10")
            await manager.add_message(
                thread_id=thread_id,
                role=role,
                content=f"{long_content} - Message {i}",
                tokens=token_counter.count(f"{long_content} - Message {i}")
            )

        # Get updated messages with the same test_query
        logger.info("TEST_COMPRESSION: Getting updated messages after adding long messages...")
        messages = await manager.get_session_history(thread_id, test_query)
        before_count = len(messages)
        before_tokens = sum(token_counter.count(msg.content) for msg in messages)

        logger.info(f"TEST_COMPRESSION: Before compression: {before_count} messages, ~{int(before_tokens)} tokens")

        # Apply compression directly using the FUNCTION (not class)
        logger.info(f"TEST_COMPRESSION: Calling compress_history_if_needed with threshold=150")
        compressed_messages = await compress_history_if_needed(
            history=messages,
            threshold=150 
        )

        logger.info(f"TEST_COMPRESSION: compress_history_if_needed returned {len(compressed_messages)} messages")
        
        after_count = len(compressed_messages)
        after_tokens = sum(token_counter.count(msg.content) for msg in compressed_messages)
        
        # Вычисляем количество "сжатых" сообщений
        pruned_count = 0
        compressed_samples = []
        for msg in compressed_messages[:3]:  # Проверяем первые 3 сообщения
            if msg.content.startswith("[COMPRESSED]"):
                pruned_count += 1
                compressed_samples.append(msg.content[:100])

        logger.info(f"TEST_COMPRESSION: After compression: {after_count} messages, ~{int(after_tokens)} tokens")
        logger.info(f"TEST_COMPRESSION: Found {pruned_count} compressed messages")
        if compressed_samples:
            logger.info(f"TEST_COMPRESSION: Sample compressed content: {compressed_samples}")

        # Verify compression occurred
        compression_happened = (after_tokens < before_tokens) or (after_count < before_count)
        
        # NOTE: If API call fails or tokens are small, compression might not happen. 
        # This is a soft check.
        if compression_happened:
            logger.info("TEST_COMPRESSION: Compression test passed successfully")
        else:
            logger.warning(f"TEST_COMPRESSION: No compression happened. "
                          f"Before: {before_tokens} tokens, After: {after_tokens} tokens, "
                          f"Threshold: 150")
            
            # Детальная диагностика
            total_tokens = sum(token_counter.count(msg.content) for msg in messages)
            logger.info(f"TEST_COMPRESSION: Total tokens in history: {total_tokens}")
            logger.info(f"TEST_COMPRESSION: Threshold check: {total_tokens} <= {150}? {total_tokens <= 150}")

        return {
            "before_count": before_count,
            "before_tokens": before_tokens,
            "after_count": after_count,
            "after_tokens": after_tokens,
            "compressed_message_count": pruned_count,
            "compression_happened": compression_happened
        }

    except AssertionError as e:
        logger.error(f"TEST_COMPRESSION: Assertion failed: {e}")
        raise
    except Exception as e:
        logger.exception(f"TEST_COMPRESSION: Unexpected error in compression test: {e}")
        logger.error(f"TEST_COMPRESSION: Error type: {type(e).__name__}")
        logger.error(f"TEST_COMPRESSION: Error args: {e.args}")
        raise


def test_prune_context() -> Dict[str, Any]:
    """Test context pruning for irrelevant tool results."""
    logger = logging.getLogger(__name__)
    logger.info("Testing context pruning for irrelevant tool results...")

    try:
        # Create test messages with tool results
        messages = [
            Message(id="1", thread_id="test", role="user", content="Show me the auth service code", tokens=0),
            Message(id="2", thread_id="test", role="tool", content="File: app/services/auth.py\nCode: def authenticate(): ...", tokens=0),
            Message(id="3", thread_id="test", role="tool", content="File: app/utils/logger.py\nCode: def log(): ...", tokens=0),
            Message(id="4", thread_id="test", role="tool", content="File: app/models/user.py\nCode: class User: ...", tokens=0),
            Message(id="5", thread_id="test", role="assistant", content="Here's the auth service code...", tokens=0),
            Message(id="6", thread_id="test", role="user", content="Now modify only app/services/auth.py", tokens=0),
        ]

        original_count = len(messages)
        logger.info(f"Original messages: {original_count}")

        # User query mentioning specific file
        user_query = "Modify the authenticate function in app/services/auth.py"

        # Apply pruning directly using the FUNCTION
        # CORRECT ARGUMENTS: history, current_query
        pruned_messages = prune_irrelevant_context(
            history=messages,
            current_query=user_query
        )

        pruned_count = len(pruned_messages)
        # Count PRUNED markers
        pruned_tool_count = sum(1 for m in pruned_messages if m.content.startswith("[PRUNED"))
        
        logger.info(f"After pruning: {pruned_count} messages")
        logger.info(f"Pruned {pruned_tool_count} tool messages (content replaced)")

        # Verify irrelevant tool results were removed
        tool_messages_after = [m for m in pruned_messages if m.role == "tool"]

        # Check that relevant tool results (auth.py) kept original content
        auth_tools = [m for m in tool_messages_after if "auth.py" in m.content and not m.content.startswith("[PRUNED")]
        
        # Check that irrelevant tool results were pruned (content replaced with [PRUNED...])
        pruned_tools = [m for m in tool_messages_after if m.content.startswith("[PRUNED")]
        
        # Verify: auth.py tool should remain with original content
        assert len(auth_tools) == 1, f"Relevant tool result (auth.py) should remain unchanged, got {len(auth_tools)}"
        
        # Verify: other tools (logger.py, user.py) should be pruned
        assert len(pruned_tools) == 2, f"Irrelevant tool results should be pruned, got {len(pruned_tools)}"

        logger.success("Context pruning test passed successfully")

        return {
            "original_count": original_count,
            "final_count": pruned_count,
            "pruned_tool_count": len(pruned_tools),
            "relevant_tools_kept": len(auth_tools),
            "files_mentioned": ["app/services/auth.py"]
        }

    except AssertionError as e:
        logger.error(f"Assertion failed: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error in pruning test: {e}")
        raise


async def cleanup_test_database() -> bool:
    """Clean up test database file."""
    logger = logging.getLogger(__name__)
    logger.info("Cleaning up test database...")

    try:
        db_path = Path("tests/test_history.db")
        if db_path.exists():
            gc.collect()  # Force garbage collection to close DB handles
            await asyncio.sleep(0.5)  # Give Windows time to release file lock
            try:
                db_path.unlink()
                logger.success(f"Removed test database: {db_path}")
            except PermissionError:
                logger.warning(f"Could not delete database file (locked): {db_path}")
                return False
            return True
        else:
            logger.info("Test database does not exist, nothing to clean up")
            return True
    except Exception as e:
        logger.error(f"Failed to clean up test database: {e}")
        return False


async def main() -> int:
    """Main test runner."""
    print("DEBUG: Inside main function...", file=sys.stderr)
    
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
        thread_id = None
        manager = None
        try:
            thread_id, manager = await test_create_history()
            test_results["create_history"] = True
            logger.info(f"Create History result: thread_id={thread_id}, manager={manager is not None}")
        except Exception as e:
            logger.exception(f"Create history test failed: {e}")
            test_results["create_history"] = False
            
        # Test 4: History Compression (if history created successfully)
        logger.info(f"Checking condition for compression test: "
                   f"create_history={test_results.get('create_history')}, "
                   f"thread_id={thread_id}, "
                   f"manager={manager is not None}")
        
        if test_results.get("create_history") and thread_id and manager:
            logger.info("Starting test: History Compression")
            try:
                compression_stats = await test_history_compression(manager, thread_id)
                logger.info(f"Compression test returned: {compression_stats}")
                test_results["history_compression"] = compression_stats.get("compression_happened", False)
                test_results["compression_details"] = compression_stats
            except Exception as e:
                logger.exception(f"History compression test failed with error: {e}")
                test_results["history_compression"] = False
                test_results["compression_error"] = str(e)
        else:
            logger.warning("Skipping compression test - condition not met")
            test_results["history_compression"] = False
            test_results["compression_skipped"] = True

        # Test 5: Prune Context
        logger.info("Starting test: Prune Context")
        try:
            prune_stats = test_prune_context()
            test_results["prune_context"] = prune_stats.get("pruned_tool_count", 0) > 0
            test_results["prune_details"] = prune_stats
        except Exception as e:
            logger.exception(f"Prune context test failed: {e}")
            test_results["prune_context"] = False

        # Cleanup
        logger.info("Starting cleanup")
        test_results["cleanup"] = await cleanup_test_database()

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

    passed = sum(1 for key, result in test_results.items() 
                 if key in ["imports", "api_connection", "create_history", 
                           "history_compression", "prune_context", "cleanup"] 
                 and result is True)
    
    total = sum(1 for key in ["imports", "api_connection", "create_history", 
                             "history_compression", "prune_context", "cleanup"] 
                if key in test_results)

    print(f"\nSummary: {passed}/{total} tests passed")

    test_order = ["imports", "api_connection", "create_history", 
                  "history_compression", "prune_context", "cleanup"]
    
    for test_name in test_order:
        if test_name in test_results:
            result = test_results[test_name]
            status = "✓ PASS" if result else "✗ FAIL"
            color = '\033[92m' if result else '\033[91m'
            reset = '\033[0m'
            
            # Добавляем дополнительную информацию
            details = ""
            if test_name == "history_compression" and "compression_details" in test_results:
                details = f" (compressed: {test_results['compression_details'].get('compressed_message_count', 0)})"
            elif test_name == "history_compression" and "compression_skipped" in test_results:
                details = " (SKIPPED)"
            elif test_name == "history_compression" and "compression_error" in test_results:
                details = f" (ERROR: {test_results['compression_error'][:50]})"
            
            print(f"  {color}{status}{reset}: {test_name}{details}")

    print("\n" + "=" * 40)
    
    # Детальный вывод результата теста сжатия
    if "compression_details" in test_results:
        details = test_results["compression_details"]
        print(f"\nCompression Test Details:")
        print(f"  Before: {details.get('before_count')} messages, {details.get('before_tokens')} tokens")
        print(f"  After:  {details.get('after_count')} messages, {details.get('after_tokens')} tokens")
        print(f"  Compressed messages: {details.get('compressed_message_count')}")
        print(f"  Happened: {details.get('compression_happened')}")

    # Return exit code
    if passed == total:
        logger.success("All tests passed!")
        return 0
    else:
        logger.error(f"{total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    print("DEBUG: Entering main block", file=sys.stderr)
    
    # Configure event loop for Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except Exception as e:
        print(f"CRITICAL MAIN FAILURE: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)