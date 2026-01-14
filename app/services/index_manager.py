# app/services/index_manager.py
"""
Index Manager - координирует создание и обновление всех индексов проекта.

Гарантирует правильный порядок операций:
1. Semantic Index (код) → compact_index.json, semantic_index.json
2. Project Map (все файлы) → project_map.json (использует compact_index для описаний кода)

Предоставляет:
- FullIndexBuilder: полная индексация с нуля
- IncrementalIndexUpdater: инкрементальное обновление

БЕЗОПАСНОСТЬ:
- Автоматическое сжатие semantic_index если > 60000 токенов
- Сжатый индекс сохраняет хеши для инкрементального обновления
"""

from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

from app.utils.token_counter import TokenCounter

logger = logging.getLogger(__name__)


# ============== КОНСТАНТЫ ==============

SEMANTIC_INDEX_TOKEN_LIMIT = 60000  # Лимит токенов для semantic_index
COMPRESSED_INDEX_FILENAME = "semantic_index_compressed.json"


# ============== ВАЛИДАЦИЯ НАСТРОЕК ==============

def _validate_settings():
    """Проверяет наличие необходимых настроек в settings.py"""
    from config.settings import cfg
    
    required_attrs = [
        'PROJECT_MAP_MAX_FILE_TOKENS',
        'PROJECT_MAP_DESCRIBE_MODEL',
    ]
    
    missing = []
    for attr in required_attrs:
        if not hasattr(cfg, attr):
            missing.append(attr)
    
    if missing:
        raise ImportError(
            f"Missing required settings in config/settings.py: {', '.join(missing)}\n"
            f"Please add:\n"
            f"  PROJECT_MAP_MAX_FILE_TOKENS = 30000\n"
            f"  PROJECT_MAP_DESCRIBE_MODEL = MODEL_NORMAL"
        )

# Валидация при импорте
_validate_settings()


# ============== DATA CLASSES ==============

@dataclass
class IndexStats:
    """Статистика индексации"""
    # Semantic index stats
    code_files_indexed: int = 0
    classes_found: int = 0
    functions_found: int = 0
    code_tokens_total: int = 0
    
    # Project map stats
    total_files: int = 0
    code_files: int = 0
    non_code_files: int = 0
    ai_descriptions_generated: int = 0
    ai_descriptions_failed: int = 0
    ai_descriptions_skipped: int = 0  # Файлы > 30k токенов
    total_tokens: int = 0
    
    # Compression stats
    index_compressed: bool = False
    original_index_tokens: int = 0
    compressed_index_tokens: int = 0
    
    # Timing
    semantic_index_time_sec: float = 0.0
    project_map_time_sec: float = 0.0
    total_time_sec: float = 0.0
    
    # Errors
    errors: List[Dict[str, str]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code_files_indexed": self.code_files_indexed,
            "classes_found": self.classes_found,
            "functions_found": self.functions_found,
            "code_tokens_total": self.code_tokens_total,
            "total_files": self.total_files,
            "code_files": self.code_files,
            "non_code_files": self.non_code_files,
            "ai_descriptions_generated": self.ai_descriptions_generated,
            "ai_descriptions_failed": self.ai_descriptions_failed,
            "ai_descriptions_skipped": self.ai_descriptions_skipped,
            "total_tokens": self.total_tokens,
            "index_compressed": self.index_compressed,
            "original_index_tokens": self.original_index_tokens,
            "compressed_index_tokens": self.compressed_index_tokens,
            "semantic_index_time_sec": round(self.semantic_index_time_sec, 2),
            "project_map_time_sec": round(self.project_map_time_sec, 2),
            "total_time_sec": round(self.total_time_sec, 2),
            "errors_count": len(self.errors),
            "errors": self.errors[:10],  # Первые 10 ошибок
        }


