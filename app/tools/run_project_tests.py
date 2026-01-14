# app/tools/run_project_tests.py
"""
Run Project Tests Tool - Execute unittest tests for analysis.

This tool allows the AI agent to run Python unittest tests against
the VirtualFileSystem, enabling test-driven development workflow.

Features:
- Runs tests on VirtualFileSystem (staged changes, not real files)
- Supports file-level or chunk-level (class/function) testing
- Limited to 5 calls per session (tracked externally)
- Timeout protection (default 30 sec, max 60 sec)
- Output limited to 2000 chars
- Returns structured XML result for Orchestrator parsing

Integration with VirtualFileSystem:
- Uses VFS.read_file() for automatic overlay of staged changes
- Respects file deletions (is_deletion flag)
- Includes NEW files from staging that don't exist on disk yet
- Falls back to real files when VFS is not provided

Usage Example:
    from app.tools.run_project_tests import run_project_tests
    from app.services.virtual_fs import VirtualFileSystem
    
    vfs = VirtualFileSystem(project_dir)
    vfs.stage_change("myapp/calculator.py", new_code)
    
    result = run_project_tests(
        project_dir=project_dir,
        test_path="tests/test_calculator.py",
        virtual_fs=vfs,
        chunk_name="TestAdd",  # Optional: run only this class
        timeout_sec=30
    )
    # result is XML string with test results
"""

from __future__ import annotations

import subprocess
import tempfile
import shutil
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from app.services.virtual_fs import VirtualFileSystem

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

# Maximum number of test runs allowed per session
# This is tracked externally by ToolUsageStats in the orchestrator
MAX_TEST_RUNS_PER_SESSION = 5

# Default timeout for test execution (seconds)
DEFAULT_TIMEOUT_SEC = 30

# Maximum allowed timeout (cannot exceed this even if requested)
MAX_TIMEOUT_SEC = 60

# Maximum characters in test output (truncated if exceeded)
MAX_OUTPUT_CHARS = 2000

