# app/services/project_scanner.py
from __future__ import annotations
import hashlib
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Set, Optional

from app.utils.file_types import FileTypeDetector
from app.utils.token_counter import TokenCounter
from app.services.python_chunker import SmartPythonChunker, PythonChunk
from app.services.json_chunker import SmartJSONChunker, JSONChunk


PROJECT_MAP_FILENAME = "project_map.json"

# Папки, которые нужно игнорировать
IGNORE_DIRS: Set[str] = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache"
}

# Файлы, которые нужно игнорировать
IGNORE_FILES: Set[str] = {
    ".env", ".DS_Store", "Thumbs.db", "NTUSER.DAT"
}

# Расширения бинарных файлов (не считаем токены)
BINARY_EXTENSIONS: Set[str] = {
    ".dat", ".exe", ".dll", ".so", ".pyc", ".pyo", ".pyd",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tiff",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac", ".ogg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".o", ".a", ".lib", ".obj",
    ".class", ".jar", ".war",
    ".lock",  # package-lock.json и т.п. бывают огромными
}

# Типы файлов, для которых точно считаем токены
COUNTABLE_FILE_TYPES: Set[str] = {
    # Код
    "code/python", "code/go", "code/javascript", "code/typescript",
    "code/java", "code/c", "code/cpp", "code/csharp", "code/rust",
    "code/ruby", "code/php", "code/swift", "code/kotlin", "code/scala",
    "code/lua", "code/perl", "code/r", "code/julia",
    # Текст
    "text/plain", "text/markdown", "text/rst", "text/txt",
    # Данные
    "data/json", "data/yaml", "data/xml", "data/csv", "data/toml",
    # Другое
    "sql", "config", "shell", "dockerfile", "makefile",
}


@dataclass
class FileEntry:
    path: str
    type: str
    hash: str
    tokens_total: int