@dataclass
class SyncStats:
    """Статистика инкрементального обновления"""
    # Changes detected
    new_files: int = 0
    modified_files: int = 0
    deleted_files: int = 0
    moved_files: int = 0
    unchanged_files: int = 0
    
    # AI descriptions
    ai_descriptions_generated: int = 0
    ai_descriptions_failed: int = 0
    
    # Compression
    index_compressed: bool = False
    
    # Timing
    total_time_sec: float = 0.0
    
    # Errors
    errors: List[Dict[str, str]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_files": self.new_files,
            "modified_files": self.modified_files,
            "deleted_files": self.deleted_files,
            "moved_files": self.moved_files,
            "unchanged_files": self.unchanged_files,
            "ai_descriptions_generated": self.ai_descriptions_generated,
            "ai_descriptions_failed": self.ai_descriptions_failed,
            "index_compressed": self.index_compressed,
            "total_time_sec": round(self.total_time_sec, 2),
            "errors_count": len(self.errors),
        }


# ============== PROGRESS CALLBACK ==============

ProgressCallback = Callable[[str, int, int], None]  # (message, current, total)


def default_progress_callback(message: str, current: int, total: int) -> None:
    """Дефолтный callback - просто логирует"""
    if total > 0:
        logger.info(f"[{current}/{total}] {message}")
    else:
        logger.info(message)


# ============== INDEX COMPRESSION ==============

class IndexCompressor:
    """
    Сжимает semantic_index если он превышает лимит токенов.
    
    Сжатый формат:
    - Плоская структура без вложенности
    - Файлы: путь, директория, импорты одной строкой, хеш
    - Классы: имя, файл, строки, методы одной строкой, описание, хеш
    - Функции: имя, файл, строки, описание, хеш
    """
    
    def __init__(self):
        self.token_counter = TokenCounter()
    
    def check_and_compress(
        self, 
        index: Dict[str, Any],
        project_path: Path
    ) -> tuple[Dict[str, Any], bool, int, int]:
        """
        Проверяет размер индекса и сжимает если нужно.
        
        Args:
            index: Полный semantic_index
            project_path: Путь к проекту для сохранения
            
        Returns:
            (index, was_compressed, original_tokens, final_tokens)
            - index: оригинальный или сжатый индекс
            - was_compressed: True если был сжат
            - original_tokens: токены до сжатия
            - final_tokens: токены после (или те же если не сжимали)
        """
        # Считаем токены оригинального индекса
        index_str = json.dumps(index, ensure_ascii=False)
        original_tokens = self.token_counter.count(index_str)
        
        logger.info(f"Semantic index size: {original_tokens} tokens (limit: {SEMANTIC_INDEX_TOKEN_LIMIT})")
        
        # Если в пределах лимита - возвращаем как есть
        if original_tokens <= SEMANTIC_INDEX_TOKEN_LIMIT:
            return index, False, original_tokens, original_tokens
        
        # Нужно сжатие
        logger.warning(
            f"Semantic index exceeds limit ({original_tokens} > {SEMANTIC_INDEX_TOKEN_LIMIT}), "
            f"compressing..."
        )
        
        compressed = self._compress_index(index)
        
        # Считаем токены сжатого индекса
        compressed_str = json.dumps(compressed, ensure_ascii=False)
        compressed_tokens = self.token_counter.count(compressed_str)
        
        # Сохраняем сжатый индекс
        compressed_path = project_path / ".ai-agent" / COMPRESSED_INDEX_FILENAME
        compressed_path.parent.mkdir(parents=True, exist_ok=True)
        
        with compressed_path.open("w", encoding="utf-8") as f:
            json.dump(compressed, f, ensure_ascii=False, indent=2)
        
        logger.info(
            f"Index compressed: {original_tokens} -> {compressed_tokens} tokens "
            f"(saved {original_tokens - compressed_tokens} tokens, "
            f"{100 - (compressed_tokens * 100 // original_tokens)}% reduction)"
        )
        
        return compressed, True, original_tokens, compressed_tokens
    
    def _compress_index(self, index: Dict[str, Any]) -> Dict[str, Any]:
        """
        Создаёт сжатую версию индекса.
        
        Убирает:
        - Иерархию (файлы содержат классы/функции)
        - Подробные references
        - globals
        - full_path (оставляем только относительный)
        - tokens для отдельных элементов
        
        Оставляем:
        - Хеши для инкрементального обновления
        - AI-описания
        - Строки (lines) для навигации
        - Методы классов (одной строкой)
        """
        compressed = {
            "version": index.get("version", "1.2") + "-compressed",
            "compressed": True,
            "created_at": index.get("created_at", ""),
            "updated_at": index.get("updated_at", ""),
            "root_path": index.get("root_path", ""),
            "total_files": 0,
            "files": [],
            "classes": [],
            "functions": [],
        }
        
        files_data = index.get("files", {})
        
        for file_path, file_info in files_data.items():
            # Добавляем файл
            imports_info = file_info.get("imports", {})
            imports_list = imports_info.get("modules", []) if imports_info else []
            
            compressed["files"].append({
                "path": file_path,
                "dir": str(Path(file_path).parent),
                "imports": ", ".join(imports_list[:20]),  # Ограничиваем до 20 импортов
                "hash": file_info.get("file_hash", ""),
            })
            
            # Добавляем классы
            for cls in file_info.get("classes", []):
                methods = cls.get("methods", [])
                compressed["classes"].append({
                    "name": cls.get("name", ""),
                    "file": file_path,
                    "lines": cls.get("lines", ""),
                    "methods": ", ".join(methods[:15]),  # Ограничиваем до 15 методов
                    "description": cls.get("description", ""),
                    "hash": cls.get("content_hash", ""),
                })
            
            # Добавляем функции
            for func in file_info.get("functions", []):
                compressed["functions"].append({
                    "name": func.get("name", ""),
                    "file": file_path,
                    "lines": func.get("lines", ""),
                    "description": func.get("description", ""),
                    "hash": func.get("content_hash", ""),
                })
        
        compressed["total_files"] = len(compressed["files"])
        
        return compressed
    
    def is_compressed(self, index: Dict[str, Any]) -> bool:
        """Проверяет, является ли индекс сжатым"""
        return index.get("compressed", False)


