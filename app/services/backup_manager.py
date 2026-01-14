# app/services/backup_manager.py
"""
Backup Manager для Agent Mode.

Обеспечивает безопасное резервное копирование файлов перед модификацией.
Поддерживает:
- Создание бэкапов с временными метками
- Восстановление из бэкапа
- Автоматическую очистку старых бэкапов
- Историю бэкапов для каждой сессии
"""

from __future__ import annotations

import shutil
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class BackupRecord:
    """Запись о созданном бэкапе"""
    original_path: str          # Исходный путь файла (нормализованный с /)
    backup_path: str            # Путь к бэкапу
    created_at: str             # ISO timestamp
    file_size: int              # Размер файла в байтах
    session_id: str             # ID сессии для группировки
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BackupRecord':
        return cls(**data)


@dataclass
class BackupSession:
    """Сессия бэкапов (группа связанных бэкапов)"""
    session_id: str
    created_at: str
    description: str
    backups: List[BackupRecord] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "description": self.description,
            "backups": [b.to_dict() for b in self.backups],
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BackupSession':
        backups = [BackupRecord.from_dict(b) for b in data.get("backups", [])]
        return cls(
            session_id=data["session_id"],
            created_at=data["created_at"],
            description=data.get("description", ""),
            backups=backups,
        )


# ============================================================================
# MAIN CLASS
# ============================================================================

