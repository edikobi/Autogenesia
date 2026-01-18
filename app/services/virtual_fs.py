# app/services/virtual_fs.py
"""
Virtual File System Layer для Agent Mode.

Позволяет "применять" изменения к файлам без реальной модификации.
Все проверки (синтаксис, импорты, интеграция) работают с виртуальным
состоянием, а реальные файлы изменяются только после подтверждения.

Ключевые возможности:
- Staging изменений (как git add)
- Чтение файлов с учётом pending changes
- Поиск затронутых файлов (зависимости и зависимые)
- Commit (применение) или discard (отмена)
- Интеграция с HistoryManager для сохранения истории изменений
"""

from __future__ import annotations

import ast
import re
import logging
import asyncio
from pathlib import Path
from typing import Dict, Set, Optional, List, Any, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime

if TYPE_CHECKING:
    from app.services.backup_manager import BackupManager
    from app.history.manager import HistoryManager

from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class ChangeType(Enum):
    """Тип изменения файла"""
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


@dataclass
class PendingChange:
    """
    Представляет ожидающее применения изменение файла.
    
    Attributes:
        file_path: Относительный путь от корня проекта
        original_content: Содержимое файла до изменения (None для новых)
        new_content: Новое содержимое файла
        change_type: Тип изменения (ChangeType enum)
        staged_at: Время добавления в staging
    """
    file_path: str
    original_content: Optional[str]  # None для новых файлов
    new_content: str
    change_type: ChangeType = ChangeType.MODIFY
    staged_at: datetime = field(default_factory=datetime.now)
    
    @property
    def is_new_file(self) -> bool:
        """Файл создаётся впервые"""
        return self.change_type == ChangeType.CREATE or self.original_content is None
    
    @property
    def is_modification(self) -> bool:
        """Это изменение существующего файла"""
        return self.change_type == ChangeType.MODIFY and self.original_content is not None
    
    @property
    def is_deletion(self) -> bool:
        """Это удаление файла"""
        return self.change_type == ChangeType.DELETE
    
    @property
    def has_changes(self) -> bool:
        """Проверяет, есть ли реальные изменения"""
        if self.original_content is None:
            return bool(self.new_content)
        return self.original_content != self.new_content
    
    @property
    def lines_added(self) -> int:
        """Количество добавленных строк"""
        if not self.original_content:
            return len(self.new_content.splitlines()) if self.new_content else 0
        
        old_lines = set(self.original_content.splitlines())
        new_lines = self.new_content.splitlines()
        return sum(1 for line in new_lines if line not in old_lines)
    
    @property
    def lines_removed(self) -> int:
        """Количество удалённых строк"""
        if not self.original_content:
            return 0
        
        old_lines = self.original_content.splitlines()
        new_lines = set(self.new_content.splitlines())
        return sum(1 for line in old_lines if line not in new_lines)


@dataclass
class AffectedFiles:
    """Результат анализа затронутых файлов"""
    changed_files: Set[str] = field(default_factory=set)      # Изменённые существующие
    new_files: Set[str] = field(default_factory=set)          # Созданные новые
    deleted_files: Set[str] = field(default_factory=set)      # Удалённые
    dependent_files: Set[str] = field(default_factory=set)    # Файлы, импортирующие изменённые
    dependency_files: Set[str] = field(default_factory=set)   # Файлы, которые изменённые импортируют
    test_files: Set[str] = field(default_factory=set)         # Связанные тестовые файлы
    
    @property
    def all_modified(self) -> Set[str]:
        """Все файлы с pending changes"""
        return self.changed_files | self.new_files
    
    @property
    def all_affected(self) -> Set[str]:
        """Все затронутые файлы (для валидации)"""
        return self.changed_files | self.new_files | self.dependent_files
    
    @property
    def all_files(self) -> Set[str]:
        """Абсолютно все связанные файлы"""
        return (self.changed_files | self.new_files | self.deleted_files |
                self.dependent_files | self.dependency_files | self.test_files)
    
    def to_dict(self) -> Dict[str, List[str]]:
        return {
            "changed": sorted(self.changed_files),
            "new": sorted(self.new_files),
            "deleted": sorted(self.deleted_files),
            "dependents": sorted(self.dependent_files),
            "dependencies": sorted(self.dependency_files),
            "tests": sorted(self.test_files),
            "all": sorted(self.all_files),
        }


@dataclass
class CommitResult:
    """Результат применения изменений"""
    success: bool
    applied_files: List[str]
    created_files: List[str]
    deleted_files: List[str]
    backups: Dict[str, str]
    errors: List[str]
    change_record_ids: List[str]
    
    @property
    def total_files(self) -> int:
        return len(self.applied_files) + len(self.created_files) + len(self.deleted_files)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "applied": self.applied_files,
            "created": self.created_files,
            "deleted": self.deleted_files,
            "backups": self.backups,
            "errors": self.errors,
            "change_record_ids": self.change_record_ids,
            "total_files": self.total_files,
        }

# ============================================================================
# MAIN CLASS
# ============================================================================