class ProjectScanner:
    """
    Строит карту проекта (структура файлов) и, при необходимости,
    возвращает детальные чанки для Python-файлов.
    """

    def __init__(self, root_path: str):
        self.root_path = Path(root_path).resolve()
        self.type_detector = FileTypeDetector()
        self.token_counter = TokenCounter()
        self.python_chunker = SmartPythonChunker(self.token_counter)
        self.json_chunker = SmartJSONChunker()

    def _hash_file(self, path: Path) -> str:
        """Безопасный хеш файла с обработкой ошибок."""
        hasher = hashlib.md5()
        try:
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (PermissionError, OSError):
            return "access_denied"

    def _should_ignore(self, path: Path) -> bool:
        """Проверка, нужно ли игнорировать файл/папку."""
        # Игнорируем скрытые файлы/папки (начинаются с точки на Windows тоже бывают)
        if path.name.startswith("."):
            return True
        if path.name in IGNORE_FILES:
            return True
        return False

    def _is_binary_file(self, path: Path) -> bool:
        """Проверяет, является ли файл бинарным."""
        return path.suffix.lower() in BINARY_EXTENSIONS

    def _count_file_tokens(self, path: Path) -> int:
        """
        Подсчитывает токены в файле с обработкой различных кодировок.
        Возвращает 0 для бинарных файлов или при ошибке чтения.
        """
        # Бинарные файлы не считаем
        if self._is_binary_file(path):
            return 0
        
        # Пробуем разные кодировки
        encodings_to_try = ["utf-8", "utf-8-sig", "latin-1", "cp1251", "cp1252"]
        
        for encoding in encodings_to_try:
            try:
                text = path.read_text(encoding=encoding)
                return self.token_counter.count(text)
            except UnicodeDecodeError:
                continue
            except (PermissionError, OSError, MemoryError):
                # Файл недоступен или слишком большой
                return 0
        
        # Ни одна кодировка не подошла — возможно бинарный файл
        return 0

    def _should_count_tokens(self, ftype: str, path: Path) -> bool:
        """
        Определяет, нужно ли считать токены для данного типа файла.
        """
        # Бинарные расширения — точно нет
        if self._is_binary_file(path):
            return False
        
        # Известные типы для подсчёта
        if ftype in COUNTABLE_FILE_TYPES:
            return True
        
        # Любой код или текст
        if ftype.startswith("code/") or ftype.startswith("text/") or ftype.startswith("data/"):
            return True
        
        # config, sql и подобные
        if ftype in {"sql", "config", "shell", "dockerfile", "makefile"}:
            return True
        
        # Для unknown пробуем, если расширение текстовое
        if ftype in {"other", "unknown"}:
            text_extensions = {
                ".txt", ".md", ".rst", ".log", ".ini", ".cfg", ".conf",
                ".sh", ".bash", ".zsh", ".fish",
                ".html", ".htm", ".css", ".scss", ".sass", ".less",
                ".xml", ".xsl", ".xslt",
                ".json", ".yaml", ".yml", ".toml",
                ".sql", ".graphql", ".gql",
                ".env.example", ".env.sample", ".gitignore", ".dockerignore",
                ".editorconfig", ".prettierrc", ".eslintrc",
            }
            if path.suffix.lower() in text_extensions:
                return True
            # Файлы без расширения с текстовыми именами
            if path.suffix == "" and path.name.lower() in {
                "readme", "license", "changelog", "makefile", "dockerfile",
                "vagrantfile", "gemfile", "rakefile", "procfile",
            }:
                return True
        
        return False

    def _scan_single_file(self, full_path: Path) -> Optional[FileEntry]:
        """Сканирует один файл и возвращает FileEntry или None."""
        # Пропускаем игнорируемые файлы
        if full_path.name in IGNORE_FILES or full_path.name.startswith("."):
            return None
        
        # Пропускаем служебные файлы индекса
        if full_path.name in {PROJECT_MAP_FILENAME, "semantic_index.json", 
                              "detailed_index.json", "chunks_index.json", 
                              "token_stats.json", "compact_index.json",
                              "compact_index.md", "project_map.md"}:
            return None
        
        # Пропускаем бинарные системные файлы
        if full_path.suffix.lower() in {".dat", ".exe", ".dll", ".so", ".pyc", ".pyo"}:
            return None
        
        ftype = self.type_detector.detect(str(full_path))
        file_hash = self._hash_file(full_path)
        
        # Если не смогли прочитать — пропускаем
        if file_hash == "access_denied":
            return None
        
        # Считаем токены для всех подходящих файлов
        tokens_total = 0
        if self._should_count_tokens(ftype, full_path):
            tokens_total = self._count_file_tokens(full_path)
        
        return FileEntry(
            path=str(full_path.relative_to(self.root_path)),
            type=ftype,
            hash=file_hash,
            tokens_total=tokens_total,
        )

    def _load_existing_map(self) -> Optional[Dict[str, Any]]:
        """Загружает существующую карту проекта с диска."""
        out_path = self.root_path / PROJECT_MAP_FILENAME
        if not out_path.exists():
            return None
        
        try:
            with out_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def scan(self) -> Dict[str, Any]:
        """
        Обходит директорию root_path и формирует карту проекта.
        Считает токены для ВСЕХ текстовых файлов, не только Python.
        """
        files: List[FileEntry] = []

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            # Фильтруем директории IN PLACE (чтобы os.walk не заходил в них)
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]

            for filename in filenames:
                full_path = Path(dirpath) / filename
                entry = self._scan_single_file(full_path)
                if entry:
                    files.append(entry)

        project_map = {
            "root": str(self.root_path),
            "files": [asdict(f) for f in files],
        }

        # Сохраняем на диск
        out_path = self.root_path / PROJECT_MAP_FILENAME
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(project_map, f, ensure_ascii=False, indent=2)

        return project_map

    def sync_scan(self) -> Dict[str, Any]:
        """
        Инкрементальное обновление карты проекта.
        
        Обнаруживает:
        - Новые файлы (добавленные)
        - Изменённые файлы (хеш изменился)
        - Удалённые файлы (больше не существуют)
        - Перемещённые файлы (тот же хеш, другой путь)
        
        Returns:
            Обновлённая карта проекта
        """
        existing_map = self._load_existing_map()
        
        if not existing_map:
            # Нет существующей карты — делаем полный скан
            return self.scan()
        
        # Строим lookup по пути и хешу
        existing_by_path: Dict[str, Dict] = {
            f["path"]: f for f in existing_map.get("files", [])
        }
        existing_by_hash: Dict[str, Dict] = {
            f["hash"]: f for f in existing_map.get("files", [])
        }
        
        # Сканируем текущие файлы
        updated_files: List[FileEntry] = []
        current_paths: Set[str] = set()
        
        stats = {"new": 0, "modified": 0, "unchanged": 0, "moved": 0}
        
        for dirpath, dirnames, filenames in os.walk(self.root_path):
            # Фильтруем директории
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
            
            for filename in filenames:
                full_path = Path(dirpath) / filename
                entry = self._scan_single_file(full_path)
                
                if not entry:
                    continue
                
                current_paths.add(entry.path)
                
                if entry.path in existing_by_path:
                    existing = existing_by_path[entry.path]
                    if existing["hash"] == entry.hash:
                        # Не изменился — оставляем как есть
                        stats["unchanged"] += 1
                    else:
                        # Изменился
                        stats["modified"] += 1
                elif entry.hash in existing_by_hash:
                    # Перемещён (тот же хеш, другой путь)
                    stats["moved"] += 1
                else:
                    # Новый файл
                    stats["new"] += 1
                
                updated_files.append(entry)
        
        # Подсчитываем удалённые (те, что были в existing, но нет в current)
        deleted_count = len(set(existing_by_path.keys()) - current_paths)
        
        project_map = {
            "root": str(self.root_path),
            "files": [asdict(f) for f in updated_files],
            "sync_stats": {
                "new": stats["new"],
                "modified": stats["modified"],
                "unchanged": stats["unchanged"],
                "moved": stats["moved"],
                "deleted": deleted_count,
            }
        }
        
        # Сохраняем на диск
        out_path = self.root_path / PROJECT_MAP_FILENAME
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(project_map, f, ensure_ascii=False, indent=2)
        
        return project_map

    def get_python_chunks(self, relative_path: str) -> List[PythonChunk]:
        """
        Вернёт иерархические чанки для Python-файла относительно root.
        """
        full_path = self.root_path / relative_path
        return self.python_chunker.chunk_file(str(full_path))
    
    def get_json_chunks(self, relative_path: str) -> List[JSONChunk]:
        """Возвращает чанки для JSON-файла."""
        full_path = self.root_path / relative_path
        return self.json_chunker.chunk_file(str(full_path))
    
    def get_chunks_for_file(self, relative_path: str):
        """Универсальный метод: определяет тип и возвращает чанки."""
        ftype = self.type_detector.detect(relative_path)
        
        if ftype == "code/python":
            return self.get_python_chunks(relative_path)
        elif ftype == "data/json":
            return self.get_json_chunks(relative_path)
        else:
            return []  # Для других типов пока нет чанкера