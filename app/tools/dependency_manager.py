# app/tools/dependency_manager.py
"""
Dependency Manager Tool - автоматическая установка зависимостей.

Позволяет AI-агенту:
1. Просматривать установленные пакеты
2. Устанавливать недостающие библиотеки
3. Искать правильное имя пакета на PyPI
"""

import subprocess
import sys
import json
import logging
import re
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set, Tuple
import ast
from enum import Enum
from importlib.metadata import packages_distributions
from app.services.language_adapter import AdapterManager


logger = logging.getLogger(__name__)


# ============================================================================
# FALLBACK МАППИНГ: import name → pip package name
# Используется ТОЛЬКО для пакетов, которые ещё НЕ установлены.
# Для установленных пакетов используется динамический маппинг через
# importlib.metadata.packages_distributions()
# ============================================================================

FALLBACK_IMPORT_TO_PACKAGE: Dict[str, str] = {
    "docx": "python-docx",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "dateutil": "python-dateutil",
    "OpenSSL": "pyOpenSSL",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
    "usb": "pyusb",
    "wx": "wxPython",
    "gi": "PyGObject",
    "cv": "opencv-python",
    "skimage": "scikit-image",
    "tf": "tensorflow",
    "pd": "pandas",
    "np": "numpy",
    "plt": "matplotlib",
    "sns": "seaborn",
    # Google API libraries
    "googleapiclient": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "google_auth_httplib2": "google-auth-httplib2",
    # Common web/API libraries
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    # AI/ML API clients
    "tiktoken": "tiktoken",
    "anthropic": "anthropic",
    "openai": "openai",
}

# Backward compatibility alias
IMPORT_TO_PACKAGE = FALLBACK_IMPORT_TO_PACKAGE


def build_dynamic_import_mapping() -> Dict[str, str]:
    """
    Builds a dynamic mapping from import names to pip package names.
    
    Uses importlib.metadata.packages_distributions() to get the actual
    mapping for all installed packages, then merges with FALLBACK_IMPORT_TO_PACKAGE
    for packages that are not yet installed.
    
    Returns:
        Dict mapping import names to pip package names
    """
    # Start with fallback mappings (for uninstalled packages)
    result = FALLBACK_IMPORT_TO_PACKAGE.copy()
    
    try:
        # Get dynamic mapping from installed packages
        # packages_distributions() returns: {'import_name': ['dist-name1', 'dist-name2'], ...}
        dynamic_mapping = packages_distributions()
        
        for import_name, dist_list in dynamic_mapping.items():
            if dist_list:
                # Use the first distribution name (most common case)
                # This overwrites fallback with actual installed package name
                result[import_name] = dist_list[0]
        
        logger.debug(f"Built dynamic import mapping with {len(result)} entries")
        
    except Exception as e:
        logger.warning(f"Failed to build dynamic import mapping: {e}, using fallback only")
    
    return result


# Module-level cache for dynamic mapping
_dynamic_import_mapping_cache: Optional[Dict[str, str]] = None


def get_import_to_package_mapping(refresh: bool = False) -> Dict[str, str]:
    """
    Returns the import-to-package mapping, using cache when possible.
    
    Args:
        refresh: If True, rebuild the mapping from scratch
        
    Returns:
        Dict mapping import names to pip package names
    """
    global _dynamic_import_mapping_cache
    
    if _dynamic_import_mapping_cache is None or refresh:
        _dynamic_import_mapping_cache = build_dynamic_import_mapping()
    
    return _dynamic_import_mapping_cache




# Пакеты которые НЕЛЬЗЯ устанавливать (опасные или системные)
BLOCKED_PACKAGES: Set[str] = {
    "subprocess", "shutil", "builtins",
    "ctypes", "multiprocessing", "signal", "pty",
}

# Стандартная библиотека Python (не нужно устанавливать)
STDLIB_MODULES: Set[str] = {
    "abc", "argparse", "ast", "asyncio", "base64", "collections",
    "concurrent", "configparser", "contextlib", "copy", "csv",
    "dataclasses", "datetime", "decimal", "difflib", "email",
    "enum", "functools", "glob", "hashlib", "heapq", "html",
    "http", "importlib", "inspect", "io", "itertools", "json",
    "logging", "math", "multiprocessing", "os", "pathlib",
    "pickle", "platform", "pprint", "queue", "random", "re",
    "shutil", "signal", "socket", "sqlite3", "ssl", "string",
    "struct", "subprocess", "sys", "tempfile", "textwrap",
    "threading", "time", "traceback", "types", "typing",
    "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
    "zipfile", "zlib", "__future__", "builtins", "typing_extensions",
    "array", "bisect", "calendar", "cmath", "code", "codecs",
    "codeop", "colorsys", "compileall", "contextvars", "cProfile",
    "crypt", "curses", "dbm", "dis", "doctest", "encodings",
    "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "gc", "getopt", "getpass",
    "gettext", "grp", "gzip", "hmac", "imaplib", "ipaddress",
    "keyword", "linecache", "locale", "lzma", "mailbox", "mimetypes",
    "mmap", "modulefinder", "netrc", "nntplib", "numbers", "operator",
    "optparse", "ossaudiodev", "parser", "pdb", "pickletools",
    "pipes", "pkgutil", "poplib", "posix", "posixpath", "profile",
    "pstats", "pwd", "py_compile", "pyclbr", "pydoc", "quopri",
    "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex",
    "smtpd", "smtplib", "sndhdr", "spwd", "stat", "statistics",
    "stringprep", "sunau", "symbol", "symtable", "sysconfig",
    "syslog", "tabnanny", "tarfile", "telnetlib", "termios", "test",
    "token", "tokenize", "trace", "tty", "turtle", "turtledemo",
    "unicodedata", "uu", "venv", "wave", "webbrowser", "winreg",
    "winsound", "wsgiref", "xdrlib", "xmlrpc", "zipapp", "zipimport",
    "_thread", "aifc", "asynchat", "asyncore", "audioop", "bdb",
    "binhex", "chunk", "cgi", "cgitb", "cmd", "copyreg", "formatter",
    "graphlib", "idlelib", "imghdr", "imp", "lib2to3", "macpath",
    "mailcap", "marshal", "nis", "nntplib", "plistlib",
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class InstallResult(Enum):
    SUCCESS = "success"
    ALREADY_INSTALLED = "already_installed"
    BLOCKED = "blocked"
    STDLIB = "stdlib"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@dataclass
class PackageInfo:
    """Информация об установленном пакете"""
    name: str
    version: str
    location: Optional[str] = None


@dataclass
class InstallationResult:
    """Результат установки пакета"""
    package: str
    status: InstallResult
    message: str
    version: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "package": self.package,
            "status": self.status.value,
            "message": self.message,
            "version": self.version,
        }


