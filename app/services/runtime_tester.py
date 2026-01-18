# app/services/runtime_tester.py
"""
Runtime Tester — Unified testing system for all application types.

Handles:
- Standard Python scripts (runpy)
- GUI/Game applications (headless import + startup test)
- SQL operations (in-memory/temp database validation)
- API-dependent code (network check + graceful handling)
- Web applications (skip with INFO)

Key principles:
- NEVER skip silently — always report what was/wasn't tested
- Dynamic timeouts based on project size
- Graceful degradation for missing dependencies
"""

from __future__ import annotations

import ast
import os
import re
import sys
import json
import socket
import logging
import tempfile
import subprocess
import shutil
import asyncio
import uuid
import traceback
import time
from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Pattern
from enum import Enum

FRAMEWORK_REGISTRY_PATH = "config/framework_registry.json"

if TYPE_CHECKING:
    from app.services.virtual_fs import VirtualFileSystem

from app.utils.token_counter import TokenCounter

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class AppType(Enum):
    """Application type classification."""
    STANDARD = "standard"           # Regular Python script
    GUI = "gui"                     # GUI/Game application (pygame, tkinter, etc.)
    TUI = "tui"                     # Terminal UI (curses, textual, rich)
    SQL_SQLITE = "sql_sqlite"       # SQLite database operations
    SQL_POSTGRES = "sql_postgres"   # PostgreSQL database operations
    SQL_GENERIC = "sql_generic"     # Pure SQL files (.sql)
    API_DEPENDENT = "api"           # Requires network/external API
    WEB_APP = "web"                 # Web framework (Flask, Django, etc.)
    DAEMON = "daemon"               # Background service/daemon (watchdog, schedule, etc.)
    INTERACTIVE = "interactive"     # Interactive CLI with input() menus
    CLI = "cli"                     # Command-line interface applications
    TESTING = "testing"             # Testing framework (pytest, unittest, nose, etc.)
    UNKNOWN = "unknown"
    PACKAGE_MODULE = "package_module"  # Python package module (import only)