# ============== FULL INDEX BUILDER ==============

class FullIndexBuilder:
    """
    Полная индексация проекта с нуля.
    
    Порядок операций:
    1. Semantic Index (анализ кода) → compact_index.json, semantic_index.json
    2. Проверка размера semantic_index, сжатие если > 60k токенов
    3. Project Map (все файлы) → project_map.json (с AI-описаниями для не-код файлов)
    
    Usage:
        builder = FullIndexBuilder("/path/to/project")
        stats = await builder.build(on_progress=my_callback)
    """
    
    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.compressor = IndexCompressor()
        self._ensure_ai_agent_dir()
    
    def _ensure_ai_agent_dir(self):
        """Создаёт директорию .ai-agent если не существует"""
        ai_agent_dir = self.project_path / ".ai-agent"
        ai_agent_dir.mkdir(parents=True, exist_ok=True)
    
    async def build(
        self,
        on_progress: Optional[ProgressCallback] = None
    ) -> IndexStats:
        """
        Выполняет полную индексацию проекта.
        
        Args:
            on_progress: Callback для отслеживания прогресса
                        (message: str, current: int, total: int)
        
        Returns:
            IndexStats с полной статистикой
        """
        progress = on_progress or default_progress_callback
        stats = IndexStats()
        start_time = datetime.now()
        
        try:
            # ========== STEP 1: Semantic Index ==========
            progress("Building semantic index (code analysis)...", 0, 3)
            semantic_start = datetime.now()
            
            semantic_index = await self._build_semantic_index(stats, progress)
            
            stats.semantic_index_time_sec = (datetime.now() - semantic_start).total_seconds()
            progress("Semantic index complete", 1, 3)
            
            # ========== STEP 2: Check & Compress Index ==========
            progress("Checking index size...", 1, 3)
            
            final_index, was_compressed, original_tokens, final_tokens = \
                self.compressor.check_and_compress(semantic_index, self.project_path)
            
            stats.index_compressed = was_compressed
            stats.original_index_tokens = original_tokens
            stats.compressed_index_tokens = final_tokens
            
            if was_compressed:
                progress(f"Index compressed: {original_tokens} -> {final_tokens} tokens", 1, 3)
            
            # ========== STEP 3: Project Map ==========
            progress("Building project map (all files with descriptions)...", 2, 3)
            map_start = datetime.now()
            
            await self._build_project_map(stats, progress)
            
            stats.project_map_time_sec = (datetime.now() - map_start).total_seconds()
            progress("Project map complete", 3, 3)
            
        except Exception as e:
            logger.error(f"Full index build failed: {e}")
            stats.errors.append({"stage": "build", "error": str(e)})
            raise
        
        finally:
            stats.total_time_sec = (datetime.now() - start_time).total_seconds()
        
        logger.info(
            f"Full index complete: {stats.code_files_indexed} code files, "
            f"{stats.total_files} total files, {stats.total_time_sec:.1f}s"
            + (f", compressed" if stats.index_compressed else "")
        )
        
        return stats
    
    async def _build_semantic_index(
        self,
        stats: IndexStats,
        progress: ProgressCallback
    ) -> Dict[str, Any]:
        """Строит semantic index (анализ кода) и возвращает его"""
        from app.builders.semantic_index_builder import SemanticIndexer
        
        indexer = SemanticIndexer(str(self.project_path))
        
        # build_index_async возвращает индекс
        index = await indexer.build_index_async()
        
        # Собираем статистику
        for file_path, file_data in index.get("files", {}).items():
            stats.code_files_indexed += 1
            stats.classes_found += len(file_data.get("classes", []))
            stats.functions_found += len(file_data.get("functions", []))
            
            # Считаем токены
            for cls in file_data.get("classes", []):
                stats.code_tokens_total += cls.get("tokens", 0)
            for func in file_data.get("functions", []):
                stats.code_tokens_total += func.get("tokens", 0)
        
        return index
    
    async def _build_project_map(
        self,
        stats: IndexStats,
        progress: ProgressCallback
    ) -> None:
        """Строит project map с AI-описаниями"""
        from app.services.project_map_builder import ProjectMapBuilder
        
        builder = ProjectMapBuilder(str(self.project_path))
        
        # Получаем compact_index для контекста AI
        compact_index_path = self.project_path / ".ai-agent" / "compact_index.md"
        code_map_context = ""
        if compact_index_path.exists():
            try:
                code_map_context = compact_index_path.read_text(encoding="utf-8")
            except Exception:
                pass
        
        # Создаём wrapper для отслеживания AI-описаний
        original_generate = builder._generate_single_description
        
        async def tracked_generate(file_entry, context):
            try:
                result = await original_generate(file_entry, context)
                if result and result != "Description unavailable":
                    stats.ai_descriptions_generated += 1
                else:
                    stats.ai_descriptions_failed += 1
                progress(f"Described: {file_entry.path}", 0, 0)
                return result
            except Exception as e:
                stats.ai_descriptions_failed += 1
                stats.errors.append({"file": file_entry.path, "error": str(e)})
                return "Description unavailable"
        
        builder._generate_single_description = tracked_generate
        
        # Строим карту
        project_map = await builder.build_map(code_map_context)
        
        # Собираем статистику
        stats.total_files = project_map.total_files
        stats.total_tokens = project_map.total_tokens
        
        from config.settings import cfg
        for f in project_map.files:
            if f.is_code:
                stats.code_files += 1
            else:
                stats.non_code_files += 1
            
            # Файлы, пропущенные из-за размера
            if f.tokens > cfg.PROJECT_MAP_MAX_FILE_TOKENS:
                stats.ai_descriptions_skipped += 1


