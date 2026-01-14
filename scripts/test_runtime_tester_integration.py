# tests/test_runtime_tester_comprehensive.py
"""
Comprehensive test suite for RuntimeTester.

Tests ALL edge cases and integration points.

NOTE: Some tests (infinite loop, input blocking) will wait for actual timeouts.
Expected total runtime: ~6-8 minutes.
"""

import os
import sys
import asyncio
import tempfile
import shutil
import socket
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch, MagicMock
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.virtual_fs import VirtualFileSystem, ChangeType
from app.services.runtime_tester import (
    RuntimeTester,
    RuntimeTestSummary,
    TestResult,
    TestStatus,
    AppType,
    ProjectAnalysis,
    TimeoutCalculator,
    SizeCategory,
    FRAMEWORK_PATTERNS,
)
from app.services.change_validator import (
    ChangeValidator,
    ValidationResult,
    ValidationLevel,
    ValidatorConfig,
    IssueSeverity,
)
from app.agents.feedback_handler import (
    FeedbackHandler,
    create_runtime_test_feedback,
    RuntimeTestFeedback,
)


# ============================================================================
# TEST FILES
# ============================================================================

STANDARD_OK = '''
def main():
    print("OK")
if __name__ == "__main__":
    main()
'''

STANDARD_ERROR = '''
def main():
    return 1 / 0
if __name__ == "__main__":
    main()
'''

STANDARD_INFINITE = '''
while True:
    pass
'''

EMPTY_FILE = ''

COMMENTS_ONLY = '''
# This file has only comments
# Nothing executable here
"""
Docstring only
"""
'''

FILE_WITH_INPUT = '''
def main():
    name = input("Enter name: ")  # This will block!
    print(f"Hello, {name}")
if __name__ == "__main__":
    main()
'''

RELATIVE_IMPORT_FILE = '''
from . import sibling_module  # Relative import - will fail standalone
def main():
    pass
'''

NEAR_TIMEOUT = '''
import time
time.sleep(2)  # Should complete well within timeout
print("Near timeout OK")
'''

PYGAME_OK = '''
import pygame
class Game:
    def __init__(self):
        pygame.init()
'''

PYGAME_WITH_DISPLAY = '''
import pygame
class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((800, 600))
'''

TKINTER_OK = '''
import tkinter as tk
class App(tk.Tk):
    pass
'''

SQLITE_OK = '''
import sqlite3
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE test (id INTEGER)")
conn.close()
print("SQLite OK")
'''

SQLITE_FILE_DB = '''
import sqlite3
conn = sqlite3.connect("test_database.db")  # File-based DB
conn.execute("CREATE TABLE users (id INTEGER)")
conn.close()
print("SQLite file DB OK")
'''

SQLITE_WRITE_READ = '''
import sqlite3
conn = sqlite3.connect("test_persist.db")
conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
conn.execute("INSERT INTO items (name) VALUES ('test_item')")
conn.commit()

# Read back
cursor = conn.execute("SELECT name FROM items")
result = cursor.fetchone()
assert result[0] == 'test_item', f"Expected 'test_item', got {result}"
print("Data persisted correctly!")
conn.close()
'''

POSTGRES_CODE = '''
import psycopg2
def get_connection():
    return psycopg2.connect("postgresql://localhost/mydb")
    
class DatabaseManager:
    def __init__(self):
        self.conn = None
    
    def connect(self):
        self.conn = get_connection()
'''

API_SIMPLE = '''
import requests
def fetch():
    return requests.get("https://httpbin.org/get", timeout=5)
if __name__ == "__main__":
    print("API module loaded")  # Don't actually call API in main
'''

API_BAD_URL = '''
import requests
if __name__ == "__main__":
    # This will fail with connection error
    r = requests.get("http://localhost:99999/nonexistent", timeout=2)
    print(r.status_code)
'''

FLASK_APP = '''
from flask import Flask
app = Flask(__name__)
@app.route("/")
def index():
    return "Hello"
'''

FASTAPI_APP = '''
from fastapi import FastAPI
app = FastAPI()
@app.get("/")
def root():
    return {"msg": "Hello"}
'''

CONDITIONAL_IMPORTS = '''
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

def process_data(data):
    if HAS_NUMPY:
        return np.array(data).mean()
    else:
        return sum(data) / len(data)

if __name__ == "__main__":
    result = process_data([1, 2, 3, 4, 5])
    print(f"Result: {result}")
'''