@dataclass
class BulkInstallResult:
    """Результат массовой установки"""
    total: int
    successful: int
    failed: int
    skipped: int
    results: List[InstallationResult] = field(default_factory=list)
    
    @property
    def all_success(self) -> bool:
        return self.failed == 0
    
    def to_dict(self) -> Dict:
        return {
            "total": self.total,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "all_success": self.all_success,
            "results": [r.to_dict() for r in self.results],
        }


# ============================================================================
# DEPENDENCY MANAGER
# ============================================================================

class DependencyManager:
    """
    Менеджер зависимостей для AI-агента.
    
    Позволяет:
    - Просматривать установленные пакеты
    - Устанавливать недостающие пакеты
    - Резолвить import name → pip package name
    """
    
    def __init__(self, project_root: Optional[Path] = None, auto_create_venv: bool = True):
        """
        Initialize DependencyManager.
        
        Args:
            project_root: Root directory of the project
            auto_create_venv: If True, create .venv if not found
        """
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self._installed_cache = None
        
        # Detect existing venv
        self._detect_venv()
        
        # Create venv if needed
        if auto_create_venv and self._venv_path is None:
            try:
                if self.ensure_project_venv():
                    self._detect_venv()
                else:
                    logger.warning("Failed to create venv, falling back to sys.executable")
            except Exception as e:
                logger.warning(f"Error creating venv: {e}, falling back to sys.executable")
        
        # Update pip and python paths
        self._update_paths()
        
        # Initialize environments for other languages (JS/TS, Go, Java)
        if auto_create_venv:
            try:
                self._language_env_results = self.ensure_language_environments()
            except Exception as e:
                logger.warning(f"Error initializing language environments: {e}")
                self._language_env_results = {}
        else:
            self._language_env_results = {}
        
        logger.info(
            f"DependencyManager initialized: project={self.project_root}, "
            f"venv={self._venv_path if self._venv_path else 'not found'}"
        )
    
    
    
    def _detect_venv(self) -> Optional[Path]:
        """Автоопределение виртуального окружения"""
        self._venv_path = None
        venv_names = ["venv", ".venv", "env", ".env"]
        
        for name in venv_names:
            venv_dir = self.project_root / name
            if venv_dir.exists() and (venv_dir / "pyvenv.cfg").exists():
                self._venv_path = venv_dir
                return venv_dir
        
        # Проверяем VIRTUAL_ENV
        import os
        virtual_env = os.environ.get("VIRTUAL_ENV")
        if virtual_env:
            self._venv_path = Path(virtual_env)
            return Path(virtual_env)
        
        return None
    
    def _update_paths(self) -> None:
        """Update pip and python paths based on detected venv."""
        self._pip_path = self._get_pip_path()
        self._python_path = self._get_python_path()
    
    
    def _get_pip_path(self) -> str:
        """Получить путь к pip"""
        if self._venv_path:
            if sys.platform == "win32":
                pip = self._venv_path / "Scripts" / "pip.exe"
            else:
                pip = self._venv_path / "bin" / "pip"
            if pip.exists():
                return str(pip)
        
        return sys.executable.replace("python", "pip")
    
    
    def _get_python_path(self) -> str:
        """Получить путь к Python интерпретатору проекта"""
        if self._venv_path:
            if sys.platform == "win32":
                python_exe = self._venv_path / "Scripts" / "python.exe"
            else:
                python_exe = self._venv_path / "bin" / "python"
            
            if python_exe.exists():
                return str(python_exe)
        
        return sys.executable

    def ensure_project_venv(self) -> bool:
        """
        Ensures a virtual environment exists in the project.
        Creates .venv if not found.
        
        Returns:
            True if venv exists or was created successfully
        """
        # Check if venv already exists and is valid
        if (self._venv_path is not None and 
            self._venv_path.exists() and 
            (self._venv_path / "pyvenv.cfg").exists()):
            logger.info(f"Virtual environment already exists at {self._venv_path}")
            return True
        
        # Create .venv
        venv_path = self.project_root / ".venv"
        
        try:
            logger.info(f"Creating virtual environment at {venv_path}")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_path)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully created venv at {venv_path}")
                self._venv_path = venv_path
                return True
            else:
                logger.error(f"Failed to create venv: {result.stderr}")
                return False
                
        except subprocess.CalledProcessError as e:
            logger.error(f"CalledProcessError creating venv: {e}")
            return False
        except Exception as e:
            logger.error(f"Exception creating venv: {e}")
            return False
    
    def _detect_project_languages(self) -> Set[str]:
        """
        Detects programming languages used in the project based on file extensions and config files.
        
        Returns:
            Set of language names: 'python', 'javascript', 'typescript', 'go', 'java'
        """
        languages: Set[str] = set()
        
        # Check for config files first (most reliable)
        config_indicators = {
            'python': ['requirements.txt', 'setup.py', 'pyproject.toml', 'Pipfile'],
            'javascript': ['package.json'],
            'typescript': ['tsconfig.json'],
            'go': ['go.mod', 'go.sum'],
            'java': ['pom.xml', 'build.gradle', 'build.gradle.kts'],
        }
        
        for language, config_files in config_indicators.items():
            for config_file in config_files:
                if (self.project_root / config_file).exists():
                    languages.add(language)
                    break
        
        # Scan for source files if no config found
        extension_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.jsx': 'javascript',
            '.mjs': 'javascript',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.go': 'go',
            '.java': 'java',
        }
        
        try:
            for file_path in self.project_root.rglob('*'):
                if not file_path.is_file():
                    continue
                
                # Skip common non-source directories
                parts = file_path.parts
                if any(p in ('node_modules', 'venv', '.venv', '__pycache__', '.git', 'vendor', 'target', 'build') for p in parts):
                    continue
                
                suffix = file_path.suffix.lower()
                if suffix in extension_map:
                    languages.add(extension_map[suffix])
                
                # Early exit if we found all languages
                if len(languages) >= 5:
                    break
        except Exception as e:
            logger.debug(f"Error scanning for languages: {e}")
        
        return languages
    
    def _ensure_npm_environment(self) -> bool:
        """
        Ensures npm environment is initialized. Creates package.json if not exists.
        
        Returns:
            True if npm environment exists or was created successfully
        """
        # Check if npm is available
        if shutil.which('npm') is None:
            logger.warning("npm not available. Install Node.js to use JavaScript/TypeScript features.")
            return False
        
        package_json = self.project_root / "package.json"
        
        # If package.json exists, environment is ready
        if package_json.exists():
            logger.debug(f"npm environment already initialized at {self.project_root}")
            return True
        
        # Initialize npm project
        try:
            logger.info(f"Initializing npm environment at {self.project_root}")
            result = subprocess.run(
                ['npm', 'init', '-y'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
                cwd=str(self.project_root)
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully initialized npm environment at {self.project_root}")
                return True
            else:
                logger.warning(f"Failed to initialize npm: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.warning("npm init timed out")
            return False
        except Exception as e:
            logger.warning(f"Error initializing npm environment: {e}")
            return False
    
    def _ensure_go_environment(self) -> bool:
        """
        Ensures Go module environment is initialized. Creates go.mod if not exists.
        
        Returns:
            True if Go environment exists or was created successfully
        """
        # Check if go is available
        if shutil.which('go') is None:
            logger.warning("go not available. Install Go to use Go features.")
            return False
        
        go_mod = self.project_root / "go.mod"
        
        # If go.mod exists, environment is ready
        if go_mod.exists():
            logger.debug(f"Go module already initialized at {self.project_root}")
            return True
        
        # Initialize go module
        try:
            # Use directory name as module name
            module_name = self.project_root.name
            
            logger.info(f"Initializing Go module at {self.project_root}")
            result = subprocess.run(
                ['go', 'mod', 'init', module_name],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
                cwd=str(self.project_root)
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully initialized Go module: {module_name}")
                return True
            else:
                logger.warning(f"Failed to initialize go mod: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.warning("go mod init timed out")
            return False
        except Exception as e:
            logger.warning(f"Error initializing Go environment: {e}")
            return False
    
    def _ensure_maven_environment(self) -> bool:
        """
        Checks if Maven environment is available. Does not create pom.xml automatically
        as Maven projects require specific configuration.
        
        Returns:
            True if Maven is available and pom.xml exists, False otherwise
        """
        # Check if maven is available
        if shutil.which('mvn') is None:
            logger.warning("Maven (mvn) not available. Install Maven to use Java dependency management.")
            return False
        
        pom_xml = self.project_root / "pom.xml"
        
        # If pom.xml exists, environment is ready
        if pom_xml.exists():
            logger.debug(f"Maven project found at {self.project_root}")
            return True
        
        # Check for Gradle as alternative
        build_gradle = self.project_root / "build.gradle"
        build_gradle_kts = self.project_root / "build.gradle.kts"
        
        if build_gradle.exists() or build_gradle_kts.exists():
            logger.debug(f"Gradle project found at {self.project_root}")
            return True
        
        # Don't auto-create pom.xml - it requires specific configuration
        logger.info(
            f"No Maven/Gradle configuration found in {self.project_root}. "
            "Java dependency management will be limited. "
            "Create pom.xml or build.gradle to enable full Java support."
        )
        return False
    
    def ensure_language_environments(self) -> Dict[str, bool]:
        """
        Detects project languages and ensures appropriate environments are initialized.
        
        Returns:
            Dict mapping language name to initialization success status
        """
        results: Dict[str, bool] = {}
        
        # Detect languages used in project
        languages = self._detect_project_languages()
        
        if not languages:
            logger.debug("No specific languages detected in project")
            return results
        
        logger.info(f"Detected languages in project: {languages}")
        
        # Initialize environments for detected languages
        if 'python' in languages:
            # Python venv is handled by ensure_project_venv, already called in __init__
            results['python'] = self._venv_path is not None
        
        if 'javascript' in languages or 'typescript' in languages:
            results['javascript'] = self._ensure_npm_environment()
            if 'typescript' in languages:
                results['typescript'] = results['javascript']
        
        if 'go' in languages:
            results['go'] = self._ensure_go_environment()
        
        if 'java' in languages:
            results['java'] = self._ensure_maven_environment()
        
        # Log summary
        initialized = [lang for lang, success in results.items() if success]
        failed = [lang for lang, success in results.items() if not success]
        
        if initialized:
            logger.info(f"Language environments ready: {initialized}")
        if failed:
            logger.warning(f"Language environments not available: {failed}")
        
        return results
    
    def ensure_formatting_tools(self, tools: Optional[List[str]] = None) -> Dict[str, bool]:
        """Ensures formatting tools are installed in the project's virtual environment. Installs black, autopep8, isort, yapf if not already present. Returns dict of {tool_name: success}."""
        import subprocess
        
        # 1. Define default tools list if not provided
        if tools is None:
            tools = ["black", "autopep8", "isort", "yapf"]
        
        # 2. Create result dict
        results: Dict[str, bool] = {}
        
        # 3. For each tool in tools list
        for tool in tools:
            try:
                # Check if tool is already available
                result = subprocess.run(
                    [self._python_path, "-m", tool, "--version"],
                    capture_output=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    results[tool] = True
                    logger.info(f"Formatting tool '{tool}' is already available")
                    continue
                
                # If not available, install via pip
                logger.info(f"Installing formatting tool '{tool}'...")
                install_result = subprocess.run(
                    [self._python_path, "-m", "pip", "install", tool],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=120
                )
                
                results[tool] = (install_result.returncode == 0)
                
                if install_result.returncode == 0:
                    logger.info(f"Successfully installed formatting tool '{tool}'")
                else:
                    logger.warning(f"Failed to install formatting tool '{tool}': {install_result.stderr}")
            
            except subprocess.TimeoutExpired:
                results[tool] = False
                logger.warning(f"Timeout while checking/installing formatting tool '{tool}'")
            except FileNotFoundError:
                results[tool] = False
                logger.warning(f"Python executable not found at {self._python_path}")
            except Exception as e:
                results[tool] = False
                logger.warning(f"Error with formatting tool '{tool}': {e}")
        
        # 4. Invalidate cache after installations
        self._installed_cache = None
        
        # 5. Return results dict
        return results
    
    # ========================================================================
    # PUBLIC API: LIST PACKAGES
    # ========================================================================
    
    def list_installed_packages(self, refresh: bool = False) -> Dict[str, PackageInfo]:
        """
        Получить список установленных пакетов.
        
        Args:
            refresh: Принудительно обновить кэш
            
        Returns:
            Dict[package_name, PackageInfo]
        """
        if self._installed_cache is not None and not refresh:
            return self._installed_cache
        
        packages = {}
        
        try:
            result = subprocess.run(
                [self._python_path, "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            
            if result.returncode == 0:
                pip_list = json.loads(result.stdout)
                for pkg in pip_list:
                    name = pkg.get("name", "")
                    version = pkg.get("version", "")
                    # Нормализуем имя
                    normalized = name.lower().replace("-", "_")
                    packages[normalized] = PackageInfo(
                        name=name,
                        version=version,
                    )
                    # Также добавляем оригинальное имя
                    packages[name.lower()] = packages[normalized]
                    
        except Exception as e:
            logger.error(f"Failed to list packages: {e}")
        
        self._installed_cache = packages
        return packages
    
    
    
    def is_installed(self, package_name: str) -> bool:
        """Проверить, установлен ли пакет"""
        packages = self.list_installed_packages()
        normalized = package_name.lower().replace("-", "_")
        return normalized in packages or package_name.lower() in packages
    
    def get_installed_version(self, package_name: str) -> Optional[str]:
        """Получить версию установленного пакета"""
        packages = self.list_installed_packages()
        normalized = package_name.lower().replace("-", "_")
        
        info = packages.get(normalized) or packages.get(package_name.lower())
        return info.version if info else None
    
    # ========================================================================
    # PUBLIC API: RESOLVE NAMES
    # ========================================================================
    
    def resolve_package_name(self, import_name: str) -> str:
        """
        Преобразовать имя импорта в имя pip-пакета.
        
        Uses dynamic mapping from importlib.metadata for installed packages,
        falls back to FALLBACK_IMPORT_TO_PACKAGE for uninstalled packages.
        
        Examples:
            'docx' → 'python-docx'
            'cv2' → 'opencv-python'
            'googleapiclient' → 'google-api-python-client'
            'requests' → 'requests'
        """
        root_module = import_name.split(".")[0]
        mapping = get_import_to_package_mapping()
        return mapping.get(root_module, root_module)
    
    
    def is_stdlib(self, module_name: str) -> bool:
        """Проверить, является ли модуль частью stdlib"""
        root_module = module_name.split(".")[0]
        return root_module in STDLIB_MODULES
    
    def is_blocked(self, package_name: str) -> bool:
        """Проверить, заблокирован ли пакет"""
        return package_name.lower() in BLOCKED_PACKAGES
    
    # ========================================================================
    # PUBLIC API: INSTALL
    # ========================================================================
    
    def install_package(
        self,
        package_name: str,
        version: Optional[str] = None,
    ) -> InstallationResult:
        """
        Установить pip-пакет.
        
        Args:
            package_name: Имя пакета (pip name)
            version: Конкретная версия (опционально)
            
        Returns:
            InstallationResult
        """
        # Валидация
        if not package_name or not re.match(r'^[a-zA-Z0-9_\-\.]+$', package_name):
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message=f"Invalid package name: {package_name}",
            )
        
        # Проверка на blocked
        if self.is_blocked(package_name):
            return InstallationResult(
                package=package_name,
                status=InstallResult.BLOCKED,
                message=f"Package '{package_name}' is blocked for security",
            )
        
        # Проверка на stdlib
        if self.is_stdlib(package_name):
            return InstallationResult(
                package=package_name,
                status=InstallResult.STDLIB,
                message=f"'{package_name}' is part of Python stdlib",
            )
        
        # Проверка уже установлен
        if self.is_installed(package_name):
            ver = self.get_installed_version(package_name)
            return InstallationResult(
                package=package_name,
                status=InstallResult.ALREADY_INSTALLED,
                message=f"'{package_name}' already installed",
                version=ver,
            )
        
        # Формируем команду
        cmd = [self._python_path, "-m", "pip", "install"]
        
        if version:
            cmd.append(f"{package_name}=={version}")
        else:
            cmd.append(package_name)
        
        logger.info(f"Installing: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=300,
                cwd=str(self.project_root),
            )
            
            if result.returncode == 0:
                # Сбрасываем кэш
                self._installed_cache = None
                installed_ver = self.get_installed_version(package_name)
                
                logger.info(f"Successfully installed {package_name} {installed_ver}")
                
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.SUCCESS,
                    message=f"Installed {package_name}",
                    version=installed_ver,
                )
            else:
                error_msg = result.stderr[-500:] if result.stderr else "Unknown error"
                logger.error(f"Failed to install {package_name}: {error_msg}")
                
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.FAILED,
                    message=f"Installation failed: {error_msg}",
                )
                
        except subprocess.TimeoutExpired:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message="Installation timed out (5 min)",
            )
        except Exception as e:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message=f"Error: {e}",
            )

    
    def install_from_import(self, import_name: str) -> InstallationResult:
        """
        Установить пакет по имени импорта.
        
        Автоматически резолвит import name → pip package name.
        """
        package_name = self.resolve_package_name(import_name)
        return self.install_package(package_name)
    
    
    def install_npm_package(self, package_name: str, version: Optional[str] = None, dev: bool = False) -> InstallationResult:
        """Install an npm package for JavaScript/TypeScript projects."""
        try:
            # Check if npm is available
            if shutil.which('npm') is None:
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.FAILED,
                    message="npm not available. Install Node.js to use npm packages."
                )
            
            # Build command
            cmd = ['npm', 'install']
            
            if dev:
                cmd.append('--save-dev')
            
            if version:
                cmd.append(f"{package_name}@{version}")
            else:
                cmd.append(package_name)
            
            # Run subprocess
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=300,
                cwd=str(self.project_root)
            )
            
            if result.returncode == 0:
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.SUCCESS,
                    message=f"Successfully installed {package_name}"
                )
            else:
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.FAILED,
                    message=result.stderr[:500] if result.stderr else "Unknown error"
                )
        
        except subprocess.TimeoutExpired:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message="Installation timed out (5 min)"
            )
        except Exception as e:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message=str(e)
            )
    
    def install_go_module(self, module_path: str) -> InstallationResult:
        """Install a Go module using go get."""
        try:
            # Check if go is available
            if shutil.which('go') is None:
                return InstallationResult(
                    package=module_path,
                    status=InstallResult.FAILED,
                    message="go not available. Install Go to use Go modules."
                )
            
            # Build command
            cmd = ['go', 'get', module_path]
            
            # Run subprocess
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=300,
                cwd=str(self.project_root)
            )
            
            if result.returncode == 0:
                return InstallationResult(
                    package=module_path,
                    status=InstallResult.SUCCESS,
                    message=f"Successfully installed {module_path}"
                )
            else:
                return InstallationResult(
                    package=module_path,
                    status=InstallResult.FAILED,
                    message=result.stderr[:500] if result.stderr else "Unknown error"
                )
        
        except subprocess.TimeoutExpired:
            return InstallationResult(
                package=module_path,
                status=InstallResult.FAILED,
                message="Installation timed out (5 min)"
            )
        except Exception as e:
            return InstallationResult(
                package=module_path,
                status=InstallResult.FAILED,
                message=str(e)
            )
    
    def install_maven_dependency(self, group_id: str, artifact_id: str, version: Optional[str] = None) -> InstallationResult:
        """
        Install a Maven dependency for Java projects.
        
        If Maven is not installed, returns a non-critical warning that allows
        the pipeline to continue (the error will be reported but not block execution).
        
        Args:
            group_id: Maven group ID (e.g., 'org.apache.commons')
            artifact_id: Maven artifact ID (e.g., 'commons-lang3')
            version: Optional version (e.g., '3.12.0')
            
        Returns:
            InstallationResult with status and message
        """
        package_name = f"{group_id}:{artifact_id}" + (f":{version}" if version else "")
        
        try:
            # Check if Maven is available
            if shutil.which('mvn') is None:
                # Return a special status that indicates Maven is not available
                # This is NOT a critical error - the pipeline should continue
                logger.warning(
                    f"Maven not available. Cannot install Java dependency: {package_name}. "
                    "Install Maven to manage Java dependencies automatically."
                )
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.NOT_FOUND,
                    message=(
                        "Maven not available. Java dependency management requires Maven. "
                        "Please install Maven or add the dependency manually to pom.xml. "
                        "This is not a critical error - compilation will be attempted anyway."
                    )
                )
            
            # Check if pom.xml exists
            pom_path = self.project_root / "pom.xml"
            if not pom_path.exists():
                logger.warning(f"No pom.xml found in {self.project_root}. Cannot add Maven dependency.")
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.FAILED,
                    message=(
                        f"No pom.xml found in project root. "
                        "Create a pom.xml file or use 'mvn archetype:generate' to initialize a Maven project."
                    )
                )
            
            # Build Maven command to add dependency
            # Using dependency:get to download the dependency to local repository
            cmd = [
                'mvn', 'dependency:get',
                f'-DgroupId={group_id}',
                f'-DartifactId={artifact_id}',
            ]
            
            if version:
                cmd.append(f'-Dversion={version}')
            
            logger.info(f"Installing Maven dependency: {package_name}")
            
            # Run subprocess
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=300,
                cwd=str(self.project_root)
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully installed Maven dependency: {package_name}")
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.SUCCESS,
                    message=f"Successfully downloaded {package_name} to local Maven repository"
                )
            else:
                error_msg = result.stderr[:500] if result.stderr else "Unknown error"
                logger.warning(f"Failed to install Maven dependency {package_name}: {error_msg}")
                return InstallationResult(
                    package=package_name,
                    status=InstallResult.FAILED,
                    message=f"Maven dependency installation failed: {error_msg}"
                )
        
        except subprocess.TimeoutExpired:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message="Maven dependency installation timed out (5 min)"
            )
        except Exception as e:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message=str(e)
            )
    
    def install_dependency_for_language(self, package_name: str, language: Optional[str] = None, version: Optional[str] = None) -> InstallationResult:
        """
        Install a dependency for a specific language.
        
        If language is None, tries all supported package managers in order:
        Python (pip) → JavaScript (npm) → Go (go get) → Java (Maven)
        
        For Java, attempts to parse Maven coordinates from package_name
        (format: 'groupId:artifactId' or 'groupId:artifactId:version').
        
        Args:
            package_name: Package name or Maven coordinates for Java
            language: Target language (python, javascript, typescript, go, java)
            version: Optional version specifier
            
        Returns:
            InstallationResult with status and message
        """
        try:
            if language is not None:
                language_lower = language.lower()
                
                if language_lower == "python":
                    return self.install_from_import(package_name)
                
                elif language_lower in ("javascript", "typescript"):
                    return self.install_npm_package(package_name, version)
                
                elif language_lower == "go":
                    return self.install_go_module(package_name)
                
                elif language_lower == "java":
                    # Parse Maven coordinates from package_name
                    # Expected format: 'groupId:artifactId' or 'groupId:artifactId:version'
                    parts = package_name.split(':')
                    if len(parts) >= 2:
                        group_id = parts[0]
                        artifact_id = parts[1]
                        maven_version = parts[2] if len(parts) > 2 else version
                        return self.install_maven_dependency(group_id, artifact_id, maven_version)
                    else:
                        # Cannot parse as Maven coordinates
                        return InstallationResult(
                            package=package_name,
                            status=InstallResult.FAILED,
                            message=(
                                f"Invalid Java dependency format: '{package_name}'. "
                                "Use Maven coordinates format: 'groupId:artifactId' or 'groupId:artifactId:version'"
                            )
                        )
                
                else:
                    return InstallationResult(
                        package=package_name,
                        status=InstallResult.FAILED,
                        message=f"Unsupported language: {language}"
                    )
            
            else:
                # Auto-detection: try all supported package managers in order
                # Python first (most common)
                result = self.install_from_import(package_name)
                if result.status == InstallResult.SUCCESS:
                    return result
                
                # Try npm (JavaScript/TypeScript)
                result = self.install_npm_package(package_name, version)
                if result.status == InstallResult.SUCCESS:
                    return result
                
                # Try go
                result = self.install_go_module(package_name)
                if result.status == InstallResult.SUCCESS:
                    return result
                
                # Try Java: check if package_name looks like Maven coordinates
                if ':' in package_name:
                    parts = package_name.split(':')
                    if len(parts) >= 2:
                        group_id = parts[0]
                        artifact_id = parts[1]
                        maven_version = parts[2] if len(parts) > 2 else version
                        result = self.install_maven_dependency(group_id, artifact_id, maven_version)
                        if result.status == InstallResult.SUCCESS:
                            return result
                
                # Return last result (go) if all failed
                return result
        
        except Exception as e:
            return InstallationResult(
                package=package_name,
                status=InstallResult.FAILED,
                message=str(e)
            )
    
    
    def detect_missing_dependencies_from_errors(self, error_message: str, language: str) -> List[str]:
        """
        Detects missing dependencies from error messages.
        
        Supports patterns for:
        - Python: ImportError, ModuleNotFoundError
        - JavaScript/Node: Cannot find module
        - TypeScript: TS2307 errors
        - Go: module not found
        - Java: cannot find symbol
        """
        missing_deps = []
        
        if not error_message:
            return missing_deps
        
        try:
            # Python patterns
            if language.lower() in ('python', 'py'):
                # Pattern: ModuleNotFoundError: No module named 'requests'
                py_pattern = r"(?:ModuleNotFoundError|ImportError):\s*No module named ['\"]([^'\"]+)['\"]"
                matches = re.findall(py_pattern, error_message)
                missing_deps.extend(matches)
                
                # Pattern: cannot import name 'X' from 'module'
                py_import_pattern = r"cannot import name ['\"]([^'\"]+)['\"]"
                matches = re.findall(py_import_pattern, error_message)
                missing_deps.extend(matches)
            
            # JavaScript/Node patterns
            if language.lower() in ('javascript', 'js', 'node'):
                # Pattern: Cannot find module 'express'
                js_pattern = r"Cannot find module ['\"]([^'\"]+)['\"]"
                matches = re.findall(js_pattern, error_message)
                missing_deps.extend(matches)
                
                # Pattern: MODULE_NOT_FOUND
                js_not_found = r"code: 'MODULE_NOT_FOUND'.*require\(['\"]([^'\"]+)['\"]\)"
                matches = re.findall(js_not_found, error_message, re.DOTALL)
                missing_deps.extend(matches)
            
            # TypeScript patterns
            if language.lower() in ('typescript', 'ts'):
                # Pattern: error TS2307: Cannot find module 'lodash'
                ts_pattern = r"error TS2307: Cannot find module ['\"]([^'\"]+)['\"]"
                matches = re.findall(ts_pattern, error_message)
                missing_deps.extend(matches)
            
            # Go patterns
            if language.lower() in ('go', 'golang'):
                # Pattern: go: module github.com/user/pkg: not found
                go_pattern = r"go: module ([^\s:]+):\s*not found"
                matches = re.findall(go_pattern, error_message)
                missing_deps.extend(matches)
                
                # Pattern: cannot find package "github.com/user/pkg"
                go_pkg_pattern = r"cannot find package ['\"]([^'\"]+)['\"]"
                matches = re.findall(go_pkg_pattern, error_message)
                missing_deps.extend(matches)
            
            # Java patterns
            if language.lower() in ('java', 'jvm'):
                # Pattern: error: cannot find symbol class ArrayList
                # Note: This is harder to map to Maven coordinates, so we just log a warning
                java_pattern = r"error: cannot find symbol.*class ([A-Z][a-zA-Z0-9_]+)"
                matches = re.findall(java_pattern, error_message)
                if matches:
                    logger.warning(
                        f"Java import errors detected for classes: {matches}. "
                        f"Manual Maven dependency resolution may be required."
                    )
                    # Don't add to missing_deps as we can't reliably map class names to Maven coordinates
            
        except re.error as e:
            logger.warning(f"Regex error in detect_missing_dependencies_from_errors: {e}")
        
        # Remove duplicates while preserving order
        seen = set()
        unique_deps = []
        for dep in missing_deps:
            if dep not in seen:
                seen.add(dep)
                unique_deps.append(dep)
        
        return unique_deps
    
    
    def install_missing_from_validation(
        self,
        validation_issues: List[Dict],
    ) -> BulkInstallResult:
        """
        Установить все недостающие пакеты из ошибок валидации.
        
        Args:
            validation_issues: Список issues от ChangeValidator (в формате dict)
            
        Returns:
            BulkInstallResult
        """
        # Собираем уникальные недостающие модули
        missing_modules: Set[str] = set()
        
        for issue in validation_issues:
            # FIX: level может быть строкой "imports" или "ValidationLevel.IMPORTS"
            level = issue.get("level", "")
            if isinstance(level, str):
                level_str = level.lower()
            else:
                level_str = str(level).lower()
            
            # Проверяем что это ошибка импорта
            if "import" in level_str:
                message = issue.get("message", "")
                if "not found" in message.lower():
                    # Парсим "Module 'xxx' not found" или "Module 'xxx.yyy' not found"
                    match = re.search(r"['\"]([^'\"]+)['\"]", message)
                    if match:
                        module_name = match.group(1)
                        # Берём только корневой модуль (docx из docx.shared)
                        root_module = module_name.split(".")[0]
                        
                        # Пропускаем stdlib и заблокированные
                        if not self.is_stdlib(root_module) and not self.is_blocked(root_module):
                            missing_modules.add(root_module)
        
        if not missing_modules:
            logger.info("No missing modules to install")
            return BulkInstallResult(total=0, successful=0, failed=0, skipped=0)
        
        logger.info(f"Auto-installing {len(missing_modules)} missing packages: {missing_modules}")
        
        results: List[InstallationResult] = []
        successful = 0
        failed = 0
        skipped = 0
        
        for module in missing_modules:
            result = self.install_from_import(module)
            results.append(result)
            
            if result.status == InstallResult.SUCCESS:
                successful += 1
                logger.info(f"✅ Installed: {module} → {result.package} {result.version}")
            elif result.status == InstallResult.FAILED:
                failed += 1
                logger.error(f"❌ Failed to install: {module} - {result.message}")
            else:
                skipped += 1
                logger.info(f"⏭️ Skipped: {module} ({result.status.value})")
        
        return BulkInstallResult(
            total=len(missing_modules),
            successful=successful,
            failed=failed,
            skipped=skipped,
            results=results,
        )

    def scan_and_install_all_dependencies(
        self,
        directory: Optional[Path] = None,
        recursive: bool = True,
    ) -> BulkInstallResult:
        """Scans all Python files in directory, extracts imports via AST, and installs missing packages. Called after any validation error to ensure all dependencies are available."""
        import ast
        from typing import Set
        
        # 1. Set scan_dir
        scan_dir = directory if directory else self.project_root
        
        # 2. Create empty set for all imports
        all_imports: Set[str] = set()
        
        # 3. Define file pattern
        pattern = "**/*.py" if recursive else "*.py"
        
        # 4. Iterate over all Python files
        for file_path in scan_dir.glob(pattern):
            # Skip hidden directories and common exclusions
            path_parts = file_path.parts
            skip = False
            for part in path_parts:
                if part.startswith('.') or part in ('__pycache__', 'venv', '.venv', 'node_modules', 'build', 'dist'):
                    skip = True
                    break
            
            if skip:
                continue
            
            # Read file content with error handling
            try:
                content = file_path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to read {file_path}: {e}")
                continue
            
            # Parse with AST
            try:
                tree = ast.parse(content)
            except SyntaxError:
                logger.debug(f"Skipped {file_path}: syntax error")
                continue
            
            # Walk AST nodes
            for node in ast.walk(tree):
                # Handle ast.Import nodes
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        # Add top-level module name
                        module_name = alias.name.split('.')[0]
                        all_imports.add(module_name)
                
                # Handle ast.ImportFrom nodes
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        # Add top-level module name
                        module_name = node.module.split('.')[0]
                        all_imports.add(module_name)
        
        # 5. Create set for missing modules
        missing_modules: Set[str] = set()
        
        # 6. For each import, check if it's missing
        for import_name in all_imports:
            # Skip stdlib
            if self.is_stdlib(import_name):
                continue
            
            # Skip blocked
            if self.is_blocked(import_name):
                continue
            
            # Resolve package name
            package_name = self.resolve_package_name(import_name)
            
            # Skip if already installed
            if self.is_installed(package_name):
                continue
            
            # Add to missing
            missing_modules.add(import_name)
        
        # 7. If no missing modules, return empty result
        if not missing_modules:
            return BulkInstallResult(total=0, successful=0, failed=0, skipped=0)
        
        # 8. Log info
        logger.info(f"Auto-installing {len(missing_modules)} missing packages from project scan: {missing_modules}")
        
        # 9. Initialize results
        results: List[InstallationResult] = []
        successful = 0
        failed = 0
        skipped = 0
        
        # 10. For each missing module, install
        for module in missing_modules:
            result = self.install_from_import(module)
            results.append(result)
            
            if result.status == InstallResult.SUCCESS:
                successful += 1
            elif result.status == InstallResult.FAILED:
                failed += 1
            else:
                skipped += 1
        
        # 11. Return result
        return BulkInstallResult(
            total=len(missing_modules),
            successful=successful,
            failed=failed,
            skipped=skipped,
            results=results
        )

    def install_from_requirements(self, requirements_path: Optional[Path] = None) -> BulkInstallResult:
        """
        Reads requirements.txt and installs any missing packages before validation.
        
        Args:
            requirements_path: Path to requirements file. Defaults to project_root/requirements.txt
            
        Returns:
            BulkInstallResult with installation statistics
        """
        if requirements_path is None:
            requirements_path = self.project_root / "requirements.txt"
        
        if not requirements_path.exists():
            logger.debug(f"No requirements.txt found at {requirements_path}")
            return BulkInstallResult(total=0, successful=0, failed=0, skipped=0)
        
        try:
            content = requirements_path.read_text(encoding='utf-8')
        except (FileNotFoundError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to read requirements.txt: {e}")
            return BulkInstallResult(total=0, successful=0, failed=0, skipped=0)
        
        # Parse requirements
        packages_to_check: List[str] = []
        for line in content.splitlines():
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Skip editable installs and URLs
            if line.startswith('-e') or line.startswith('git+') or line.startswith('http'):
                continue
            
            # Extract package name from various formats:
            # package==1.0.0, package>=1.0, package[extra], package
            package_name = line
            
            # Remove version specifiers
            for sep in ['==', '>=', '<=', '!=', '~=', '<', '>']:
                if sep in package_name:
                    package_name = package_name.split(sep)[0]
                    break
            
            # Remove extras like [dev], [test]
            if '[' in package_name:
                package_name = package_name.split('[')[0]
            
            package_name = package_name.strip()
            
            if package_name and not self.is_stdlib(package_name):
                packages_to_check.append(package_name)
        
        if not packages_to_check:
            logger.debug("No packages to install from requirements.txt")
            return BulkInstallResult(total=0, successful=0, failed=0, skipped=0)
        
        # Check which packages are missing
        missing_packages: List[str] = []
        for pkg in packages_to_check:
            # Resolve import name to pip name
            pip_name = self.resolve_package_name(pkg)
            if not self.is_installed(pip_name) and not self.is_installed(pkg):
                missing_packages.append(pkg)
        
        if not missing_packages:
            logger.info(f"All {len(packages_to_check)} packages from requirements.txt are already installed")
            return BulkInstallResult(total=len(packages_to_check), successful=0, failed=0, skipped=len(packages_to_check))
        
        logger.info(f"Installing {len(missing_packages)} missing packages from requirements.txt: {missing_packages}")
        
        # Install missing packages
        results: List[InstallationResult] = []
        successful = 0
        failed = 0
        skipped = 0
        
        for pkg in missing_packages:
            result = self.install_from_import(pkg)
            results.append(result)
            
            if result.status == InstallResult.SUCCESS:
                successful += 1
                logger.info(f"✅ Installed from requirements.txt: {pkg}")
            elif result.status == InstallResult.FAILED:
                failed += 1
                logger.warning(f"❌ Failed to install from requirements.txt: {pkg} - {result.message}")
            else:
                skipped += 1
        
        return BulkInstallResult(
            total=len(missing_packages),
            successful=successful,
            failed=failed,
            skipped=skipped,
            results=results,
        )


# ============================================================================
# TOOL FUNCTIONS (для вызова из ToolExecutor)
# ============================================================================

def list_installed_packages_tool(
    project_dir: str,
    language: Optional[str] = None,
    python_path: Optional[str] = None
) -> str:
    """
    Lists installed packages for one or all supported languages.

    Args:
        project_dir: Path to project directory
        language: Optional language filter ('python', 'javascript', 'typescript', 'go', 'java').
                If None, lists packages for all detected languages.
        python_path: Optional path to Python executable to use for Python packages.

    Returns:
        XML formatted list of packages grouped by language
    """
    import subprocess
    import json

    project_path = Path(project_dir)
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_lines.append('<installed_packages>')

    languages_to_check = []
    if language:
        lang_lower = language.lower()
        # Treat typescript as javascript (both use npm)
        if lang_lower == "typescript":
            lang_lower = "javascript"
        languages_to_check = [lang_lower]
    else:
        # Check all supported languages
        languages_to_check = ["python", "javascript", "go", "java"]

    for lang in languages_to_check:
        xml_lines.append(f'  <language name="{lang}">')

        if lang == "python":
            # --- Python: use pip list ---
            packages = []
            effective_python = python_path or sys.executable
            try:
                result = subprocess.run(
                    [effective_python, "-m", "pip", "list", "--format=json"],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=30
                )
                if result.returncode == 0:
                    pip_list = json.loads(result.stdout)
                    packages = [
                        {"name": pkg.get("name", ""), "version": pkg.get("version", "")}
                        for pkg in pip_list
                    ]
            except Exception as e:
                logger.warning(f"Failed to list Python packages: {e}")
            for pkg in packages:
                name = pkg.get("name", "unknown")
                version = pkg.get("version", "unknown")
                xml_lines.append(f'    <package name="{name}" version="{version}" />')

        elif lang == "javascript":
            # --- JavaScript/TypeScript: use npm list ---
            if shutil.which('npm') is None:
                xml_lines.append('    <!-- npm not available -->')
            else:
                try:
                    result = subprocess.run(
                        ['npm', 'list', '--json', '--depth=0'],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=30,
                        cwd=str(project_path)
                    )
                    # npm list exits with non-zero if there are peer dep issues; still parse stdout
                    if result.stdout.strip():
                        try:
                            npm_data = json.loads(result.stdout)
                            deps = npm_data.get("dependencies", {})
                            for pkg_name, pkg_info in deps.items():
                                ver = pkg_info.get("version", "unknown") if isinstance(pkg_info, dict) else "unknown"
                                xml_lines.append(f'    <package name="{pkg_name}" version="{ver}" />')
                        except json.JSONDecodeError:
                            xml_lines.append('    <!-- Failed to parse npm list output -->')
                    else:
                        xml_lines.append('    <!-- No npm packages found or package.json missing -->')
                except Exception as e:
                    logger.warning(f"Failed to list npm packages: {e}")
                    xml_lines.append(f'    <!-- Error: {e} -->')

        elif lang == "go":
            # --- Go: parse go.sum or use go list ---
            go_mod = project_path / "go.mod"
            if not go_mod.exists():
                xml_lines.append('    <!-- go.mod not found in project root -->')
            elif shutil.which('go') is None:
                xml_lines.append('    <!-- go binary not available -->')
            else:
                try:
                    result = subprocess.run(
                        ['go', 'list', '-m', '-json', 'all'],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=30,
                        cwd=str(project_path)
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        # Output is multiple JSON objects concatenated
                        # Parse them one by one
                        decoder = json.JSONDecoder()
                        raw = result.stdout.strip()
                        pos = 0
                        while pos < len(raw):
                            try:
                                obj, idx = decoder.raw_decode(raw, pos)
                                mod_path = obj.get("Path", "")
                                mod_ver = obj.get("Version", "")
                                if mod_path and mod_ver:
                                    xml_lines.append(f'    <package name="{mod_path}" version="{mod_ver}" />')
                                pos = idx
                                # Skip whitespace between JSON objects
                                while pos < len(raw) and raw[pos] in ' \t\n\r':
                                    pos += 1
                            except json.JSONDecodeError:
                                break
                    else:
                        xml_lines.append('    <!-- No Go modules found -->')
                except Exception as e:
                    logger.warning(f"Failed to list Go modules: {e}")
                    xml_lines.append(f'    <!-- Error: {e} -->')

        elif lang == "java":
            # --- Java: parse pom.xml dependencies ---
            pom_xml = project_path / "pom.xml"
            build_gradle = project_path / "build.gradle"
            build_gradle_kts = project_path / "build.gradle.kts"

            if pom_xml.exists():
                try:
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(str(pom_xml))
                    root = tree.getroot()
                    # Maven namespaces
                    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
                    # Try with namespace first, then without
                    deps_section = root.find('.//m:dependencies', ns)
                    if deps_section is None:
                        deps_section = root.find('.//dependencies')
                    if deps_section is not None:
                        for dep in deps_section.findall('m:dependency', ns) or deps_section.findall('dependency'):
                            group_id = dep.find('m:groupId', ns) or dep.find('groupId')
                            artifact_id = dep.find('m:artifactId', ns) or dep.find('artifactId')
                            version_el = dep.find('m:version', ns) or dep.find('version')
                            g = group_id.text if group_id is not None else "unknown"
                            a = artifact_id.text if artifact_id is not None else "unknown"
                            v = version_el.text if version_el is not None else "unknown"
                            xml_lines.append(f'    <package name="{g}:{a}" version="{v}" />')
                    else:
                        xml_lines.append('    <!-- No dependencies section found in pom.xml -->')
                except Exception as e:
                    logger.warning(f"Failed to parse pom.xml: {e}")
                    xml_lines.append(f'    <!-- Error parsing pom.xml: {e} -->')
            elif build_gradle.exists() or build_gradle_kts.exists():
                # Basic Gradle support: read file and extract dependency lines
                gradle_file = build_gradle if build_gradle.exists() else build_gradle_kts
                try:
                    content = gradle_file.read_text(encoding='utf-8')
                    dep_pattern = re.compile(
                        r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s+['\"]([^'\"]+)['\"]"""
                    )
                    matches = dep_pattern.findall(content)
                    for match in matches:
                        # Parse Maven coordinates: groupId:artifactId:version
                        parts = match.split(':')
                        if len(parts) >= 2:
                            group_id = parts[0]
                            artifact_id = parts[1]
                            version = parts[2] if len(parts) > 2 else "unknown"
                            xml_lines.append(f'    <package name="{group_id}:{artifact_id}" version="{version}" />')
                    if not matches:
                        xml_lines.append('    <!-- No dependencies found in build.gradle -->')
                except Exception as e:
                    logger.warning(f"Failed to parse build.gradle: {e}")
                    xml_lines.append(f'    <!-- Error parsing build.gradle: {e} -->')
            else:
                xml_lines.append('    <!-- No pom.xml or build.gradle found -->')

        xml_lines.append(f'  </language>')

    xml_lines.append('</installed_packages>')
    return '\n'.join(xml_lines)


def install_dependency_tool(
    project_dir: str,
    package_name: str,
    language: Optional[str] = None,
    version: Optional[str] = None,
    python_path: Optional[str] = None
) -> str:
    """
    Installs a dependency for the specified programming language.

    Args:
        project_dir: Path to project directory
        package_name: Package identifier (pip name, npm name, go module path, or Maven coordinates)
        language: Target language: 'python', 'javascript', 'typescript', 'go', 'java'.
                 If None, attempts installation for all supported languages.
        version: Optional version specifier
        python_path: Optional path to Python executable (used for Python installs)

    Returns:
        XML formatted result
    """
    try:
        manager = DependencyManager(Path(project_dir), auto_create_venv=True)

        # Override python_path if provided (relevant for Python installs)
        if python_path and language and language.lower() == "python":
            manager._python_path = python_path
            if python_path.endswith("python.exe"):
                manager._pip_path = python_path.replace("python.exe", "pip.exe")
            elif python_path.endswith("python"):
                manager._pip_path = python_path.replace("python", "pip")
            else:
                python_dir = Path(python_path).parent
                if sys.platform == "win32":
                    manager._pip_path = str(python_dir / "pip.exe")
                else:
                    manager._pip_path = str(python_dir / "pip")

        # Route to the appropriate language installer
        result = manager.install_dependency_for_language(
            package_name=package_name,
            language=language,
            version=version,
        )

        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_lines.append('<result>')
        xml_lines.append(f'  <status>{result.status.value}</status>')
        xml_lines.append(f'  <package>{result.package}</package>')
        if language:
            xml_lines.append(f'  <language>{language}</language>')
        else:
            xml_lines.append(f'  <language>auto-detected</language>')
        xml_lines.append(f'  <message>{result.message}</message>')
        if result.version:
            xml_lines.append(f'  <version>{result.version}</version>')
        xml_lines.append('</result>')

        return '\n'.join(xml_lines)

    except Exception as e:
        logger.error(f"Failed to install {package_name} ({language}): {e}")
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_lines.append('<result>')
        xml_lines.append(f'  <status>error</status>')
        xml_lines.append(f'  <package>{package_name}</package>')
        if language:
            xml_lines.append(f'  <language>{language}</language>')
        xml_lines.append(f'  <error>{str(e)}</error>')
        xml_lines.append('</result>')

        return '\n'.join(xml_lines)


def search_pypi_tool(query: str) -> str:
    """
    Tool: Поиск пакета на PyPI.
    
    Args:
        query: Поисковый запрос
        
    Returns:
        XML с результатами
    """
    import urllib.request
    import urllib.parse
    
    try:
        # PyPI JSON API
        url = f"https://pypi.org/pypi/{urllib.parse.quote(query)}/json"
        
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Agent/1.0"})
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            
            info = data.get("info", {})
            name = info.get("name", query)
            version = info.get("version", "unknown")
            summary = info.get("summary", "")[:200]
            
            return f"""<pypi_package>
  <name>{name}</name>
  <version>{version}</version>
  <summary>{summary}</summary>
  <install_command>pip install {name}</install_command>
</pypi_package>"""
            
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Попробуем поиск
            return _search_pypi_suggestions(query)
        return f"""<error>
  <message>PyPI error: {e}</message>
</error>"""
    except Exception as e:
        return f"""<error>
  <message>Search failed: {e}</message>
</error>"""


def _search_pypi_suggestions(query: str) -> str:
    """Поиск похожих пакетов на PyPI"""
    import urllib.request
    
    try:
        # Используем простой поиск через XML-RPC или web scraping
        # Упрощённый вариант - проверяем известные маппинги
        
        suggestions = []
        query_lower = query.lower()
        
        # Проверяем маппинг
        if query_lower in IMPORT_TO_PACKAGE:
            suggestions.append(IMPORT_TO_PACKAGE[query_lower])
        
        # Добавляем вариации
        variations = [
            f"python-{query}",
            f"py{query}",
            f"{query}-python",
        ]
        
        for var in variations:
            if var not in suggestions:
                suggestions.append(var)
        
        if suggestions:
            items = [f"  <suggestion>{s}</suggestion>" for s in suggestions[:5]]
            return f"""<pypi_search query="{query}">
  <status>Package '{query}' not found directly</status>
  <suggestions>
{chr(10).join(items)}
  </suggestions>
  <hint>Try: install_dependency with one of the suggestions</hint>
</pypi_search>"""
        
        return f"""<pypi_search query="{query}">
  <status>No matches found</status>
  <hint>Check the package name on https://pypi.org</hint>
</pypi_search>"""
        
    except Exception as e:
        return f"""<error>
  <message>Search failed: {e}</message>
</error>"""