class BackupManager:
    """
    Менеджер резервных копий для Agent Mode.
    
    Структура хранения:
    .ai-agent/
    └── backups/
        ├── manifest.json          # Манифест всех сессий
        ├── 20240115_143052/        # Сессия (timestamp)
        │   ├── session.json        # Метаданные сессии
        │   ├── app_services_auth.py
        │   └── app_utils_helper.py
        └── 20240115_150030/
            └── ...
    
    Example:
        >>> backup = BackupManager("/path/to/project")
        >>> session = backup.start_session("Fix auth bug")
        >>> backup.create_backup("app/services/auth.py")
        >>> backup.create_backup("app/utils/helper.py")
        >>> # После ошибки:
        >>> backup.restore_session(session.session_id)
    """
    
    BACKUP_DIR_NAME = ".ai-agent/backups"
    MANIFEST_FILE = "manifest.json"
    SESSION_FILE = "session.json"
    DEFAULT_RETENTION_DAYS = 7
    
    def __init__(
        self,
        project_root: str,
        retention_days: int = DEFAULT_RETENTION_DAYS
    ):
        """
        Args:
            project_root: Корень проекта
            retention_days: Сколько дней хранить бэкапы
        """
        self.project_root = Path(project_root).resolve()
        self.backup_root = self.project_root / self.BACKUP_DIR_NAME
        self.retention_days = retention_days
        
        # Текущая активная сессия
        self._current_session: Optional[BackupSession] = None
        
        # Создаём директорию бэкапов
        self.backup_root.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"BackupManager initialized: {self.backup_root}")
    
    # ========================================================================
    # PATH NORMALIZATION (КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ!)
    # ========================================================================
    
    def _normalize_path(self, path: str) -> str:
        """
        Нормализует путь к единому формату с forward slashes.
        
        Это критически важно для Windows, где пути могут быть
        как с \\ так и с /. Все пути в BackupRecord хранятся
        с forward slashes для кросс-платформенной совместимости.
        
        Args:
            path: Путь в любом формате
            
        Returns:
            Путь с forward slashes
        """
        return str(path).replace('\\', '/')
    
    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================
    
    def start_session(self, description: str = "") -> BackupSession:
        """
        Начинает новую сессию бэкапов.
        
        Args:
            description: Описание сессии (e.g., "Fix auth bug")
            
        Returns:
            BackupSession объект
        """
        # Добавляем микросекунды для гарантии уникальности
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_id = timestamp
        
        self._current_session = BackupSession(
            session_id=session_id,
            created_at=datetime.now().isoformat(),
            description=description,
            backups=[],
        )
        
        # Создаём директорию сессии
        session_dir = self.backup_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Started backup session: {session_id} ({description})")
        
        return self._current_session    
    
    def end_session(self) -> Optional[BackupSession]:
        """
        Завершает текущую сессию и сохраняет метаданные.
        
        Returns:
            Завершённая сессия или None
        """
        if not self._current_session:
            return None
        
        session = self._current_session
        
        # Сохраняем метаданные сессии
        session_dir = self.backup_root / session.session_id
        session_file = session_dir / self.SESSION_FILE
        
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session.to_dict(), f, indent=2, ensure_ascii=False)
        
        # Обновляем манифест
        self._update_manifest(session)
        
        logger.info(
            f"Ended backup session: {session.session_id} "
            f"({len(session.backups)} files backed up)"
        )
        
        self._current_session = None
        return session
    
    def get_current_session(self) -> Optional[BackupSession]:
        """Возвращает текущую активную сессию"""
        return self._current_session
    
    # ========================================================================
    # BACKUP OPERATIONS
    # ========================================================================
    
    def create_backup(self, file_path: str) -> Optional[str]:
        """
        Создаёт бэкап файла.
        
        Если нет активной сессии — создаёт автоматически.
        
        Args:
            file_path: Путь к файлу (абсолютный или относительный)
            
        Returns:
            Путь к бэкапу или None если файл не существует
        """
        # Нормализуем путь
        if Path(file_path).is_absolute():
            full_path = Path(file_path)
            try:
                rel_path = full_path.relative_to(self.project_root)
            except ValueError:
                rel_path = Path(file_path)
        else:
            rel_path = Path(file_path)
            full_path = self.project_root / rel_path
        
        # ВАЖНО: нормализуем rel_path к единому формату с forward slashes
        rel_path_str = self._normalize_path(rel_path)
        
        # Проверяем существование файла
        if not full_path.exists():
            logger.debug(f"File does not exist, skipping backup: {file_path}")
            return None
        
        # Создаём сессию если нет активной
        if not self._current_session:
            self.start_session("Auto-created session")
        
        # Формируем имя файла бэкапа (заменяем / на _)
        backup_filename = rel_path_str.replace('/', '_')
        
        session_dir = self.backup_root / self._current_session.session_id
        backup_path = session_dir / backup_filename
        
        # Копируем файл
        try:
            shutil.copy2(full_path, backup_path)
            
            # Создаём запись о бэкапе с НОРМАЛИЗОВАННЫМ путём
            record = BackupRecord(
                original_path=rel_path_str,  # Используем нормализованный путь!
                backup_path=str(backup_path),
                created_at=datetime.now().isoformat(),
                file_size=full_path.stat().st_size,
                session_id=self._current_session.session_id,
            )
            
            self._current_session.backups.append(record)
            
            logger.info(f"Backed up: {rel_path_str} -> {backup_filename}")
            
            return str(backup_path)
            
        except Exception as e:
            logger.error(f"Failed to backup {file_path}: {e}")
            return None
    
    def restore_file(self, original_path: str, session_id: str = None) -> bool:
        """
        Восстанавливает файл из бэкапа.
        
        Args:
            original_path: Исходный путь файла
            session_id: ID сессии (если None — ищет в последней)
            
        Returns:
            True если восстановление успешно
        """
        # Нормализуем путь для поиска
        rel_path = self._normalize_path(original_path)
        
        # Находим бэкап
        backup_record = self._find_backup_record(rel_path, session_id)
        
        if not backup_record:
            logger.error(f"Backup not found for: {rel_path}")
            return False
        
        backup_path = Path(backup_record.backup_path)
        
        if not backup_path.exists():
            logger.error(f"Backup file missing: {backup_path}")
            return False
        
        # Восстанавливаем
        try:
            target_path = self.project_root / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            shutil.copy2(backup_path, target_path)
            
            logger.info(f"Restored: {rel_path} from session {backup_record.session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore {original_path}: {e}")
            return False
    
    def restore_session(self, session_id: str) -> Dict[str, bool]:
        """
        Восстанавливает все файлы из сессии.
        
        Args:
            session_id: ID сессии
            
        Returns:
            Dict {file_path: success} для каждого файла
        """
        session = self._load_session(session_id)
        
        if not session:
            logger.error(f"Session not found: {session_id}")
            return {}
        
        results = {}
        
        for backup in session.backups:
            # original_path уже нормализован при создании
            success = self.restore_file(backup.original_path, session_id)
            results[backup.original_path] = success
        
        restored = sum(1 for v in results.values() if v)
        logger.info(
            f"Restored session {session_id}: "
            f"{restored}/{len(results)} files"
        )
        
        return results
    
    # ========================================================================
    # LISTING & QUERY
    # ========================================================================
    
    def list_sessions(self, limit: int = 10) -> List[BackupSession]:
        """
        Возвращает список сессий (последние первыми).
        
        Args:
            limit: Максимальное количество сессий
            
        Returns:
            Список BackupSession
        """
        manifest = self._load_manifest()
        sessions = []
        
        # Берём последние limit сессий из манифеста
        session_summaries = manifest.get("sessions", [])[-limit:]
        
        for session_summary in session_summaries:
            # Загружаем полную сессию из файла
            session_id = session_summary.get("session_id")
            if session_id:
                full_session = self._load_session(session_id)
                if full_session:
                    sessions.append(full_session)
                else:
                    # Если файл сессии не найден, создаём из summary
                    try:
                        sessions.append(BackupSession(
                            session_id=session_id,
                            created_at=session_summary.get("created_at", ""),
                            description=session_summary.get("description", ""),
                            backups=[],
                        ))
                    except Exception as e:
                        logger.warning(f"Failed to load session {session_id}: {e}")
        
        return list(reversed(sessions))
    
    def get_session(self, session_id: str) -> Optional[BackupSession]:
        """Возвращает сессию по ID"""
        return self._load_session(session_id)
    
    def list_backups_for_file(self, file_path: str) -> List[BackupRecord]:
        """
        Возвращает все бэкапы для конкретного файла.
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            Список BackupRecord (последние первыми)
        """
        # Нормализуем путь для поиска
        rel_path = self._normalize_path(file_path)
        backups = []
        
        for session in self.list_sessions(limit=100):
            for backup in session.backups:
                # Сравниваем нормализованные пути
                if self._normalize_path(backup.original_path) == rel_path:
                    backups.append(backup)
        
        return backups
    
    # ========================================================================
    # CLEANUP
    # ========================================================================
    
    def cleanup_old_backups(self) -> int:
        """
        Удаляет бэкапы старше retention_days.
        
        Returns:
            Количество удалённых сессий
        """
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        deleted_count = 0
        
        manifest = self._load_manifest()
        sessions_to_keep = []
        
        for session_data in manifest.get("sessions", []):
            try:
                created_at = datetime.fromisoformat(session_data["created_at"])
                
                if created_at < cutoff:
                    # Удаляем директорию сессии
                    session_dir = self.backup_root / session_data["session_id"]
                    if session_dir.exists():
                        shutil.rmtree(session_dir)
                        deleted_count += 1
                        logger.info(f"Deleted old backup session: {session_data['session_id']}")
                else:
                    sessions_to_keep.append(session_data)
                    
            except Exception as e:
                logger.warning(f"Error processing session for cleanup: {e}")
                sessions_to_keep.append(session_data)
        
        # Обновляем манифест
        manifest["sessions"] = sessions_to_keep
        self._save_manifest(manifest)
        
        if deleted_count:
            logger.info(f"Cleanup complete: {deleted_count} sessions deleted")
        
        return deleted_count
    
    def get_total_backup_size(self) -> int:
        """Возвращает общий размер всех бэкапов в байтах"""
        total = 0
        
        if self.backup_root.exists():
            for file in self.backup_root.rglob("*"):
                if file.is_file():
                    total += file.stat().st_size
        
        return total
    
    # ========================================================================
    # PRIVATE METHODS
    # ========================================================================
    
    def _find_backup_record(
        self,
        original_path: str,
        session_id: str = None
    ) -> Optional[BackupRecord]:
        """
        Находит запись бэкапа для файла.
        
        Args:
            original_path: Путь к файлу (должен быть уже нормализован)
            session_id: ID сессии для поиска (опционально)
            
        Returns:
            BackupRecord или None
        """
        # Нормализуем путь на всякий случай
        rel_path = self._normalize_path(original_path)
        
        if session_id:
            session = self._load_session(session_id)
            if session:
                for backup in session.backups:
                    # Сравниваем нормализованные пути
                    if self._normalize_path(backup.original_path) == rel_path:
                        return backup
        else:
            # Ищем в последних сессиях
            for session in self.list_sessions(limit=10):
                for backup in session.backups:
                    if self._normalize_path(backup.original_path) == rel_path:
                        return backup
        
        return None
    
    def _load_session(self, session_id: str) -> Optional[BackupSession]:
        """Загружает сессию из файла"""
        session_file = self.backup_root / session_id / self.SESSION_FILE
        
        if not session_file.exists():
            logger.debug(f"Session file not found: {session_file}")
            return None
        
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return BackupSession.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None
    
    def _load_manifest(self) -> Dict[str, Any]:
        """Загружает манифест"""
        manifest_path = self.backup_root / self.MANIFEST_FILE
        
        if not manifest_path.exists():
            return {"sessions": []}
        
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load manifest: {e}")
            return {"sessions": []}
    
    def _save_manifest(self, manifest: Dict[str, Any]):
        """Сохраняет манифест"""
        manifest_path = self.backup_root / self.MANIFEST_FILE
        
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save manifest: {e}")
    
    def _update_manifest(self, session: BackupSession):
        """Добавляет сессию в манифест"""
        manifest = self._load_manifest()
        
        # Проверяем, нет ли уже такой сессии
        existing_ids = [s.get("session_id") for s in manifest.get("sessions", [])]
        if session.session_id in existing_ids:
            logger.debug(f"Session {session.session_id} already in manifest, skipping")
            return
        
        # Добавляем сессию (сокращённая версия)
        session_summary = {
            "session_id": session.session_id,
            "created_at": session.created_at,
            "description": session.description,
            "files_count": len(session.backups),
        }
        
        manifest["sessions"].append(session_summary)
        self._save_manifest(manifest)
    
    def __repr__(self) -> str:
        session_info = f", active_session={self._current_session.session_id}" if self._current_session else ""
        return f"BackupManager(root='{self.backup_root}'{session_info})"