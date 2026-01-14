# scripts/test_run_project_tests.py
"""
Comprehensive test suite for run_project_tests tool.

This script ACTUALLY tests the tool by:
1. Creating real temporary project structures
2. Using real VirtualFileSystem with staged changes
3. Running real unittest via subprocess
4. Verifying XML output parsing
5. Testing edge cases (failures, timeouts, deletions)

Run: python scripts/test_run_project_tests.py
"""

import sys
import os
import tempfile
import shutil
import time
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


# ============================================================================
# TEST INFRASTRUCTURE
# ============================================================================

@dataclass
class TestResultRecord:
    """Result of a single test."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    details: Optional[str] = None


class TestRunner:
    """Runs tests and collects results."""
    
    def __init__(self):
        self.results: List[TestResultRecord] = []
        self.temp_dirs: List[str] = []
    
    def create_temp_project(self) -> str:
        """Create temporary project directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_project_")
        self.temp_dirs.append(temp_dir)
        return temp_dir
    
    def cleanup(self):
        """Clean up all temporary directories."""
        for temp_dir in self.temp_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
    
    def record(self, name: str, passed: bool, message: str, 
               duration_ms: float, details: str = None):
        """Record test result."""
        self.results.append(TestResultRecord(
            name=name,
            passed=passed,
            message=message,
            duration_ms=duration_ms,
            details=details
        ))
    
    def print_summary(self):
        """Print test results summary."""
        table = Table(title="Test Results", box=box.ROUNDED)
        table.add_column("Test", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Time", justify="right")
        table.add_column("Message", style="dim")
        
        passed = 0
        failed = 0
        
        for r in self.results:
            status = "[green]✓ PASS[/]" if r.passed else "[red]✗ FAIL[/]"
            if r.passed:
                passed += 1
            else:
                failed += 1
            
            msg = r.message[:50] + "..." if len(r.message) > 50 else r.message
            table.add_row(
                r.name,
                status,
                str(int(r.duration_ms)) + "ms",
                msg
            )
        
        console.print(table)
        console.print()
        
        if failed == 0:
            console.print(Panel(
                "[bold green]All " + str(passed) + " tests passed![/]",
                title="Summary"
            ))
        else:
            console.print(Panel(
                "[bold red]" + str(failed) + " failed[/], [green]" + str(passed) + " passed[/]",
                title="Summary"
            ))
        
        return failed == 0


# ============================================================================
# TEST FIXTURES - Real Python files for testing
# ============================================================================

SAMPLE_MODULE_CODE = '''
"""Sample module for testing."""

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

def subtract(a: int, b: int) -> int:
    """Subtract b from a."""
    return a - b

def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b

def divide(a: int, b: int) -> float:
    """Divide a by b."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


class Calculator:
    """Simple calculator class."""
    
    def __init__(self, initial: int = 0):
        self.value = initial
    
    def add(self, x: int) -> int:
        self.value += x
        return self.value
    
    def reset(self):
        self.value = 0
'''

# Test file that passes (8 tests total: 3 + 2 + 3)
PASSING_TEST_CODE = '''
"""Tests for sample module - all passing."""
import unittest
from myapp.calculator import add, subtract, multiply, Calculator


class TestAddFunction(unittest.TestCase):
    """Tests for add function."""
    
    def test_add_positive(self):
        self.assertEqual(add(2, 3), 5)
    
    def test_add_negative(self):
        self.assertEqual(add(-1, -1), -2)
    
    def test_add_zero(self):
        self.assertEqual(add(0, 5), 5)


class TestSubtractFunction(unittest.TestCase):
    """Tests for subtract function."""
    
    def test_subtract_positive(self):
        self.assertEqual(subtract(5, 3), 2)
    
    def test_subtract_negative_result(self):
        self.assertEqual(subtract(3, 5), -2)


class TestCalculatorClass(unittest.TestCase):
    """Tests for Calculator class."""
    
    def test_init_default(self):
        calc = Calculator()
        self.assertEqual(calc.value, 0)
    
    def test_init_with_value(self):
        calc = Calculator(10)
        self.assertEqual(calc.value, 10)
    
    def test_add_method(self):
        calc = Calculator(5)
        result = calc.add(3)
        self.assertEqual(result, 8)
        self.assertEqual(calc.value, 8)


if __name__ == "__main__":
    unittest.main()
'''

# Test file with failures
FAILING_TEST_CODE = '''
"""Tests that will fail."""
import unittest
from myapp.calculator import add, subtract, divide


class TestFailures(unittest.TestCase):
    """Tests designed to fail."""
    
    def test_wrong_addition(self):
        # This will FAIL - wrong expected value
        self.assertEqual(add(2, 2), 5)  # Should be 4
    
    def test_wrong_subtraction(self):
        # This will FAIL
        self.assertEqual(subtract(10, 3), 5)  # Should be 7


class TestErrors(unittest.TestCase):
    """Tests designed to raise errors."""
    
    def test_divide_by_zero(self):
        # This will ERROR - unhandled exception
        result = divide(10, 0)  # Raises ValueError


if __name__ == "__main__":
    unittest.main()
'''

# Test file with import error
IMPORT_ERROR_TEST = '''
"""Test file with import error."""
import unittest
from nonexistent_module import something  # This doesn't exist


class TestImportFail(unittest.TestCase):
    def test_something(self):
        pass
'''

# Modified module (for VFS staging test)
MODIFIED_MODULE_CODE = '''
"""Modified sample module - multiply is now broken."""

def add(a: int, b: int) -> int:
    return a + b

def subtract(a: int, b: int) -> int:
    return a - b

def multiply(a: int, b: int) -> int:
    # BUG: Returns wrong result
    return a + b  # Should be a * b

def divide(a: int, b: int) -> float:
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


class Calculator:
    def __init__(self, initial: int = 0):
        self.value = initial
    
    def add(self, x: int) -> int:
        self.value += x
        return self.value
    
    def reset(self):
        self.value = 0
'''

# Test for multiply (will fail with modified module)
MULTIPLY_TEST_CODE = '''
"""Test multiply function."""
import unittest
from myapp.calculator import multiply


class TestMultiply(unittest.TestCase):
    def test_multiply_positive(self):
        self.assertEqual(multiply(3, 4), 12)
    
    def test_multiply_zero(self):
        self.assertEqual(multiply(5, 0), 0)
    
    def test_multiply_negative(self):
        self.assertEqual(multiply(-2, 3), -6)


if __name__ == "__main__":
    unittest.main()
'''


def create_project_structure(project_dir: str) -> None:
    """Create a realistic project structure for testing."""
    project_path = Path(project_dir)
    
    # Create directories
    (project_path / "myapp").mkdir(parents=True)
    (project_path / "tests").mkdir(parents=True)
    
    # Create __init__.py files
    (project_path / "myapp" / "__init__.py").write_text("")
    (project_path / "tests" / "__init__.py").write_text("")
    
    # Create source module
    (project_path / "myapp" / "calculator.py").write_text(SAMPLE_MODULE_CODE)
    
    # Create test files
    (project_path / "tests" / "test_passing.py").write_text(PASSING_TEST_CODE)
    (project_path / "tests" / "test_failing.py").write_text(FAILING_TEST_CODE)
    (project_path / "tests" / "test_multiply.py").write_text(MULTIPLY_TEST_CODE)


# ============================================================================
# ACTUAL TESTS
# ============================================================================

def test_1_basic_passing_tests(runner: TestRunner) -> None:
    """TEST 1: Run tests that should all pass."""
    console.print("\n[bold cyan]TEST 1: Basic Passing Tests[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_passing.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        assert "<!-- TEST_RESULT -->" in xml_result, "Missing XML marker"
        assert "<status>PASSED</status>" in xml_result, "Should be PASSED"
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse XML"
        
        # FIXED: test_passing.py has 8 tests (3 + 2 + 3), not 9
        assert result.success == True, "Expected success=True, got " + str(result.success)
        assert result.tests_run == 8, "Expected 8 tests, got " + str(result.tests_run)
        assert result.tests_passed == 8, "Expected 8 passed, got " + str(result.tests_passed)
        assert result.tests_failed == 0, "Expected 0 failed"
        assert len(result.failed_tests) == 0, "Should have no failed tests"
        
        duration = (time.time() - start) * 1000
        runner.record("Basic Passing Tests", True, 
                     "8/8 tests passed, XML parsed correctly", duration)
        console.print("[green]  ✓ All assertions passed[/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Basic Passing Tests", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_2_failing_tests(runner: TestRunner) -> None:
    """TEST 2: Run tests that should fail."""
    console.print("\n[bold cyan]TEST 2: Failing Tests Detection[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_failing.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        assert "<status>FAILED</status>" in xml_result, "Should be FAILED"
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse XML"
        
        # test_failing.py has 3 tests: 2 FAILs + 1 ERROR
        assert result.success == False, "Expected success=False"
        assert result.tests_run == 3, "Expected 3 tests, got " + str(result.tests_run)
        assert result.tests_failed >= 2, "Expected at least 2 failures"
        assert result.tests_errors >= 1, "Expected at least 1 error"
        assert len(result.failed_tests) >= 2, "Expected failed_tests details"
        
        duration = (time.time() - start) * 1000
        runner.record("Failing Tests Detection", True,
                     "Detected " + str(result.tests_failed) + " failures, " + str(result.tests_errors) + " errors", duration)
        console.print("[green]  ✓ Failures and errors detected correctly[/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Failing Tests Detection", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_3_specific_test_class(runner: TestRunner) -> None:
    """TEST 3: Run only a specific test class."""
    console.print("\n[bold cyan]TEST 3: Specific Test Class (chunk_name)[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_passing.py",
            chunk_name="TestAddFunction",
            virtual_fs=None,
            timeout_sec=30
        )
        
        assert "<status>PASSED</status>" in xml_result, "Should pass"
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse"
        assert result.tests_run == 3, "Expected 3 tests (TestAddFunction), got " + str(result.tests_run)
        assert result.tests_passed == 3, "Expected 3 passed"
        
        duration = (time.time() - start) * 1000
        runner.record("Specific Test Class", True,
                     "Ran only TestAddFunction: " + str(result.tests_run) + " tests", duration)
        console.print("[green]  ✓ chunk_name filtering works[/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Specific Test Class", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_4_specific_test_method(runner: TestRunner) -> None:
    """TEST 4: Run only a specific test method."""
    console.print("\n[bold cyan]TEST 4: Specific Test Method[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_passing.py",
            chunk_name="TestAddFunction.test_add_positive",
            virtual_fs=None,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse"
        assert result.tests_run == 1, "Expected 1 test, got " + str(result.tests_run)
        assert result.success == True, "Single test should pass"
        
        duration = (time.time() - start) * 1000
        runner.record("Specific Test Method", True,
                     "Ran single method: " + str(result.tests_run) + " test", duration)
        console.print("[green]  ✓ Single method execution works[/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Specific Test Method", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_5_vfs_staged_changes(runner: TestRunner) -> None:
    """TEST 5: Test with VirtualFileSystem staged changes (CRITICAL)."""
    console.print("\n[bold cyan]TEST 5: VFS Staged Changes (CRITICAL)[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        from app.services.virtual_fs import VirtualFileSystem
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        # Verify original tests pass
        console.print("  → Verifying original code passes tests...")
        original_result_xml = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_multiply.py",
            virtual_fs=None,
            timeout_sec=30
        )
        original_result = parse_test_result_xml(original_result_xml)
        assert original_result.success == True, "Original multiply tests should pass"
        console.print("    Original: " + str(original_result.tests_passed) + "/" + str(original_result.tests_run) + " passed ✓")
        
        # Stage BROKEN version
        console.print("  → Staging BROKEN multiply() in VFS...")
        vfs = VirtualFileSystem(project_dir)
        vfs.stage_change("myapp/calculator.py", MODIFIED_MODULE_CODE)
        console.print("    VFS staged with broken multiply() ✓")
        
        # Run tests WITH VFS - should FAIL
        console.print("  → Running tests against VFS (should FAIL)...")
        vfs_result_xml = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_multiply.py",
            virtual_fs=vfs,
            timeout_sec=30
        )
        
        vfs_result = parse_test_result_xml(vfs_result_xml)
        assert vfs_result is not None, "Failed to parse VFS result"
        assert vfs_result.success == False, "Tests should FAIL with VFS broken code!"
        assert vfs_result.tests_failed >= 2, "Expected at least 2 failures"
        console.print("    VFS result: " + str(vfs_result.tests_failed) + " failures ✓")
        
        # Critical verification
        assert original_result.success != vfs_result.success, "Original and VFS must differ!"
        
        duration = (time.time() - start) * 1000
        runner.record("VFS Staged Changes", True,
                     "Original: PASS, VFS: FAIL (" + str(vfs_result.tests_failed) + " failures)", duration)
        console.print("[green]  ✓ VFS overlay is working correctly![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("VFS Staged Changes", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_6_vfs_new_file(runner: TestRunner) -> None:
    """TEST 6: Test with NEW file staged in VFS (doesn't exist on disk)."""
    console.print("\n[bold cyan]TEST 6: VFS New File (not on disk)[/]")
    start = time.time()
    
    NEW_MODULE = '''
"""Brand new module created in VFS."""

def greet(name: str) -> str:
    return f"Hello, {name}!"

def farewell(name: str) -> str:
    return f"Goodbye, {name}!"
'''
    
    NEW_TEST = '''
"""Tests for new module."""
import unittest
from myapp.greeter import greet, farewell


class TestGreeter(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("World"), "Hello, World!")
    
    def test_farewell(self):
        self.assertEqual(farewell("World"), "Goodbye, World!")


if __name__ == "__main__":
    unittest.main()
'''
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        from app.services.virtual_fs import VirtualFileSystem
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        # Stage NEW files in VFS
        console.print("  → Staging NEW files in VFS...")
        vfs = VirtualFileSystem(project_dir)
        vfs.stage_change("myapp/greeter.py", NEW_MODULE)
        vfs.stage_change("tests/test_greeter.py", NEW_TEST)
        console.print("    New files staged in VFS ✓")
        
        # Run tests against VFS
        console.print("  → Running tests on VFS-only files...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_greeter.py",
            virtual_fs=vfs,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse"
        assert result.tests_run == 2, "Expected 2 tests, got " + str(result.tests_run)
        assert result.success == True, "Tests should pass!"
        
        duration = (time.time() - start) * 1000
        runner.record("VFS New File", True,
                     "Ran " + str(result.tests_run) + " tests on VFS-only files", duration)
        console.print("[green]  ✓ VFS new files work correctly![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("VFS New File", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_7_vfs_deleted_file(runner: TestRunner) -> None:
    """TEST 7: Test with file DELETED in VFS."""
    console.print("\n[bold cyan]TEST 7: VFS Deleted File[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        from app.services.virtual_fs import VirtualFileSystem
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        # Verify original tests pass
        original_xml = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_passing.py",
            virtual_fs=None,
            timeout_sec=30
        )
        original_result = parse_test_result_xml(original_xml)
        assert original_result.success == True, "Original should pass"
        console.print("  → Original: " + str(original_result.tests_passed) + " tests pass ✓")
        
        # Stage DELETION
        console.print("  → Staging DELETION of calculator.py in VFS...")
        vfs = VirtualFileSystem(project_dir)
        vfs.stage_deletion("myapp/calculator.py")
        console.print("    File marked as deleted in VFS ✓")
        
        # Run tests - should fail
        console.print("  → Running tests (should fail - import error)...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_passing.py",
            virtual_fs=vfs,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse"
        assert result.success == False, "Tests should fail when dependency is deleted"
        
        duration = (time.time() - start) * 1000
        runner.record("VFS Deleted File", True,
                     "Deletion respected, import error detected", duration)
        console.print("[green]  ✓ VFS deletion works correctly![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("VFS Deleted File", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_8_timeout_handling(runner: TestRunner) -> None:
    """TEST 8: Test timeout handling."""
    console.print("\n[bold cyan]TEST 8: Timeout Handling[/]")
    start = time.time()
    
    SLOW_TEST = '''
"""Test that takes too long."""
import unittest
import time


class TestSlow(unittest.TestCase):
    def test_slow(self):
        time.sleep(10)
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
'''
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        (Path(project_dir) / "tests" / "test_slow.py").write_text(SLOW_TEST)
        
        console.print("  → Running slow test with 2 second timeout...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_slow.py",
            virtual_fs=None,
            timeout_sec=2
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse"
        assert result.success == False, "Should fail due to timeout"
        assert result.error_message is not None, "Should have error message"
        assert "timeout" in result.error_message.lower(), "Error should mention timeout"
        
        duration = (time.time() - start) * 1000
        runner.record("Timeout Handling", True,
                     "Timeout detected and reported", duration)
        console.print("[green]  ✓ Timeout handling works![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Timeout Handling", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_9_import_error_handling(runner: TestRunner) -> None:
    """TEST 9: Test import error handling."""
    console.print("\n[bold cyan]TEST 9: Import Error Handling[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        (Path(project_dir) / "tests" / "test_import_error.py").write_text(IMPORT_ERROR_TEST)
        
        console.print("  → Running test with import error...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_import_error.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Failed to parse"
        
        # FIXED: Import error means success=False (tests couldn't run properly)
        assert result.success == False, "Should fail due to import error"
        
        # Should have error info somewhere
        has_error_info = (
            (result.error_message and "import" in result.error_message.lower()) or
            "ModuleNotFoundError" in result.test_output or
            "ImportError" in result.test_output or
            "nonexistent_module" in result.test_output
        )
        assert has_error_info, "Should report import error"
        
        duration = (time.time() - start) * 1000
        runner.record("Import Error Handling", True,
                     "Import error detected and reported", duration)
        console.print("[green]  ✓ Import error handling works![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Import Error Handling", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_10_nonexistent_test_file(runner: TestRunner) -> None:
    """TEST 10: Test handling of non-existent test file."""
    console.print("\n[bold cyan]TEST 10: Non-existent Test File[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        console.print("  → Trying to run non-existent test file...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_does_not_exist.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        is_error = "<!-- TEST_ERROR -->" in xml_result or "<status>ERROR</status>" in xml_result
        assert is_error, "Should return error status"
        
        duration = (time.time() - start) * 1000
        runner.record("Non-existent Test File", True,
                     "Error handled gracefully", duration)
        console.print("[green]  ✓ Non-existent file handled correctly![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Non-existent Test File", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_11_feedback_handler_integration(runner: TestRunner) -> None:
    """TEST 11: FeedbackHandler Integration with TestRunFeedback."""
    console.print("\n[bold cyan]TEST 11: FeedbackHandler Integration[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        from app.agents.feedback_handler import (
            FeedbackHandler, 
            TestRunFeedback,
            FailedTest,
            create_test_run_feedback_from_xml
        )
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        # Run failing tests
        console.print("  → Running failing tests...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_failing.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        # Create TestRunFeedback from XML using helper function
        console.print("  → Creating TestRunFeedback from XML...")
        feedback = create_test_run_feedback_from_xml(xml_result, "tests/test_failing.py")
        assert feedback is not None, "Should create TestRunFeedback"
        assert feedback.success == False, "Should be failure"
        assert feedback.tests_failed >= 2, "Should have at least 2 failures"
        console.print("    TestRunFeedback created: " + str(feedback.tests_failed) + " failures ✓")
        
        # Add to FeedbackHandler
        console.print("  → Adding to FeedbackHandler...")
        handler = FeedbackHandler()
        handler.add_test_run_result(feedback)
        
        assert len(handler._test_run_results) == 1, "Should have 1 test result"
        
        # Get formatted feedback
        formatted = handler.get_feedback_for_orchestrator()
        assert "test_run_results" in formatted, "Should have test_run_results key"
        assert len(formatted["test_run_results"]) > 0, "Should have content"
        assert "TEST RUN RESULT" in formatted["test_run_results"], "Should have formatted output"
        console.print("    Formatted feedback ready for Orchestrator ✓")
        
        duration = (time.time() - start) * 1000
        runner.record("FeedbackHandler Integration", True,
                     "TestRunFeedback + FeedbackHandler work correctly", duration)
        console.print("[green]  ✓ FeedbackHandler integration works![/]")
        
    except ImportError as e:
        duration = (time.time() - start) * 1000
        runner.record("FeedbackHandler Integration", False, 
                     "Missing import: " + str(e), duration)
        console.print("[red]  ✗ Import failed: " + str(e) + "[/]")
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("FeedbackHandler Integration", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_12_feedback_loop_integration(runner: TestRunner) -> None:
    """TEST 12: FeedbackLoopState Integration with test runs."""
    console.print("\n[bold cyan]TEST 12: FeedbackLoopState Integration[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import run_project_tests
        from app.agents.feedback_loop import FeedbackLoopState, create_feedback_loop
        from app.agents.feedback_handler import create_test_run_feedback_from_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        # Create FeedbackLoopState
        console.print("  → Creating FeedbackLoopState with max_test_runs=3...")
        loop_state = create_feedback_loop(
            session_id="test-session-123",
            user_request="Fix the calculator tests",
            max_test_runs=3
        )
        
        # Check initial state
        assert loop_state.can_run_tests() == True, "Should allow tests initially"
        assert loop_state.test_runs == 0, "Should start at 0"
        console.print("    Initial state: can_run_tests=True, test_runs=0 ✓")
        
        # Run tests and record
        console.print("  → Running tests and recording...")
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_failing.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        # Create feedback and record
        feedback = create_test_run_feedback_from_xml(xml_result, "tests/test_failing.py")
        assert feedback is not None, "Should create feedback"
        
        record = loop_state.record_test_run(feedback)
        assert record is not None, "Should return TestRunRecord"
        assert loop_state.test_runs == 1, "Should be 1 after recording"
        assert loop_state.can_run_tests() == True, "Should still allow (1 < 3)"
        console.print("    After 1st run: test_runs=1, can_run_tests=True ✓")
        
        # Record more runs to hit limit
        loop_state.record_test_run(feedback)
        loop_state.record_test_run(feedback)
        
        assert loop_state.test_runs == 3, "Should be 3"
        assert loop_state.can_run_tests() == False, "Should NOT allow (3 >= 3)"
        console.print("    After 3rd run: test_runs=3, can_run_tests=False ✓")
        
        # Check test run history
        assert len(loop_state.test_run_history) == 3, "Should have 3 records"
        last_run = loop_state.get_last_test_run()
        assert last_run is not None, "Should have last run"
        assert last_run.success == False, "Last run should be failure"
        console.print("    History tracking works ✓")
        
        # Check summary via get_test_run_summary()
        summary = loop_state.get_test_run_summary()
        assert summary["total_runs"] == 3, "Summary should show 3 runs"
        assert summary["remaining_quota"] == 0, "No remaining quota"
        console.print("    Summary: " + str(summary) + " ✓")
        
        duration = (time.time() - start) * 1000
        runner.record("FeedbackLoopState Integration", True,
                     "Test run tracking and limits work correctly", duration)
        console.print("[green]  ✓ FeedbackLoopState integration works![/]")
        
    except ImportError as e:
        duration = (time.time() - start) * 1000
        runner.record("FeedbackLoopState Integration", False,
                     "Missing import: " + str(e), duration)
        console.print("[red]  ✗ Import failed: " + str(e) + "[/]")
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("FeedbackLoopState Integration", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")
        
def test_13_tool_executor_integration(runner: TestRunner) -> None:
    """TEST 13: ToolExecutor Integration."""
    console.print("\n[bold cyan]TEST 13: ToolExecutor Integration[/]")
    start = time.time()
    
    try:
        from app.tools.tool_executor import ToolExecutor
        from app.services.virtual_fs import VirtualFileSystem
        from app.tools.run_project_tests import parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        vfs = VirtualFileSystem(project_dir)
        
        console.print("  → Creating ToolExecutor with VFS...")
        executor = ToolExecutor(
            project_dir=project_dir,
            index={},
            virtual_fs=vfs
        )
        
        # Execute tool
        console.print("  → Executing run_project_tests via ToolExecutor...")
        result = executor.execute("run_project_tests", {
            "test_path": "tests/test_passing.py",
            "chunk_name": "TestAddFunction",
            "timeout_sec": 30
        })
        
        assert "<!-- TEST_RESULT -->" in result, "Should return XML result"
        assert "<status>PASSED</status>" in result, "Should pass"
        console.print("    Tool executed successfully ✓")
        
        # Test with VFS changes
        console.print("  → Testing with VFS staged changes...")
        vfs.stage_change("myapp/calculator.py", MODIFIED_MODULE_CODE)
        
        result_with_vfs = executor.execute("run_project_tests", {
            "test_path": "tests/test_multiply.py",
            "timeout_sec": 30
        })
        
        assert "<status>FAILED</status>" in result_with_vfs, "Should fail with VFS broken code"
        console.print("    VFS changes applied via executor ✓")
        
        duration = (time.time() - start) * 1000
        runner.record("ToolExecutor Integration", True,
                     "Full executor integration works", duration)
        console.print("[green]  ✓ ToolExecutor integration works![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("ToolExecutor Integration", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_14_orchestrator_tool_usage_stats(runner: TestRunner) -> None:
    """TEST 14: Orchestrator ToolUsageStats for test runs."""
    console.print("\n[bold cyan]TEST 14: Orchestrator ToolUsageStats[/]")
    start = time.time()
    
    try:
        from app.agents.orchestrator import ToolUsageStats, MAX_TEST_RUNS
        
        console.print("  → Creating ToolUsageStats...")
        stats = ToolUsageStats()
        
        # Initial state
        assert stats.can_run_tests() == True, "Should allow tests initially"
        assert stats.get_remaining_test_runs() == MAX_TEST_RUNS, "Should have full quota"
        assert stats.test_run_count == 0, "Should start at 0"
        console.print("    Initial state: can_run_tests=True, remaining=" + str(MAX_TEST_RUNS) + " ✓")
        
        # Increment test runs
        console.print("  → Recording test runs...")
        for i in range(MAX_TEST_RUNS):
            stats.increment("run_project_tests")
            remaining = stats.get_remaining_test_runs()
            console.print("    After run " + str(i + 1) + ": remaining=" + str(remaining))
        
        assert stats.test_run_count == MAX_TEST_RUNS, "Should be at max"
        assert stats.can_run_tests() == False, "Should NOT allow more tests"
        assert stats.get_remaining_test_runs() == 0, "Should have 0 remaining"
        console.print("    Limit enforcement correct ✓")
        
        # Also verify web_search is independent
        assert stats.can_use_web_search() == True, "Web search should still be available"
        console.print("    Web search independent of test runs ✓")
        
        duration = (time.time() - start) * 1000
        runner.record("Orchestrator ToolUsageStats", True,
                     "Test run tracking in ToolUsageStats works", duration)
        console.print("[green]  ✓ ToolUsageStats works![/]")
        
    except ImportError as e:
        duration = (time.time() - start) * 1000
        runner.record("Orchestrator ToolUsageStats", False,
                     "Missing: " + str(e), duration)
        console.print("[red]  ✗ Import failed: " + str(e) + "[/]")
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Orchestrator ToolUsageStats", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")



def test_15_xml_parsing_edge_cases(runner: TestRunner) -> None:
    """TEST 15: XML parsing edge cases."""
    console.print("\n[bold cyan]TEST 15: XML Parsing Edge Cases[/]")
    start = time.time()
    
    try:
        from app.tools.run_project_tests import parse_test_result_xml
        
        # Test 1: Empty/invalid XML
        result = parse_test_result_xml("")
        assert result is None or result.success == False, "Empty XML should fail gracefully"
        console.print("  → Empty XML handled ✓")
        
        result = parse_test_result_xml("not xml at all")
        assert result is None or result.success == False, "Invalid XML should fail gracefully"
        console.print("  → Invalid XML handled ✓")
        
        result = parse_test_result_xml("<xml><but>incomplete")
        assert result is None or result.success == False, "Incomplete XML should fail gracefully"
        console.print("  → Incomplete XML handled ✓")
        
        duration = (time.time() - start) * 1000
        runner.record("XML Parsing Edge Cases", True, "All edge cases handled", duration)
        console.print("[green]  ✓ XML parsing is robust![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("XML Parsing Edge Cases", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_16_vfs_multiple_changes(runner: TestRunner) -> None:
    """TEST 16: VFS with multiple simultaneous changes."""
    console.print("\n[bold cyan]TEST 16: VFS Multiple Changes[/]")
    start = time.time()
    
    SECOND_MODULE = '''
def helper():
    return "helper"
'''
    
    TEST_USING_BOTH = '''
import unittest
from myapp.calculator import add
from myapp.helper import helper

class TestBoth(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 1), 2)
    
    def test_helper(self):
        self.assertEqual(helper(), "helper")
'''
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        from app.services.virtual_fs import VirtualFileSystem
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        vfs = VirtualFileSystem(project_dir)
        
        # Stage multiple new files
        vfs.stage_change("myapp/helper.py", SECOND_MODULE)
        vfs.stage_change("tests/test_both.py", TEST_USING_BOTH)
        
        console.print("  → Staged 2 new files in VFS...")
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_both.py",
            virtual_fs=vfs,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Should parse"
        assert result.tests_run == 2, "Should run 2 tests"
        assert result.success == True, "Both tests should pass"
        
        duration = (time.time() - start) * 1000
        runner.record("VFS Multiple Changes", True, 
                     "Multiple VFS changes work together", duration)
        console.print("[green]  ✓ Multiple VFS changes work![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("VFS Multiple Changes", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_17_empty_test_file(runner: TestRunner) -> None:
    """TEST 17: Test file with no tests."""
    console.print("\n[bold cyan]TEST 17: Empty Test File (0 tests)[/]")
    start = time.time()
    
    EMPTY_TEST = '''
"""Test file with no actual tests."""
import unittest

class TestEmpty(unittest.TestCase):
    pass  # No test methods

if __name__ == "__main__":
    unittest.main()
'''
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        (Path(project_dir) / "tests" / "test_empty.py").write_text(EMPTY_TEST)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_empty.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Should parse"
        assert result.tests_run == 0, "Should have 0 tests"
        # 0 tests = technically success (nothing failed)
        assert result.success == True, "0 tests should be success"
        
        duration = (time.time() - start) * 1000
        runner.record("Empty Test File", True, "0 tests handled correctly", duration)
        console.print("[green]  ✓ Empty test file handled![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Empty Test File", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_18_syntax_error_in_test(runner: TestRunner) -> None:
    """TEST 18: Syntax error in test file."""
    console.print("\n[bold cyan]TEST 18: Syntax Error in Test[/]")
    start = time.time()
    
    SYNTAX_ERROR_TEST = '''
"""Test with syntax error."""
import unittest

class TestSyntax(unittest.TestCase):
    def test_something(self)  # Missing colon!
        pass
'''
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        (Path(project_dir) / "tests" / "test_syntax.py").write_text(SYNTAX_ERROR_TEST)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_syntax.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Should parse"
        assert result.success == False, "Syntax error should fail"
        
        # Should mention syntax error somewhere
        has_syntax_info = (
            (result.error_message and "syntax" in result.error_message.lower()) or
            "SyntaxError" in result.test_output
        )
        assert has_syntax_info, "Should report syntax error"
        
        duration = (time.time() - start) * 1000
        runner.record("Syntax Error in Test", True, "Syntax error detected", duration)
        console.print("[green]  ✓ Syntax error handled![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("Syntax Error in Test", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")


def test_19_fixture_setup_failure(runner: TestRunner) -> None:
    """TEST 19: Test with setUp failure."""
    console.print("\n[bold cyan]TEST 19: setUp Failure[/]")
    start = time.time()
    
    SETUP_FAIL_TEST = '''
"""Test with setUp that fails."""
import unittest

class TestSetupFail(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("Setup failed!")
    
    def test_never_runs(self):
        self.assertTrue(True)
    
    def test_also_never_runs(self):
        self.assertTrue(True)
'''
    
    try:
        from app.tools.run_project_tests import run_project_tests, parse_test_result_xml
        
        project_dir = runner.create_temp_project()
        create_project_structure(project_dir)
        
        (Path(project_dir) / "tests" / "test_setup.py").write_text(SETUP_FAIL_TEST)
        
        xml_result = run_project_tests(
            project_dir=project_dir,
            test_path="tests/test_setup.py",
            virtual_fs=None,
            timeout_sec=30
        )
        
        result = parse_test_result_xml(xml_result)
        assert result is not None, "Should parse"
        assert result.success == False, "setUp failure should fail"
        assert result.tests_errors >= 2, "Both tests should error due to setUp"
        
        duration = (time.time() - start) * 1000
        runner.record("setUp Failure", True, 
                     str(result.tests_errors) + " errors from setUp failure", duration)
        console.print("[green]  ✓ setUp failure handled![/]")
        
    except Exception as e:
        duration = (time.time() - start) * 1000
        runner.record("setUp Failure", False, str(e), duration)
        console.print("[red]  ✗ Failed: " + str(e) + "[/]")

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run all tests."""
    console.print(Panel(
        "[bold]run_project_tests Tool - Comprehensive Test Suite[/]\n\n"
        "This suite verifies REAL functionality, not mocks:\n"
        "• Real subprocess execution of unittest\n"
        "• Real VirtualFileSystem with staged changes\n"
        "• Real file I/O in temporary directories\n"
        "• Real XML parsing and validation\n"
        "• Integration with FeedbackHandler, FeedbackLoopState, ToolUsageStats",
        title="🧪 Test Suite",
        border_style="cyan"
    ))
    
    runner = TestRunner()
    
    try:
        # Core functionality tests
        test_1_basic_passing_tests(runner)
        test_2_failing_tests(runner)
        test_3_specific_test_class(runner)
        test_4_specific_test_method(runner)
        
        # VFS integration tests (CRITICAL)
        test_5_vfs_staged_changes(runner)
        test_6_vfs_new_file(runner)
        test_7_vfs_deleted_file(runner)
        
        # Error handling tests
        test_8_timeout_handling(runner)
        test_9_import_error_handling(runner)
        test_10_nonexistent_test_file(runner)
        
        # Integration tests
        test_11_feedback_handler_integration(runner)
        test_12_feedback_loop_integration(runner)
        test_13_tool_executor_integration(runner)
        test_14_orchestrator_tool_usage_stats(runner)
        
    finally:
        runner.cleanup()
    
    console.print("\n")
    success = runner.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())