PACKAGE_INIT = '''
from .utils import helper_function
__version__ = "1.0.0"
'''

PACKAGE_UTILS = '''
def helper_function():
    return "Hello from utils"
'''

PACKAGE_MAIN = '''
from mypackage import helper_function
if __name__ == "__main__":
    print(helper_function())
'''


# ============================================================================
# TIMEOUT CONSTANTS FOR TESTS
# ============================================================================

TIMEOUT_STANDARD = 300       # 5 minutes for most tests
TIMEOUT_BLOCKING = 120       # 2 minutes for blocking tests (infinite loop, input)
TIMEOUT_QUICK = 60           # 1 minute for quick tests


# ============================================================================
# TEST CLASS
# ============================================================================

class ComprehensiveRuntimeTest:
    """Comprehensive test suite."""
    
    def __init__(self):
        self.temp_dir: Optional[Path] = None
        self.vfs: Optional[VirtualFileSystem] = None
        self.test_results: Dict[str, Tuple[bool, str]] = {}
        
    def setup(self):
        """Create temp directory."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="runtime_comprehensive_"))
        self.vfs = VirtualFileSystem(str(self.temp_dir))
        print(f"\n📁 Temp dir: {self.temp_dir}")
        print(f"⏱️  Note: Some tests wait for timeouts. Expected total: ~6-8 min\n")
    
    def teardown(self):
        """Cleanup."""
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def create_file(self, name: str, content: str) -> str:
        """Create a test file."""
        self.vfs.stage_change(name, content, ChangeType.CREATE)
        file_path = self.temp_dir / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding='utf-8')
        return name
    
    def record(self, test_name: str, passed: bool, message: str):
        """Record test result."""
        self.test_results[test_name] = (passed, message)
        icon = "✅" if passed else "❌"
        print(f"  {icon} {test_name}: {message}")
    
    # ========================================================================
    # UNIT TESTS
    # ========================================================================
    
    async def test_timeout_calculator(self):
        """Test TimeoutCalculator logic."""
        print("\n🧪 TEST: TimeoutCalculator")
        
        cat, total, per_file = TimeoutCalculator.calculate(5000, 10)
        self.record(
            "small_project_category",
            cat == SizeCategory.SMALL,
            f"5K tokens -> {cat.value} (expected: small)"
        )
        
        cat, total, per_file = TimeoutCalculator.calculate(30000, 50)
        self.record(
            "medium_project_category",
            cat == SizeCategory.MEDIUM,
            f"30K tokens -> {cat.value} (expected: medium)"
        )
        
        cat, total, per_file = TimeoutCalculator.calculate(100000, 200)
        self.record(
            "large_project_category",
            cat == SizeCategory.LARGE,
            f"100K tokens -> {cat.value} (expected: large)"
        )
        
        gui_timeout = TimeoutCalculator.get_timeout_for_type(AppType.GUI)
        std_timeout = TimeoutCalculator.get_timeout_for_type(AppType.STANDARD)
        self.record(
            "gui_shorter_than_standard",
            gui_timeout < std_timeout,
            f"GUI={gui_timeout}s < Standard={std_timeout}s"
        )
    
    async def test_framework_detection(self):
        """Test framework detection."""
        print("\n🧪 TEST: Framework Detection")
        
        tester = RuntimeTester(self.vfs)
        
        fw, app_type = tester._detect_frameworks("import pygame\nclass Game: pass", "game.py")
        self.record(
            "detect_pygame",
            'pygame' in fw and app_type == AppType.GUI,
            f"pygame -> {app_type.value}"
        )
        
        fw, app_type = tester._detect_frameworks("from flask import Flask", "app.py")
        self.record(
            "detect_flask",
            'flask' in fw and app_type == AppType.WEB_APP,
            f"flask -> {app_type.value}"
        )
        
        fw, app_type = tester._detect_frameworks("import sqlite3", "db.py")
        self.record(
            "detect_sqlite",
            'sqlite3' in fw and app_type == AppType.SQL_SQLITE,
            f"sqlite3 -> {app_type.value}"
        )
        
        fw, app_type = tester._detect_frameworks("import psycopg2", "db.py")
        self.record(
            "detect_postgres",
            'psycopg2' in fw and app_type == AppType.SQL_POSTGRES,
            f"psycopg2 -> {app_type.value}"
        )
        
        content = "import sqlite3\nimport requests"
        fw, app_type = tester._detect_frameworks(content, "mixed.py")
        self.record(
            "mixed_priority",
            app_type == AppType.SQL_SQLITE,
            f"sqlite3+requests -> {app_type.value} (expected: sql_sqlite)"
        )
        
        fw, app_type = tester._detect_frameworks("print('hello')", "hello.py")
        self.record(
            "detect_standard",
            app_type == AppType.STANDARD,
            f"no frameworks -> {app_type.value}"
        )
    
    async def test_project_analysis(self):
        """Test project analysis."""
        print("\n🧪 TEST: Project Analysis")
        
        files = [
            self.create_file("std.py", STANDARD_OK),
            self.create_file("game.py", PYGAME_OK),
            self.create_file("db.py", SQLITE_OK),
        ]
        
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        analysis = await tester.analyze_project(files)
        
        self.record(
            "analysis_file_count",
            analysis.file_count == 3,
            f"file_count={analysis.file_count} (expected: 3)"
        )
        
        self.record(
            "analysis_has_tokens",
            analysis.total_tokens > 0,
            f"total_tokens={analysis.total_tokens}"
        )
        
        self.record(
            "analysis_has_frameworks",
            len(analysis.detected_frameworks) >= 2,
            f"frameworks={list(analysis.detected_frameworks.keys())}"
        )
        
        self.record(
            "analysis_categorizes_files",
            len(analysis.file_types[AppType.GUI.value]) == 1,
            f"GUI files={analysis.file_types[AppType.GUI.value]}"
        )
    
    # ========================================================================
    # RUNTIME TESTS - STANDARD
    # ========================================================================
    
    async def test_standard_files(self):
        """Test standard Python file execution."""
        print("\n🧪 TEST: Standard Python Files")
        
        files = [
            ("std_ok.py", STANDARD_OK, TestStatus.PASSED),
            ("std_error.py", STANDARD_ERROR, TestStatus.FAILED),
            ("std_empty.py", EMPTY_FILE, TestStatus.PASSED),
            ("std_comments.py", COMMENTS_ONLY, TestStatus.PASSED),
        ]
        
        for name, content, _ in files:
            self.create_file(name, content)
        
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        summary = await tester.run_all_tests(
            files=[f[0] for f in files],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_STANDARD,
        )
        
        results_by_file = {r.file_path: r for r in summary.results}
        
        for name, _, expected_status in files:
            result = results_by_file.get(name)
            if result:
                passed = result.status == expected_status
                self.record(
                    f"standard_{name}",
                    passed,
                    f"{result.status.value} (expected: {expected_status.value})"
                )
            else:
                self.record(f"standard_{name}", False, "NOT FOUND in results")
    
    async def test_timeout_handling(self):
        """Test timeout detection for infinite loop."""
        print("\n🧪 TEST: Timeout Handling (infinite loop)")
        print("  ⏳ This test waits for actual timeout (~90s)...")
        
        self.create_file("infinite.py", STANDARD_INFINITE)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        
        start = time.time()
        summary = await tester.run_all_tests(
            files=["infinite.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_BLOCKING,
        )
        elapsed = time.time() - start
        
        infinite_result = summary.results[0] if summary.results else None
        
        self.record(
            "infinite_loop_timeout",
            infinite_result and infinite_result.status == TestStatus.TIMEOUT,
            f"Status: {infinite_result.status.value if infinite_result else 'N/A'}, took {elapsed:.1f}s"
        )
    
    async def test_near_timeout_passes(self):
        """Test that file completing before timeout passes."""
        print("\n🧪 TEST: Near-timeout file passes")
        
        self.create_file("near_timeout.py", NEAR_TIMEOUT)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        summary = await tester.run_all_tests(
            files=["near_timeout.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
        )
        
        result = summary.results[0] if summary.results else None
        self.record(
            "near_timeout_passes",
            result and result.status == TestStatus.PASSED,
            f"Status: {result.status.value if result else 'N/A'}"
        )
    
    # ========================================================================
    # RUNTIME TESTS - GUI
    # ========================================================================
    
    async def test_gui_headless(self):
        """Test GUI headless import."""
        print("\n🧪 TEST: GUI Headless Import")
        
        self.create_file("pygame_test.py", PYGAME_OK)
        self.create_file("tkinter_test.py", TKINTER_OK)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        analysis = await tester.analyze_project(["pygame_test.py", "tkinter_test.py"])
        
        summary = await tester.run_all_tests(
            files=["pygame_test.py", "tkinter_test.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        results_by_file = {r.file_path: r for r in summary.results}
        
        pygame_result = results_by_file.get("pygame_test.py")
        if pygame_result:
            if pygame_result.status == TestStatus.PASSED:
                self.record("pygame_headless", True, "Passed (pygame installed)")
            elif "No module named 'pygame'" in str(pygame_result.message):
                self.record("pygame_headless", True, "Correctly failed (pygame not installed)")
            else:
                self.record("pygame_headless", False, pygame_result.message)
        else:
            self.record("pygame_headless", False, "No result")
        
        tkinter_result = results_by_file.get("tkinter_test.py")
        if tkinter_result:
            self.record(
                "tkinter_headless",
                tkinter_result.status == TestStatus.PASSED,
                f"Status: {tkinter_result.status.value}"
            )
        else:
            self.record("tkinter_headless", False, "No result")
    
    async def test_gui_headless_no_display_error(self):
        """Test that GUI headless mode doesn't produce display errors."""
        print("\n🧪 TEST: GUI Headless No Display Errors")
        
        self.create_file("pygame_display.py", PYGAME_WITH_DISPLAY)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        analysis = await tester.analyze_project(["pygame_display.py"])
        
        summary = await tester.run_all_tests(
            files=["pygame_display.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        result = summary.results[0] if summary.results else None
        
        if result:
            # Should pass OR fail with "pygame not installed" — NOT with display error
            is_ok = (
                result.status == TestStatus.PASSED or
                "No module named 'pygame'" in str(result.message)
            )
            # Should NOT have display-related errors
            error_text = str(result.message).lower() + str(result.details or "").lower()
            has_display_error = any(x in error_text for x in 
                                   ["no available video device", "no video mode", 
                                    "display", "couldn't open"])
            
            self.record(
                "gui_no_display_error",
                is_ok or not has_display_error,
                f"Status: {result.status.value}, display_error: {has_display_error}"
            )
        else:
            self.record("gui_no_display_error", False, "No result")
    
    # ========================================================================
    # RUNTIME TESTS - SQL
    # ========================================================================
    
    async def test_sqlite_operations(self):
        """Test SQLite operations."""
        print("\n🧪 TEST: SQLite Operations")
        
        self.create_file("sqlite_memory.py", SQLITE_OK)
        self.create_file("sqlite_file.py", SQLITE_FILE_DB)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        analysis = await tester.analyze_project(["sqlite_memory.py", "sqlite_file.py"])
        
        summary = await tester.run_all_tests(
            files=["sqlite_memory.py", "sqlite_file.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        results_by_file = {r.file_path: r for r in summary.results}
        
        memory_result = results_by_file.get("sqlite_memory.py")
        self.record(
            "sqlite_memory",
            memory_result and memory_result.status == TestStatus.PASSED,
            f"Status: {memory_result.status.value if memory_result else 'N/A'}"
        )
        
        file_result = results_by_file.get("sqlite_file.py")
        self.record(
            "sqlite_file_redirect",
            file_result and file_result.status == TestStatus.PASSED,
            f"Status: {file_result.status.value if file_result else 'N/A'}"
        )
        
        original_db = self.temp_dir / "test_database.db"
        self.record(
            "sqlite_no_original_db",
            not original_db.exists(),
            f"Original DB exists: {original_db.exists()}"
        )
    
    async def test_sqlite_data_persistence(self):
        """Test that SQLite data actually writes and reads correctly."""
        print("\n🧪 TEST: SQLite Data Persistence")
        
        self.create_file("sqlite_persist.py", SQLITE_WRITE_READ)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        analysis = await tester.analyze_project(["sqlite_persist.py"])
        
        summary = await tester.run_all_tests(
            files=["sqlite_persist.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        result = summary.results[0] if summary.results else None
        self.record(
            "sqlite_data_persists",
            result and result.status == TestStatus.PASSED,
            f"Status: {result.status.value if result else 'N/A'}"
        )
    
    async def test_postgres_without_url(self):
        """Test PostgreSQL code without test database URL (import-only)."""
        print("\n🧪 TEST: PostgreSQL without URL")
        
        self.create_file("postgres_test.py", POSTGRES_CODE)
        self.vfs.commit_all_sync()
        
        # Ensure no test URL
        old_url = os.environ.pop('POSTGRES_TEST_URL', None)
        old_db_url = os.environ.pop('DATABASE_URL', None)
        
        try:
            tester = RuntimeTester(self.vfs)
            analysis = await tester.analyze_project(["postgres_test.py"])
            
            # Should be detected as PostgreSQL
            self.record(
                "postgres_detected",
                "postgres_test.py" in analysis.file_types[AppType.SQL_POSTGRES.value],
                f"File types: {[k for k,v in analysis.file_types.items() if 'postgres_test.py' in v]}"
            )
            
            summary = await tester.run_all_tests(
                files=["postgres_test.py"],
                temp_dir=str(self.temp_dir),
                total_timeout=TIMEOUT_QUICK,
                analysis=analysis,
            )
            
            result = summary.results[0] if summary.results else None
            
            # Should pass (import-only) or fail gracefully (missing psycopg2)
            acceptable = result and result.status in (TestStatus.PASSED, TestStatus.FAILED)
            not_crash = result and result.status != TestStatus.ERROR
            
            self.record(
                "postgres_import_only",
                acceptable and not_crash,
                f"Status: {result.status.value if result else 'N/A'}"
            )
        finally:
            if old_url:
                os.environ['POSTGRES_TEST_URL'] = old_url
            if old_db_url:
                os.environ['DATABASE_URL'] = old_db_url
    
    # ========================================================================
    # RUNTIME TESTS - WEB APPS
    # ========================================================================
    
    async def test_web_apps_skipped(self):
        """Test that web apps are properly skipped."""
        print("\n🧪 TEST: Web Apps Skipping")
        
        self.create_file("flask_test.py", FLASK_APP)
        self.create_file("fastapi_test.py", FASTAPI_APP)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        analysis = await tester.analyze_project(["flask_test.py", "fastapi_test.py"])
        
        self.record(
            "flask_detected_as_web",
            "flask_test.py" in analysis.file_types[AppType.WEB_APP.value],
            f"Flask in web files: {'flask_test.py' in analysis.file_types[AppType.WEB_APP.value]}"
        )
        
        summary = await tester.run_all_tests(
            files=["flask_test.py", "fastapi_test.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        results_by_file = {r.file_path: r for r in summary.results}
        
        flask_result = results_by_file.get("flask_test.py")
        fastapi_result = results_by_file.get("fastapi_test.py")
        
        self.record(
            "flask_skipped",
            flask_result and flask_result.status == TestStatus.SKIPPED,
            f"Status: {flask_result.status.value if flask_result else 'N/A'}"
        )
        
        self.record(
            "fastapi_skipped",
            fastapi_result and fastapi_result.status == TestStatus.SKIPPED,
            f"Status: {fastapi_result.status.value if fastapi_result else 'N/A'}"
        )
    
    # ========================================================================
    # RUNTIME TESTS - API
    # ========================================================================
    
    async def test_api_with_network(self):
        """Test API-dependent code with network available."""
        print("\n🧪 TEST: API with Network")
        
        self.create_file("api_simple.py", API_SIMPLE)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        has_network = await tester._check_network()
        
        analysis = await tester.analyze_project(["api_simple.py"])
        summary = await tester.run_all_tests(
            files=["api_simple.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        api_result = summary.results[0] if summary.results else None
        
        if has_network:
            self.record(
                "api_with_network",
                api_result and api_result.status == TestStatus.PASSED,
                f"Network available, status: {api_result.status.value if api_result else 'N/A'}"
            )
        else:
            self.record(
                "api_without_network",
                api_result and api_result.status in (TestStatus.PASSED, TestStatus.SKIPPED),
                f"Network unavailable, status: {api_result.status.value if api_result else 'N/A'}"
            )
    
    async def test_api_without_network(self):
        """Test API-dependent code with mocked network failure."""
        print("\n🧪 TEST: API without Network (mocked)")
        
        self.create_file("api_no_net.py", API_SIMPLE)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        tester._network_available = False
        
        analysis = await tester.analyze_project(["api_no_net.py"])
        summary = await tester.run_all_tests(
            files=["api_no_net.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        api_result = summary.results[0] if summary.results else None
        
        self.record(
            "api_import_only_fallback",
            api_result and api_result.status == TestStatus.PASSED,
            f"Status: {api_result.status.value if api_result else 'N/A'}"
        )
    
    async def test_api_network_error_handling(self):
        """Test that network errors are handled gracefully (not crashes)."""
        print("\n🧪 TEST: API Network Error Handling")
        
        self.create_file("api_bad_url.py", API_BAD_URL)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        tester._network_available = True  # Force network check to pass
        
        analysis = await tester.analyze_project(["api_bad_url.py"])
        summary = await tester.run_all_tests(
            files=["api_bad_url.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
            analysis=analysis,
        )
        
        result = summary.results[0] if summary.results else None
        
        # Network errors should result in PASSED (with warning) or graceful FAILED
        # NOT a crash (ERROR) or timeout
        acceptable = result and result.status in (TestStatus.PASSED, TestStatus.FAILED)
        
        self.record(
            "api_network_error_graceful",
            acceptable,
            f"Status: {result.status.value if result else 'N/A'} (acceptable: passed/failed)"
        )
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    async def test_input_causes_timeout(self):
        """Test that input() causes timeout."""
        print("\n🧪 TEST: input() causes timeout")
        print("  ⏳ This test waits for actual timeout (~90s)...")
        
        self.create_file("input_only.py", FILE_WITH_INPUT)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        
        start = time.time()
        summary = await tester.run_all_tests(
            files=["input_only.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_BLOCKING,
        )
        elapsed = time.time() - start
        
        result = summary.results[0] if summary.results else None
        
        if result and result.status == TestStatus.SKIPPED:
            self.record(
                "input_causes_timeout",
                False,
                f"File was SKIPPED instead of tested: {result.message}"
            )
        else:
            self.record(
                "input_causes_timeout",
                result and result.status == TestStatus.TIMEOUT,
                f"Status: {result.status.value if result else 'N/A'}, took {elapsed:.1f}s"
            )
    
    async def test_relative_imports(self):
        """Test handling of relative imports."""
        print("\n🧪 TEST: Relative Imports")
        
        self.create_file("relative.py", RELATIVE_IMPORT_FILE)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        summary = await tester.run_all_tests(
            files=["relative.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
        )
        
        result = summary.results[0] if summary.results else None
        self.record(
            "relative_import_fails",
            result and result.status == TestStatus.FAILED,
            f"Status: {result.status.value if result else 'N/A'}"
        )
    
    async def test_conditional_imports(self):
        """Test files with try/except imports."""
        print("\n🧪 TEST: Conditional Imports")
        
        self.create_file("conditional.py", CONDITIONAL_IMPORTS)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        summary = await tester.run_all_tests(
            files=["conditional.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
        )
        
        result = summary.results[0] if summary.results else None
        # Should pass regardless of whether numpy is installed
        self.record(
            "conditional_import_works",
            result and result.status == TestStatus.PASSED,
            f"Status: {result.status.value if result else 'N/A'}"
        )
    
    async def test_package_with_init(self):
        """Test package with __init__.py."""
        print("\n🧪 TEST: Package with __init__.py")
        
        # Create package structure
        (self.temp_dir / "mypackage").mkdir(exist_ok=True)
        
        self.create_file("mypackage/__init__.py", PACKAGE_INIT)
        self.create_file("mypackage/utils.py", PACKAGE_UTILS)
        self.create_file("main_pkg.py", PACKAGE_MAIN)
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        summary = await tester.run_all_tests(
            files=["main_pkg.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
        )
        
        result = summary.results[0] if summary.results else None
        self.record(
            "package_import_works",
            result and result.status == TestStatus.PASSED,
            f"Status: {result.status.value if result else 'N/A'}"
        )
    
    # ========================================================================
    # STATISTICS VALIDATION
    # ========================================================================
    
    async def test_statistics_consistency(self):
        """Test that statistics are consistent."""
        print("\n🧪 TEST: Statistics Consistency")
        
        files = [
            self.create_file("stat1.py", STANDARD_OK),
            self.create_file("stat2.py", STANDARD_ERROR),
            self.create_file("stat3.py", FLASK_APP),
        ]
        self.vfs.commit_all_sync()
        
        tester = RuntimeTester(self.vfs)
        summary = await tester.run_all_tests(
            files=files,
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_STANDARD,
        )
        
        total = summary.passed + summary.failed + summary.skipped + summary.errors + summary.timeouts
        self.record(
            "stats_total_matches",
            total == summary.total_files,
            f"Sum={total}, total_files={summary.total_files}"
        )
        
        self.record(
            "stats_results_count",
            len(summary.results) == summary.total_files,
            f"Results={len(summary.results)}, total_files={summary.total_files}"
        )
        
        self.record(
            "stats_duration_positive",
            summary.total_duration_ms > 0,
            f"Duration: {summary.total_duration_ms:.0f}ms"
        )
        
        all_have_duration = all(r.duration_ms >= 0 for r in summary.results)
        self.record(
            "stats_all_results_have_duration",
            all_have_duration,
            f"All results have duration: {all_have_duration}"
        )
    
    # ========================================================================
    # INTEGRATION TESTS
    # ========================================================================
    
    async def test_change_validator_integration(self):
        """Test integration with ChangeValidator."""
        print("\n🧪 TEST: ChangeValidator Integration")
        
        self.vfs = VirtualFileSystem(str(self.temp_dir))
        
        files = [
            ("cv_ok.py", STANDARD_OK),
            ("cv_error.py", STANDARD_ERROR),
        ]
        
        for name, content in files:
            self.vfs.stage_change(name, content, ChangeType.CREATE)
            (self.temp_dir / name).write_text(content, encoding='utf-8')
        
        config = ValidatorConfig(
            enabled_levels=[
                ValidationLevel.SYNTAX,
                ValidationLevel.IMPORTS,
                ValidationLevel.RUNTIME,
            ],
            runtime_timeout_sec=TIMEOUT_STANDARD,
        )
        
        validator = ChangeValidator(vfs=self.vfs, config=config)
        result = await validator.validate()
        
        self.record(
            "cv_has_runtime_stats",
            result.runtime_files_checked > 0,
            f"Runtime files checked: {result.runtime_files_checked}"
        )
        
        self.record(
            "cv_has_summary",
            result.runtime_test_summary is not None,
            f"Has summary: {result.runtime_test_summary is not None}"
        )
        
        if result.runtime_test_summary:
            summary_total = result.runtime_test_summary.get("total_files", 0)
            self.record(
                "cv_summary_matches",
                summary_total == result.runtime_files_checked,
                f"Summary total={summary_total}, checked={result.runtime_files_checked}"
            )
    
    async def test_feedback_handler_integration(self):
        """Test integration with FeedbackHandler."""
        print("\n🧪 TEST: FeedbackHandler Integration")
        
        summary_dict = {
            "total_files": 3,
            "passed": 1,
            "failed": 1,
            "skipped": 1,
            "timeouts": 0,
            "errors": 0,
            "success": False,
            "results": [
                {"file_path": "ok.py", "status": "passed", "app_type": "standard", "message": "OK"},
                {"file_path": "err.py", "status": "failed", "app_type": "standard", "message": "Error"},
                {"file_path": "skip.py", "status": "skipped", "app_type": "web", "message": "Skipped"},
            ],
            "analysis": {
                "size_category": "small",
                "detected_frameworks": {"flask": "Flask"},
            },
        }
        
        feedback = create_runtime_test_feedback(summary_dict)
        
        self.record(
            "feedback_created",
            feedback is not None,
            f"Feedback created: {feedback is not None}"
        )
        
        if feedback:
            self.record(
                "feedback_counts_correct",
                feedback.passed == 1 and feedback.failed == 1 and feedback.skipped == 1,
                f"passed={feedback.passed}, failed={feedback.failed}, skipped={feedback.skipped}"
            )
            
            handler = FeedbackHandler()
            handler.add_runtime_test_feedback(summary_dict)
            
            all_feedback = handler.get_feedback_for_orchestrator()
            
            self.record(
                "handler_includes_runtime",
                "runtime_test_feedback" in all_feedback and all_feedback["runtime_test_feedback"],
                f"Has runtime_test_feedback: {'runtime_test_feedback' in all_feedback}"
            )
            
            formatted = feedback.to_prompt_format()
            self.record(
                "feedback_formatted",
                "RUNTIME VALIDATION" in formatted and "FAILED FILES" in formatted,
                f"Formatted length: {len(formatted)} chars"
            )
    
    # ========================================================================
    # ERROR HANDLING
    # ========================================================================
    
    async def test_error_handling(self):
        """Test error handling in RuntimeTester."""
        print("\n🧪 TEST: Error Handling")
        
        tester = RuntimeTester(self.vfs)
        
        summary = await tester.run_all_tests(
            files=["nonexistent.py"],
            temp_dir=str(self.temp_dir),
            total_timeout=TIMEOUT_QUICK,
        )
        
        result = summary.results[0] if summary.results else None
        self.record(
            "nonexistent_file_handled",
            result and result.status in (TestStatus.ERROR, TestStatus.FAILED),
            f"Status: {result.status.value if result else 'N/A'}"
        )
        
        summary = await tester.run_all_tests(
            files=["test.py"],
            temp_dir="/nonexistent/path/that/does/not/exist",
            total_timeout=TIMEOUT_QUICK,
        )
        
        result = summary.results[0] if summary.results else None
        self.record(
            "invalid_temp_dir_handled",
            result and result.status in (TestStatus.ERROR, TestStatus.FAILED),
            f"Status: {result.status.value if result else 'N/A'}"
        )
    
    # ========================================================================
    # RUN ALL
    # ========================================================================
    
    async def run_all(self) -> bool:
        """Run all tests."""
        self.setup()
        
        total_start = time.time()
        
        try:
            # === Quick unit tests first ===
            await self.test_timeout_calculator()
            await self.test_framework_detection()
            await self.test_project_analysis()
            
            # === Standard runtime tests ===
            await self.test_standard_files()
            await self.test_near_timeout_passes()
            
            # === GUI tests ===
            await self.test_gui_headless()
            await self.test_gui_headless_no_display_error()  # NEW
            
            # === SQL tests ===
            await self.test_sqlite_operations()
            await self.test_sqlite_data_persistence()  # NEW
            await self.test_postgres_without_url()     # NEW
            
            # === Web app tests ===
            await self.test_web_apps_skipped()
            
            # === API tests ===
            await self.test_api_with_network()
            await self.test_api_without_network()
            await self.test_api_network_error_handling()  # NEW
            
            # === Edge cases (some are slow) ===
            await self.test_timeout_handling()      # ~90s wait
            await self.test_input_causes_timeout()  # ~90s wait
            await self.test_relative_imports()
            await self.test_conditional_imports()   # NEW
            await self.test_package_with_init()     # NEW
            
            # === Statistics ===
            await self.test_statistics_consistency()
            
            # === Integration tests ===
            await self.test_change_validator_integration()
            await self.test_feedback_handler_integration()
            
            # === Error handling ===
            await self.test_error_handling()
            
            total_elapsed = time.time() - total_start
            
            # Summary
            print("\n" + "="*60)
            print("📊 TEST SUMMARY")
            print("="*60)
            
            passed = sum(1 for p, _ in self.test_results.values() if p)
            failed = sum(1 for p, _ in self.test_results.values() if not p)
            
            print(f"\n  Total tests: {len(self.test_results)}")
            print(f"  ✅ Passed: {passed}")
            print(f"  ❌ Failed: {failed}")
            print(f"  ⏱️  Total time: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
            
            if failed > 0:
                print("\n  Failed tests:")
                for name, (p, msg) in self.test_results.items():
                    if not p:
                        print(f"    • {name}: {msg}")
            
            print(f"\n  {'🎉 ALL TESTS PASSED!' if failed == 0 else '⚠️ SOME TESTS FAILED'}")
            print("="*60)
            
            return failed == 0
            
        finally:
            self.teardown()


# ============================================================================
# MAIN
# ============================================================================

async def main():
    print("\n" + "="*70)
    print("🧪 COMPREHENSIVE RUNTIME TESTER TEST SUITE")
    print("="*70)
    print("\n⚠️  WARNING: This test suite takes ~6-8 minutes to complete.")
    print("    Some tests wait for actual timeouts (infinite loop, input blocking).\n")
    
    test = ComprehensiveRuntimeTest()
    success = await test.run_all()
    
    return success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)