# Directories to skip when copying project files to temp environment
# These are typically generated files, caches, or virtual environments
SKIP_DIRS = {
    # Python caches
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.tox',
    
    # Version control
    '.git',
    '.svn',
    '.hg',
    
    # Virtual environments
    'venv',
    '.venv',
    'env',
    'node_modules',
    
    # IDE and editor
    '.idea',
    '.vscode',
    
    # Build artifacts
    'build',
    'dist',
    '*.egg-info',
    
    # Our agent's working directory
    '.ai-agent',
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class TestResult:
    """
    Structured test execution result.
    
    This dataclass holds all information about a test run,
    including success status, counts, output, and failure details.
    
    Attributes:
        success: True if all tests passed (no failures, no errors)
        tests_run: Total number of tests executed
        tests_passed: Number of tests that passed
        tests_failed: Number of tests that failed (assertion errors)
        tests_errors: Number of tests with errors (exceptions)
        test_output: Raw output from unittest (truncated to MAX_OUTPUT_CHARS)
        failed_tests: List of dicts with details about each failed test
                      Each dict has: type, name, traceback
        execution_time_ms: Total execution time in milliseconds
        error_message: System-level error (timeout, import error, etc.)
                       None if tests ran successfully (even if they failed)
    """
    success: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    tests_errors: int
    test_output: str
    failed_tests: List[Dict[str, str]]
    execution_time_ms: float
    error_message: Optional[str] = None


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def run_project_tests(
    project_dir: str,
    test_path: str,
    virtual_fs: Optional['VirtualFileSystem'] = None,
    chunk_name: Optional[str] = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """
    Run unittest tests for analysis purposes.
    
    This is the main entry point for the test runner tool. It creates
    an isolated test environment, copies files (with VFS overlay if provided),
    executes unittest in a subprocess, and returns XML-formatted results.
    
    Tests are executed on VirtualFileSystem staged files, NOT real files.
    This allows testing code changes before committing them to disk.
    """
    import time
    start_time = time.time()
    
    # Enforce maximum timeout limit for safety
    timeout_sec = min(timeout_sec, MAX_TIMEOUT_SEC)
    
    try:
        # Input validation
        if not test_path:
            return _format_error("test_path is required")
        
        if not test_path.endswith('.py'):
            return _format_error(
                "test_path must be a .py file: " + test_path
            )
        
        # Get Python interpreter from project's venv
        python_path = None
        if virtual_fs is not None:
            try:
                python_path = virtual_fs.get_project_python()
            except Exception as e:
                logger.warning("Could not get project Python: %s", e)
                python_path = sys.executable
        else:
            python_path = sys.executable
        
        # Create temporary directory for test execution
        with tempfile.TemporaryDirectory(prefix='test_') as temp_dir:
            logger.info(
                "Created temp directory: %s (project_dir=%s, test_path=%s)",
                temp_dir, project_dir, test_path
            )
            
            # Copy project files to temp directory
            if virtual_fs is not None:
                # Use VirtualFileSystem to materialize files
                try:
                    materialized_files = virtual_fs.materialize_to_directory(temp_dir)
                    logger.info("Materialized %d files from VFS", len(materialized_files))
                except Exception as e:
                    logger.error("Failed to materialize VFS: %s", e, exc_info=True)
                    return _format_error(f"Failed to materialize project files: {e}")
            else:
                # Copy from real filesystem
                try:
                    _copy_project_files(project_dir, temp_dir)
                    logger.info("Copied project files from %s", project_dir)
                except Exception as e:
                    logger.error("Failed to copy project files: %s", e, exc_info=True)
                    return _format_error(f"Failed to copy project files: {e}")
            
            # Verify test file exists
            test_file_path = Path(temp_dir) / test_path
            if not test_file_path.exists():
                return _format_error(
                    f"Test file not found: {test_path} (in {temp_dir})"
                )
            
            # Convert file path to module name
            # e.g., "tests/test_module.py" -> "tests.test_module"
            module_name = test_path[:-3].replace('/', '.').replace('\\', '.')
            
            # Build test target
            if chunk_name:
                test_target = f"{module_name}.{chunk_name}"
            else:
                test_target = module_name
            
            logger.info("Test target: %s", test_target)
            
            # Execute unittest with project's Python interpreter
            result = _execute_unittest(
                temp_dir=temp_dir,
                test_target=test_target,
                timeout_sec=timeout_sec,
                python_path=python_path,
            )
            
            # Add execution metadata
            result.execution_time_ms = (time.time() - start_time) * 1000
            result.chunk_name = chunk_name
            
            logger.info(
                "Test execution complete: success=%s, tests_run=%d, "
                "tests_passed=%d, tests_failed=%d, tests_errors=%d, "
                "execution_time=%.0fms",
                result.success,
                result.tests_run,
                result.tests_passed,
                result.tests_failed,
                result.tests_errors,
                result.execution_time_ms,
            )
            
            # Format and return result
            return _format_result(result)
    
    except Exception as e:
        logger.error("Unexpected error in run_project_tests: %s", e, exc_info=True)
        return _format_error(f"Unexpected error: {e}")

# ============================================================================
# TEST ENVIRONMENT MANAGEMENT
# ============================================================================

class _TestEnvironmentContext:
    """
    Context manager for isolated test environment.
    
    Creates a temporary directory that is automatically cleaned up
    when the context exits, even if an exception occurs.
    
    Usage:
        with _TestEnvironmentContext(temp_dir) as path:
            # temp_dir exists and contains project files
            run_tests(path)
        # temp_dir is deleted here
    """
    
    def __init__(self, temp_dir: str):
        """
        Initialize with path to temporary directory.
        
        Args:
            temp_dir: Path to the temporary directory (already created)
        """
        self.temp_dir = temp_dir
    
    def __enter__(self) -> str:
        """Return the temp directory path."""
        return self.temp_dir
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Clean up the temporary directory.
        
        This runs even if an exception occurred in the with block.
        Errors during cleanup are logged but don't raise.
        """
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Failed to cleanup temp dir: %s", e)
        # Return False to propagate any exception from the with block
        return False


def _create_test_environment(
    project_dir: str,
    virtual_fs: Optional['VirtualFileSystem'] = None,
) -> _TestEnvironmentContext:
    """
    Create isolated temporary directory with project files.
    
    This function creates a complete copy of the project's Python files
    in a temporary directory. If a VirtualFileSystem is provided, its
    staged changes are overlaid on top of the real files.
    
    The isolation ensures:
    1. Tests don't modify the real project files
    2. Staged changes can be tested without committing
    3. Multiple test runs don't interfere with each other
    
    Args:
        project_dir: Path to the project root directory
        virtual_fs: Optional VirtualFileSystem with staged changes
    
    Returns:
        Context manager that yields the temp directory path
        and cleans up on exit
    
    Raises:
        RuntimeError: If temp directory creation fails
    """
    # Create temporary directory with recognizable prefix
    temp_dir = tempfile.mkdtemp(prefix="ai_test_")
    
    try:
        if virtual_fs is not None and hasattr(virtual_fs, 'read_file'):
            # VFS is available - use it for overlay
            _copy_from_vfs(virtual_fs, project_dir, temp_dir)
        else:
            # No VFS - copy real files only (fallback mode)
            _copy_project_files(project_dir, temp_dir)
        
        return _TestEnvironmentContext(temp_dir)
        
    except Exception as e:
        # Clean up on failure
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("Failed to create test environment: " + str(e))


def _copy_from_vfs(
    virtual_fs: 'VirtualFileSystem',
    project_dir: str,
    temp_dir: str
) -> None:
    """
    Copy files from VirtualFileSystem to temp directory.
    
    This function implements the VFS overlay logic:
    1. Get list of all Python files (from VFS or by scanning)
    2. Add any NEW files that exist only in VFS staging
    3. For each file:
       - Skip if marked for deletion in VFS
       - Read content via VFS (automatically returns staged version if exists)
       - Write to temp directory
    
    The key insight is that VFS.read_file() automatically handles the overlay:
    - If file has staged changes -> returns staged content
    - If file has no staged changes -> returns content from disk
    - If file is marked deleted -> returns None
    
    Args:
        virtual_fs: VirtualFileSystem instance
        project_dir: Path to project root (for reference)
        temp_dir: Path to temporary directory (destination)
    """
    project_path = Path(project_dir)
    temp_path = Path(temp_dir)
    
    # ----------------------------------------------------------------
    # Step 1: Collect all files to process
    # ----------------------------------------------------------------
    files_to_copy: Set[str] = set()
    
    # Get existing Python files from project
    if hasattr(virtual_fs, 'get_all_python_files'):
        try:
            existing_files = virtual_fs.get_all_python_files()
            files_to_copy.update(existing_files)
            logger.debug("VFS: Found %d existing Python files", len(existing_files))
        except Exception as e:
            logger.warning("Failed to get Python files from VFS: %s", e)
            # Fallback to manual scan
            files_to_copy.update(_scan_python_files(project_path))
    else:
        # VFS doesn't have get_all_python_files method, scan manually
        files_to_copy.update(_scan_python_files(project_path))
    
    # ----------------------------------------------------------------
    # Step 2: Add staged files (includes NEW files not on disk)
    # ----------------------------------------------------------------
    staged_paths: List[str] = []
    if hasattr(virtual_fs, 'get_staged_files'):
        try:
            staged_paths = virtual_fs.get_staged_files()
            # Add all staged paths - they might be new files
            files_to_copy.update(staged_paths)
            logger.debug("VFS: Found %d staged files", len(staged_paths))
        except Exception as e:
            logger.warning("Failed to get staged files from VFS: %s", e)
    
    # ----------------------------------------------------------------
    # Step 3: Process each file
    # ----------------------------------------------------------------
    copied_count = 0
    skipped_deleted = 0
    skipped_none = 0
    
    for rel_path in files_to_copy:
        # Check if file is marked for deletion
        if hasattr(virtual_fs, 'get_change'):
            try:
                change = virtual_fs.get_change(rel_path)
                if change is not None:
                    # Check is_deletion property
                    if hasattr(change, 'is_deletion') and change.is_deletion:
                        logger.debug("Skipping deleted file: %s", rel_path)
                        skipped_deleted += 1
                        continue
            except Exception as e:
                # If we can't check, proceed with copying
                logger.debug("Could not check change for %s: %s", rel_path, e)
        
        # Read content using VFS (handles overlay automatically)
        try:
            content = virtual_fs.read_file(rel_path)
        except Exception as e:
            logger.debug("Could not read %s from VFS: %s", rel_path, e)
            content = None
        
        # Skip if file doesn't exist or has no content
        if content is None:
            logger.debug("Skipping non-existent/empty file: %s", rel_path)
            skipped_none += 1
            continue
        
        # Write to temp directory
        dest_file = temp_path / rel_path
        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(content, encoding='utf-8')
            copied_count += 1
        except Exception as e:
            logger.warning("Failed to write %s to temp: %s", rel_path, e)
    
    logger.info(
        "Test environment created: %d files copied, "
        "%d deleted, %d skipped (VFS overlay applied)",
        copied_count, skipped_deleted, skipped_none
    )


def _scan_python_files(project_path: Path) -> Set[str]:
    """
    Scan project directory for Python files.
    
    This is a fallback when VFS.get_all_python_files() is not available.
    It recursively finds all .py files while respecting SKIP_DIRS.
    
    Args:
        project_path: Path to project root
    
    Returns:
        Set of relative paths to .py files (using forward slashes)
    """
    files: Set[str] = set()
    
    for py_file in project_path.rglob('*.py'):
        try:
            rel_path = py_file.relative_to(project_path)
            
            # Skip excluded directories
            if _should_skip_path(rel_path):
                continue
            
            # Normalize path separators (always use forward slash)
            files.add(str(rel_path).replace('\\', '/'))
            
        except ValueError:
            # File is not relative to project_path (shouldn't happen)
            continue
    
    return files


def _should_skip_path(rel_path: Path) -> bool:
    """
    Check if path should be skipped based on SKIP_DIRS patterns.
    
    A path is skipped if any of its components:
    - Starts with a dot (hidden files/directories)
    - Matches an entry in SKIP_DIRS
    - Matches a wildcard pattern in SKIP_DIRS (e.g., "*.egg-info")
    
    Args:
        rel_path: Relative path to check
    
    Returns:
        True if path should be skipped, False otherwise
    """
    parts = rel_path.parts
    
    for part in parts:
        # Skip hidden directories/files (start with .)
        if part.startswith('.'):
            return True
        
        # Skip known directories (exact match)
        if part in SKIP_DIRS:
            return True
        
        # Check wildcard patterns (e.g., "*.egg-info")
        for pattern in SKIP_DIRS:
            if '*' in pattern:
                import fnmatch
                if fnmatch.fnmatch(part, pattern):
                    return True
    
    return False


def _copy_project_files(project_dir: str, temp_dir: str) -> None:
    """
    Copy Python files from project to temp directory (without VFS).
    
    This is the fallback when VirtualFileSystem is not available.
    It simply copies all .py files from the project to temp.
    
    Args:
        project_dir: Path to project root (source)
        temp_dir: Path to temporary directory (destination)
    """
    project_path = Path(project_dir)
    temp_path = Path(temp_dir)
    
    copied_count = 0
    
    for src_file in project_path.rglob('*.py'):
        try:
            rel_path = src_file.relative_to(project_path)
            
            # Skip excluded directories
            if _should_skip_path(rel_path):
                continue
            
            # Copy file to temp
            dest_file = temp_path / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest_file)
            copied_count += 1
            
        except Exception as e:
            logger.debug("Failed to copy %s: %s", src_file, e)
    
    logger.info("Test environment created: %d files copied (no VFS)", copied_count)


# ============================================================================
# TEST EXECUTION
# ============================================================================

def _path_to_module(test_path: str) -> str:
    """
    Convert file path to Python module path.
    
    Examples:
        "tests/test_auth.py" -> "tests.test_auth"
        "tests/unit/test_models.py" -> "tests.unit.test_models"
        "test_simple.py" -> "test_simple"
    
    Args:
        test_path: Path to Python file (with .py extension)
    
    Returns:
        Module path suitable for unittest discovery
    """
    # Replace path separators with dots
    module = test_path.replace('/', '.').replace('\\', '.')
    
    # Remove .py extension
    if module.endswith('.py'):
        module = module[:-3]
    
    return module


def _execute_unittest(
    temp_dir: str,
    test_target: str,
    timeout_sec: int,
    python_path: Optional[str] = None,
) -> TestResult:
    """
    Execute unittest in subprocess.
    
    Runs Python unittest module in a subprocess with:
    - PYTHONPATH set to temp_dir (so imports work)
    - Verbose output (-v flag) for better parsing
    - Timeout protection
    - Output capture (stdout + stderr)
    
    Args:
        temp_dir: Temporary directory with project files
        test_target: Test specification (module, class, or method)
        timeout_sec: Maximum execution time in seconds
        python_path: Path to Python interpreter (defaults to sys.executable)
    
    Returns:
        TestResult with parsed output
    """
    # Use provided python_path or fall back to sys.executable
    if python_path is None:
        python_path = sys.executable
    
    # Build command
    cmd = [
        python_path, '-m', 'unittest',
        test_target,
        '-v',  # Verbose output for better parsing
    ]
    
    logger.info("Running: %s in %s", ' '.join(cmd), temp_dir)
    
    try:
        # Set up environment
        env = os.environ.copy()
        env['PYTHONPATH'] = temp_dir  # Allow imports from temp dir
        env['PYTHONDONTWRITEBYTECODE'] = '1'  # Don't create __pycache__
        
        # Run in subprocess with timeout
        result = subprocess.run(
            cmd,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_sec,
            env=env,
        )
        
        # unittest writes to stderr, combine with stdout
        output = result.stdout + result.stderr
        
        logger.debug("Test output (%d chars): %s...", len(output), output[:500])
        
        # Parse results
        return _parse_unittest_output(output, result.returncode)
        
    except subprocess.TimeoutExpired:
        logger.warning("Test execution timed out after %ds", timeout_sec)
        return TestResult(
            success=False,
            tests_run=0,
            tests_passed=0,
            tests_failed=0,
            tests_errors=0,
            test_output="",
            failed_tests=[],
            execution_time_ms=timeout_sec * 1000,
            error_message=(
                "Test execution timed out after " + str(timeout_sec) + " seconds. "
                "Consider testing smaller chunks or increasing timeout."
            ),
        )
        
    except FileNotFoundError:
        logger.error("Python interpreter not found: %s", python_path)
        return TestResult(
            success=False,
            tests_run=0,
            tests_passed=0,
            tests_failed=0,
            tests_errors=0,
            test_output="",
            failed_tests=[],
            execution_time_ms=0,
            error_message=f"Python interpreter not found: {python_path}",
        )
        
    except Exception as e:
        logger.error("Test execution error: %s", e, exc_info=True)
        return TestResult(
            success=False,
            tests_run=0,
            tests_passed=0,
            tests_failed=0,
            tests_errors=0,
            test_output="",
            failed_tests=[],
            execution_time_ms=0,
            error_message="Unexpected error: " + str(e),
        )


def _parse_unittest_output(output: str, return_code: int) -> TestResult:
    """
    Parse unittest verbose output into structured result.
    
    Extracts from unittest output:
    - Number of tests run
    - Pass/fail/error counts
    - Details of failed tests (name, traceback)
    - Detects import and syntax errors
    
    Example unittest output:
        test_add_positive (tests.test_calc.TestAdd) ... ok
        test_add_negative (tests.test_calc.TestAdd) ... ok
        test_multiply (tests.test_calc.TestMultiply) ... FAIL
        
        ======================================================================
        FAIL: test_multiply (tests.test_calc.TestMultiply)
        ----------------------------------------------------------------------
        Traceback (most recent call last):
          File "...", line 15, in test_multiply
            self.assertEqual(result, 12)
        AssertionError: 7 != 12
        
        ----------------------------------------------------------------------
        Ran 3 tests in 0.001s
        
        FAILED (failures=1)
    
    Args:
        output: Combined stdout + stderr from unittest
        return_code: Process return code (0 = success)
    
    Returns:
        TestResult with parsed data
    """
    # Default values
    tests_run = 0
    tests_passed = 0
    tests_failed = 0
    tests_errors = 0
    failed_tests: List[Dict[str, str]] = []
    
    # ----------------------------------------------------------------
    # Parse summary line: "Ran X tests in Y.YYYs"
    # ----------------------------------------------------------------
    ran_match = re.search(r'Ran (\d+) tests? in', output)
    if ran_match:
        tests_run = int(ran_match.group(1))
    
    # ----------------------------------------------------------------
    # Parse result status
    # ----------------------------------------------------------------
    if 'OK' in output and return_code == 0:
        # All tests passed
        tests_passed = tests_run
    else:
        # Some tests failed or errored
        
        # Parse failures count from "FAILED (failures=X)"
        fail_match = re.search(r'failures=(\d+)', output)
        if fail_match:
            tests_failed = int(fail_match.group(1))
        
        # Parse errors count from "FAILED (errors=X)" or "FAILED (failures=X, errors=Y)"
        error_match = re.search(r'errors=(\d+)', output)
        if error_match:
            tests_errors = int(error_match.group(1))
        
        # Calculate passed tests
        tests_passed = max(0, tests_run - tests_failed - tests_errors)
    
    # ----------------------------------------------------------------
    # Extract failed test details
    # ----------------------------------------------------------------
    failed_tests = _extract_failed_tests(output)
    
    # ----------------------------------------------------------------
    # Check for system-level errors (tests didn't run at all)
    # ----------------------------------------------------------------
    error_message = None
    if tests_run == 0 and return_code != 0:
        # Tests failed to run - try to identify why
        
        if 'ModuleNotFoundError' in output:
            # Missing import
            match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", output)
            if match:
                error_message = "Import error: Module '" + match.group(1) + "' not found"
            else:
                error_message = "Import error: Module not found"
                
        elif 'ImportError' in output:
            # Import failed for other reason
            match = re.search(r'ImportError: ([^\n]+)', output)
            if match:
                error_message = "Import error: " + match.group(1)
            else:
                error_message = "Import error occurred"
                
        elif 'SyntaxError' in output:
            # Syntax error in code
            error_message = "Syntax error in test file or dependencies"
            
        elif 'AttributeError' in output:
            # Attribute error (often from bad imports or missing methods)
            match = re.search(r'AttributeError: ([^\n]+)', output)
            if match:
                error_message = "Attribute error: " + match.group(1)
            else:
                error_message = "Attribute error occurred"
                
        else:
            # Unknown error
            error_message = "Tests failed to run. Check the output for details."
    
    # ----------------------------------------------------------------
    # Truncate output if too long
    # ----------------------------------------------------------------
    truncated_output = output[:MAX_OUTPUT_CHARS]
    if len(output) > MAX_OUTPUT_CHARS:
        truncated_output += (
            "\n\n... [truncated, " + 
            str(len(output) - MAX_OUTPUT_CHARS) + 
            " chars omitted]"
        )
    
    return TestResult(
        success=(return_code == 0 and tests_failed == 0 and tests_errors == 0),
        tests_run=tests_run,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        tests_errors=tests_errors,
        test_output=truncated_output,
        failed_tests=failed_tests,
        execution_time_ms=0,  # Will be set by caller
        error_message=error_message,
    )


def _extract_failed_tests(output: str) -> List[Dict[str, str]]:
    """
    Extract details of failed tests from unittest output.
    
    Parses the FAIL and ERROR blocks in unittest verbose output
    to extract test name and traceback for each failure.
    
    Example block format:
        ======================================================================
        FAIL: test_multiply (tests.test_calc.TestMultiply)
        ----------------------------------------------------------------------
        Traceback (most recent call last):
          File "...", line 15, in test_multiply
            self.assertEqual(result, 12)
        AssertionError: 7 != 12
    
    Args:
        output: Unittest output string
    
    Returns:
        List of dicts, each with:
        - type: "fail" or "error"
        - name: Full test name (e.g., "tests.test_calc.TestMultiply.test_multiply")
        - traceback: Truncated traceback string
    """
    failed_tests: List[Dict[str, str]] = []
    
    # Pattern for FAIL or ERROR blocks in unittest verbose output
    # Matches the header and captures everything until the next separator
    fail_pattern = re.compile(
        r'={70}\n'                     # Line of 70 equals signs
        r'(FAIL|ERROR): (\w+) \(([^)]+)\)\n'  # FAIL: test_name (module.Class)
        r'-{70}\n'                     # Line of 70 dashes
        r'(.*?)'                       # Traceback content (non-greedy)
        r'(?=\n={70}|\n-{70}|\nRan \d+|$)',   # Until next separator or end
        re.DOTALL
    )
    
    for match in fail_pattern.finditer(output):
        fail_type = match.group(1).lower()  # "fail" or "error"
        test_name = match.group(2)           # e.g., "test_multiply"
        test_class = match.group(3)          # e.g., "tests.test_calc.TestMultiply"
        traceback = match.group(4).strip()
        
        # Truncate long tracebacks
        max_tb_len = 500
        if len(traceback) > max_tb_len:
            traceback = traceback[:max_tb_len] + "\n... [truncated]"
        
        failed_tests.append({
            "type": fail_type,
            "name": test_class + "." + test_name,
            "traceback": traceback,
        })
    
    # Alternative pattern for simpler error format (some Python versions)
    if not failed_tests:
        simple_pattern = re.compile(
            r'(FAIL|ERROR): ([\w.]+)\n'
            r'-+\n'
            r'(.*?)'
            r'(?=\n[A-Z]+:|\nRan \d+|$)',
            re.DOTALL
        )
        
        for match in simple_pattern.finditer(output):
            traceback = match.group(3).strip()
            if len(traceback) > 500:
                traceback = traceback[:500] + "\n... [truncated]"
            
            failed_tests.append({
                "type": match.group(1).lower(),
                "name": match.group(2),
                "traceback": traceback,
            })
    
    return failed_tests


# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def _format_result_xml(
    result: TestResult,
    test_path: str,
    chunk_name: Optional[str],
) -> str:
    """
    Format test result as XML for Orchestrator.
    
    Creates structured XML that can be easily parsed by the Orchestrator
    to understand test results and take appropriate action.
    
    Args:
        result: TestResult object with all test data
        test_path: Original test file path (for reference)
        chunk_name: Test class/method name if specified (for reference)
    
    Returns:
        XML-formatted string with test results
    """
    status = "PASSED" if result.success else "FAILED"
    
    # ----------------------------------------------------------------
    # Build failed tests XML section
    # ----------------------------------------------------------------
    failed_xml = ""
    if result.failed_tests:
        failed_items = []
        for ft in result.failed_tests:
            # Escape CDATA end sequence in traceback
            traceback = ft['traceback'].replace(']]>', ']]&gt;')
            item = (
                '    <failed_test>\n'
                '      <type>' + ft['type'] + '</type>\n'
                '      <name>' + _escape_xml(ft['name']) + '</name>\n'
                '      <traceback><![CDATA[' + traceback + ']]></traceback>\n'
                '    </failed_test>'
            )
            failed_items.append(item)
        failed_xml = '\n  <failed_tests>\n' + '\n'.join(failed_items) + '\n  </failed_tests>'
    
    # ----------------------------------------------------------------
    # Build error message XML section
    # ----------------------------------------------------------------
    error_xml = ""
    if result.error_message:
        error_xml = '\n  <system_error><![CDATA[' + result.error_message + ']]></system_error>'
    
    # ----------------------------------------------------------------
    # Determine test target description
    # ----------------------------------------------------------------
    target = chunk_name if chunk_name else "entire file"
    
    # ----------------------------------------------------------------
    # Escape output for CDATA
    # ----------------------------------------------------------------
    output = result.test_output.replace(']]>', ']]&gt;')
    
    # ----------------------------------------------------------------
    # Build complete XML
    # ----------------------------------------------------------------
    xml = (
        '<!-- TEST_RESULT -->\n'
        '<test_result>\n'
        '  <status>' + status + '</status>\n'
        '  <test_file>' + _escape_xml(test_path) + '</test_file>\n'
        '  <test_target>' + _escape_xml(target) + '</test_target>\n'
        '  <summary>\n'
        '    <tests_run>' + str(result.tests_run) + '</tests_run>\n'
        '    <tests_passed>' + str(result.tests_passed) + '</tests_passed>\n'
        '    <tests_failed>' + str(result.tests_failed) + '</tests_failed>\n'
        '    <tests_errors>' + str(result.tests_errors) + '</tests_errors>\n'
        '    <execution_time_ms>' + str(int(result.execution_time_ms)) + '</execution_time_ms>\n'
        '  </summary>' + failed_xml + error_xml + '\n'
        '  <output><![CDATA[' + output + ']]></output>\n'
        '</test_result>'
    )
    
    return xml


def _format_error(message: str) -> str:
    """
    Format system error as XML.
    
    Used for errors that occur before tests can run,
    such as missing test file or invalid arguments.
    
    Args:
        message: Error message to include
    
    Returns:
        XML-formatted error string
    """
    return (
        '<!-- TEST_ERROR -->\n'
        '<test_result>\n'
        '  <status>ERROR</status>\n'
        '  <system_error><![CDATA[' + message + ']]></system_error>\n'
        '</test_result>'
    )


def _escape_xml(text: str) -> str:
    """
    Escape special XML characters.
    
    Replaces characters that have special meaning in XML
    with their entity references.
    
    Args:
        text: String to escape
    
    Returns:
        Escaped string safe for XML content
    """
    return (
        text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )


# ============================================================================
# RESULT PARSING (for feedback_handler integration)
# ============================================================================

def parse_test_result_xml(xml_result: str) -> Optional[TestResult]:
    """
    Parse XML test result back into TestResult object.
    
    This function is the inverse of _format_result_xml. It parses
    the XML output from run_project_tests and creates a TestResult
    object that can be used by FeedbackHandler.
    
    Used by:
    - feedback_handler.create_test_run_feedback_from_xml()
    - Any code that needs programmatic access to test results
    
    Args:
        xml_result: XML string from run_project_tests()
    
    Returns:
        TestResult object, or None if parsing fails
    
    Example:
        xml = run_project_tests(project_dir, test_path, vfs)
        result = parse_test_result_xml(xml)
        if result and not result.success:
            print(f"Failed tests: {result.tests_failed}")
            for ft in result.failed_tests:
                print(f"  - {ft['name']}: {ft['type']}")
    """
    try:
        # ----------------------------------------------------------------
        # Check if it's an error response
        # ----------------------------------------------------------------
        if '<!-- TEST_ERROR -->' in xml_result:
            error_match = re.search(
                r'<system_error><!\[CDATA\[(.*?)\]\]></system_error>',
                xml_result,
                re.DOTALL
            )
            return TestResult(
                success=False,
                tests_run=0,
                tests_passed=0,
                tests_failed=0,
                tests_errors=0,
                test_output="",
                failed_tests=[],
                execution_time_ms=0,
                error_message=error_match.group(1) if error_match else "Unknown error",
            )
        
        # ----------------------------------------------------------------
        # Extract status
        # ----------------------------------------------------------------
        status_match = re.search(r'<status>(\w+)</status>', xml_result)
        if not status_match:
            return None
        status = status_match.group(1)
        
        # ----------------------------------------------------------------
        # Helper functions for extraction
        # ----------------------------------------------------------------
        def extract_int(tag: str) -> int:
            """Extract integer value from XML tag."""
            pattern = '<' + tag + r'>(\d+)</' + tag + '>'
            match = re.search(pattern, xml_result)
            return int(match.group(1)) if match else 0
        
        def extract_float(tag: str) -> float:
            """Extract float value from XML tag."""
            pattern = '<' + tag + r'>([\d.]+)</' + tag + '>'
            match = re.search(pattern, xml_result)
            return float(match.group(1)) if match else 0.0
        
        # ----------------------------------------------------------------
        # Extract summary values
        # ----------------------------------------------------------------
        tests_run = extract_int('tests_run')
        tests_passed = extract_int('tests_passed')
        tests_failed = extract_int('tests_failed')
        tests_errors = extract_int('tests_errors')
        execution_time = extract_float('execution_time_ms')
        
        # ----------------------------------------------------------------
        # Extract output
        # ----------------------------------------------------------------
        output_match = re.search(
            r'<output><!\[CDATA\[(.*?)\]\]></output>',
            xml_result,
            re.DOTALL
        )
        output = output_match.group(1) if output_match else ""
        
        # ----------------------------------------------------------------
        # Extract system error (if any)
        # ----------------------------------------------------------------
        error_match = re.search(
            r'<system_error><!\[CDATA\[(.*?)\]\]></system_error>',
            xml_result,
            re.DOTALL
        )
        error_message = error_match.group(1) if error_match else None
        
        # ----------------------------------------------------------------
        # Extract failed tests
        # ----------------------------------------------------------------
        failed_tests: List[Dict[str, str]] = []
        failed_pattern = re.compile(
            r'<failed_test>.*?'
            r'<type>(\w+)</type>.*?'
            r'<name>([^<]+)</name>.*?'
            r'<traceback><!\[CDATA\[(.*?)\]\]></traceback>.*?'
            r'</failed_test>',
            re.DOTALL
        )
        for match in failed_pattern.finditer(xml_result):
            failed_tests.append({
                "type": match.group(1),
                "name": match.group(2),
                "traceback": match.group(3),
            })
        
        # ----------------------------------------------------------------
        # Build and return TestResult
        # ----------------------------------------------------------------
        return TestResult(
            success=(status == "PASSED"),
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_errors=tests_errors,
            test_output=output,
            failed_tests=failed_tests,
            execution_time_ms=execution_time,
            error_message=error_message,
        )
        
    except Exception as e:
        logger.error("Failed to parse test result XML: %s", e)
        return None

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_test_limit_error(
    current_count: int, 
    max_count: int = MAX_TEST_RUNS_PER_SESSION
) -> str:
    """
    Format error message when test run limit is reached.
    
    This function is called by the orchestrator when the agent
    tries to run tests but has already used all allowed runs.
    
    The message guides the agent to:
    1. Use existing test results
    2. Focus on fixing identified issues
    3. Be more targeted in future sessions
    
    Args:
        current_count: Number of test runs already used
        max_count: Maximum allowed test runs per session
    
    Returns:
        XML-formatted error message
    
    Example:
        if not tool_usage_stats.can_use_tool("run_project_tests"):
            return format_test_limit_error(
                tool_usage_stats.get_usage("run_project_tests"),
                MAX_TEST_RUNS_PER_SESSION
            )
    """
    # Build message as list of lines (avoids f-string issues)
    message_lines = [
        "Test run limit reached. You have used all " + str(max_count) + " allowed test runs for this session.",
        "Test runs used: " + str(current_count) + "/" + str(max_count),
        "",
        "Please complete your analysis using the test results you already have.",
        "Focus on fixing the identified issues before requesting more tests.",
        "",
        "Tips:",
        "- Review the failed test outputs carefully",
        "- Check if similar issues exist in related code",
        "- Consider running more targeted tests (specific class/method) on next session",
    ]
    message = "\n".join(message_lines)
    
    return (
        '<!-- TEST_LIMIT_ERROR -->\n'
        '<test_result>\n'
        '  <status>LIMIT_REACHED</status>\n'
        '  <system_error><![CDATA[' + message + ']]></system_error>\n'
        '</test_result>'
    )


def get_test_runs_remaining(current_count: int) -> int:
    """
    Calculate remaining test runs for the session.
    
    Helper function to check how many test runs are left.
    
    Args:
        current_count: Number of test runs already used
    
    Returns:
        Number of remaining test runs (0 or positive)
    """
    return max(0, MAX_TEST_RUNS_PER_SESSION - current_count)


def can_run_more_tests(current_count: int) -> bool:
    """
    Check if more test runs are allowed.
    
    Helper function to check if the limit has been reached.
    
    Args:
        current_count: Number of test runs already used
    
    Returns:
        True if more tests can be run, False if limit reached
    """
    return current_count < MAX_TEST_RUNS_PER_SESSION