# ============== INCREMENTAL INDEX UPDATER ==============

class IncrementalIndexUpdater:
    """
    Инкрементальное обновление индексов проекта.
    
    Обнаруживает:
    - Новые файлы
    - Изменённые файлы (по хешу)
    - Удалённые файлы
    - Перемещённые файлы (тот же хеш, другой путь)
    
    Обновляет только изменённые части, сохраняя существующие описания.
    После обновления проверяет размер и сжимает если нужно.
    
    Usage:
        updater = IncrementalIndexUpdater("/path/to/project")
        stats = await updater.sync(on_progress=my_callback)
    """
    
    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.compressor = IndexCompressor()
    
    async def sync(
        self,
        on_progress: Optional[ProgressCallback] = None
    ) -> SyncStats:
        """
        Выполняет инкрементальное обновление индексов.
        
        Args:
            on_progress: Callback для отслеживания прогресса
        
        Returns:
            SyncStats со статистикой изменений
        """
        progress = on_progress or default_progress_callback
        stats = SyncStats()
        start_time = datetime.now()
        
        try:
            # Проверяем, существуют ли индексы
            if not self._indexes_exist():
                progress("No existing indexes found, running full build...", 0, 1)
                builder = FullIndexBuilder(str(self.project_path))
                full_stats = await builder.build(on_progress)
                
                # Конвертируем в SyncStats
                stats.new_files = full_stats.total_files
                stats.ai_descriptions_generated = full_stats.ai_descriptions_generated
                stats.ai_descriptions_failed = full_stats.ai_descriptions_failed
                stats.index_compressed = full_stats.index_compressed
                stats.total_time_sec = full_stats.total_time_sec
                return stats
            
            # ========== STEP 1: Sync Semantic Index ==========
            progress("Syncing semantic index...", 0, 3)
            semantic_index = await self._sync_semantic_index(stats, progress)
            
            # ========== STEP 2: Check & Compress ==========
            progress("Checking index size...", 1, 3)
            
            if semantic_index:
                final_index, was_compressed, _, _ = \
                    self.compressor.check_and_compress(semantic_index, self.project_path)
                stats.index_compressed = was_compressed
            
            # ========== STEP 3: Sync Project Map ==========
            progress("Syncing project map...", 2, 3)
            await self._sync_project_map(stats, progress)
            
            progress("Sync complete", 3, 3)
            
        except Exception as e:
            logger.error(f"Incremental sync failed: {e}")
            stats.errors.append({"stage": "sync", "error": str(e)})
            raise
        
        finally:
            stats.total_time_sec = (datetime.now() - start_time).total_seconds()
        
        logger.info(
            f"Sync complete: +{stats.new_files} new, ~{stats.modified_files} modified, "
            f"-{stats.deleted_files} deleted, {stats.total_time_sec:.1f}s"
        )
        
        return stats
    
    def _indexes_exist(self) -> bool:
        """Проверяет, существуют ли файлы индексов"""
        ai_agent_dir = self.project_path / ".ai-agent"
        
        semantic_exists = (ai_agent_dir / "semantic_index.json").exists()
        map_exists = (ai_agent_dir / "project_map.json").exists()
        
        return semantic_exists and map_exists
    
    async def _sync_semantic_index(
        self,
        stats: SyncStats,
        progress: ProgressCallback
    ) -> Optional[Dict[str, Any]]:
        """Инкрементальное обновление semantic index"""
        from app.builders.semantic_index_builder import SemanticIndexer
        
        indexer = SemanticIndexer(str(self.project_path))
        
        # sync_index возвращает статистику и обновляет индекс на диске
        sync_result = await indexer.sync_index()
        
        stats.new_files = sync_result.get("added", 0)
        stats.modified_files = sync_result.get("modified", 0)
        stats.deleted_files = sync_result.get("deleted", 0)
        stats.moved_files = sync_result.get("moved", 0)
        
        # Загружаем обновлённый индекс для проверки размера
        index_path = self.project_path / ".ai-agent" / "semantic_index.json"
        if index_path.exists():
            try:
                with index_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        
        return None
    
    async def _sync_project_map(
        self,
        stats: SyncStats,
        progress: ProgressCallback
    ) -> None:
        """Инкрементальное обновление project map"""
        from app.services.project_map_builder import ProjectMapBuilder
        
        builder = ProjectMapBuilder(str(self.project_path))
        
        # Получаем compact_index для контекста
        compact_index_path = self.project_path / ".ai-agent" / "compact_index.md"
        code_map_context = ""
        if compact_index_path.exists():
            try:
                code_map_context = compact_index_path.read_text(encoding="utf-8")
            except Exception:
                pass
        
        # Подсчёт AI-описаний
        original_generate = builder._generate_single_description
        
        async def tracked_generate(file_entry, context):
            try:
                result = await original_generate(file_entry, context)
                if result and result != "Description unavailable":
                    stats.ai_descriptions_generated += 1
                else:
                    stats.ai_descriptions_failed += 1
                progress(f"Described: {file_entry.path}", 0, 0)
                return result
            except Exception as e:
                stats.ai_descriptions_failed += 1
                stats.errors.append({"file": file_entry.path, "error": str(e)})
                return "Description unavailable"
        
        builder._generate_single_description = tracked_generate
        
        # sync_map делает инкрементальное обновление
        await builder.sync_map(code_map_context)


