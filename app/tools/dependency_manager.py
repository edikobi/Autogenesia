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
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set, Tuple
from enum import Enum
from importlib.metadata import packages_distributions


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

# ============================================================================
# TOOL FUNCTIONS (для вызова из ToolExecutor)
# ============================================================================

def list_installed_packages_tool(project_dir: str, python_path: Optional[str] = None) -> str:
    """
    Lists installed packages in the project.
    
    Args:
        project_dir: Path to project directory
        python_path: Optional path to Python executable to use
        
    Returns:
        XML formatted list of packages
    """
    import subprocess
    import json
    
    packages = []
    
    if python_path:
        # Use provided python_path directly
        try:
            result = subprocess.run(
                [python_path, "-m", "pip", "list", "--format=json"],
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
            logger.warning(f"Failed to list packages with python_path {python_path}: {e}")
            packages = []
    else:
        # Fall back to DependencyManager logic
        try:
            manager = DependencyManager(Path(project_dir), auto_create_venv=False)
            packages = manager.list_installed_packages()
        except Exception as e:
            logger.warning(f"Failed to list packages: {e}")
            packages = []
    
    # Format as XML
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_lines.append('<packages>')
    
    for pkg in packages:
        name = pkg.get("name", "unknown")
        version = pkg.get("version", "unknown")
        xml_lines.append(f'  <package name="{name}" version="{version}" />')
    
    xml_lines.append('</packages>')
    
    return '\n'.join(xml_lines)


def install_dependency_tool(
    project_dir: str, 
    import_name: str, 
    version: Optional[str] = None,
    python_path: Optional[str] = None
) -> str:
    """
    Installs a dependency in the project.
    
    Args:
        project_dir: Path to project directory
        import_name: Name of the package to install
        version: Optional version specifier
        python_path: Optional path to Python executable to use
        
    Returns:
        XML formatted result
    """
    try:
        manager = DependencyManager(Path(project_dir), auto_create_venv=True)
        
        # Override python_path if provided
        if python_path:
            manager._python_path = python_path
            # Derive pip_path from python_path
            if python_path.endswith("python.exe"):
                manager._pip_path = python_path.replace("python.exe", "pip.exe")
            elif python_path.endswith("python"):
                manager._pip_path = python_path.replace("python", "pip")
            else:
                # Fallback: assume pip is in same directory
                python_dir = Path(python_path).parent
                if sys.platform == "win32":
                    manager._pip_path = str(python_dir / "pip.exe")
                else:
                    manager._pip_path = str(python_dir / "pip")
        
        success = manager.install_package(import_name, version)
        
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_lines.append('<result>')
        xml_lines.append(f'  <status>{"success" if success else "failed"}</status>')
        xml_lines.append(f'  <package>{import_name}</package>')
        if version:
            xml_lines.append(f'  <version>{version}</version>')
        xml_lines.append('</result>')
        
        return '\n'.join(xml_lines)
        
    except Exception as e:
        logger.error(f"Failed to install {import_name}: {e}")
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_lines.append('<result>')
        xml_lines.append(f'  <status>error</status>')
        xml_lines.append(f'  <package>{import_name}</package>')
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