class TestStatus(Enum):
    """Test execution status."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    ERROR = "error"


class SizeCategory(Enum):
    """Project size category for timeout calculation."""
    SMALL = "small"      # <10K tokens
    MEDIUM = "medium"    # 10K-50K tokens
    LARGE = "large"      # >50K tokens


# Framework detection patterns: import_name -> (app_type, display_name)
FRAMEWORK_PATTERNS: Dict[str, Tuple[AppType, str]] = {
    # GUI frameworks
    'pygame': (AppType.GUI, 'Pygame game framework'),
    'tkinter': (AppType.GUI, 'Tkinter GUI'),
    'PyQt5': (AppType.GUI, 'PyQt5 GUI'),
    'PyQt6': (AppType.GUI, 'PyQt6 GUI'),
    'PySide2': (AppType.GUI, 'PySide2 GUI'),
    'PySide6': (AppType.GUI, 'PySide6 GUI'),
    'kivy': (AppType.GUI, 'Kivy GUI'),
    'arcade': (AppType.GUI, 'Arcade game framework'),
    'pyglet': (AppType.GUI, 'Pyglet game framework'),
    'wx': (AppType.GUI, 'wxPython GUI'),
    'turtle': (AppType.GUI, 'Turtle graphics'),
    'customtkinter': (AppType.GUI, 'CustomTkinter GUI'),
    'dearpygui': (AppType.GUI, 'Dear PyGui'),
    'pyqtgraph': (AppType.GUI, 'PyQtGraph'),
    
    # TUI frameworks (Terminal UI)
    'curses': (AppType.TUI, 'Curses TUI'),
    'textual': (AppType.TUI, 'Textual TUI'),
    'rich': (AppType.TUI, 'Rich console'),  # Can be TUI-like
    'blessed': (AppType.TUI, 'Blessed TUI'),
    'urwid': (AppType.TUI, 'Urwid TUI'),
    'prompt_toolkit': (AppType.TUI, 'Prompt Toolkit'),
    'npyscreen': (AppType.TUI, 'Npyscreen TUI'),
    'asciimatics': (AppType.TUI, 'Asciimatics TUI'),
    
    # Database
    'sqlite3': (AppType.SQL_SQLITE, 'SQLite database'),
    'psycopg2': (AppType.SQL_POSTGRES, 'PostgreSQL (psycopg2)'),
    'psycopg': (AppType.SQL_POSTGRES, 'PostgreSQL (psycopg3)'),
    'asyncpg': (AppType.SQL_POSTGRES, 'PostgreSQL (asyncpg)'),
    'sqlalchemy': (AppType.SQL_SQLITE, 'SQLAlchemy ORM'),  # Default to SQLite
    'peewee': (AppType.SQL_SQLITE, 'Peewee ORM'),
    'tortoise': (AppType.SQL_SQLITE, 'Tortoise ORM'),
    
    # Network/API
    'requests': (AppType.API_DEPENDENT, 'HTTP requests'),
    'httpx': (AppType.API_DEPENDENT, 'HTTPX client'),
    'aiohttp': (AppType.API_DEPENDENT, 'aiohttp client'),
    'urllib3': (AppType.API_DEPENDENT, 'urllib3'),
    'websocket': (AppType.API_DEPENDENT, 'WebSocket'),
    'websockets': (AppType.API_DEPENDENT, 'WebSockets'),
    'socket': (AppType.API_DEPENDENT, 'Socket networking'),
    
    # Web frameworks (skip)
    'flask': (AppType.WEB_APP, 'Flask web framework'),
    'django': (AppType.WEB_APP, 'Django web framework'),
    'fastapi': (AppType.WEB_APP, 'FastAPI web framework'),
    'starlette': (AppType.WEB_APP, 'Starlette web framework'),
    'bottle': (AppType.WEB_APP, 'Bottle web framework'),
    'tornado': (AppType.WEB_APP, 'Tornado web framework'),
    'sanic': (AppType.WEB_APP, 'Sanic web framework'),
    'aiohttp.web': (AppType.WEB_APP, 'aiohttp web server'),

    # Daemon/Service frameworks (infinite loops)
    'watchdog': (AppType.DAEMON, 'Watchdog file monitor'),
    'watchdog.observers': (AppType.DAEMON, 'Watchdog observer'),
    'schedule': (AppType.DAEMON, 'Schedule task runner'),
    'apscheduler': (AppType.DAEMON, 'APScheduler'),
    'celery': (AppType.DAEMON, 'Celery task queue'),
    'rq': (AppType.DAEMON, 'Redis Queue'),
    'dramatiq': (AppType.DAEMON, 'Dramatiq task queue'),
    'huey': (AppType.DAEMON, 'Huey task queue'),
    'arq': (AppType.DAEMON, 'ARQ async queue'),
    
    # CLI frameworks
    'argparse': (AppType.CLI, 'Argparse CLI framework'),
    'click': (AppType.CLI, 'Click CLI framework'),
    'typer': (AppType.CLI, 'Typer CLI framework'),
    'fire': (AppType.CLI, 'Google Fire CLI framework'),
    'docopt': (AppType.CLI, 'Docopt CLI framework'),
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class ProjectAnalysis:
    """Result of project analysis before testing."""
    total_tokens: int
    total_chars: int
    file_count: int
    size_category: SizeCategory
    total_timeout_seconds: int
    per_file_timeout_seconds: int
    detected_frameworks: Dict[str, str]  # framework -> display_name
    file_types: Dict[str, List[str]]     # AppType.value -> [file_paths]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "total_chars": self.total_chars,
            "file_count": self.file_count,
            "size_category": self.size_category.value,
            "total_timeout_seconds": self.total_timeout_seconds,
            "per_file_timeout_seconds": self.per_file_timeout_seconds,
            "detected_frameworks": self.detected_frameworks,
            "file_types": self.file_types,
        }


@dataclass
class TestResult:
    """Result of testing a single file."""
    file_path: str
    app_type: AppType
    status: TestStatus
    message: str
    duration_ms: float = 0.0
    details: Optional[str] = None
    suggestion: Optional[str] = None
    
    @property
    def success(self) -> bool:
        return self.status in (TestStatus.PASSED, TestStatus.SKIPPED)
    
    @property
    def is_error(self) -> bool:
        return self.status in (TestStatus.FAILED, TestStatus.ERROR, TestStatus.TIMEOUT)
    
    @property
    def is_skipped(self) -> bool:
        return self.status == TestStatus.SKIPPED
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "app_type": self.app_type.value,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "details": self.details,
            "suggestion": self.suggestion,
            "success": self.success,
        }


@dataclass
class RuntimeTestSummary:
    """Summary of all runtime tests."""
    total_files: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    timeouts: int = 0
    results: List[TestResult] = field(default_factory=list)
    analysis: Optional[ProjectAnalysis] = None
    total_duration_ms: float = 0.0
    
    def add_result(self, result: TestResult):
        self.results.append(result)
        self.total_files += 1
        
        if result.status == TestStatus.PASSED:
            self.passed += 1
        elif result.status == TestStatus.FAILED:
            self.failed += 1
        elif result.status == TestStatus.SKIPPED:
            self.skipped += 1
        elif result.status == TestStatus.ERROR:
            self.errors += 1
        elif result.status == TestStatus.TIMEOUT:
            self.timeouts += 1
    
    @property
    def success(self) -> bool:
        """Overall success: no failures, errors, or timeouts."""
        return self.failed == 0 and self.errors == 0 and self.timeouts == 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_files": self.total_files,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "success": self.success,
            "results": [r.to_dict() for r in self.results],
            "analysis": self.analysis.to_dict() if self.analysis else None,
            "total_duration_ms": self.total_duration_ms,
        }


# ============================================================================
# TIMEOUT CALCULATOR
# ============================================================================

class TimeoutCalculator:
    """Calculate dynamic timeouts based on project size."""
    
    # Token thresholds
    SMALL_THRESHOLD = 10_000      # <10K tokens
    MEDIUM_THRESHOLD = 50_000     # 10K-50K tokens
    
    # Total timeouts (seconds)
    TOTAL_TIMEOUTS = {
        SizeCategory.SMALL: 15 * 60,    # 15 minutes
        SizeCategory.MEDIUM: 45 * 60,   # 45 minutes
        SizeCategory.LARGE: 120 * 60,   # 120 minutes
    }
    
    # Per-file timeouts (seconds) — INCREASED for better testing
    PER_FILE_TIMEOUTS = {
        AppType.STANDARD: 90,       # 90 seconds
        AppType.GUI: 45,            # 45 seconds (was 20) — GUI needs more time to init
        AppType.TUI: 30,            # 30 seconds — TUI apps (curses, textual)
        AppType.SQL_SQLITE: 90,     # 90 seconds
        AppType.SQL_POSTGRES: 90,   # 90 seconds
        AppType.SQL_GENERIC: 60,    # 60 seconds — pure SQL files
        AppType.API_DEPENDENT: 45,  # 45 seconds
        AppType.WEB_APP: 0,         # Skipped
        AppType.DAEMON: 30,         # 30 seconds — import-only for daemons
        AppType.INTERACTIVE: 15,    # 15 seconds — quick test with simulated input
        AppType.CLI: 30,            # 30 seconds — CLI testing
        AppType.TESTING: 120,       # 120 seconds for testing frameworks (pytest/unittest)
        AppType.UNKNOWN: 60,        # 60 seconds
    }
    
    @classmethod
    def calculate(cls, total_tokens: int, file_count: int) -> Tuple[SizeCategory, int, int]:
        """
        Calculate timeouts based on project size.
        
        Returns:
            Tuple of (size_category, total_timeout, per_file_timeout)
        """
        if total_tokens < cls.SMALL_THRESHOLD:
            category = SizeCategory.SMALL
        elif total_tokens < cls.MEDIUM_THRESHOLD:
            category = SizeCategory.MEDIUM
        else:
            category = SizeCategory.LARGE
        
        total_timeout = cls.TOTAL_TIMEOUTS[category]
        
        # Per-file timeout: distribute total evenly, but with min/max bounds
        per_file = max(45, min(300, total_timeout // max(1, file_count)))
        
        return category, total_timeout, per_file
    
    @classmethod
    def get_timeout_for_type(cls, app_type: AppType) -> int:
        """Get timeout for specific app type."""
        return cls.PER_FILE_TIMEOUTS.get(app_type, 60)



class FrameworkDetector:
    """Enhanced framework detector with usage pattern matching and weighted scoring.
    
    Detects frameworks using:
    1. Import analysis (AST-based)
    2. Usage pattern matching (function calls, class inheritance, decorators)
    3. Weighted scoring system for conflict resolution
    
    Supports both old (string→string) and new (object with patterns) JSON registry formats.
    """
    
    def __init__(self, vfs: Optional['VirtualFileSystem'] = None, project_root: Optional[Path] = None, source_roots: Optional[List[str]] = None):
        """Initialize framework detector with optional VirtualFileSystem support."""
        self.logger = logging.getLogger(__name__)
        self.framework_patterns: Dict[str, Tuple[AppType, str, List[Pattern], float]] = {}
        self.import_to_framework: Dict[str, str] = {}
        self.vfs = vfs
        self.project_root = project_root
        self.source_roots = source_roots if source_roots is not None else ['']
        self._stdlib_modules: Optional[Set[str]] = None
    
    
    
    
    def load_from_registry(self, registry_data: Dict[str, Any]) -> None:
        """
        Load framework patterns from registry data.
        
        Supports both old (string) and new (object with patterns) formats.
        
        Args:
            registry_data: Dictionary with categories containing framework definitions
        """
        self.framework_patterns = {}
        self.import_to_framework = {}
        
        category_to_app_type = {
            'gui': AppType.GUI,
            'tui': AppType.TUI,
            'database': AppType.SQL_SQLITE,
            'network': AppType.API_DEPENDENT,
            'web': AppType.WEB_APP,
            'daemon': AppType.DAEMON,
            'cli': AppType.CLI,
            'testing': AppType.TESTING,
            'data_science': AppType.STANDARD,
            'misc': AppType.STANDARD,
        }
        
        total_patterns = 0
        
        for category, modules in registry_data.items():
            app_type = category_to_app_type.get(category, AppType.STANDARD)
            
            for framework_key, framework_value in modules.items():
                # Parse framework config (supports both old and new formats)
                description = ""
                usage_patterns = []
                weight = 1.0
                
                if isinstance(framework_value, str):
                    # Old format: "framework": "description"
                    description = framework_value
                    usage_patterns = []
                    weight = 1.0
                elif isinstance(framework_value, dict):
                    # New format: "framework": {"description": "...", "usage_patterns": [...], "weight": 1.2}
                    description = framework_value.get('description', '')
                    usage_patterns = framework_value.get('usage_patterns', [])
                    weight = framework_value.get('weight', 1.0)
                else:
                    continue
                
                # Compile usage patterns to regex Pattern objects
                compiled_patterns: List[Pattern] = []
                for pattern_str in usage_patterns:
                    try:
                        compiled_patterns.append(re.compile(pattern_str))
                        total_patterns += 1
                    except re.error as e:
                        self.logger.warning(f"Invalid regex pattern '{pattern_str}' for {framework_key}: {e}")
                
                # Store framework pattern
                self.framework_patterns[framework_key] = (app_type, description, compiled_patterns, weight)
                self.import_to_framework[framework_key] = framework_key
                
                # Add dotted variants (e.g., 'aiohttp.web' → 'aiohttp')
                if '.' in framework_key:
                    base = framework_key.split('.')[0]
                    if base not in self.import_to_framework:
                        self.import_to_framework[base] = framework_key
        
        self.logger.debug(
            f"Loaded {len(self.framework_patterns)} frameworks with {total_patterns} usage patterns"
        )
    
    
    
    def detect(self, content: str, file_path: str) -> Tuple[Dict[str, str], AppType]:
        """
        Detect frameworks used in file with recursive import analysis.
        
        Args:
            content: File content as string
            file_path: Path to file (for logging)
            
        Returns:
            Tuple of (detected_frameworks_dict, primary_app_type)
        """
        return self._detect_recursive(content, file_path, max_depth=2, visited=None)
    
    
    
    def _detect_single_file(self, content: str, file_path: str) -> Tuple[Dict[str, str], AppType]:
        """Analyze a single file for framework detection without recursion. This is the original detect logic extracted into a separate method."""
        detected: Dict[str, str] = {}
        weights: Dict[str, float] = defaultdict(float)
        
        # 1. Import-based detection
        imported_modules = self._extract_imports_ast(content)
        for module in imported_modules:
            if module in self.import_to_framework:
                framework_key = self.import_to_framework[module]
                if framework_key in self.framework_patterns:
                    app_type, description, patterns, weight = self.framework_patterns[framework_key]
                    if framework_key not in detected:
                        detected[framework_key] = description
                    weights[framework_key] += weight
        
        # 2. Usage pattern detection
        pattern_matches = self._match_usage_patterns(content)
        for framework, weight in pattern_matches.items():
            if framework in self.framework_patterns:
                app_type, description, patterns, base_weight = self.framework_patterns[framework]
                if framework not in detected:
                    detected[framework] = description
                weights[framework] += weight
        
        # 3. Specialized pattern detection
        cli_matches = self._detect_cli_patterns(content)
        for framework, weight in cli_matches.items():
            if framework not in detected:
                detected[framework] = f"{framework} CLI framework"
            weights[framework] += weight
        
        daemon_matches = self._detect_daemon_patterns(content)
        for framework, weight in daemon_matches.items():
            if framework not in detected:
                detected[framework] = f"{framework} daemon pattern"
            weights[framework] += weight
        
        testing_matches = self._detect_testing_patterns(content)
        for framework, weight in testing_matches.items():
            if framework not in detected:
                detected[framework] = f"{framework} testing framework"
            weights[framework] += weight
        
        # 4. Calculate final AppType
        type_weights = self._calculate_app_type_weights(weights)
        
        # Select AppType with highest weight
        if type_weights:
            selected_app_type = max(type_weights.items(), key=lambda x: x[1])[0]
        else:
            selected_app_type = AppType.STANDARD
        
        # Handle ties with priority order
        if not type_weights or type_weights[selected_app_type] == 0:
            selected_app_type = AppType.STANDARD
        else:
            priority = [AppType.WEB_APP, AppType.DAEMON, AppType.GUI, AppType.SQL_POSTGRES, 
                    AppType.SQL_SQLITE, AppType.API_DEPENDENT, AppType.CLI, AppType.TESTING, 
                    AppType.STANDARD]
            for app_type in priority:
                if app_type in type_weights and type_weights[app_type] > 0:
                    selected_app_type = app_type
                    break
        
        return detected, selected_app_type
    
    
    def _detect_recursive(self, content: str, file_path: str, max_depth: int = 2, visited: Optional[Set[str]] = None) -> Tuple[Dict[str, str], AppType]:
        """Recursively detect frameworks by analyzing local imports. Propagates strong types (CLI, GUI, DAEMON, etc.) from imported modules to the entry point."""
        if visited is None:
            visited = set()
        
        # Normalize path
        file_path = file_path.replace('\\', '/')
        
        # Add to visited
        visited.add(file_path)
        
        # Detect in current file
        detected, app_type = self._detect_single_file(content, file_path)
        
        # Define strong types that should stop recursion
        STRONG_TYPES = {AppType.GUI, AppType.WEB_APP, AppType.TUI, AppType.DAEMON, AppType.CLI}
        
        # If we found a strong type, return immediately
        if app_type in STRONG_TYPES:
            return detected, app_type
        
        # If max depth reached or no VFS, return current result
        if max_depth <= 0 or self.vfs is None:
            return detected, app_type
        
        # Extract local imports
        local_imports = self._extract_local_imports(content)
        
        # Recursively check each local import
        for module_name in local_imports:
            resolved_path = self._resolve_import_to_file(module_name, file_path)
            
            if resolved_path is None or resolved_path in visited:
                continue
            
            try:
                # Try VFS first
                child_content = self.vfs.read_file(resolved_path)
                
                # Fallback: Read from physical disk if not in VFS
                if child_content is None and self.project_root:
                    try:
                        full_path = self.project_root / resolved_path
                        if full_path.exists():
                            child_content = full_path.read_text(encoding='utf-8', errors='replace')
                    except Exception:
                        pass
                
                if child_content is None:
                    continue
                
                child_detected, child_app_type = self._detect_recursive(
                    child_content, resolved_path, max_depth - 1, visited
                )
                
                # If child has strong type, inherit it
                if child_app_type in STRONG_TYPES:
                    app_type = child_app_type
                    detected.update(child_detected)
                    self.logger.debug(
                        f"Inherited {child_app_type.value} from {resolved_path} for {file_path}"
                    )
                    break
            
            except Exception as e:
                self.logger.debug(f"Error analyzing import {module_name}: {e}")
                continue
        
        return detected, app_type
    
    
    def _extract_local_imports(self, content: str) -> Set[str]:
        """Extract local project imports from Python source, excluding stdlib and pip packages."""
        local_imports: Set[str] = set()
        
        # Get stdlib modules
        stdlib = self._get_stdlib_modules()
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return local_imports
        
        try:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    # Case: import module, import module.submodule
                    for alias in node.names:
                        base_module = alias.name.split('.')[0].lower()
                        if base_module not in stdlib and base_module not in self.import_to_framework:
                            local_imports.add(alias.name)
                
                elif isinstance(node, ast.ImportFrom):
                    # Case: from module import name
                    if node.level > 0:
                        # Relative import (from . import x, from .sub import x)
                        if node.module:
                            # Add the module itself (e.g., 'sub' from 'from .sub import x')
                            local_imports.add(node.module)
                        
                        # Add all imported names as potential local dependencies
                        for alias in node.names:
                            if alias.name and alias.name != '*':
                                local_imports.add(alias.name)
                    
                    else:
                        # Absolute import (from app import gui, from app.services import auth)
                        if node.module:
                            base_module = node.module.split('.')[0].lower()
                            if base_module not in stdlib and base_module not in self.import_to_framework:
                                # Add the module itself
                                local_imports.add(node.module)
                                
                                # CRITICAL: Add module.alias combinations
                                # This forces scanner to check for submodules
                                # e.g., 'from app import gui' -> add 'app.gui' to check for app/gui.py
                                for alias in node.names:
                                    if alias.name and alias.name != '*':
                                        local_imports.add(f"{node.module}.{alias.name}")
        
        except Exception:
            pass
        
        return local_imports
    
    
    def _resolve_import_to_file(self, module_name: str, context_file_path: Optional[str] = None) -> Optional[str]:
        """
        Resolve a module name to a file path using discovered source roots and context.
        """
        if self.vfs is None:
            return None
        
        rel_path = module_name.replace('.', '/')
        search_roots = []
        
        if context_file_path:
            parent_dir = str(Path(context_file_path).parent).replace('\\', '/')
            if parent_dir == '.':
                parent_dir = ''
            search_roots.append(parent_dir)
        
        search_roots.extend(self.source_roots)
        
        unique_roots = []
        seen = set()
        for r in search_roots:
            if r not in seen:
                unique_roots.append(r)
                seen.add(r)
        
        for root in unique_roots:
            if root:
                base_path = f"{root}/{rel_path}"
            else:
                base_path = rel_path
            
            candidates = [f"{base_path}.py", f"{base_path}/__init__.py"]
            for candidate in candidates:
                if self.vfs.file_exists(candidate):
                    return candidate
                
                if self.project_root:
                    try:
                        full_path = self.project_root / candidate
                        if full_path.exists():
                            return candidate
                    except Exception:
                        pass
        
        return None
    
    
    def _get_stdlib_modules(self) -> Set[str]:
        """Get a set of Python standard library module names (cached)."""
        if self._stdlib_modules is not None:
            return self._stdlib_modules
        
        stdlib = {
            'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio', 'asyncore',
            'atexit', 'audioop', 'base64', 'bdb', 'binascii', 'binhex', 'bisect', 'builtins',
            'bz2', 'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd', 'code', 'codecs',
            'codeop', 'collections', 'colorsys', 'compileall', 'concurrent', 'configparser',
            'contextlib', 'contextvars', 'copy', 'copyreg', 'cProfile', 'crypt', 'csv', 'ctypes',
            'curses', 'dataclasses', 'datetime', 'dbm', 'decimal', 'difflib', 'dis', 'distutils',
            'doctest', 'dummy_thread', 'dummy_threading', 'email', 'encodings', 'ensurepip',
            'enum', 'errno', 'faulthandler', 'fcntl', 'filecmp', 'fileinput', 'fnmatch',
            'fractions', 'ftplib', 'functools', 'gc', 'getopt', 'getpass', 'gettext', 'glob',
            'grp', 'gzip', 'hashlib', 'heapq', 'hmac', 'html', 'http', 'imaplib', 'imghdr',
            'imp', 'importlib', 'inspect', 'io', 'ipaddress', 'itertools', 'json', 'keyword',
            'lib2to3', 'linecache', 'locale', 'logging', 'lzma', 'mailbox', 'mailcap', 'marshal',
            'math', 'mimetypes', 'mmap', 'modulefinder', 'msilib', 'msvcrt', 'multiprocessing',
            'netrc', 'nis', 'nntplib', 'numbers', 'operator', 'optparse', 'os', 'ossaudiodev',
            'parser', 'pathlib', 'pdb', 'pickle', 'pickletools', 'pipes', 'pkgutil', 'platform',
            'plistlib', 'poplib', 'posix', 'posixpath', 'pprint', 'profile', 'pstats', 'pty',
            'pwd', 'py_compile', 'pyclbr', 'pydoc', 'queue', 'quopri', 'random', 're', 'readline',
            'reprlib', 'resource', 'rlcompleter', 'runpy', 'sched', 'secrets', 'select', 'selectors',
            'shelve', 'shlex', 'shutil', 'signal', 'site', 'smtpd', 'smtplib', 'sndhdr', 'socket',
            'socketserver', 'spwd', 'sqlite3', 'ssl', 'stat', 'statistics', 'string', 'stringprep',
            'struct', 'subprocess', 'sunau', 'symbol', 'symtable', 'sys', 'sysconfig', 'syslog',
            'tabnanny', 'tarfile', 'telnetlib', 'tempfile', 'termios', 'test', 'textwrap', 'threading',
            'time', 'timeit', 'tkinter', 'token', 'tokenize', 'trace', 'traceback', 'tracemalloc',
            'tty', 'turtle', 'types', 'typing', 'unicodedata', 'unittest', 'urllib', 'uu', 'uuid',
            'venv', 'warnings', 'wave', 'weakref', 'webbrowser', 'winreg', 'winsound', 'wsgiref',
            'xdrlib', 'xml', 'xmlrpc', 'zipapp', 'zipfile', 'zipimport', 'zlib',
        }
        
        self._stdlib_modules = stdlib
        return stdlib
    
    def _extract_imports_ast(self, content: str) -> Set[str]:
        """
        Extract all imported module names from Python source using AST.
        
        Args:
            content: Python source code as string
            
        Returns:
            Set of imported module names (lowercase)
        """
        imports = set()
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            self.logger.debug("AST parsing failed, falling back to string detection")
            return imports
        except Exception as e:
            self.logger.debug(f"AST parsing error: {e}")
            return imports
        
        try:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name:
                            base_module = alias.name.split('.')[0].lower()
                            if base_module:
                                imports.add(base_module)
                
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        base_module = node.module.split('.')[0].lower()
                        if base_module:
                            imports.add(base_module)
        except Exception as e:
            self.logger.debug(f"Error walking AST: {e}")
        
        return imports
    
    def _match_usage_patterns(self, content: str) -> Dict[str, float]:
        """
        Match usage patterns for frameworks.
        
        Args:
            content: File content
            
        Returns:
            Dictionary of framework → weight
        """
        matches: Dict[str, float] = {}
        
        for framework, (app_type, description, patterns, weight) in self.framework_patterns.items():
            for pattern in patterns:
                if pattern.search(content):
                    matches[framework] = matches.get(framework, 0) + weight
                    break
        
        return matches
    
    
    def _detect_cli_patterns(self, content: str) -> Dict[str, float]:
        """Detects CLI framework patterns in content."""
        scores = {}
        
        cli_patterns = {
            r'argparse\.ArgumentParser\(': ('argparse', 1.0),
            r'click\.(?:command|group)\(': ('click', 1.5),
            r'@click\.': ('click', 1.0),
            r'typer\.(?:Typer|run)\(': ('typer', 1.0),
            r'fire\.Fire\(': ('fire', 1.0),
            r'sys\.argv\b': ('sys_argv', 0.5),
        }
        
        for pattern, (framework, weight) in cli_patterns.items():
            try:
                matches = len(re.findall(pattern, content))
                if matches > 0:
                    if framework not in scores:
                        scores[framework] = 0.0
                    scores[framework] += matches * weight
            except re.error as e:
                self.logger.debug(f"Regex error in pattern '{pattern}': {e}")
        
        return scores
    
    
    
    def _detect_daemon_patterns(self, content: str) -> Dict[str, float]:
        """
        Detect daemon/service patterns.
        
        Args:
            content: File content
            
        Returns:
            Dictionary of framework → weight
        """
        daemon_weights: Dict[str, float] = {}
        
        daemon_patterns = [
            ('observer.start()', 'watchdog', 1.0),
            ('observer.join()', 'watchdog', 1.0),
            ('Observer()', 'watchdog', 1.0),
            ('schedule.run_pending()', 'schedule', 1.0),
            ('scheduler.start()', 'apscheduler', 1.0),
        ]
        
        for pattern, framework, weight in daemon_patterns:
            if pattern in content:
                daemon_weights[framework] = daemon_weights.get(framework, 0) + weight
        
        # Check for top-level infinite loop
        try:
            tree = ast.parse(content)
            for node in tree.body:
                if isinstance(node, ast.While):
                    if isinstance(node.test, ast.Constant) and node.test.value is True:
                        daemon_weights['infinite_loop'] = daemon_weights.get('infinite_loop', 0) + 0.8
                    elif isinstance(node.test, ast.NameConstant) and node.test.value is True:
                        daemon_weights['infinite_loop'] = daemon_weights.get('infinite_loop', 0) + 0.8
                elif isinstance(node, ast.If):
                    for body_node in node.body:
                        if isinstance(body_node, ast.While):
                            if isinstance(body_node.test, ast.Constant) and body_node.test.value is True:
                                daemon_weights['infinite_loop'] = daemon_weights.get('infinite_loop', 0) + 0.8
                            elif isinstance(body_node.test, ast.NameConstant) and body_node.test.value is True:
                                daemon_weights['infinite_loop'] = daemon_weights.get('infinite_loop', 0) + 0.8
        except Exception:
            pass
        
        return daemon_weights
    
    def _detect_testing_patterns(self, content: str) -> Dict[str, float]:
        """
        Detect testing framework patterns.
        
        Args:
            content: File content
            
        Returns:
            Dictionary of framework → weight
        """
        testing_weights: Dict[str, float] = {}
        
        testing_patterns = [
            (r'def test_\w+', 'pytest', 0.7),
            (r'class Test\w*', 'unittest', 0.7),
            (r'class \w*TestCase', 'unittest', 0.8),
            (r'@pytest\.(?:fixture|mark|skip)', 'pytest', 1.0),
            (r'@unittest\.skip', 'unittest', 1.0),
            (r'unittest\.main\(\)', 'unittest', 1.0),
        ]
        
        for pattern_str, framework, weight in testing_patterns:
            try:
                if re.search(pattern_str, content):
                    testing_weights[framework] = testing_weights.get(framework, 0) + weight
            except re.error:
                pass
        
        return testing_weights
    
    def _calculate_app_type_weights(self, framework_weights: Dict[str, float]) -> Dict[AppType, float]:
        """
        Calculate app type weights from framework weights.
        
        Args:
            framework_weights: Dictionary of framework → weight
            
        Returns:
            Dictionary of AppType → total_weight
        """
        type_weights: Dict[AppType, float] = defaultdict(float)
        
        for framework, weight in framework_weights.items():
            if framework in self.framework_patterns:
                app_type = self.framework_patterns[framework][0]
                type_weights[app_type] += weight
        
        return type_weights


# ============================================================================
# MAIN TESTER CLASS
# ============================================================================

class RuntimeTester:
    """
    Unified runtime tester for all application types.
    
    Detects application type and applies appropriate testing strategy:
    - Standard: runpy.run_path()
    - GUI: headless import + startup test
    - SQL: in-memory/temp database
    - API: network check + graceful handling
    - Web: skip with INFO
    """
    
    def __init__(self, vfs: 'VirtualFileSystem'):
        """
        Initialize RuntimeTester.
        
        Args:
            vfs: VirtualFileSystem instance for reading files
        """
        self.vfs = vfs
        self._network_available = None
        self._project_python: Optional[str] = None
        self.token_counter = TokenCounter()
        self._framework_registry: Dict[str, Dict[str, str]] = {}
        self._framework_patterns: Dict[str, Tuple[AppType, str]] = {}
        self._load_framework_registry()
    
    
    def _discover_source_roots(self) -> List[str]:
        """
        Dynamically discover project source roots.
        
        Strategies:
        1. Explicit Packages: The parent of any top-level package (dir with __init__.py) is a root.
        2. Implicit Namespace Roots (PEP 420): Any top-level directory containing Python code 
        but NO __init__.py is considered a potential source root (e.g., 'src', 'lib', 'scripts').
        """
        roots = set([''])  # Project root is always a candidate
        
        try:
            # Use all files (real + staged)
            all_files = self.vfs.get_all_python_files()
            
            # Helper to check if a dir is an explicit package
            # We cache this to avoid repeated VFS checks
            package_dirs = set()
            for f in all_files:
                if f.endswith('__init__.py'):
                    package_dirs.add(str(Path(f).parent).replace('\\', '/'))

            # Strategy 1: Backtrack from explicit packages
            for pkg_path in package_dirs:
                current = pkg_path
                parent = str(Path(current).parent).replace('\\', '/')
                if parent == '.': parent = ''
                
                # Walk up as long as parent is also a package
                while parent in package_dirs:
                    current = parent
                    parent = str(Path(current).parent).replace('\\', '/')
                    if parent == '.': parent = ''
                
                roots.add(parent)
            
            # Strategy 2: Detect top-level implicit roots (PEP 420)
            for f in all_files:
                # Normalize path
                path = f.replace('\\', '/')
                parts = path.split('/')
                
                # If file is in a subdirectory (not in root)
                if len(parts) > 1:
                    top_level_dir = parts[0]
                    
                    # If this top-level dir is NOT an explicit package (no __init__.py)
                    # It acts as a namespace root (e.g., 'src', 'lib')
                    if top_level_dir not in package_dirs:
                        roots.add(top_level_dir)

        except Exception as e:
            logger.warning(f"Error discovering source roots: {e}")
            
        # Sort by length (shortest first) to prioritize root over nested folders, 
        # but standard alphabetical is fine too.
        return sorted(list(roots))
    
    
    def _load_framework_registry(self) -> None:
        """
        Load framework registry from JSON file with fallback to built-in patterns.

        Tries to load from config/framework_registry.json first, then falls back
        to built-in FRAMEWORK_PATTERNS. Updates self._framework_registry and 
        self._framework_patterns for use in detection.
        """
        # Try to load JSON registry
        try:
            registry_path = self.vfs.project_root / FRAMEWORK_REGISTRY_PATH
            if registry_path.exists():
                with open(registry_path, 'r', encoding='utf-8') as f:
                    self._framework_registry = json.load(f)
                logger.debug(f"Loaded framework registry from {registry_path}")
            else:
                logger.warning(f"Framework registry not found at {registry_path}, using built-in patterns")
                self._framework_registry = {}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse framework registry JSON: {e}, using built-in patterns")
            self._framework_registry = {}
        except Exception as e:
            logger.error(f"Error loading framework registry: {e}, using built-in patterns")
            self._framework_registry = {}
        
        # Category to AppType mapping
        category_to_app_type = {
            'gui': AppType.GUI,
            'tui': AppType.TUI,
            'database': AppType.SQL_SQLITE,  # Default to SQLite
            'network': AppType.API_DEPENDENT,
            'web': AppType.WEB_APP,
            'daemon': AppType.DAEMON,
            'cli': AppType.CLI,
            'testing': AppType.TESTING,
            'data_science': AppType.STANDARD,
            'misc': AppType.STANDARD,
        }
        
        # Build framework patterns from JSON registry
        json_count = 0
        for category, modules in self._framework_registry.items():
            app_type = category_to_app_type.get(category, AppType.STANDARD)
            for module_name, description in modules.items():
                if isinstance(description, str):
                    self._framework_patterns[module_name] = (app_type, description)
                    json_count += 1
        
        # Merge with built-in FRAMEWORK_PATTERNS (JSON takes priority)
        builtin_count = 0
        for module_name, (app_type, description) in FRAMEWORK_PATTERNS.items():
            if module_name not in self._framework_patterns:
                self._framework_patterns[module_name] = (app_type, description)
                builtin_count += 1
        
        logger.info(
            f"Loaded {len(self._framework_patterns)} framework patterns "
            f"({json_count} from JSON, {builtin_count} built-in)"
        )
        
        # Initialize enhanced framework detector with discovered source roots
        source_roots = self._discover_source_roots()
        logger.debug(f"Discovered source roots: {source_roots}")
        
        self.framework_detector = FrameworkDetector(
            vfs=self.vfs, 
            project_root=self.vfs.project_root,
            source_roots=source_roots
        )
        self.framework_detector.load_from_registry(self._framework_registry)
        
        logger.debug(f"Enhanced framework detector initialized with {len(self.framework_detector.framework_patterns)} patterns")
        
        # Debug: show loaded categories and counts
        json_categories = list(self._framework_registry.keys())
        total_json_frameworks = sum(len(mods) for mods in self._framework_registry.values())
        logger.debug(
            f"Framework registry loaded: {len(json_categories)} categories, "
            f"{total_json_frameworks} frameworks from JSON, "
            f"{len(self._framework_patterns)} total patterns"
        )
        
        if not json_categories:
            logger.warning(
                "JSON framework registry is empty or failed to load. "
                "Using built-in patterns only."
            )
    
    
    
    def _get_framework_mapping_info(self) -> Dict[str, Any]:
        """
        Returns debug information about loaded framework mappings for logging/troubleshooting.
        """
        json_counts = {}
        for category, modules in self._framework_registry.items():
            json_counts[category] = len(modules)
        
        builtin_count = len(FRAMEWORK_PATTERNS)
        
        return {
            "json_categories": list(self._framework_registry.keys()),
            "json_counts": json_counts,
            "total_json_frameworks": sum(json_counts.values()),
            "builtin_patterns": builtin_count,
            "total_patterns": len(self._framework_patterns),
            "detector_initialized": hasattr(self, 'framework_detector') and self.framework_detector is not None,
        }
    
    
    
    @staticmethod
    def _extract_imports_ast(content: str) -> Set[str]:
        """
        Extract all imported module names from Python source using AST.

        Uses abstract syntax tree parsing to get exact imports, avoiding false
        positives from strings/comments. Returns base module names (first part
        of dotted imports).

        Args:
            content: Python source code as string
            
        Returns:
            Set of imported module names (e.g., {'os', 'sys', 'tkinter'})
        """
        imports = set()
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            logger.debug("AST parsing failed for file, falling back to string detection")
            return imports
        except Exception as e:
            logger.debug(f"AST parsing error: {e}, falling back to string detection")
            return imports
        
        try:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    # Handle: import module, import module as alias
                    for alias in node.names:
                        if alias.name:
                            # Take first part of dotted import
                            base_module = alias.name.split('.')[0].lower()
                            if base_module:
                                imports.add(base_module)
                
                elif isinstance(node, ast.ImportFrom):
                    # Handle: from module import name
                    if node.module:
                        base_module = node.module.split('.')[0].lower()
                        if base_module:
                            imports.add(base_module)
        except Exception as e:
            logger.debug(f"Error walking AST: {e}")
            return imports
        
        return imports
    
    def _has_top_level_loop(self, content: str) -> bool:
        """Check if file contains a top-level infinite loop (module level or in main block)."""
        try:
            tree = ast.parse(content)
        except Exception:
            return False
        
        # Check top-level nodes
        for node in tree.body:
            # Check for while True at module level
            if isinstance(node, ast.While):
                # Check if test is constant True
                if isinstance(node.test, ast.Constant) and node.test.value is True:
                    return True
                # Support ast.NameConstant for older Python versions
                elif isinstance(node.test, ast.NameConstant) and node.test.value is True:
                    return True
            
            # Check for if __name__ == "__main__" block
            elif isinstance(node, ast.If):
                # Iterate through body of if statement
                for body_node in node.body:
                    if isinstance(body_node, ast.While):
                        # Check if test is constant True
                        if isinstance(body_node.test, ast.Constant) and body_node.test.value is True:
                            return True
                        # Support ast.NameConstant for older Python versions
                        elif isinstance(body_node.test, ast.NameConstant) and body_node.test.value is True:
                            return True
        
        return False
    
    
    
    def _get_project_python(self) -> str:
        """
        Возвращает путь к Python интерпретатору проекта (с кэшированием).
        """
        if self._project_python is None:
            try:
                self._project_python = self.vfs.get_project_python()
            except (AttributeError, Exception) as e:
                logger.warning(f"Failed to get project Python from VFS: {e}, falling back to sys.executable")
                self._project_python = sys.executable
        return self._project_python
    
    
    def _setup_test_environment(self, file_path: str, temp_dir: str) -> Tuple[Dict[str, str], List[str]]:
        """
        Подготавливает окружение для тестирования файла.
        
        Возвращает:
            (env_dict, pythonpath_list)
        """
        env = os.environ.copy()
        
        # Start with temp_dir as primary root
        pythonpath_parts = [temp_dir]
        
        # Add discovered source roots
        # This supports src/, app/, backend/, or any other structure dynamically
        source_roots = self._discover_source_roots()
        for root in source_roots:
            if root:  # Skip empty root (already added as temp_dir)
                root_path = Path(temp_dir) / root
                if root_path.exists():
                    pythonpath_parts.append(str(root_path))
        
        # Add original project root if different from temp_dir
        # (This is a fallback for things not copied to VFS, though VFS should be complete)
        project_root = str(self.vfs.project_root)
        if os.path.exists(project_root) and project_root != temp_dir:
            pythonpath_parts.append(project_root)
        
        # Set PYTHONPATH
        env['PYTHONPATH'] = os.pathsep.join(pythonpath_parts)
        
        return env, pythonpath_parts
    
    
    def _get_headless_env_for_framework(self, framework: str, app_type: AppType) -> Dict[str, str]:
        """Returns environment variables for headless testing of specific framework."""
        env = {}
        
        GUI_HEADLESS_CONFIG = {
            'pygame': {'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy', 'DISPLAY': ''},
            'pyqt5': {'QT_QPA_PLATFORM': 'offscreen', 'DISPLAY': ''},
            'pyqt6': {'QT_QPA_PLATFORM': 'offscreen', 'DISPLAY': ''},
            'pyside2': {'QT_QPA_PLATFORM': 'offscreen', 'DISPLAY': ''},
            'pyside6': {'QT_QPA_PLATFORM': 'offscreen', 'DISPLAY': ''},
            'pyqtgraph': {'QT_QPA_PLATFORM': 'offscreen', 'DISPLAY': ''},
            'kivy': {'KIVY_NO_CONSOLELOG': '1', 'KIVY_NO_FILELOG': '1', 'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy'},
            'arcade': {'PYGLET_HEADLESS': '1', 'SDL_VIDEODRIVER': 'dummy', 'DISPLAY': ''},
            'pyglet': {'PYGLET_HEADLESS': '1', 'SDL_VIDEODRIVER': 'dummy', 'DISPLAY': ''},
            'tkinter': {'DISPLAY': ':99', 'TK_SILENCE_DEPRECATION': '1'},
            'customtkinter': {'DISPLAY': ':99', 'TK_SILENCE_DEPRECATION': '1'},
            'wx': {'DISPLAY': ':99'},
            'turtle': {'DISPLAY': ':99'},
            'dearpygui': {'DISPLAY': ''},
        }
        
        TUI_HEADLESS_CONFIG = {
            'textual': {'TEXTUAL_DRIVER': 'headless', 'TERM': 'xterm-256color'},
            'rich': {'TERM': 'dumb', 'NO_COLOR': '1', 'FORCE_COLOR': '0'},
            'curses': {'TERM': 'dumb'},
            'blessed': {'TERM': 'dumb', 'NO_COLOR': '1'},
            'urwid': {'TERM': 'dumb', 'NO_COLOR': '1'},
            'npyscreen': {'TERM': 'dumb', 'NO_COLOR': '1'},
            'asciimatics': {'TERM': 'dumb', 'NO_COLOR': '1'},
            'prompt_toolkit': {'TERM': 'dumb', 'NO_COLOR': '1'},
        }
        
        if app_type == AppType.GUI:
            framework_lower = framework.lower() if framework else 'pygame'
            return GUI_HEADLESS_CONFIG.get(framework_lower, {})
        elif app_type == AppType.TUI:
            framework_lower = framework.lower() if framework else 'curses'
            return TUI_HEADLESS_CONFIG.get(framework_lower, {})
        
        return {}
    
    
    def _detect_gui_framework(self, file_path: str, temp_dir: str) -> Optional[str]:
        """Detects which GUI/TUI framework is used in the file by analyzing imports."""
        try:
            full_path = Path(temp_dir) / file_path
            content = full_path.read_text(encoding='utf-8', errors='replace')
            
            imports = self._extract_imports_ast(content)
            
            gui_frameworks = ['pygame', 'pyqt5', 'pyqt6', 'pyside2', 'pyside6', 'kivy', 'arcade', 
                            'pyglet', 'tkinter', 'customtkinter', 'wx', 'turtle', 'dearpygui', 'pyqtgraph']
            tui_frameworks = ['curses', 'textual', 'rich', 'blessed', 'urwid', 'prompt_toolkit', 
                            'npyscreen', 'asciimatics']
            
            all_frameworks = gui_frameworks + tui_frameworks
            
            for imp in imports:
                if imp in all_frameworks:
                    return imp
            
            return None
        except Exception as e:
            logger.debug(f"Error detecting GUI framework for {file_path}: {e}")
            return None
    
    
    async def _test_with_mock_fallback(self, file_path: str, temp_dir: str, app_type: AppType, framework: Optional[str], timeout: int) -> TestResult:
        """Performs mock-based import test as fallback when headless test times out without errors."""
        full_path = Path(temp_dir) / file_path
        safe_full_path = json.dumps(str(full_path))
        safe_temp_dir = json.dumps(str(temp_dir))
        
        if app_type == AppType.GUI:
            mock_script = f'''
import sys
import os
from unittest.mock import MagicMock
import importlib.util

sys.path.insert(0, {safe_temp_dir}.strip('"'))

# Mock GUI frameworks
gui_modules = [
    'pygame', 'tkinter', 'PyQt5', 'PyQt5.QtWidgets', 'PyQt5.QtCore', 'PyQt5.QtGui',
    'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
    'PySide2', 'PySide2.QtWidgets', 'PySide2.QtCore', 'PySide2.QtGui',
    'PySide6', 'PySide6.QtWidgets', 'PySide6.QtCore', 'PySide6.QtGui',
    'kivy', 'kivy.app', 'kivy.uix', 'kivy.uix.widget',
    'arcade', 'pyglet', 'wx', 'turtle', 'dearpygui', 'pyqtgraph',
    'customtkinter'
]

for module_name in gui_modules:
    sys.modules[module_name] = MagicMock()

try:
    spec = importlib.util.spec_from_file_location("gui_module", {safe_full_path}.strip('"'))
    if spec is None:
        print("MOCK_IMPORT_ERROR")
        sys.exit(1)
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print("MOCK_IMPORT_SUCCESS")
    sys.exit(0)
except Exception as e:
    print(f"MOCK_IMPORT_ERROR: {{e}}")
    sys.exit(1)
    '''
        else:  # AppType.TUI
            mock_script = f'''
import sys
import os
from unittest.mock import MagicMock
import importlib.util

sys.path.insert(0, {safe_temp_dir}.strip('"'))

# Mock TUI frameworks
tui_modules = [
    'curses', 'textual', 'rich', 'blessed', 'urwid', 'prompt_toolkit', 'npyscreen', 'asciimatics'
]

for module_name in tui_modules:
    sys.modules[module_name] = MagicMock()

try:
    spec = importlib.util.spec_from_file_location("tui_module", {safe_full_path}.strip('"'))
    if spec is None:
        print("MOCK_IMPORT_ERROR")
        sys.exit(1)
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print("MOCK_IMPORT_SUCCESS")
    sys.exit(0)
except Exception as e:
    print(f"MOCK_IMPORT_ERROR: {{e}}")
    sys.exit(1)
    '''
        
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', mock_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
                cwd=temp_dir,
            )
            
            if 'MOCK_IMPORT_SUCCESS' in result.stdout:
                return TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.PASSED,
                    message="Validated via mock import (headless test timed out)",
                    details=result.stdout,
                )
            else:
                return TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.FAILED,
                    message="Mock import validation failed",
                    details=result.stdout + result.stderr,
                )
        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=app_type,
                status=TestStatus.TIMEOUT,
                message="Mock import test timed out",
            )
        except Exception as e:
            return TestResult(
                file_path=file_path,
                app_type=app_type,
                status=TestStatus.ERROR,
                message=f"Mock import test error: {e}",
            )
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    async def analyze_project(self, files: List[str]) -> ProjectAnalysis:
        """
        Analyze project before testing.
        
        Determines:
        - Total size (tokens/chars)
        - Detected frameworks
        - App types for each file
        - Appropriate timeouts
        
        Args:
            files: List of file paths to analyze
            
        Returns:
            ProjectAnalysis with all metadata
        """
        total_tokens = 0
        total_chars = 0
        detected_frameworks: Dict[str, str] = {}
        file_types: Dict[str, List[str]] = {t.value: [] for t in AppType}
        
        for file_path in files:
            content = self.vfs.read_file(file_path)
            if content is None:
                continue
            
            # Count size
            tokens = self.token_counter.count(content)
            total_tokens += tokens
            total_chars += len(content)
            
            # Detect frameworks and app type
            frameworks, app_type = self._detect_frameworks(content, file_path)
            detected_frameworks.update(frameworks)
            file_types[app_type.value].append(file_path)
        
        # Calculate timeouts
        size_category, total_timeout, per_file_timeout = TimeoutCalculator.calculate(
            total_tokens, len(files)
        )
        
        analysis = ProjectAnalysis(
            total_tokens=total_tokens,
            total_chars=total_chars,
            file_count=len(files),
            size_category=size_category,
            total_timeout_seconds=total_timeout,
            per_file_timeout_seconds=per_file_timeout,
            detected_frameworks=detected_frameworks,
            file_types=file_types,
        )
        
        logger.info(
            f"Project analysis: {total_tokens} tokens, {len(files)} files, "
            f"category={size_category.value}, timeout={total_timeout}s, "
            f"frameworks={list(detected_frameworks.keys())}"
        )
        
        return analysis
    
    async def run_all_tests(
        self,
        files: List[str],
        temp_dir: str,
        total_timeout: int,
        analysis: Optional[ProjectAnalysis] = None,
    ) -> RuntimeTestSummary:
        """
        Run tests for all files with appropriate strategies.
        
        Args:
            files: List of file paths to test
            temp_dir: Materialized VFS directory
            total_timeout: Total timeout budget
            analysis: Pre-computed analysis (optional)
            
        Returns:
            RuntimeTestSummary with all results
        """
        import time
        start_time = time.time()
        
        summary = RuntimeTestSummary()
        
        if analysis is None:
            analysis = await self.analyze_project(files)
        
        summary.analysis = analysis
        
        # Group files by type
        for file_path in files:
            if not file_path.endswith('.py'):
                continue
            
            # Determine app type (with transitive detection)
            app_type = self._detect_transitive_frameworks(file_path, analysis)
            
            # Check if we've exceeded total timeout
            elapsed = time.time() - start_time
            if elapsed > total_timeout:
                summary.add_result(TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.SKIPPED,
                    message=f"Skipped: total timeout budget ({total_timeout}s) exceeded",
                ))
                continue
            
            # Get appropriate timeout for this file type
            file_timeout = TimeoutCalculator.get_timeout_for_type(app_type)
            remaining = total_timeout - elapsed
            actual_timeout = min(file_timeout, int(remaining))
            
            if actual_timeout < 10:  # Less than 10 seconds left
                summary.add_result(TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.SKIPPED,
                    message="Skipped: insufficient time remaining",
                ))
                continue
            
            # Run appropriate test
            result = await self._test_file(file_path, temp_dir, app_type, actual_timeout)
            summary.add_result(result)
        
        summary.total_duration_ms = (time.time() - start_time) * 1000
        
        logger.info(
            f"Runtime tests complete: {summary.passed}/{summary.total_files} passed, "
            f"{summary.failed} failed, {summary.skipped} skipped, "
            f"{summary.total_duration_ms:.0f}ms total"
        )
        
        return summary
    
    # ========================================================================
    # FRAMEWORK DETECTION
    # ========================================================================
    
    def _detect_frameworks(self, content: str, file_path: str) -> Tuple[Dict[str, str], AppType]:
        """
        Detects frameworks using enhanced FrameworkDetector with JSON registry support.
        """
        # Use enhanced detector with JSON registry
        detected, detected_app_type = self.framework_detector.detect(content, file_path)
        
        app_type = detected_app_type
        
        # Apply priority logic (GUI/Web > TUI > CLI > API > Standard)
        # This ensures consistent type selection across the project
        framework_priority = {
            AppType.GUI: 100,
            AppType.WEB_APP: 90,
            AppType.TUI: 80,
            AppType.SQL_POSTGRES: 75,
            AppType.SQL_SQLITE: 70,
            AppType.DAEMON: 65,
            AppType.API_DEPENDENT: 60,
            AppType.CLI: 50,
            AppType.TESTING: 40,
            AppType.STANDARD: 10,
        }
        
        # Check for async patterns (fallback detection)
        if 'async' not in detected and re.search(r'async\s+def|await\s+', content):
            detected['async'] = 'Async/await patterns detected'
        
        # Check for top-level loop (Daemon indicator) - fallback
        if self._has_top_level_loop(content):
            detected['top_level_loop'] = 'Top-level event loop detected'
            if app_type == AppType.STANDARD:
                app_type = AppType.DAEMON

        # Special Case: CLI decorators (often not just imports)
        if app_type != AppType.CLI:
            cli_patterns = ['@click.', '@app.command', 'typer.run', 'fire.Fire']
            if any(p in content for p in cli_patterns):
                detected['cli_pattern'] = 'CLI decorator detected'
                app_type = AppType.CLI
        
        # Ensure we don't downgrade from higher priority types
        current_priority = framework_priority.get(app_type, 0)
        
        # Double-check import-based detection as fallback
        imports = self._extract_imports_ast(content)
        for module in imports:
            if module in self._framework_patterns:
                pattern_app_type, description = self._framework_patterns[module]
                pattern_priority = framework_priority.get(pattern_app_type, 0)
                if pattern_priority > current_priority:
                    app_type = pattern_app_type
                    current_priority = pattern_priority
        
        return detected, app_type




    def _detect_transitive_frameworks(self, file_path: str, analysis: ProjectAnalysis) -> AppType:
        """
        Check if file imports project modules that use specific frameworks.
        
        Uses FrameworkDetector's recursive analysis to propagate AppTypes 
        from imported modules to the importer (entry points like main.py).
        
        Priority: GUI > WEB > TUI > DAEMON > CLI
        """
        own_type = self._get_file_app_type(file_path, analysis)
        
        STRONG_TYPES = {AppType.GUI, AppType.WEB_APP, AppType.TUI, AppType.DAEMON, AppType.CLI}
        
        if own_type in STRONG_TYPES:
            return own_type
        
        content = self.vfs.read_file(file_path)
        if content is None:
            return own_type
        
        try:
            is_entry = self._is_entry_point(file_path, content)
            
            if is_entry:
                deep_scan_result = self._deep_scan_entry_point_frameworks(file_path)
                if deep_scan_result in STRONG_TYPES:
                    logger.debug(f"Entry point {file_path} detected as {deep_scan_result.value} via deep scan")
                    return deep_scan_result
            else:
                detected_frameworks, detected_app_type = self.framework_detector.detect(content, file_path)
                
                if detected_app_type in STRONG_TYPES:
                    logger.debug(
                        f"Transitive framework detection: {file_path} -> {detected_app_type.value} "
                        f"(frameworks: {list(detected_frameworks.keys())})"
                    )
                    return detected_app_type
        
        except Exception as e:
            logger.debug(f"Error in transitive framework detection for {file_path}: {e}")
        
        return own_type



    def _is_entry_point(self, file_path: str, content: Optional[str] = None) -> bool:
        """Determines if a file is an entry point (main script that launches the application)."""
        ENTRY_POINT_NAMES = {'main.py', 'run.py', 'app.py', 'start.py', 'launcher.py', 'cli.py', '__main__.py', 'manage.py', 'server.py', 'bot.py', 'game.py'}
        
        file_name = Path(file_path).name.lower()
        if file_name in ENTRY_POINT_NAMES:
            return True
        
        if content is None:
            try:
                content = self.vfs.read_file(file_path)
            except Exception:
                return False
        
        if content is None:
            return False
        
        try:
            pattern = r'if\s+__name__\s*==\s*["\']__main__["\']'
            return bool(re.search(pattern, content))
        except Exception:
            return False


    def _deep_scan_entry_point_frameworks(self, file_path: str) -> AppType:
        """Performs deep recursive scan of all imports from an entry point file to detect long-running frameworks. Unlike _detect_recursive which has depth limit, this scans ALL reachable files."""
        STRONG_TYPES = {AppType.GUI, AppType.WEB_APP, AppType.TUI, AppType.DAEMON}
        
        visited: Set[str] = set()
        to_visit: List[str] = [file_path]
        found_strong_type: Optional[AppType] = None
        
        try:
            while to_visit and found_strong_type is None:
                current_path = to_visit.pop(0)
                
                if current_path in visited:
                    continue
                
                visited.add(current_path)
                
                try:
                    content = self.vfs.read_file(current_path)
                    
                    if content is None and self.vfs.project_root:
                        try:
                            full_path = self.vfs.project_root / current_path
                            if full_path.exists():
                                content = full_path.read_text(encoding='utf-8', errors='replace')
                        except Exception:
                            pass
                    
                    if content is None:
                        continue
                    
                    detected, app_type = self.framework_detector._detect_single_file(content, current_path)
                    
                    if app_type in STRONG_TYPES:
                        found_strong_type = app_type
                        logger.info(f"Entry point {file_path} inherits {app_type.value} from {current_path}")
                        break
                    
                    local_imports = self.framework_detector._extract_local_imports(content)
                    
                    for module_name in local_imports:
                        resolved_path = self.framework_detector._resolve_import_to_file(module_name, current_path)
                        if resolved_path and resolved_path not in visited:
                            to_visit.append(resolved_path)
                
                except Exception:
                    continue
        
        except Exception as e:
            logger.error(f"Error in deep scan for entry point {file_path}: {e}")
        
        if found_strong_type:
            return found_strong_type
        
        return AppType.STANDARD


    def _get_file_app_type(self, file_path: str, analysis: ProjectAnalysis) -> AppType:
        """Get app type for specific file from analysis."""
        for app_type_str, files in analysis.file_types.items():
            if file_path in files:
                try:
                    return AppType(app_type_str)
                except ValueError:
                    logger.warning(
                        f"Invalid app type '{app_type_str}' for file {file_path}, "
                        f"falling back to STANDARD"
                    )
                    return AppType.STANDARD
        return AppType.STANDARD
    
    # ========================================================================
    # FILE TESTING
    # ========================================================================
    
    async def _test_file(
                self,
                file_path: str,
                temp_dir: str,
                app_type: AppType,
                timeout: int,
            ) -> TestResult:
            """
            Test a single file with appropriate strategy.
            
            Args:
                file_path: Relative file path
                temp_dir: Temp directory with materialized VFS
                app_type: Detected application type
                timeout: Timeout for this file
                
            Returns:
                TestResult
            """
            import time
            start_time = time.time()
            
            logger.debug(f"Testing {file_path} as {app_type.value} (timeout={timeout}s)")
            
            try:
                if app_type == AppType.WEB_APP:
                    result = await self._test_web_app(file_path, temp_dir)
                elif app_type == AppType.GUI:
                    result = await self._test_gui(file_path, temp_dir, timeout)
                elif app_type == AppType.TUI:
                    result = await self._test_tui(file_path, temp_dir, timeout)
                elif app_type == AppType.SQL_SQLITE:
                    result = await self._test_sqlite(file_path, temp_dir, timeout)
                elif app_type == AppType.SQL_POSTGRES:
                    result = await self._test_postgres(file_path, temp_dir, timeout)
                elif app_type == AppType.SQL_GENERIC:
                    result = await self._test_sql_file(file_path, temp_dir, timeout)
                elif app_type == AppType.API_DEPENDENT:
                    result = await self._test_api_dependent(file_path, temp_dir, timeout)
                elif app_type == AppType.DAEMON:
                    result = await self._test_daemon(file_path, temp_dir, timeout)
                elif app_type == AppType.INTERACTIVE:
                    result = await self._test_interactive(file_path, temp_dir, timeout)
                elif app_type == AppType.CLI:
                    result = await self._test_cli(file_path, temp_dir, timeout)
                elif app_type == AppType.TESTING:
                    result = await self._test_testing_framework(file_path, temp_dir, timeout)
                else:
                    # Check if this is a package module (not a standalone script)
                    if self._is_inside_package(file_path, temp_dir):
                        # Read file to check if it has main guard
                        try:
                            full_path = Path(temp_dir) / file_path
                            content = full_path.read_text(encoding='utf-8', errors='replace')
                            if not self._has_main_guard(content):
                                # Package module without main guard - test via import
                                result = await self._test_package_module(file_path, temp_dir, timeout)
                                result.app_type = AppType.PACKAGE_MODULE
                                return result
                        except Exception as e:
                            logger.debug(f"Failed to check main guard for {file_path}: {e}")
                            # Fall back to standard testing
                    
                    # Default to standard testing
                    result = await self._test_standard(file_path, temp_dir, timeout)
                
                result.duration_ms = (time.time() - start_time) * 1000
                return result
                
            except Exception as e:
                logger.error(f"Error testing {file_path}: {e}", exc_info=True)
                return TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.ERROR,
                    message=f"Test execution error: {e}",
                    duration_ms=(time.time() - start_time) * 1000,
                )
    
    # ========================================================================
    # STANDARD TESTING
    # ========================================================================
    
    async def _test_standard(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test standard Python script execution.
        
        Runs the script using runpy.run_path() with proper sys.path configuration.
        Handles SystemExit and exceptions gracefully.
        """
        logger.info(f"Testing standard Python script: {file_path}")
        
        # Get full path in temp_dir
        full_path = Path(temp_dir) / file_path
        if not full_path.exists():
            return TestResult(
                file_path=file_path,
                app_type=AppType.STANDARD,
                status=TestStatus.FAILED,
                message=f"File not found in temp directory: {file_path}",
                duration_ms=0,
            )
        
        # Build pythonpath_parts (project roots to prepend to sys.path)
        pythonpath_parts = []
        
        # Add temp_dir as primary root
        pythonpath_parts.append(temp_dir)
        
        # Add src/ if it exists
        src_dir = Path(temp_dir) / 'src'
        if src_dir.exists():
            pythonpath_parts.append(str(src_dir))
        
        # Add app/ if it exists
        app_dir = Path(temp_dir) / 'app'
        if app_dir.exists():
            pythonpath_parts.append(str(app_dir))
        
        # Escape path for Python string
        safe_full_path = str(full_path).replace('\\', '\\\\').replace('"', '\\"')
        
        # Build command to run with proper sys.path configuration
        # FIX: Do NOT clear sys.path, just prepend project roots to ensure priority
        # Clearing sys.path removes standard library paths on some systems
        command_script = f"""
import sys
import os
import runpy
import traceback

# Prepend project roots to sys.path
project_roots = {json.dumps(pythonpath_parts)}
for root in reversed(project_roots):  # Add in reverse to maintain priority (first item ends up first)
    if root not in sys.path:
        sys.path.insert(0, root)

try:
    # Run the script
    runpy.run_path(r"{safe_full_path}", run_name='__main__')
    print("RUNTIME_SUCCESS: Execution completed")
except SystemExit as e:
    # Script called sys.exit() - check if it's an error
    if e.code != 0 and e.code is not None:
        print(f"RUNTIME_SYSTEM_EXIT: Script exited with code {{e.code}}")
        sys.exit(e.code)
    else:
        print("RUNTIME_SUCCESS: Script exited normally")
except Exception as e:
    print(f"RUNTIME_ERROR: {{type(e).__name__}}: {{e}}")
    traceback.print_exc()
    sys.exit(1)
    """
        
        # Run the script
        start_time = time.time()
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', command_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Parse output
            stdout = result.stdout
            stderr = result.stderr
            
            # Check for success markers
            if 'RUNTIME_SUCCESS' in stdout:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.STANDARD,
                    status=TestStatus.PASSED,
                    message="Script executed successfully",
                    duration_ms=duration_ms,
                )
            
            # Check for errors
            if 'RUNTIME_ERROR' in stdout or result.returncode != 0:
                error_msg = stdout if 'RUNTIME_ERROR' in stdout else stderr
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.STANDARD,
                    status=TestStatus.FAILED,
                    message="Script execution failed",
                    details=error_msg,
                    duration_ms=duration_ms,
                )
            
            # Default: success if no error markers
            return TestResult(
                file_path=file_path,
                app_type=AppType.STANDARD,
                status=TestStatus.PASSED,
                message="Script executed successfully",
                duration_ms=duration_ms,
            )
            
        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000
            return TestResult(
                file_path=file_path,
                app_type=AppType.STANDARD,
                status=TestStatus.TIMEOUT,
                message=f"Script execution timed out after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return TestResult(
                file_path=file_path,
                app_type=AppType.STANDARD,
                status=TestStatus.ERROR,
                message=f"Error running script: {e}",
                details=traceback.format_exc(),
                duration_ms=duration_ms,
            )
    
    
    
    async def _test_package_module(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test package/module import.
        
        Imports the module and checks for Click decorators that might cause issues.
        Uses proper sys.path configuration without clearing it.
        """
        logger.info(f"Testing package module import: {file_path}")
        
        # Convert file path to module name
        # e.g., "app/services/auth.py" -> "app.services.auth"
        module_name = file_path.replace('\\', '/').replace('/', '.').replace('.py', '')
        
        # Build pythonpath_parts
        pythonpath_parts = []
        
        # Add temp_dir as primary root
        pythonpath_parts.append(temp_dir)
        
        # Add src/ if it exists
        src_dir = Path(temp_dir) / 'src'
        if src_dir.exists():
            pythonpath_parts.append(str(src_dir))
        
        # Add app/ if it exists
        app_dir = Path(temp_dir) / 'app'
        if app_dir.exists():
            pythonpath_parts.append(str(app_dir))
        
        # Build proper import script
        # FIX: Do NOT clear sys.path, just prepend project roots
        import_script = f"""
import sys
import os
import traceback
import inspect

# Add project roots to sys.path
for root in reversed({json.dumps(pythonpath_parts)}):
    if root not in sys.path:
        sys.path.insert(0, root)

try:
    # Import the module
    __import__('{module_name}')
    print("IMPORT_SUCCESS: Module imported successfully")
    
    # Check for Click decorators that might cause issues
    if '{module_name}' in sys.modules:
        module = sys.modules['{module_name}']
        
        click_objects = []
        for name in dir(module):
            try:
                obj = getattr(module, name)
                if hasattr(obj, '__name__') and hasattr(obj, '__module__'):
                    if obj.__module__ == '{module_name}':
                        # Check for click params or source code decorator
                        if hasattr(obj, '__click_params__'):
                            click_objects.append(name)
                        elif hasattr(obj, '__code__'):
                            try:
                                src = inspect.getsource(obj)
                                if '@click' in src:
                                    click_objects.append(name)
                            except:
                                pass
            except:
                pass
        
        if click_objects:
            print(f"IMPORT_WARNING: Module contains Click-decorated objects: {{', '.join(click_objects)}}")
            print("Note: Click decorators may cause issues if module is imported multiple times")
    
    sys.exit(0)
    
except Exception as e:
    print(f"IMPORT_ERROR: {{type(e).__name__}}: {{e}}")
    traceback.print_exc()
    sys.exit(1)
    """
        
        # Run the import test
        start_time = time.time()
        try:
            result = await asyncio.create_subprocess_exec(
                self._get_project_python(), '-c', import_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=temp_dir,
            )
            
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    result.communicate(),
                    timeout=timeout,
                )
                stdout = stdout_bytes.decode('utf-8', errors='replace')
                stderr = stderr_bytes.decode('utf-8', errors='replace')
                returncode = result.returncode
            except asyncio.TimeoutError:
                result.kill()
                await result.wait()
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.PACKAGE_MODULE,
                    status=TestStatus.TIMEOUT,
                    message=f"Module import timed out after {timeout}s",
                    duration_ms=duration_ms,
                )
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Parse output
            if 'IMPORT_SUCCESS' in stdout:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.PACKAGE_MODULE,
                    status=TestStatus.PASSED,
                    message="Module imported successfully",
                    duration_ms=duration_ms,
                )
            
            if 'IMPORT_WARNING' in stdout:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.PACKAGE_MODULE,
                    status=TestStatus.PASSED,
                    message="Module imported with warnings (Click decorators detected)",
                    details=stdout,
                    duration_ms=duration_ms,
                )
            
            if 'IMPORT_ERROR' in stdout or returncode != 0:
                error_msg = stdout if 'IMPORT_ERROR' in stdout else stderr
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.PACKAGE_MODULE,
                    status=TestStatus.FAILED,
                    message="Module import failed",
                    details=error_msg,
                    duration_ms=duration_ms,
                )
            
            # Default: success
            return TestResult(
                file_path=file_path,
                app_type=AppType.PACKAGE_MODULE,
                status=TestStatus.PASSED,
                message="Module imported successfully",
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return TestResult(
                file_path=file_path,
                app_type=AppType.PACKAGE_MODULE,
                status=TestStatus.ERROR,
                message=f"Error importing module: {e}",
                details=traceback.format_exc(),
                duration_ms=duration_ms,
            )

    
    def _is_inside_package(self, file_path: str, temp_dir: str) -> bool:
        """Check if file is inside a Python package (has __init__.py in parent directory)."""
        try:
            full_path = Path(temp_dir) / file_path
            parent_dir = full_path.parent
            
            # Check if parent directory has __init__.py
            if (parent_dir / "__init__.py").exists():
                return True
            
            # Also check grandparent for nested packages
            if (parent_dir.parent / "__init__.py").exists():
                return True
            
            return False
        except Exception:
            return False
     
      
    def _has_main_guard(self, content: str) -> bool:
        """Check if Python file contains if __name__ == '__main__' guard."""
        import re
        try:
            pattern = r'if\s+__name__\s*==\s*[\"\']__main__[\"\']:'
            return bool(re.search(pattern, content))
        except re.error:
            return False
        
        
    def _file_path_to_module(self, file_path: str) -> str:
        """Convert file path to Python module name (e.g., 'app/utils/helper.py' -> 'app.utils.helper')."""
        # Remove .py extension
        module = file_path[:-3]
        
        # Replace path separators with dots
        module = module.replace('/', '.').replace('\\', '.')
        
        # Remove __init__ suffix if present
        if module.endswith('.__init__'):
            module = module[:-9]
        
        return module
    
    
    def _is_potential_long_running(self, content: str) -> bool:
        """Check if file content suggests a long-running application (loops, servers, GUIs)."""
        try:
            patterns = [
                r'while\s+(?:True|1)\s*:',
                r'\.run_forever\(',
                r'\.serve_forever\(',
                r'\.mainloop\(',
                r'\.start_polling\(',
                r'uvicorn\.run\(',
                r'gunicorn',
                r'app\.run\(',
                r'bot\.run\(',
                r'client\.run\(',
                r'asyncio\.run\(',
                r'sys\.exit\(app\.exec',
            ]
            return any(re.search(p, content) for p in patterns)
        except Exception:
            return False
    
    
    # ========================================================================
    # GUI TESTING
    # ========================================================================
    
    async def _test_gui(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """Test GUI application using standalone script execution in headless mode."""
        try:
            framework = self._detect_gui_framework(file_path, temp_dir)
            headless_env = self._get_headless_env_for_framework(framework or 'pygame', AppType.GUI)
            
            safe_file_path = json.dumps(file_path)
            safe_temp_dir = json.dumps(str(temp_dir))
            
            # Build environment setup code
            env_setup_lines = []
            for key, value in headless_env.items():
                env_setup_lines.append(f"os.environ['{key}'] = '{value}'")
            env_setup_code = '\n'.join(env_setup_lines) if env_setup_lines else "pass"
            
            # Generate standard Python script (not pytest)
            test_content = f"""
import os
import sys
import importlib.util

# Setup headless environment
{env_setup_code}

# Add temp_dir to path
sys.path.insert(0, {safe_temp_dir}.strip('"'))

file_path = {safe_file_path}.strip('"')

try:
    # Import the module using importlib
    spec = importlib.util.spec_from_file_location("gui_module", file_path)
    if spec is None:
        print("ERROR: Could not load spec for file")
        sys.exit(1)
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    # Check for GUI classes
    gui_classes = ['Game', 'App', 'Application', 'Window', 'Main', 'MainWindow']
    found = []
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name in gui_classes:
            found.append(name)
    
    if found:
        print(f"FOUND_CLASSES: {{', '.join(found)}}")
    
    print("GUI_TEST_PASSED")
    sys.exit(0)
    
except Exception as e:
    print(f"ERROR: {{type(e).__name__}}: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
    """
            
            headless_timeout = min(timeout, 15)
            code, stdout, stderr = await self._run_generated_script(temp_dir, test_content, headless_timeout)
            output = stdout + stderr
            
            if code == 0:
                msg = "GUI application imported successfully in headless mode"
                if "FOUND_CLASSES:" in output:
                    classes = output.split("FOUND_CLASSES:")[1].split('\n')[0].strip()
                    msg += f" (Found: {classes})"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.GUI,
                    status=TestStatus.PASSED,
                    message=msg,
                    details=output
                )
            elif code == -1:
                logger.debug(f"GUI headless test timed out for {file_path}, falling back to mock import")
                return await self._test_with_mock_fallback(file_path, temp_dir, AppType.GUI, framework, 30)
            else:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.GUI,
                    status=TestStatus.FAILED,
                    message="GUI import failed",
                    details=output[-2000:]
                )
        except Exception as e:
            logger.error(f"Error in _test_gui for {file_path}: {e}", exc_info=True)
            return TestResult(
                file_path=file_path,
                app_type=AppType.GUI,
                status=TestStatus.ERROR,
                message=f"GUI test error: {e}",
            )
    
    
    
    async def _run_generated_script(
        self, 
        temp_dir: str, 
        test_content: str, 
        timeout: int
    ) -> Tuple[int, str, str]:
        """
        Generates a temporary Python script and executes it.
        
        Args:
            temp_dir: Working directory
            test_content: Python script content to execute
            timeout: Execution timeout in seconds
            
        Returns:
            Tuple of (returncode, stdout, stderr)
        """
        test_filename = f"test_runtime_{uuid.uuid4().hex[:8]}.py"
        test_path = Path(temp_dir) / test_filename
        
        try:
            test_path.write_text(test_content, encoding='utf-8')
            
            python_path = self._get_project_python()
            
            # Prepare environment with PYTHONPATH to ensure imports work
            env = os.environ.copy()
            env['PYTHONPATH'] = os.pathsep.join([temp_dir, env.get('PYTHONPATH', '')])
            
            # Run the script directly (not via pytest)
            result = subprocess.run(
                [python_path, test_filename],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
                env=env,
            )
            
            return result.returncode, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout expired"
        except Exception as e:
            return 1, "", str(e)
        finally:
            # Cleanup
            try:
                if test_path.exists():
                    test_path.unlink()
            except:
                pass
    
    
    
    async def _test_tui(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """Test TUI application using standalone script execution with mocked terminal."""
        try:
            framework = self._detect_gui_framework(file_path, temp_dir)
            headless_env = self._get_headless_env_for_framework(framework or 'curses', AppType.TUI)
            
            safe_file_path = json.dumps(file_path)
            safe_temp_dir = json.dumps(str(temp_dir))
            
            # Build environment setup code
            env_setup_lines = []
            for key, value in headless_env.items():
                env_setup_lines.append(f"os.environ['{key}'] = '{value}'")
            env_setup_code = '\n'.join(env_setup_lines) if env_setup_lines else "pass"
            
            # Add curses mocking for curses-based TUIs
            curses_mock = ""
            if framework and framework.lower() == 'curses':
                curses_mock = """
    # Mock curses module before import
    from unittest.mock import MagicMock
    sys.modules['curses'] = MagicMock()
    """
            
            # Generate standard Python script (not pytest)
            test_content = f"""
import os
import sys
import importlib.util

# Setup headless environment
{env_setup_code}

{curses_mock}

# Add temp_dir to path
sys.path.insert(0, {safe_temp_dir}.strip('"'))

file_path = {safe_file_path}.strip('"')

try:
    # Import the module using importlib
    spec = importlib.util.spec_from_file_location("tui_module", file_path)
    if spec is None:
        print("ERROR: Could not load spec for file")
        sys.exit(1)
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    print("TUI_TEST_PASSED")
    sys.exit(0)
    
except Exception as e:
    print(f"ERROR: {{type(e).__name__}}: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
    """
            
            headless_timeout = min(timeout, 10)
            code, stdout, stderr = await self._run_generated_script(temp_dir, test_content, headless_timeout)
            output = stdout + stderr
            
            if code == 0:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.TUI,
                    status=TestStatus.PASSED,
                    message="TUI application imported successfully (mocked terminal)",
                )
            elif code == -1:
                logger.debug(f"TUI headless test timed out for {file_path}, falling back to mock import")
                return await self._test_with_mock_fallback(file_path, temp_dir, AppType.TUI, framework, 30)
            else:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.TUI,
                    status=TestStatus.FAILED,
                    message="TUI import failed",
                    details=(stdout + stderr)[-2000:]
                )
        except Exception as e:
            logger.error(f"Error in _test_tui for {file_path}: {e}", exc_info=True)
            return TestResult(
                file_path=file_path,
                app_type=AppType.TUI,
                status=TestStatus.ERROR,
                message=f"TUI test error: {e}",
            )
    
    
    # ========================================================================
    # SQL TESTING
    # ========================================================================
    
    async def _test_sqlite(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test SQLite operations with temp database.
        
        Strategy:
        1. Monkey-patch sqlite3.connect to redirect to temp DB
        2. Import module without executing __main__
        3. Verify tables were created or classes instantiated
        """
        full_path = Path(temp_dir) / file_path
        safe_temp_dir = json.dumps(str(temp_dir))
        safe_full_path = json.dumps(str(full_path))
        
        test_script = f'''
import sys
import os
import sqlite3
import tempfile

sys.path.insert(0, {safe_temp_dir}.strip('"'))

# Create unique temp database
db_path = os.path.join(tempfile.gettempdir(), f'test_{{os.getpid()}}_{{id(object())}}.db')

# Monkey-patch sqlite3.connect
original_connect = sqlite3.connect

def patched_connect(database, *args, **kwargs):
    if database == ':memory:':
        return original_connect(database, *args, **kwargs)
    return original_connect(db_path, *args, **kwargs)

sqlite3.connect = patched_connect

import importlib.util

try:
    # Load module WITHOUT executing __main__ block
    spec = importlib.util.spec_from_file_location("test_module", {safe_full_path}.strip('"'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    print("SQL_IMPORT_SUCCESS: Module imported successfully")
    
    # Try to find and instantiate database-related classes
    db_class_names = ['Database', 'DB', 'Manager', 'Storage', 'Repository', 
                    'CurrencyManager', 'DataManager', 'DBManager', 'Store']
    found_class = None
    instance = None
    
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and not name.startswith('_'):
            # Check if class name suggests database functionality
            if any(db_name.lower() in name.lower() for db_name in db_class_names):
                found_class = name
                try:
                    # Try to instantiate (this often creates tables)
                    instance = obj()
                    print(f"SQL_INIT_SUCCESS: Instantiated {{name}}")
                    break
                except TypeError as e:
                    # Constructor requires arguments - that's OK, import worked
                    print(f"SQL_CLASS_FOUND: {{name}} (requires args: {{e}})")
                    break
                except Exception as e:
                    print(f"SQL_INIT_WARNING: {{name}} init failed: {{e}}")
    
    # Check if database was created
    if os.path.exists(db_path):
        conn = original_connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        if tables:
            print(f"SQL_TABLES: Created {{len(tables)}} table(s): {{', '.join(tables)}}")
    
    sys.exit(0)
    
except Exception as e:
    print(f"SQL_ERROR: {{type(e).__name__}}: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
    
finally:
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except:
        pass
    '''
        
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', test_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            output = result.stdout + result.stderr
            
            if 'SQL_IMPORT_SUCCESS' in result.stdout or 'SQL_INIT_SUCCESS' in result.stdout:
                # Extract success message
                success_lines = [l for l in result.stdout.split('\n') if 'SQL_IMPORT_SUCCESS' in l or 'SQL_INIT_SUCCESS' in l]
                msg = success_lines[0] if success_lines else "SQL module imported successfully"
                
                # Append table info if found
                if 'SQL_TABLES' in result.stdout:
                    table_info = [l for l in result.stdout.split('\n') if 'SQL_TABLES' in l][0]
                    msg += f" ({table_info})"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.SQL_SQLITE,
                    status=TestStatus.PASSED,
                    message=msg,
                    details=result.stdout,
                )
            else:
                error_lines = [l for l in output.split('\n') if 'SQL_ERROR' in l]
                error_msg = error_lines[0] if error_lines else "SQL execution failed"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.SQL_SQLITE,
                    status=TestStatus.FAILED,
                    message=error_msg.replace('SQL_ERROR: ', ''),
                    details=output[-1000:],
                )
                
        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=AppType.SQL_SQLITE,
                status=TestStatus.TIMEOUT,
                message=f"SQLite operations timed out after {timeout}s",
            )


    
    async def _test_postgres(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test PostgreSQL operations.
        
        Strategy:
        1. Check for POSTGRES_TEST_URL or DATABASE_URL
        2. If available: run with import-only strategy (no __main__)
        3. If not: fallback to _test_import_only
        """
        # Check for test database URL
        test_url = os.environ.get('POSTGRES_TEST_URL') or os.environ.get('DATABASE_URL')
        
        if not test_url:
            # No test database — do import-only check
            return await self._test_import_only(
                file_path, temp_dir, timeout,
                AppType.SQL_POSTGRES,
                message="PostgreSQL code validated (import-only, no test database configured)",
                suggestion="Set POSTGRES_TEST_URL environment variable for full database testing",
            )
        
        # Full test with actual database using import strategy
        full_path = Path(temp_dir) / file_path
        safe_temp_dir = json.dumps(str(temp_dir))
        safe_full_path = json.dumps(str(full_path))
        
        test_script = f'''
import sys
import os

sys.path.insert(0, {safe_temp_dir}.strip('"'))

# Override database URL to use test database
os.environ['DATABASE_URL'] = os.environ.get('POSTGRES_TEST_URL', os.environ.get('DATABASE_URL', ''))

import importlib.util

try:
    # Load module WITHOUT executing __main__ block
    spec = importlib.util.spec_from_file_location("test_module", {safe_full_path}.strip('"'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    print("POSTGRES_IMPORT_SUCCESS: Module imported successfully")
    
    # Try to find and instantiate database-related classes
    db_class_names = ['Database', 'DB', 'Manager', 'Storage', 'Repository', 'Model', 'Connection']
    
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and not name.startswith('_'):
            if any(db_name.lower() in name.lower() for db_name in db_class_names):
                try:
                    instance = obj()
                    print(f"POSTGRES_INIT_SUCCESS: Instantiated {{name}}")
                    break
                except TypeError:
                    print(f"POSTGRES_CLASS_FOUND: {{name}} (requires args)")
                    break
                except Exception as e:
                    print(f"POSTGRES_INIT_WARNING: {{name}} init failed: {{e}}")
    
    sys.exit(0)
    
except Exception as e:
    print(f"POSTGRES_ERROR: {{type(e).__name__}}: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
    '''
        
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', test_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            output = result.stdout + result.stderr
            
            if 'POSTGRES_IMPORT_SUCCESS' in result.stdout or 'POSTGRES_INIT_SUCCESS' in result.stdout:
                success_lines = [l for l in result.stdout.split('\n') if 'POSTGRES_IMPORT_SUCCESS' in l or 'POSTGRES_INIT_SUCCESS' in l]
                msg = success_lines[0] if success_lines else "PostgreSQL operations completed successfully"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.SQL_POSTGRES,
                    status=TestStatus.PASSED,
                    message=msg,
                    details=result.stdout,
                )
            else:
                error_lines = [l for l in output.split('\n') if 'POSTGRES_ERROR' in l or 'Error' in l]
                error_msg = error_lines[0] if error_lines else "PostgreSQL execution failed"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.SQL_POSTGRES,
                    status=TestStatus.FAILED,
                    message=error_msg,
                    details=output[-1000:],
                )
                
        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=AppType.SQL_POSTGRES,
                status=TestStatus.TIMEOUT,
                message=f"PostgreSQL operations timed out after {timeout}s",
            )
    
    
    async def _test_sql_file(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test pure SQL file (.sql) by validating syntax.
        
        Pure SQL files are not executable Python, so we just validate
        they contain valid SQL-like statements.
        """
        full_path = Path(temp_dir) / file_path
        
        try:
            content = full_path.read_text(encoding='utf-8')
        except Exception as e:
            return TestResult(
                file_path=file_path,
                app_type=AppType.SQL_GENERIC,
                status=TestStatus.ERROR,
                message=f"Could not read SQL file: {e}",
            )
        
        # Check for SQL keywords
        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 
                       'ALTER', 'TABLE', 'INDEX', 'VIEW', 'TRIGGER', 'FUNCTION']
        
        content_upper = content.upper()
        found_keywords = [kw for kw in sql_keywords if kw in content_upper]
        
        if found_keywords:
            return TestResult(
                file_path=file_path,
                app_type=AppType.SQL_GENERIC,
                status=TestStatus.PASSED,
                message=f"SQL file validated (found {len(found_keywords)} keywords)",
                details=f"Keywords found: {', '.join(found_keywords)}",
            )
        else:
            return TestResult(
                file_path=file_path,
                app_type=AppType.SQL_GENERIC,
                status=TestStatus.SKIPPED,
                message="File does not appear to contain standard SQL statements",
            )
    
    
    # ========================================================================
    # API TESTING
    # ========================================================================
    
    async def _test_api_dependent(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test API-dependent code.
        
        Strategy:
        1. Check network availability
        2. If available: run with short timeout
        3. If not: import-only validation
        4. Network errors are warnings, not failures
        """
        has_network = await self._check_network()
        
        if not has_network:
            return await self._test_import_only(
                file_path, temp_dir, timeout,
                AppType.API_DEPENDENT,
                message="API code validated (import-only, network unavailable)",
                suggestion="Ensure network connectivity for full API testing",
            )
        
        # Full test with network
        full_path = Path(temp_dir) / file_path
        safe_temp_dir = json.dumps(str(temp_dir))
        safe_full_path = json.dumps(str(full_path))
        
        test_script = f'''
import sys
import socket

sys.path.insert(0, {safe_temp_dir}.strip('"'))

# Set short timeout for all network operations
socket.setdefaulttimeout(15)

try:
    import runpy
    runpy.run_path({safe_full_path}.strip('"'), run_name='__main__')
    print("API_SUCCESS")
    sys.exit(0)
    
except (ConnectionError, TimeoutError, socket.timeout, OSError) as e:
    # Network errors are not failures — could be external service issue
    print(f"API_NETWORK_WARNING: {{type(e).__name__}}: {{e}}")
    sys.exit(0)  # Exit 0 — this is a warning, not error
    
except Exception as e:
    print(f"API_ERROR: {{type(e).__name__}}: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
    '''
        
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', test_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            output = result.stdout + result.stderr
            
            if 'API_SUCCESS' in result.stdout:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.API_DEPENDENT,
                    status=TestStatus.PASSED,
                    message="API-dependent code executed successfully",
                )
            elif 'API_NETWORK_WARNING' in result.stdout:
                # Network error — passed with warning
                warning_line = [l for l in result.stdout.split('\n') if 'API_NETWORK_WARNING' in l][0]
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.API_DEPENDENT,
                    status=TestStatus.PASSED,
                    message=f"Code validated (network issue: {warning_line.split(': ', 1)[-1]})",
                    details="Network-dependent operations could not complete, but code structure is valid",
                )
            else:
                error_lines = [l for l in output.split('\n') if 'API_ERROR' in l]
                error_msg = error_lines[0] if error_lines else "API execution failed"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.API_DEPENDENT,
                    status=TestStatus.FAILED,
                    message=error_msg.replace('API_ERROR: ', ''),
                    details=output[-1000:],
                )
                
        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=AppType.API_DEPENDENT,
                status=TestStatus.TIMEOUT,
                message=f"API operations timed out after {timeout}s",
                suggestion="Check for blocking network calls or unresponsive endpoints",
            )
    
    
    
    async def _test_daemon(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test daemon/service application using import-only strategy.
        
        Daemon applications (watchdog, schedule, etc.) run infinite loops,
        so we only validate imports and class definitions without executing __main__.
        """
        full_path = Path(temp_dir) / file_path
        safe_temp_dir = json.dumps(str(temp_dir))
        safe_full_path = json.dumps(str(full_path))
        
        test_script = f'''
import sys
import os

sys.path.insert(0, {safe_temp_dir}.strip('"'))

import importlib.util

try:
    # Load module WITHOUT executing __main__ block
    spec = importlib.util.spec_from_file_location("test_module", {safe_full_path}.strip('"'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    print("DAEMON_IMPORT_SUCCESS: Module imported successfully")
    
    # Try to find service-related classes
    service_class_names = ['Service', 'Daemon', 'Monitor', 'Watcher', 'Handler', 
                        'Observer', 'Scheduler', 'Worker', 'Runner', 'Manager']
    
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and not name.startswith('_'):
            # Check if class name suggests service functionality
            if any(svc_name.lower() in name.lower() for svc_name in service_class_names):
                print(f"DAEMON_CLASS_FOUND: {{name}}")
                break
    
    sys.exit(0)
    
except Exception as e:
    print(f"DAEMON_ERROR: {{type(e).__name__}}: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
    '''
        
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', test_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            output = result.stdout + result.stderr
            
            if 'DAEMON_IMPORT_SUCCESS' in result.stdout:
                # Check if service class was found
                if 'DAEMON_CLASS_FOUND' in result.stdout:
                    class_line = [l for l in result.stdout.split('\n') if 'DAEMON_CLASS_FOUND' in l][0]
                    class_name = class_line.split(': ')[1] if ': ' in class_line else 'unknown'
                    return TestResult(
                        file_path=file_path,
                        app_type=AppType.DAEMON,
                        status=TestStatus.PASSED,
                        message=f"Daemon service validated (found class: {class_name})",
                        details=result.stdout,
                        suggestion="Daemon runs infinite loop — tested via import-only strategy",
                    )
                else:
                    return TestResult(
                        file_path=file_path,
                        app_type=AppType.DAEMON,
                        status=TestStatus.PASSED,
                        message="Daemon module imports successfully",
                        details=result.stdout,
                        suggestion="Daemon runs infinite loop — tested via import-only strategy",
                    )
            else:
                error_lines = [l for l in output.split('\n') if 'DAEMON_ERROR' in l or 'Error' in l]
                error_msg = error_lines[0] if error_lines else "Daemon import failed"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.DAEMON,
                    status=TestStatus.FAILED,
                    message=f"Daemon import error: {error_msg}",
                    details=output[-1000:],
                )
                
        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=AppType.DAEMON,
                status=TestStatus.TIMEOUT,
                message=f"Daemon import timed out after {timeout}s",
                suggestion="Module may have blocking initialization code at import time",
            )
    
    
    async def _test_interactive(self, file_path: str, temp_dir: str, timeout: int) -> TestResult:
        """
        Test interactive CLI application by running with simulated input.
        
        Interactive apps (menus with input()) need actual execution to catch
        runtime errors. We send exit commands to stdin and check for errors.
        """
        full_path = Path(temp_dir) / file_path
        safe_temp_dir = json.dumps(str(temp_dir))
        safe_full_path = json.dumps(str(full_path))
        
        # Common exit commands for interactive menus
        exit_commands = "0\nq\nexit\nquit\n4\n5\n6\n7\n8\n9\n\n"
        
        try:
            # Run the script with simulated input
            process = subprocess.Popen(
                [self._get_project_python(), json.loads(safe_full_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=temp_dir,
                env={**os.environ, 'PYTHONPATH': json.loads(safe_temp_dir)},
            )
            
            try:
                stdout, stderr = process.communicate(input=exit_commands, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                
                # Timeout is OK for interactive apps - check if there were errors before timeout
                if stderr and any(err in stderr for err in ['Error', 'Exception', 'Traceback']):
                    # Extract error message
                    error_lines = [l for l in stderr.split('\n') if l.strip()]
                    error_msg = "Runtime error before timeout"
                    for line in reversed(error_lines):
                        line_stripped = line.strip()
                        if (line_stripped and 
                            not line_stripped.startswith('Traceback') and
                            not line_stripped.startswith('File "') and
                            not line.startswith('    ')):
                            error_msg = line_stripped[:150]
                            break
                    
                    return TestResult(
                        file_path=file_path,
                        app_type=AppType.INTERACTIVE,
                        status=TestStatus.FAILED,
                        message=f"Interactive CLI error: {error_msg}",
                        details=stderr[-2000:],
                        suggestion="Fix the runtime error in the interactive menu code",
                    )
                
                # No errors before timeout - consider it passed
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.INTERACTIVE,
                    status=TestStatus.PASSED,
                    message="Interactive CLI started successfully (timeout is expected)",
                    details=f"stdout: {stdout[:500]}" if stdout else None,
                    suggestion="Interactive app tested with simulated input",
                )
            
            # Check for errors in stderr
            if stderr and any(err in stderr for err in ['Error', 'Exception', 'Traceback']):
                error_lines = [l for l in stderr.split('\n') if l.strip()]
                error_msg = "Runtime error"
                for line in reversed(error_lines):
                    line_stripped = line.strip()
                    if (line_stripped and 
                        not line_stripped.startswith('Traceback') and
                        not line_stripped.startswith('File "') and
                        not line.startswith('    ')):
                        error_msg = line_stripped[:150]
                        break
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.INTERACTIVE,
                    status=TestStatus.FAILED,
                    message=f"Interactive CLI error: {error_msg}",
                    details=stderr[-2000:],
                    suggestion="Fix the runtime error in the interactive menu code",
                )
            
            # Check return code
            if process.returncode != 0 and process.returncode is not None:
                # Non-zero exit might be OK if no errors in stderr
                # (e.g., user chose "exit" option)
                if not stderr or not any(err in stderr for err in ['Error', 'Exception']):
                    return TestResult(
                        file_path=file_path,
                        app_type=AppType.INTERACTIVE,
                        status=TestStatus.PASSED,
                        message=f"Interactive CLI exited with code {process.returncode} (no errors)",
                        details=f"stdout: {stdout[:500]}" if stdout else None,
                    )
            
            return TestResult(
                file_path=file_path,
                app_type=AppType.INTERACTIVE,
                status=TestStatus.PASSED,
                message="Interactive CLI executed successfully",
                details=f"stdout: {stdout[:500]}" if stdout else None,
                suggestion="Tested with simulated exit commands",
            )
            
        except FileNotFoundError:
            return TestResult(
                file_path=file_path,
                app_type=AppType.INTERACTIVE,
                status=TestStatus.ERROR,
                message="File not found",
            )
        except Exception as e:
            return TestResult(
                file_path=file_path,
                app_type=AppType.INTERACTIVE,
                status=TestStatus.ERROR,
                message=f"Test execution error: {type(e).__name__}: {e}",
            )
    
    
    
    async def _test_cli(
        self,
        file_path: str,
        temp_dir: str,
        timeout: int,
    ) -> TestResult:
        """
        Test CLI application with smart detection for infinite loops.

        Strategy:
        1. Analyze if app is "long-running" (Daemon/GUI/Server).
        2. Run with --help.
        3. If exits 0: PASSED.
        4. If exits != 0: 
           - CHECK OUTPUT for crashes (Traceback, Error, Exception).
           - If crash detected: FAIL.
           - If no crash (just usage error): Fallback to import.
        5. If Timeout:
           - If Long-Running: PASSED (It's alive).
           - If Standard: FAIL -> Fallback to import.
        """
        full_path = Path(temp_dir) / file_path
        python_path = self._get_project_python()
        
        # 1. Analyze content for loops
        is_long_running = False
        try:
            content = full_path.read_text(encoding='utf-8', errors='replace')
            is_long_running = self._is_potential_long_running(content)
        except Exception as e:
            logger.debug(f"Failed to analyze CLI content: {e}")

        # Prepare environment
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join([temp_dir, env.get('PYTHONPATH', '')])
        
        # 2. Determine execution timeout
        run_timeout = 5 if is_long_running else timeout
        
        process = None
        try:
            # Start process
            process = subprocess.Popen(
                [python_path, str(full_path), '--help'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=temp_dir,
                env=env,
            )
            
            try:
                stdout, stderr = process.communicate(timeout=run_timeout)
                
                # Case A: Exited normally
                if process.returncode == 0:
                    return TestResult(
                        file_path=file_path,
                        app_type=AppType.CLI,
                        status=TestStatus.PASSED,
                        message="CLI with --help executed successfully",
                        details=stdout[:2000]
                    )
                else:
                    # Case B: Crashed / Error code
                    # CRITICAL FIX: Check if this was a crash/traceback vs just "flag not supported"
                    combined_output = (stdout or "") + "\n" + (stderr or "")
                    
                    critical_patterns = [
                        r"Traceback \(most recent call last\):",
                        r"\b[A-Z][a-zA-Z]*Error:\s",  # Matches TypeError:, NameError:, etc.
                        r"\bException:\s",
                        r" - ERROR - ",               # Logged errors
                        r"CRITICAL",
                    ]
                    
                    for pattern in critical_patterns:
                        try:
                            if re.search(pattern, combined_output):
                                # Real bug detected - FAIL immediately, do not swallow it via fallback
                                return TestResult(
                                    file_path=file_path,
                                    app_type=AppType.CLI,
                                    status=TestStatus.FAILED,
                                    message="CLI runtime error detected during --help execution",
                                    details=combined_output[:2000]
                                )
                        except Exception as regex_error:
                            logger.debug(f"Regex error checking pattern '{pattern}': {regex_error}")
                    
                    # If no critical error found (e.g. exit 2 due to argparse usage), proceed to fallback
                    logger.debug(f"CLI --help exited with code {process.returncode} (non-critical)")
                    pass
                    
            except subprocess.TimeoutExpired:
                # Case C: Timed out (Running)
                process.kill()
                stdout, stderr = process.communicate()
                
                if is_long_running:
                    return TestResult(
                        file_path=file_path,
                        app_type=AppType.CLI,
                        status=TestStatus.PASSED,
                        message="Application started and ran successfully (Loop/Server detected)",
                        details=f"Process kept running for {run_timeout}s. stdout: {stdout[:500]}",
                        suggestion="App appears to be a long-running service/GUI"
                    )
                else:
                    logger.debug(f"Standard CLI timed out on --help: {file_path}")
                    pass

        except Exception as e:
            logger.debug(f"CLI execution error: {e}")
            if process:
                process.kill()

        # 3. Fallback: Import-only test
        return await self._test_import_only(
            file_path, 
            temp_dir, 
            timeout,
            AppType.CLI,
            message="CLI validation via import (runtime execution failed/crashed)",
            suggestion="Check if script handles '--help' or has runtime errors"
        )
    
    
    async def _test_testing_framework(
        self,
        file_path: str,
        temp_dir: str,
        timeout: int,
    ) -> TestResult:
        """
        Test testing framework files (pytest/unittest) by executing them with pytest.

        Strategy:
        1. Run file using `pytest` module
        2. Parse output for passed/failed/skipped counts
        3. Return PASSED only if tests ran and passed
        """
        full_path = Path(temp_dir) / file_path
        python_path = self._get_project_python()
        
        try:
            # Run pytest
            # We use -v for details and --tb=short to keep logs manageable
            result = subprocess.run(
                [python_path, '-m', 'pytest', str(full_path), '-v', '--tb=short'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            output = result.stdout + result.stderr
            
            # Parse summary from output (e.g. "=== 2 passed, 1 failed in 0.12s ===")
            summary_match = re.search(r'=+ (.*?) in [0-9.]+s =+', output)
            summary_text = summary_match.group(1) if summary_match else "Test execution completed"
            
            # Exit code 0: All passed
            if result.returncode == 0:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.TESTING,
                    status=TestStatus.PASSED,
                    message=f"Tests passed: {summary_text}",
                    details=output,
                )
            
            # Exit code 1: Tests failed
            elif result.returncode == 1:
                # Extract failed tests
                failed_lines = [l for l in output.split('\n') if 'FAILED' in l]
                failure_summary = failed_lines[:3]  # Show first 3 failures
                
                msg = f"Tests failed: {summary_text}"
                if failure_summary:
                    msg += f" ({'; '.join(f.strip() for f in failure_summary)})"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.TESTING,
                    status=TestStatus.FAILED,
                    message=msg,
                    details=output[-2000:],  # Last 2000 chars
                    suggestion="Review failed test cases in details",
                )
            
            # Exit code 5: No tests collected
            elif result.returncode == 5:
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.TESTING,
                    status=TestStatus.SKIPPED,
                    message="No tests collected (pytest code 5)",
                    details=output,
                )
                
            # Other codes (2, 3, 4): Errors
            else:
                error_lines = [l for l in output.split('\n') if 'Error' in l or 'Traceback' in l]
                error_msg = error_lines[0] if error_lines else f"Pytest exit code {result.returncode}"
                
                return TestResult(
                    file_path=file_path,
                    app_type=AppType.TESTING,
                    status=TestStatus.ERROR,
                    message=f"Test execution error: {error_msg}",
                    details=output[-1000:],
                )

        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=AppType.TESTING,
                status=TestStatus.TIMEOUT,
                message=f"Test execution timed out after {timeout}s",
                suggestion="Check for infinite loops in tests",
            )
        except Exception as e:
            return TestResult(
                file_path=file_path,
                app_type=AppType.TESTING,
                status=TestStatus.ERROR,
                message=f"Runner error: {type(e).__name__}: {e}",
            )
    
    
    
    # ========================================================================
    # WEB APP TESTING
    # ========================================================================
    
    async def _test_web_app(self, file_path: str, temp_dir: str) -> TestResult:
        """
        Skip web application with INFO status.
        
        Web apps require a server and are outside our testing scope.
        """
        # Still do import check to verify no syntax errors
        result = await self._test_import_only(
            file_path, temp_dir, 30,
            AppType.WEB_APP,
            message="Web application — import validated (server testing not supported)",
            suggestion="Run web server manually: python -m flask run / uvicorn app:app",
        )
        
        # Change status to SKIPPED (not PASSED) for web apps
        if result.status == TestStatus.PASSED:
            result.status = TestStatus.SKIPPED
        
        return result
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    async def _test_import_only(
        self,
        file_path: str,
        temp_dir: str,
        timeout: int,
        app_type: AppType,
        message: str,
        suggestion: Optional[str] = None,
    ) -> TestResult:
        """
        Perform import-only validation.
        
        Loads module without executing __main__, catching import errors.
        """
        full_path = Path(temp_dir) / file_path
        safe_temp_dir = json.dumps(str(temp_dir))
        safe_full_path = json.dumps(str(full_path))
        
        test_script = f'''
import sys
sys.path.insert(0, {safe_temp_dir}.strip('"'))

import importlib.util

try:
    spec = importlib.util.spec_from_file_location("test_module", {safe_full_path}.strip('"'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print("IMPORT_SUCCESS")
    sys.exit(0)
except Exception as e:
    print(f"IMPORT_ERROR: {{type(e).__name__}}: {{e}}")
    sys.exit(1)
    '''
        
        try:
            result = subprocess.run(
                [self._get_project_python(), '-c', test_script],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=temp_dir,
            )
            
            if 'IMPORT_SUCCESS' in result.stdout:
                return TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.PASSED,
                    message=message,
                    suggestion=suggestion,
                )
            else:
                error_output = result.stdout + result.stderr
                error_lines = [l for l in error_output.split('\n') if 'IMPORT_ERROR' in l or 'Error' in l]
                error_msg = error_lines[0] if error_lines else "Import failed"
                
                return TestResult(
                    file_path=file_path,
                    app_type=app_type,
                    status=TestStatus.FAILED,
                    message=f"Import error: {error_msg}",
                    details=error_output[-1000:],
                )
                
        except subprocess.TimeoutExpired:
            return TestResult(
                file_path=file_path,
                app_type=app_type,
                status=TestStatus.TIMEOUT,
                message=f"Import timed out after {timeout}s",
            )
    
    
    async def _check_network(self) -> bool:
        """
        Check network availability.
        
        Cached result to avoid repeated checks.
        """
        if self._network_available is not None:
            return self._network_available
        
        try:
            # Try to connect to Google DNS
            socket.create_connection(("8.8.8.8", 53), timeout=5)
            self._network_available = True
        except OSError:
            try:
                # Fallback: try Cloudflare DNS
                socket.create_connection(("1.1.1.1", 53), timeout=5)
                self._network_available = True
            except OSError:
                self._network_available = False
        
        logger.debug(f"Network availability: {self._network_available}")
        return self._network_available