# ============== CONVENIENCE FUNCTIONS ==============

async def build_full_index(
    project_path: str,
    on_progress: Optional[ProgressCallback] = None
) -> IndexStats:
    """
    Полная индексация проекта.
    
    Args:
        project_path: Путь к корню проекта
        on_progress: Callback (message, current, total)
    
    Returns:
        IndexStats со статистикой
    """
    builder = FullIndexBuilder(project_path)
    return await builder.build(on_progress)


async def sync_index(
    project_path: str,
    on_progress: Optional[ProgressCallback] = None
) -> SyncStats:
    """
    Инкрементальное обновление индексов.
    
    Args:
        project_path: Путь к корню проекта
        on_progress: Callback (message, current, total)
    
    Returns:
        SyncStats со статистикой изменений
    """
    updater = IncrementalIndexUpdater(project_path)
    return await updater.sync(on_progress)


def get_index_stats(project_path: str) -> Optional[Dict[str, Any]]:
    """
    Получает статистику существующих индексов.
    
    Returns:
        Dict со статистикой или None если индексы не существуют
    """
    ai_agent_dir = Path(project_path) / ".ai-agent"
    
    result = {
        "semantic_index_exists": False,
        "project_map_exists": False,
        "is_compressed": False,
        "code_files": 0,
        "total_files": 0,
        "total_tokens": 0,
    }
    
    # Semantic index
    semantic_path = ai_agent_dir / "semantic_index.json"
    if semantic_path.exists():
        result["semantic_index_exists"] = True
        try:
            with semantic_path.open("r") as f:
                data = json.load(f)
            result["code_files"] = len(data.get("files", {}))
            result["is_compressed"] = data.get("compressed", False)
        except Exception:
            pass
    
    # Check for compressed index
    compressed_path = ai_agent_dir / COMPRESSED_INDEX_FILENAME
    if compressed_path.exists():
        result["is_compressed"] = True
    
    # Project map
    map_path = ai_agent_dir / "project_map.json"
    if map_path.exists():
        result["project_map_exists"] = True
        try:
            with map_path.open("r") as f:
                data = json.load(f)
            result["total_files"] = data.get("total_files", 0)
            result["total_tokens"] = data.get("total_tokens", 0)
        except Exception:
            pass
    
    if not result["semantic_index_exists"] and not result["project_map_exists"]:
        return None
    
    return result


def is_index_compressed(project_path: str) -> bool:
    """
    Проверяет, используется ли сжатый индекс.
    
    Args:
        project_path: Путь к проекту
        
    Returns:
        True если индекс сжат
    """
    compressed_path = Path(project_path) / ".ai-agent" / COMPRESSED_INDEX_FILENAME
    return compressed_path.exists()


def load_semantic_index(project_path: str) -> Optional[Dict[str, Any]]:
    """
    Загружает semantic index (обычный или сжатый).
    
    Автоматически определяет формат и возвращает нужный.
    
    Args:
        project_path: Путь к проекту
        
    Returns:
        Dict с индексом или None
    """
    ai_agent_dir = Path(project_path) / ".ai-agent"
    
    # Сначала проверяем сжатый
    compressed_path = ai_agent_dir / COMPRESSED_INDEX_FILENAME
    if compressed_path.exists():
        try:
            with compressed_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    
    # Потом обычный
    semantic_path = ai_agent_dir / "semantic_index.json"
    if semantic_path.exists():
        try:
            with semantic_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    
    return None