class VirtualFileSystem:
    """
    Виртуальная файловая система с поддержкой pending changes.
    
    Позволяет:
    - Стейджить изменения без модификации реальных файлов
    - Читать файлы с учётом pending изменений
    - Анализировать зависимости (кто кого импортирует)
    - Применять или отменять изменения
    - Сохранять историю изменений в БД
    
    Example:
        >>> vfs = VirtualFileSystem("/path/to/project")
        >>> vfs.stage_change("app/utils/helper.py", new_code)
        >>> content = vfs.read_file("app/utils/helper.py")  # Вернёт new_code
        >>> affected = vfs.get_affected_files()
        >>> result = await vfs.commit_all(backup_manager, history_manager, thread_id)
    """
    
    # Паттерны для игнорирования при сканировании
    IGNORE_PATTERNS = {
        '__pycache__', '.git', '.svn', '.hg',
        'node_modules', 'venv', '.venv', 'env',
        '.ai-agent', '.idea', '.vscode',
        '*.pyc', '*.pyo', '*.egg-info',
    }
    
    def __init__(self, project_root: str):
        """
        Args:
            project_root: Абсолютный путь к корню проекта
        """
        self.project_root = Path(project_root).resolve()
        
        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")
        
        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")
        
        # Pending changes storage
        self._pending_changes: Dict[str, PendingChange] = {}
        
        # Cache for affected files (invalidated on any stage/unstage)
        self._affected_cache: Optional[AffectedFilesResult] = None
        
        # Cache for Python files list
        self._python_files_cache: Optional[List[str]] = None
        
        logger.info(f"VirtualFileSystem initialized: {self.project_root}")
    
    # ========================================================================
    # STAGING OPERATIONS
    # ========================================================================
    
    def stage_change(
        self,
        file_path: str,
        new_content: str,
        change_type: Optional[ChangeType] = None
    ) -> PendingChange:
        """
        Добавляет изменение в staging area.
        
        Args:
            file_path: Путь к файлу (относительный или абсолютный)
            new_content: Новое содержимое файла
            change_type: Тип изменения (None = автоопределение)
        
        Returns:
            PendingChange объект с информацией об изменении
        """
        rel_path = self._normalize_path(file_path)
        full_path = self.project_root / rel_path
        
        # Check if we already have a pending change for this file
        existing_change = self._pending_changes.get(rel_path)
        
        # If file is already staged, overwrite it with the new version
        if existing_change is not None:
            logger.debug(f"Overwriting staged file: {rel_path}")
            del self._pending_changes[rel_path]
        
        # Determine original content
        # IMPORTANT: If we already had a pending change, preserve its original_content
        # This handles the case where a file is created in iteration 1 and 
        # rewritten in iteration 2+ - we want to remember it was originally NEW
        if existing_change is not None:
            original_content = existing_change.original_content
            logger.debug(f"stage_change: {rel_path} already staged, preserving original_content={original_content is None}")
        elif full_path.exists():
            try:
                original_content = full_path.read_text(encoding='utf-8')
            except Exception as e:
                logger.warning(f"Could not read original file {rel_path}: {e}")
                original_content = None
        else:
            original_content = None
        
        # Auto-determine change type
        if change_type is None:
            if original_content is None:
                change_type = ChangeType.CREATE
            elif not new_content.strip():
                change_type = ChangeType.DELETE
            else:
                change_type = ChangeType.MODIFY
        
        # Create PendingChange
        change = PendingChange(
            file_path=rel_path,
            original_content=original_content,
            new_content=new_content,
            change_type=change_type,
        )
        
        self._pending_changes[rel_path] = change
        
        self.invalidate_cache()        
        
        # Invalidate caches
        self._affected_cache = None
        
        # Enhanced logging
        is_new = original_content is None
        logger.info(
            f"Staged {change_type.value}: {rel_path} "
            f"(+{change.lines_added}/-{change.lines_removed} lines, "
            f"{'NEW file' if is_new else 'existing file'})"
        )
        
        return change
    
    
    def stage_deletion(self, file_path: str) -> PendingChange:
        """
        Стейджит удаление файла.
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            PendingChange с change_type=DELETE
        """
        return self.stage_change(file_path, "", change_type=ChangeType.DELETE)

    
    def unstage(self, file_path: str) -> bool:
        """
        Удаляет файл из staging area.
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            True если файл был в staging и удалён
        """
        rel_path = self._normalize_path(file_path)
        
        self.invalidate_cache()
        
        if rel_path in self._pending_changes:
            del self._pending_changes[rel_path]
            self._affected_cache = None
            logger.info(f"Unstaged: {rel_path}")
            return True
        
        return False

    
    def discard_all(self) -> int:
        """
        Отменяет все pending изменения.
        
        Returns:
            Количество отменённых изменений
        """
        self.invalidate_cache()
        count = len(self._pending_changes)
        self._pending_changes.clear()
        self._affected_cache = None
        
        
        logger.info(f"Discarded all changes ({count} files)")
        return count
    
    def discard_modifications_only(self) -> int:
        """
        Discard only MODIFY changes to PRE-EXISTING files.
        Keep all changes to files that didn't exist before this session.
        
        Logic:
        - If original_content is None → file is NEW (created this session) → KEEP
        - If original_content exists AND change_type is MODIFY → DISCARD
        - CREATE and DELETE are always kept
        
        This ensures that files created during the session are preserved
        across iterations, even if they are rewritten via REPLACE_FILE.
        """
        to_remove = []
        to_preserve = []
        
        for path, change in self._pending_changes.items():
            # File is truly NEW if it had no original content (didn't exist on disk)
            is_truly_new_file = change.original_content is None
            
            if is_truly_new_file:
                # Always preserve files that didn't exist before session
                to_preserve.append(path)
            elif change.change_type == ChangeType.MODIFY:
                # Only discard modifications to pre-existing files
                to_remove.append(path)
            else:
                # Keep CREATE and DELETE operations
                to_preserve.append(path)
        
        for path in to_remove:
            del self._pending_changes[path]
        
        self.invalidate_cache()
        
        # Invalidate cache
        self._affected_cache = None
        
        logger.info(
            f"discard_modifications_only: removed {len(to_remove)} modifications to existing files, "
            f"preserved {len(to_preserve)} new/created files: {to_preserve[:5]}{'...' if len(to_preserve) > 5 else ''}"
        )
        
        return len(to_remove)

    
 
    
    def get_staged_files(self) -> List[str]:
        """Возвращает список файлов в staging"""
        return list(self._pending_changes.keys())


    def get_change(self, file_path: str) -> Optional['FileChange']:
        """
        Returns the FileChange object for a staged file, or None if not staged.
        
        Args:
            file_path: Path to the file (will be normalized)
            
        Returns:
            FileChange object or None
        """
        normalized = file_path.replace('\\', '/')
        return self._pending_changes.get(normalized)



    def get_staged_modules(self) -> Set[str]:
        """
        Возвращает множество Python модулей из staged files.
        
        Используется валидатором для проверки импортов.
        Включает как сами модули, так и все родительские пакеты.
        
        Returns:
            Set имён модулей (например: {'app', 'app.services', 'app.services.auth'})
        """
        modules: Set[str] = set()
        
        for file_path, change in self._pending_changes.items():
            if not file_path.endswith('.py'):
                continue
            
            # Пропускаем удалённые файлы
            if change.is_deletion:
                continue
            
            # Конвертируем путь в модуль
            module = self._path_to_module(file_path)
            if module:
                modules.add(module)
                # Добавляем все родительские пакеты
                parts = module.split('.')
                for i in range(1, len(parts)):
                    modules.add('.'.join(parts[:i]))
        
        return modules

    def get_all_available_modules(self) -> Set[str]:
        """
        Возвращает ВСЕ доступные Python модули: из реальной ФС + staged.
        
        Это основной метод для валидатора импортов.
        
        Returns:
            Set имён всех модулей
        """
        modules: Set[str] = set()
        
        # 1. Модули из реальной файловой системы
        for py_file in self.project_root.rglob('*.py'):
            try:
                rel_path = py_file.relative_to(self.project_root)
                
                # Пропускаем системные директории
                parts = rel_path.parts
                if any(p.startswith('.') or p in ('__pycache__', 'venv', '.venv', 'node_modules')
                    for p in parts):
                    continue
                
                module = self._path_to_module(str(rel_path))
                if module:
                    modules.add(module)
                    # Родительские пакеты
                    module_parts = module.split('.')
                    for i in range(1, len(module_parts)):
                        modules.add('.'.join(module_parts[:i]))
                        
            except ValueError:
                continue
        
        # 2. Модули из staged (перезаписывают/дополняют)
        staged = self.get_staged_modules()
        modules.update(staged)
        
        # 3. Убираем удалённые модули
        for file_path, change in self._pending_changes.items():
            if change.is_deletion and file_path.endswith('.py'):
                deleted_module = self._path_to_module(file_path)
                if deleted_module and deleted_module in modules:
                    modules.discard(deleted_module)
        
        return modules


    def get_new_files_content(self) -> Dict[str, str]:
        """
        Returns content of all newly created files (not yet committed).
        
        A file is "new" if it didn't exist before this session (original_content is None).
        This is used to provide full context to Orchestrator when validation fails.
        
        Returns:
            Dict of {file_path: content} for all new files
        """
        new_files_content: Dict[str, str] = {}
        
        for file_path, change in self._pending_changes.items():
            # File is new if it had no original content (didn't exist on disk)
            if change.original_content is None and change.new_content is not None:
                new_files_content[file_path] = change.new_content
        
        return new_files_content




    def get_new_files_with_token_limit(
        self, 
        max_tokens: int = 70000, 
        error_locations: Optional[Dict[str, List[int]]] = None
    ) -> Dict[str, str]:
        """
        Returns content of newly created files (files that didn't exist before session).
        
        A file is considered "new" if original_content is None, regardless of change_type.
        This handles the case where a file is created in iteration 1 and rewritten
        via REPLACE_FILE in iteration 2+ (which would set change_type to MODIFY).
        
        If total tokens exceed max_tokens, shows only error context with surrounding lines.
        
        Args:
            max_tokens: Maximum tokens to return (approximate, using len/4)
            error_locations: Dict of {file_path: [line_numbers]} for error context
            
        Returns:
            Dict of {file_path: content} for new files
        """
        # Collect files that didn't exist before this session
        # Key insight: original_content is None means file was created during session
        new_files = {
            path: change.new_content 
            for path, change in self._pending_changes.items() 
            if change.original_content is None  # This is the key condition!
            and change.new_content is not None
        }

        if not new_files:
            return {}

        # Calculate total tokens (approximate)
        total_tokens = sum(len(content) // 4 for content in new_files.values())
        
        logger.debug(
            f"get_new_files_with_token_limit: {len(new_files)} new files, "
            f"~{total_tokens} tokens (limit: {max_tokens})"
        )

        # If within limit, return full content
        if total_tokens <= max_tokens:
            return new_files

        # Over limit - need to truncate
        result = {}
        
        if error_locations:
            # Show context around errors
            for path, content in new_files.items():
                if path in error_locations and error_locations[path]:
                    # Show lines around each error
                    lines = content.splitlines()
                    relevant_lines: set = set()
                    
                    for err_line in error_locations[path]:
                        # Show 20 lines before and after each error
                        start = max(0, err_line - 21)
                        end = min(len(lines), err_line + 20)
                        relevant_lines.update(range(start, end))
                    
                    if not relevant_lines:
                        # Fallback: show beginning of file
                        result[path] = content[:max_tokens * 4 // max(len(new_files), 1)]
                        continue
                    
                    # Format with line numbers and omission markers
                    sorted_lines = sorted(relevant_lines)
                    formatted_parts = []
                    last_idx = -1
                    
                    for idx in sorted_lines:
                        if idx >= len(lines):
                            continue
                            
                        # Add omission marker if there's a gap
                        if last_idx != -1 and idx > last_idx + 1:
                            formatted_parts.append(f"... [lines {last_idx + 2}-{idx} omitted] ...")
                        elif last_idx == -1 and idx > 0:
                            formatted_parts.append(f"... [lines 1-{idx} omitted] ...")
                        
                        # Add line with line number for context
                        formatted_parts.append(f"{idx + 1:4d} | {lines[idx]}")
                        last_idx = idx
                    
                    # Add trailing omission marker
                    if last_idx < len(lines) - 1:
                        formatted_parts.append(f"... [lines {last_idx + 2}-{len(lines)} omitted] ...")
                    
                    result[path] = "\n".join(formatted_parts)
                else:
                    # File has no errors but we're over limit - show truncated
                    lines_to_show = min(50, len(content.splitlines()))
                    truncated = "\n".join(content.splitlines()[:lines_to_show])
                    if len(content.splitlines()) > lines_to_show:
                        truncated += f"\n... [{len(content.splitlines()) - lines_to_show} more lines, truncated due to token limit] ..."
                    result[path] = truncated
        else:
            # No error locations - truncate proportionally
            tokens_per_file = max_tokens // max(len(new_files), 1)
            chars_per_file = tokens_per_file * 4
            
            for path, content in new_files.items():
                if len(content) <= chars_per_file:
                    result[path] = content
                else:
                    # Show beginning with truncation notice
                    result[path] = content[:chars_per_file] + "\n... [truncated due to token limit] ..."

        return result

    def get_all_staged_content_with_token_limit(
        self, 
        max_tokens: int = 50000, 
        error_locations: Optional[Dict[str, List[int]]] = None
    ) -> Dict[str, str]:
        """
        Returns content of ALL staged files (both new and modified) with token limit.
        
        Unlike get_new_files_with_token_limit which only returns files that didn't exist
        before the session, this returns ALL files with pending changes.
        
        Args:
            max_tokens: Maximum tokens to return (approximate, using len/4)
            error_locations: Dict of {file_path: [line_numbers]} for error context
            
        Returns:
            Dict of {file_path: content} for all staged files
        """
        # Collect all staged files
        staged_files = {
            path: change.new_content 
            for path, change in self._pending_changes.items() 
            if change.new_content is not None
        }

        if not staged_files:
            return {}

        # Calculate total tokens (approximate)
        total_tokens = sum(len(content) // 4 for content in staged_files.values())
        
        logger.debug(
            f"get_all_staged_content_with_token_limit: {len(staged_files)} staged files, "
            f"~{total_tokens} tokens (limit: {max_tokens})"
        )

        # If within limit, return full content
        if total_tokens <= max_tokens:
            return staged_files

        # Over limit - need to truncate
        result = {}
        
        if error_locations:
            # Show context around errors
            for path, content in staged_files.items():
                if path in error_locations and error_locations[path]:
                    # Show lines around each error
                    lines = content.splitlines()
                    relevant_lines: set = set()
                    
                    for err_line in error_locations[path]:
                        # Show 20 lines before and after each error
                        start = max(0, err_line - 21)
                        end = min(len(lines), err_line + 20)
                        relevant_lines.update(range(start, end))
                    
                    if not relevant_lines:
                        # Fallback: show beginning of file
                        result[path] = content[:max_tokens * 4 // max(len(staged_files), 1)]
                        continue
                    
                    # Format with line numbers and omission markers
                    sorted_lines = sorted(relevant_lines)
                    formatted_parts = []
                    last_idx = -1
                    
                    for idx in sorted_lines:
                        if idx >= len(lines):
                            continue
                            
                        # Add omission marker if there's a gap
                        if last_idx != -1 and idx > last_idx + 1:
                            formatted_parts.append(f"... [lines {last_idx + 2}-{idx} omitted] ...")
                        elif last_idx == -1 and idx > 0:
                            formatted_parts.append(f"... [lines 1-{idx} omitted] ...")
                        
                        # Add line with line number for context
                        formatted_parts.append(f"{idx + 1:4d} | {lines[idx]}")
                        last_idx = idx
                    
                    # Add trailing omission marker
                    if last_idx < len(lines) - 1:
                        formatted_parts.append(f"... [lines {last_idx + 2}-{len(lines)} omitted] ...")
                    
                    result[path] = "\n".join(formatted_parts)
                else:
                    # File has no errors but we're over limit - show truncated
                    lines_to_show = min(50, len(content.splitlines()))
                    truncated = "\n".join(content.splitlines()[:lines_to_show])
                    if len(content.splitlines()) > lines_to_show:
                        truncated += f"\n... [{len(content.splitlines()) - lines_to_show} more lines, truncated due to token limit] ..."
                    result[path] = truncated
        else:
            # No error locations - truncate proportionally
            tokens_per_file = max_tokens // max(len(staged_files), 1)
            chars_per_file = tokens_per_file * 4
            
            for path, content in staged_files.items():
                if len(content) <= chars_per_file:
                    result[path] = content
                else:
                    # Show beginning with truncation notice
                    result[path] = content[:chars_per_file] + "\n... [truncated due to token limit] ..."

        return result



    def update_staged_file(self, file_path: str, new_content: str) -> bool:
        """
        Updates content of an already staged file without losing its original_content.
        Returns True if file was already staged, False if it was newly staged.
        """
        self.invalidate_cache()
        rel_path = self._normalize_path(file_path)
        if rel_path in self._pending_changes:
            existing = self._pending_changes[rel_path]
            self._pending_changes[rel_path] = PendingChange(
                file_path=rel_path,
                original_content=existing.original_content,  # Preserve!
                new_content=new_content,
                change_type=existing.change_type,  # Preserve!
            )
            self._affected_cache = None
            logger.info(f"Updated staged file: {rel_path}")
            return True
        else:
            self.stage_change(file_path, new_content)
            return False

    
    
    def get_change(self, file_path: str) -> Optional[PendingChange]:
        """Возвращает pending change для файла"""
        rel_path = self._normalize_path(file_path)
        return self._pending_changes.get(rel_path)

    def has_pending_changes(self) -> bool:
        """Проверяет, есть ли pending изменения"""
        return len(self._pending_changes) > 0    
    # ========================================================================
    # FILE READING (with pending changes overlay)
    # ========================================================================
    
    def read_file_original(self, file_path: str) -> Optional[str]:
        """
        Читает ОРИГИНАЛЬНЫЙ файл (без pending changes).
        Полезно для сравнения изменений.
        
        Args:
            file_path: Относительный путь к файлу
            
        Returns:
            Оригинальное содержимое файла или None
        """
        rel_path = self._normalize_path(file_path)
        full_path = self.project_root / rel_path
        
        if not full_path.exists():
            return None
        
        try:
            return full_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.warning(f"Failed to read original file {file_path}: {e}")
            return None
    
    
    def find_test_files(self, file_path: str) -> List[str]:
        """
        Находит тестовые файлы для данного модуля.
        
        Ищет по паттернам:
        - test_{name}.py
        - {name}_test.py
        - tests/test_{name}.py
        - tests/{name}_test.py
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            Список путей к тестовым файлам
        """
        if not file_path.endswith('.py'):
            return []
        
        module_name = Path(file_path).stem
        file_dir = Path(file_path).parent
        
        patterns = [
            f"test_{module_name}.py",
            f"{module_name}_test.py",
            f"tests/test_{module_name}.py",
            f"tests/{module_name}_test.py",
            f"test/test_{module_name}.py",
            str(file_dir / f"test_{module_name}.py"),
            str(file_dir / "tests" / f"test_{module_name}.py"),
        ]
        
        found = []
        for pattern in patterns:
            pattern = self._normalize_path(pattern)
            if self.file_exists(pattern):
                found.append(pattern)
        
        return found
    
    
    def get_all_python_files(self) -> List[str]:
        """
        Возвращает список всех Python файлов в проекте.
        
        Включает:
        - Файлы на диске
        - Staged файлы (новые)
        
        Исключает:
        - .venv, venv, __pycache__
        - Директории, начинающиеся с точки
        - Staged файлы, помеченные на удаление
        
        Returns:
            Список относительных путей
        """
        if self._python_files_cache is not None:
            return self._python_files_cache
        
        files_set = set()
        
        # 1. Сканируем диск
        for py_file in self.project_root.rglob('*.py'):
            try:
                rel_path = py_file.relative_to(self.project_root)
                
                # Пропускаем системные директории
                parts = rel_path.parts
                if any(p.startswith('.') or p in ('__pycache__', 'venv', '.venv', 'node_modules')
                       for p in parts):
                    continue
                
                files_set.add(self._normalize_path(str(rel_path)))
                
            except ValueError:
                continue
        
        # 2. Применяем staged изменения
        for path, change in self._pending_changes.items():
            if not path.endswith('.py'):
                continue
                
            if change.is_deletion:
                files_set.discard(path)
            else:
                # Add new or modified files (modified are likely already in set, but safe to add)
                files_set.add(path)
        
        self._python_files_cache = sorted(list(files_set))
        return self._python_files_cache



    def get_project_python(self) -> str:
        """
        Возвращает путь к Python интерпретатору проекта.

        Ищет виртуальное окружение в проекте по стандартным именам:
        - venv, .venv, env, .env

        Если найдено — возвращает путь к python внутри venv.
        Если не найдено — возвращает sys.executable (fallback).

        Returns:
            Путь к Python интерпретатору
        """
        import sys
        import os
        from pathlib import Path

        venv_names = ["venv", ".venv", "env", ".env"]
        
        # 1. Поиск в директории проекта
        for name in venv_names:
            venv_dir = self.project_root / name
            if venv_dir.is_dir():
                # Проверка на наличие pyvenv.cfg (признак venv)
                if (venv_dir / "pyvenv.cfg").exists():
                    # Определяем путь к бинарнику в зависимости от ОС
                    if os.name == 'nt':  # Windows
                        python_exe = venv_dir / "Scripts" / "python.exe"
                    else:  # Unix/macOS
                        python_exe = venv_dir / "bin" / "python"
                    
                    if python_exe.exists():
                        return str(python_exe)

        # 2. Проверка переменной окружения VIRTUAL_ENV
        env_venv = os.environ.get("VIRTUAL_ENV")
        if env_venv:
            venv_path = Path(env_venv)
            if os.name == 'nt':
                python_exe = venv_path / "Scripts" / "python.exe"
            else:
                python_exe = venv_path / "bin" / "python"
            
            if python_exe.exists():
                return str(python_exe)

        # 3. Fallback на текущий интерпретатор
        return sys.executable

    def get_project_pip_packages(self) -> Set[str]:
        """
        Возвращает множество pip-пакетов, установленных в проекте.

        Использует Python интерпретатор проекта для получения списка пакетов.
        Это гарантирует, что валидация импортов проверяет пакеты,
        доступные в окружении проекта, а не агента.

        Returns:
            Set нормализованных имён пакетов (lowercase, - → _)
        """
        import subprocess
        import json
        import logging

        packages = set()
        python_path = self.get_project_python()
        
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
                for pkg in pip_list:
                    name = pkg.get('name', '')
                    if name:
                        name_lower = name.lower()
                        packages.add(name_lower)
                        packages.add(name_lower.replace('-', '_'))
                        
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to get project pip packages: {e}")
            
        return packages

    def ensure_project_venv(self) -> bool:
        """Ensures a virtual environment exists in the project. Creates .venv if not found. Returns True if venv exists or was created successfully."""
        import subprocess
        import sys
        import os
        
        venv_names = ["venv", ".venv", "env", ".env"]
        
        # 1. Check for existing venv
        for name in venv_names:
            venv_dir = self.project_root / name
            if venv_dir.is_dir() and (venv_dir / "pyvenv.cfg").exists():
                logger.info(f"Found existing venv at {venv_dir}")
                return True
        
        # 2. Create .venv if not found
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

    def materialize_to_directory(self, target_dir: str) -> List[str]:
        """
        Материализует VFS в целевую директорию.
        
        Копирует все файлы проекта в target_dir, включая staged изменения.
        Staged файлы перезаписывают файлы из project_root.
        
        Args:
            target_dir: Целевая директория для материализации
            
        Returns:
            Список записанных файлов
        """
        import subprocess
        import site
        
        target_path = Path(target_dir)
        target_path.mkdir(parents=True, exist_ok=True)
        
        written_files = []
        
        # 1. Copy project_root, excluding staged files
        staged_files = set(self.get_staged_files())
        
        for item in self.project_root.iterdir():
            if item.name.startswith('.') or item.name in ('__pycache__', 'venv', '.venv'):
                continue
            
            dest = target_path / item.name
            
            if item.is_dir():
                def ignore_staged(directory, files):
                    ignored = set()
                    for f in files:
                        full_path = Path(directory) / f
                        try:
                            rel_path = str(full_path.relative_to(self.project_root)).replace('\\', '/')
                        except ValueError:
                            continue
                        
                        if rel_path in staged_files:
                            ignored.add(f)
                        elif f in ('__pycache__', '.git', '.venv', 'venv') or f.endswith('.pyc'):
                            ignored.add(f)
                    
                    return ignored
                
                shutil.copytree(item, dest, ignore=ignore_staged)
            else:
                rel_path = item.name
                if rel_path not in staged_files:
                    shutil.copy2(item, dest)
        
        # 2. Write all staged files
        for file_path in staged_files:
            content = self.read_file(file_path)
            
            if content is None:
                change = self.get_change(file_path)
                if change and change.is_deletion:
                    continue
                else:
                    logger.warning(f"Staged file has no content: {file_path}")
                    continue
            
            dest_path = target_path / file_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(content, encoding='utf-8')
            written_files.append(file_path)
        
        # 3. Create .pth file to ensure proper import resolution in the temp directory
        # This helps Python find modules when testing nested package structures
        try:
            # Get site-packages directory for the target Python environment
            python_executable = self.get_project_python()
            pth_filename = f"ai_assistant_temp_{os.getpid()}.pth"
            
            # Try to determine site-packages location
            site_packages_cmd = f"""
import sys
import site
if hasattr(sys, 'real_prefix'):
    # Virtual environment
    print(site.getsitepackages()[0])
else:
    # System Python
    print(site.getusersitepackages())
"""
            
            result = subprocess.run(
                [python_executable, "-c", site_packages_cmd],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            if result.returncode == 0:
                site_packages_dir = result.stdout.strip()
                if os.path.exists(site_packages_dir):
                    pth_path = os.path.join(site_packages_dir, pth_filename)
                    with open(pth_path, 'w', encoding='utf-8') as f:
                        f.write(target_dir + '\n')
                    logger.debug(f"Created .pth file at {pth_path} pointing to {target_dir}")
                    
                    # Schedule cleanup (remove .pth file after testing)
                    import atexit
                    def cleanup_pth():
                        try:
                            if os.path.exists(pth_path):
                                os.remove(pth_path)
                                logger.debug(f"Cleaned up .pth file: {pth_path}")
                        except Exception:
                            pass
                    atexit.register(cleanup_pth)
                    
        except Exception as e:
            logger.debug(f"Could not create .pth file for import resolution: {e}")
        
        return written_files

    
    def read_file(self, file_path: str) -> Optional[str]:
        """
        Читает содержимое файла с учётом pending changes.
        
        Если файл есть в staging — возвращает новое содержимое.
        Иначе читает реальный файл.
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            Содержимое файла или None если не существует
        """
        rel_path = self._normalize_path(file_path)
        
        # Сначала проверяем pending changes
        if rel_path in self._pending_changes:
            change = self._pending_changes[rel_path]
            if change.is_deletion:
                return None
            return change.new_content
        
        # Читаем реальный файл
        full_path = self.project_root / rel_path
        
        if not full_path.exists():
            return None
        
        try:
            return full_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.warning(f"Failed to read {rel_path}: {e}")
            return None    
    
    def read_file_safe(self, file_path: str, default: str = "") -> str:
        """
        Безопасно читает файл, возвращая default при ошибке.
        
        Args:
            file_path: Путь к файлу
            default: Значение по умолчанию
            
        Returns:
            Содержимое файла или default
        """
        content = self.read_file(file_path)
        if content is None:
            return default
        return content

    
    def file_exists(self, file_path: str) -> bool:
        """
        Проверяет существование файла (с учётом pending changes).
        
        Порядок проверки:
        1. Если файл в staging и НЕ удалён → True
        2. Если файл в staging и удалён → False
        3. Проверяем реальную ФС
        """
        rel_path = self._normalize_path(file_path)
        
        # Проверяем staging
        if rel_path in self._pending_changes:
            change = self._pending_changes[rel_path]
            exists = not change.is_deletion
            logger.debug(f"file_exists({rel_path}): found in staging, exists={exists}")
            return exists
        
        # Проверяем реальную ФС
        full_path = self.project_root / rel_path
        exists = full_path.exists()
        
        if not exists:
            # Дополнительная проверка: может путь был с другим разделителем
            alt_path = self.project_root / rel_path.replace('/', '\\')
            if alt_path.exists():
                exists = True
        
        return exists


    def file_exists_in_staged(self, file_path: str) -> bool:
        """
        Проверяет, существует ли файл в staged changes (без проверки реальной ФС).
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            True если файл есть в staging и не помечен на удаление
        """
        rel_path = self._normalize_path(file_path)
        
        if rel_path in self._pending_changes:
            change = self._pending_changes[rel_path]
            return not change.is_deletion
        
        return False
    
    # ========================================================================
    # DEPENDENCY ANALYSIS
    # ========================================================================
    
    def get_affected_files(self) -> AffectedFiles:
        """
        Возвращает все файлы, затронутые изменениями.
        
        Включает:
        - changed_files: изменённые существующие файлы
        - new_files: созданные новые файлы
        - deleted_files: удалённые файлы
        - dependent_files: файлы, которые импортируют изменённые
        - dependency_files: файлы, которые изменённые импортируют
        - test_files: связанные тестовые файлы
        
        Returns:
            AffectedFiles с категоризированными файлами
        """
        if self._affected_cache is not None:
            return self._affected_cache
        
        result = AffectedFiles()
        
        # Категоризируем pending changes
        for file_path, change in self._pending_changes.items():
            if change.change_type == ChangeType.CREATE:
                result.new_files.add(file_path)
            elif change.change_type == ChangeType.MODIFY:
                result.changed_files.add(file_path)
            elif change.change_type == ChangeType.DELETE:
                result.deleted_files.add(file_path)
        
        # Ищем зависимости для всех изменённых файлов
        all_modified = result.changed_files | result.new_files | result.deleted_files
        
        for changed_file in all_modified:
            if not changed_file.endswith('.py'):
                continue
            
            # Файлы, которые импортируют этот файл
            dependents = self._find_dependents(changed_file)
            result.dependent_files.update(dependents)
            
            # Файлы, которые этот файл импортирует
            dependencies = self._find_dependencies(changed_file)
            result.dependency_files.update(dependencies)
            
            # Связанные тестовые файлы
            tests = self.find_test_files(changed_file)
            result.test_files.update(tests)
        
        # Убираем сами изменённые файлы из зависимостей
        result.dependent_files -= all_modified
        result.dependency_files -= all_modified
        
        self._affected_cache = result
        
        return result    
    
    def _find_dependents(self, file_path: str) -> Set[str]:
        """
        Находит файлы, которые импортируют данный файл.
        
        Args:
            file_path: Относительный путь к файлу
            
        Returns:
            Множество путей к зависимым файлам
        """
        dependents: Set[str] = set()
        module_name = self._path_to_module(file_path)
        
        if not module_name:
            return dependents
        
        # Сканируем все Python файлы проекта
        for py_file in self._iter_python_files():
            if py_file == file_path:
                continue
            
            try:
                content = self.read_file(py_file)
                
                if self._imports_module(content, module_name):
                    dependents.add(py_file)
            except Exception as e:
                logger.debug(f"Could not analyze {py_file}: {e}")
                continue
        
        return dependents
    
    def _find_dependencies(self, file_path: str) -> Set[str]:
        """
        Находит файлы, которые данный файл импортирует.
        
        Args:
            file_path: Относительный путь к файлу
            
        Returns:
            Множество путей к файлам-зависимостям
        """
        dependencies: Set[str] = set()
        
        try:
            content = self.read_file(file_path)
        except FileNotFoundError:
            return dependencies
        
        # Парсим AST для извлечения импортов
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Если синтаксис неверный — пробуем regex fallback
            return self._find_dependencies_regex(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dep_path = self._module_to_path(alias.name)
                    if dep_path:
                        dependencies.add(dep_path)
            
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    dep_path = self._module_to_path(node.module)
                    if dep_path:
                        dependencies.add(dep_path)
        
        return dependencies
    
    def _find_dependencies_regex(self, content: str) -> Set[str]:
        """Fallback для поиска зависимостей через regex"""
        dependencies: Set[str] = set()
        
        # import X, import X.Y
        for match in re.finditer(r'^import\s+([\w.]+)', content, re.MULTILINE):
            dep_path = self._module_to_path(match.group(1))
            if dep_path:
                dependencies.add(dep_path)
        
        # from X import Y
        for match in re.finditer(r'^from\s+([\w.]+)\s+import', content, re.MULTILINE):
            dep_path = self._module_to_path(match.group(1))
            if dep_path:
                dependencies.add(dep_path)
        
        return dependencies
    
    def _imports_module(self, content: str, module_name: str) -> bool:
        """
        Проверяет, импортирует ли код данный модуль.
        
        Args:
            content: Содержимое Python файла
            module_name: Имя модуля (e.g., "app.services.auth")
            
        Returns:
            True если модуль импортируется
        """
        escaped = re.escape(module_name)
        parts = module_name.split('.')
        
        patterns = [
            # import app.services.auth
            rf'^\s*import\s+{escaped}(?:\s|,|$)',
            # from app.services.auth import X
            rf'^\s*from\s+{escaped}\s+import\s+',
        ]
        
        # from app.services import auth
        if len(parts) > 1:
            parent = re.escape(".".join(parts[:-1]))
            child = re.escape(parts[-1])
            patterns.append(rf'^\s*from\s+{parent}\s+import\s+.*\b{child}\b')
        
        for pattern in patterns:
            if re.search(pattern, content, re.MULTILINE):
                return True
        
        return False
    
    # ========================================================================
    # COMMIT / APPLY
    # ========================================================================
    
    async def commit_all(
        self,
        backup_manager: Optional['BackupManager'] = None,
        history_manager: Optional['HistoryManager'] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> CommitResult:
        """
        Применяет все pending изменения к реальным файлам.
        """
        result = CommitResult(
            success=True,
            applied_files=[],
            created_files=[],
            deleted_files=[],
            backups={},
            errors=[],
            change_record_ids=[],
        )
        
        if not self._pending_changes:
            logger.warning("No pending changes to commit")
            return result
        
        if not session_id:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if backup_manager:
            backup_manager.start_session(f"Agent Mode commit: {len(self._pending_changes)} files")
        
        for rel_path, change in list(self._pending_changes.items()):
            full_path = self.project_root / rel_path
            backup_path = None
            
            try:
                # Создаём бэкап если файл существует
                if backup_manager and full_path.exists():
                    backup_path = backup_manager.create_backup(str(full_path))
                    if backup_path:
                        result.backups[rel_path] = backup_path
                
                # Применяем изменение
                if change.is_deletion:
                    if full_path.exists():
                        full_path.unlink()
                        result.deleted_files.append(rel_path)
                        logger.info(f"Deleted: {rel_path}")
                else:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change.new_content, encoding='utf-8')
                    
                    if change.is_new_file:
                        result.created_files.append(rel_path)
                        logger.info(f"Created: {rel_path}")
                    else:
                        result.applied_files.append(rel_path)
                        logger.info(f"Applied: {rel_path}")
                
                # Записываем в историю
                if history_manager and thread_id:
                    try:
                        change_record = await history_manager.record_file_change(
                            thread_id=thread_id,
                            session_id=session_id,
                            file_path=rel_path,
                            change_type=change.change_type.value,
                            original_content=change.original_content or "",
                            new_content=change.new_content,
                            backup_path=backup_path,
                            lines_added=change.lines_added,
                            lines_removed=change.lines_removed,
                            validation_passed=True
                        )
                        result.change_record_ids.append(change_record.id)
                    except Exception as e:
                        logger.error(f"Failed to record change history for {rel_path}: {e}")
                        
            except Exception as e:
                error_msg = f"Failed to apply {rel_path}: {e}"
                result.errors.append(error_msg)
                result.success = False
                logger.error(error_msg)
        
        if backup_manager:
            backup_manager.end_session()
        
        if history_manager and result.change_record_ids:
            await history_manager.mark_changes_applied(result.change_record_ids)
        
        self.invalidate_cache()
        self._pending_changes.clear()
        self._affected_cache = None
        
        logger.info(
            f"Commit complete: {len(result.applied_files)} modified, "
            f"{len(result.created_files)} created, {len(result.deleted_files)} deleted, "
            f"{len(result.errors)} errors"
        )
        
        return result

    
    def commit_all_sync(
        self,
        backup_manager: Optional['BackupManager'] = None
    ) -> CommitResult:
        """
        Синхронная версия commit_all (без записи в историю).
        
        Используйте когда HistoryManager недоступен или не нужен.
        
        Args:
            backup_manager: BackupManager для создания бэкапов
            
        Returns:
            CommitResult
        """
        result = CommitResult(
            success=True,
            applied_files=[],
            created_files=[],
            deleted_files=[],
            backups={},
            errors=[],
            change_record_ids=[],
        )
        
        if not self._pending_changes:
            return result
        
        if backup_manager:
            backup_manager.start_session(f"Sync commit: {len(self._pending_changes)} files")
        
        for rel_path, change in list(self._pending_changes.items()):
            full_path = self.project_root / rel_path
            
            try:
                # Бэкап
                if backup_manager and full_path.exists():
                    backup_path = backup_manager.create_backup(str(full_path))
                    if backup_path:
                        result.backups[rel_path] = backup_path
                
                # Применяем
                if change.is_deletion:
                    if full_path.exists():
                        full_path.unlink()
                        result.deleted_files.append(rel_path)
                else:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change.new_content, encoding='utf-8')
                    
                    if change.is_new_file:
                        result.created_files.append(rel_path)
                    else:
                        result.applied_files.append(rel_path)
                        
            except Exception as e:
                result.errors.append(f"Failed to apply {rel_path}: {e}")
                result.success = False
        
        if backup_manager:
            backup_manager.end_session()
        
        self.invalidate_cache()

        self._pending_changes.clear()
        self._affected_cache = None
        
        return result
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def _normalize_path(self, file_path: str) -> str:
        """
        Нормализует путь к относительному от корня проекта.
        
        Args:
            file_path: Абсолютный или относительный путь
            
        Returns:
            Относительный путь с forward slashes
        """
        path = Path(file_path)
        
        if path.is_absolute():
            try:
                path = path.relative_to(self.project_root)
            except ValueError:
                # Путь не внутри проекта
                pass
        
        return str(path).replace('\\', '/')
    
    
    def _path_to_module(self, file_path: str) -> Optional[str]:
        """
        Конвертирует путь файла в имя модуля Python.
        
        Args:
            file_path: Путь вида "app/services/auth.py" или "app/services/auth/__init__.py"
            
        Returns:
            Имя модуля вида "app.services.auth" или None
        """
        if not file_path.endswith('.py'):
            return None
        
        # Normalize path separators to forward slashes first
        normalized = file_path.replace('\\', '/')
        
        # Remove .py extension
        module = normalized[:-3]
        
        # Handle __init__.py files (convert to package name)
        if module.endswith('/__init__'):
            module = module[:-9]  # Remove /__init__
        elif module.endswith('__init__'):
            module = module[:-8]  # Remove __init__ (for cases without trailing slash)
        
        # Convert path separators to dots
        module = module.replace('/', '.')
        
        # Clean up leading/trailing dots
        module = module.strip('.')
        
        return module if module else None
    
    
    
    def _module_to_path(self, module_name: str) -> Optional[str]:
        """
        Конвертирует имя модуля в путь к файлу.
        
        Args:
            module_name: Имя модуля вида "app.services.auth"
            
        Returns:
            Путь вида "app/services/auth.py" или None
        """
        # Пробуем как файл
        path = module_name.replace('.', '/') + '.py'
        if self.file_exists(path):
            return path
        
        # Пробуем как пакет
        package_path = module_name.replace('.', '/') + '/__init__.py'
        if self.file_exists(package_path):
            return package_path
        
        return None
    
    def _iter_python_files(self) -> List[str]:
        """
        Итерирует по всем Python файлам проекта.
        
        Alias for get_all_python_files() to ensure consistency with staged changes.
        
        Returns:
            Список относительных путей к .py файлам
        """
        return self.get_all_python_files()
    
    
    
    def invalidate_cache(self):
        """Принудительно инвалидирует все кэши"""
        self._affected_cache = None
        self._python_files_cache = None
    
    # ========================================================================
    # DIFF / INSPECTION
    # ========================================================================
    
    def get_diff(self, file_path: str) -> Optional[str]:
        """Генерирует unified diff для файла."""
        import difflib
        
        rel_path = self._normalize_path(file_path)
        change = self._pending_changes.get(rel_path)
        
        if not change:
            return None
        
        old_lines = (change.original_content or "").splitlines(keepends=True)
        new_lines = change.new_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
        
        return ''.join(diff)

    def get_all_diffs(self) -> Dict[str, str]:
        """Возвращает diff для всех файлов в staging"""
        diffs = {}
        for file_path in self._pending_changes:
            diff = self.get_diff(file_path)
            if diff:
                diffs[file_path] = diff
        return diffs

    def get_status(self) -> Dict[str, Any]:
        """Возвращает статус VFS."""
        affected = self.get_affected_files()
        
        total_added = sum(c.lines_added for c in self._pending_changes.values())
        total_removed = sum(c.lines_removed for c in self._pending_changes.values())
        
        return {
            "has_changes": self.has_pending_changes(),
            "staged_count": len(self._pending_changes),
            "total_lines_added": total_added,
            "total_lines_removed": total_removed,
            "new_files": list(affected.new_files),
            "modified_files": list(affected.changed_files),
            "deleted_files": list(affected.deleted_files),
            "staged_files": [
                {
                    "path": rel_path,
                    "type": change.change_type.value,
                    "is_new": change.is_new_file,
                    "lines_added": change.lines_added,
                    "lines_removed": change.lines_removed,
                }
                for rel_path, change in self._pending_changes.items()
            ],
            "affected_files": affected.to_dict(),
        }

    
    def format_status_message(self) -> str:
        """Форматирует статус для отображения пользователю"""
        status = self.get_status()
        
        if not status["has_changes"]:
            return "📭 Нет изменений в staging"
        
        lines = [
            f"📋 **Staging Area** ({status['staged_count']} файлов)",
            f"   Добавлено: +{status['total_lines_added']} строк",
            f"   Удалено: -{status['total_lines_removed']} строк",
            "",
        ]
        
        type_emoji = {"create": "🆕", "modify": "📝", "delete": "🗑️"}
        
        for file_info in status["staged_files"]:
            emoji = type_emoji.get(file_info["type"], "📄")
            stats = f"+{file_info['lines_added']}/-{file_info['lines_removed']}"
            lines.append(f"   {emoji} `{file_info['path']}` {stats}")
        
        affected = status["affected_files"]
        if affected["dependents"]:
            lines.append("")
            lines.append(f"⚠️ **Затронутые файлы** ({len(affected['dependents'])})")
            for dep in affected["dependents"][:5]:
                lines.append(f"   • `{dep}`")
            if len(affected["dependents"]) > 5:
                lines.append(f"   • ... и ещё {len(affected['dependents']) - 5}")
        
        return "\n".join(lines)
    
    def __repr__(self) -> str:
        return (
            f"VirtualFileSystem(root='{self.project_root}', "
            f"pending={len(self._pending_changes)})"
        )