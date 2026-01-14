import sqlite3
import json
import uuid
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict


@dataclass
class Thread:
    """Модель диалога (ветки разговора)."""
    id: Optional[str] = None
    user_id: str = ""
    project_path: Optional[str] = None
    project_name: str = ""
    is_archived: bool = False
    title: str = "Новый диалог"
    message_count: int = 0
    total_tokens: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Message:
    """Модель сообщения в диалоге."""
    id: Optional[str] = None
    thread_id: str = ""
    role: str = "user"  # 'user', 'assistant', 'tool', 'system'
    content: str = ""
    tokens: int = 0
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None


@dataclass
class AgentChange:
    """Модель записи об изменении файла в Agent Mode."""
    id: Optional[str] = None
    thread_id: str = ""
    message_id: Optional[str] = None      # Связь с сообщением (опционально)
    session_id: str = ""                   # ID сессии бэкапов
    file_path: str = ""                    # Относительный путь к файлу
    change_type: str = "modify"            # create, modify, delete
    original_content: Optional[str] = None # Оригинальное содержимое (для отката)
    new_content: Optional[str] = None      # Новое содержимое
    backup_path: Optional[str] = None      # Путь к бэкапу
    lines_added: int = 0
    lines_removed: int = 0
    validation_passed: bool = True
    applied: bool = False                  # Было ли применено
    rolled_back: bool = False              # Был ли откат
    user_confirmed: bool = False           # Подтвердил ли пользователь
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None


class HistoryStorage:
    """Класс для хранения истории диалогов и изменений файлов в SQLite."""

    def __init__(self, db_path: str = "history.db"):
        """
        Инициализация хранилища.

        Args:
            db_path: Путь к файлу базы данных SQLite.
        """
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Создает и возвращает соединение с базой данных."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Инициализирует таблицы базы данных, если они не существуют."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Таблица диалогов (threads)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_path TEXT,
                    project_name TEXT DEFAULT '',
                    is_archived INTEGER DEFAULT 0,
                    title TEXT NOT NULL DEFAULT 'Новый диалог',
                    message_count INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица сообщений (messages)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
                    content TEXT NOT NULL,
                    tokens INTEGER DEFAULT 0,
                    metadata TEXT,  -- JSON строка
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE
                )
            """)

            # Таблица изменений файлов (agent_changes)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_changes (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    message_id TEXT,
                    session_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    change_type TEXT NOT NULL CHECK(change_type IN ('create', 'modify', 'delete')),
                    original_content TEXT,
                    new_content TEXT,
                    backup_path TEXT,
                    lines_added INTEGER DEFAULT 0,
                    lines_removed INTEGER DEFAULT 0,
                    validation_passed INTEGER DEFAULT 1,
                    applied INTEGER DEFAULT 0,
                    rolled_back INTEGER DEFAULT 0,
                    user_confirmed INTEGER DEFAULT 0,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE,
                    FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE SET NULL
                )
            """)

            # Индексы для ускорения запросов
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_id, created_at ASC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_threads_user ON threads (user_id, updated_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_changes_thread ON agent_changes (thread_id, created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_changes_session ON agent_changes (session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_changes_file ON agent_changes (file_path)")

            conn.commit()

    # ===== CRUD операции для Threads =====

    def create_thread(
        self,
        user_id: str,
        project_path: Optional[str] = None,
        title: str = "Новый диалог"
    ) -> Thread:
        """
        Создает новый диалог.

        Args:
            user_id: ID пользователя.
            project_path: Путь к проекту (опционально).
            title: Заголовок диалога.

        Returns:
            Объект Thread с заполненным id и временными метками.
        """
        thread_id = f"thread-{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        project_name = os.path.basename(project_path) if project_path else ""

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO threads (id, user_id, project_path, project_name, is_archived, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, user_id, project_path, project_name, 0, title, now, now)
            )

            # Получаем созданную запись
            cursor.execute("SELECT * FROM threads WHERE id = ?", (thread_id,))
            row = cursor.fetchone()
            conn.commit()

            return Thread(
                id=row["id"],
                user_id=row["user_id"],
                project_path=row["project_path"],
                project_name=row["project_name"],
                is_archived=bool(row["is_archived"]),
                title=row["title"],
                message_count=row["message_count"],
                total_tokens=row["total_tokens"],
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )

    def get_thread(self, thread_id: str) -> Optional[Thread]:
        """
        Получает диалог по ID.

        Args:
            thread_id: ID диалога.

        Returns:
            Объект Thread или None, если не найден.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM threads WHERE id = ?", (thread_id,))
            row = cursor.fetchone()

            if row is None:
                return None

            return Thread(
                id=row["id"],
                user_id=row["user_id"],
                project_path=row["project_path"],
                project_name=row["project_name"],
                is_archived=bool(row["is_archived"]),
                title=row["title"],
                message_count=row["message_count"],
                total_tokens=row["total_tokens"],
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )

    def list_threads(self, user_id: str) -> List[Thread]:
        """
        Получает все диалоги пользователя, отсортированные по дате обновления (сначала новые).

        Args:
            user_id: ID пользователя.

        Returns:
            Список объектов Thread.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM threads WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
            rows = cursor.fetchall()

            return [
                Thread(
                    id=row["id"],
                    user_id=row["user_id"],
                    project_path=row["project_path"],
                    project_name=row["project_name"],
                    is_archived=bool(row["is_archived"]),
                    title=row["title"],
                    message_count=row["message_count"],
                    total_tokens=row["total_tokens"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"]
                )
                for row in rows
            ]

    def update_thread(
        self,
        thread_id: str,
        title: Optional[str] = None,
        is_archived: Optional[bool] = None
    ) -> bool:
        """
        Обновляет заголовок диалога и/или статус архивации.

        Args:
            thread_id: ID диалога.
            title: Новый заголовок (опционально).
            is_archived: Статус архивации (опционально).

        Returns:
            True, если обновление прошло успешно, False если диалог не найден.
        """
        updates = []
        params = []
        now = datetime.now().isoformat()

        if title is not None:
            updates.append("title = ?")
            params.append(title)

        if is_archived is not None:
            updates.append("is_archived = ?")
            params.append(int(is_archived))

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(now)

        params.append(thread_id)
        query = f"UPDATE threads SET {', '.join(updates)} WHERE id = ?"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0


    def update_thread_title(self, thread_id: str, new_title: str) -> bool:
        """
        Обновляет только название диалога.

        Args:
            thread_id: ID диалога.
            new_title: Новое название.

        Returns:
            True, если обновление прошло успешно, False если диалог не найден.
        """
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE threads 
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_title, now, thread_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_thread(self, thread_id: str) -> bool:
        """
        Удаляет диалог и все связанные с ним сообщения (каскадное удаление).

        Args:
            thread_id: ID диалога.

        Returns:
            True, если удаление прошло успешно, False если диалог не найден.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clear_thread_messages(self, thread_id: str) -> bool:
        """
        Удаляет все сообщения из диалога.

        Args:
            thread_id: ID диалога.

        Returns:
            True, если операция прошла успешно.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            conn.commit()
            return True

    # ===== CRUD операции для Messages =====

    def add_message(self, thread_id: str, role: str, content: str, tokens: int, metadata: Optional[Dict] = None) -> Message:
        """
        Добавляет сообщение в диалог.

        Args:
            thread_id: ID диалога.
            role: Роль отправителя ('user', 'assistant', 'tool', 'system').
            content: Текст сообщения.
            tokens: Количество токенов в сообщении.
            metadata: Дополнительные метаданные в виде словаря.

        Returns:
            Объект Message с заполненным id и временной меткой.
        """
        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        metadata_json = json.dumps(metadata) if metadata else None

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Добавляем сообщение
            cursor.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, tokens, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, thread_id, role, content, tokens, metadata_json)
            )

            # Атомарно обновляем счетчики в диалоге
            cursor.execute(
                """
                UPDATE threads
                SET message_count = message_count + 1,
                    total_tokens = total_tokens + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (tokens, thread_id)
            )

            # Получаем созданное сообщение
            cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            conn.commit()

            # Парсим метаданные обратно в словарь
            meta = json.loads(row["metadata"]) if row["metadata"] else None

            return Message(
                id=row["id"],
                thread_id=row["thread_id"],
                role=row["role"],
                content=row["content"],
                tokens=row["tokens"],
                metadata=meta,
                created_at=row["created_at"]
            )

    def get_messages(self, thread_id: str, limit: Optional[int] = None) -> List[Message]:
        """
        Получает сообщения диалога, отсортированные по времени создания (сначала старые).

        Args:
            thread_id: ID диалога.
            limit: Ограничение количества сообщений (необязательно).

        Returns:
            Список объектов Message.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at ASC"
            params = (thread_id,)

            if limit:
                query += " LIMIT ?"
                params = (thread_id, limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            messages = []
            for row in rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else None
                messages.append(Message(
                    id=row["id"],
                    thread_id=row["thread_id"],
                    role=row["role"],
                    content=row["content"],
                    tokens=row["tokens"],
                    metadata=meta,
                    created_at=row["created_at"]
                ))

            return messages

    def delete_message(self, message_id: str) -> bool:
        """
        Удаляет сообщение по ID.

        Args:
            message_id: ID сообщения.

        Returns:
            True, если удаление прошло успешно, False если сообщение не найдено.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ===== Вспомогательные методы =====

    def get_thread_with_messages(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """
        Получает диалог вместе со всеми его сообщениями.

        Args:
            thread_id: ID диалога.

        Returns:
            Словарь с диалогом и списком сообщений или None, если диалог не найден.
        """
        thread = self.get_thread(thread_id)
        if thread is None:
            return None

        messages = self.get_messages(thread_id)
        return {
            "thread": asdict(thread),
            "messages": [asdict(msg) for msg in messages]
        }

    # ===== CRUD операции для AgentChanges =====

    def add_agent_change(
        self,
        thread_id: str,
        session_id: str,
        file_path: str,
        change_type: str,
        original_content: Optional[str] = None,
        new_content: Optional[str] = None,
        backup_path: Optional[str] = None,
        lines_added: int = 0,
        lines_removed: int = 0,
        validation_passed: bool = True,
        message_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> AgentChange:
        """
        Записывает информацию об изменении файла.

        Args:
            thread_id: ID диалога
            session_id: ID сессии бэкапов
            file_path: Путь к изменённому файлу
            change_type: Тип изменения ('create', 'modify', 'delete')
            original_content: Оригинальное содержимое (для возможности отката)
            new_content: Новое содержимое
            backup_path: Путь к файлу бэкапа
            lines_added: Количество добавленных строк
            lines_removed: Количество удалённых строк
            validation_passed: Прошла ли валидация
            message_id: ID связанного сообщения (опционально)
            metadata: Дополнительные метаданные

        Returns:
            Объект AgentChange
        """
        change_id = f"change-{uuid.uuid4().hex[:12]}"
        metadata_json = json.dumps(metadata) if metadata else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_changes (
                    id, thread_id, message_id, session_id, file_path, change_type,
                    original_content, new_content, backup_path,
                    lines_added, lines_removed, validation_passed, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change_id, thread_id, message_id, session_id, file_path, change_type,
                    original_content, new_content, backup_path,
                    lines_added, lines_removed, int(validation_passed), metadata_json
                )
            )
            conn.commit()

            return AgentChange(
                id=change_id,
                thread_id=thread_id,
                message_id=message_id,
                session_id=session_id,
                file_path=file_path,
                change_type=change_type,
                original_content=original_content,
                new_content=new_content,
                backup_path=backup_path,
                lines_added=lines_added,
                lines_removed=lines_removed,
                validation_passed=validation_passed,
                metadata=metadata,
                created_at=datetime.now().isoformat()
            )

    def get_thread_changes(
        self,
        thread_id: str,
        only_applied: bool = False,
        limit: Optional[int] = None
    ) -> List[AgentChange]:
        """
        Получает историю изменений для диалога.

        Args:
            thread_id: ID диалога
            only_applied: Если True, возвращает только применённые изменения
            limit: Ограничение количества записей

        Returns:
            Список AgentChange, отсортированный по времени (новые первыми)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM agent_changes WHERE thread_id = ?"
            params = [thread_id]

            if only_applied:
                query += " AND applied = 1"

            query += " ORDER BY created_at DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            changes = []
            for row in rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else None
                changes.append(AgentChange(
                    id=row["id"],
                    thread_id=row["thread_id"],
                    message_id=row["message_id"],
                    session_id=row["session_id"],
                    file_path=row["file_path"],
                    change_type=row["change_type"],
                    original_content=row["original_content"],
                    new_content=row["new_content"],
                    backup_path=row["backup_path"],
                    lines_added=row["lines_added"],
                    lines_removed=row["lines_removed"],
                    validation_passed=bool(row["validation_passed"]),
                    applied=bool(row["applied"]),
                    rolled_back=bool(row["rolled_back"]),
                    user_confirmed=bool(row["user_confirmed"]),
                    metadata=meta,
                    created_at=row["created_at"]
                ))

            return changes

    def get_file_change_history(self, file_path: str, limit: int = 10) -> List[AgentChange]:
        """
        Получает историю изменений конкретного файла (по всем диалогам).

        Args:
            file_path: Путь к файлу
            limit: Максимальное количество записей

        Returns:
            Список AgentChange
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM agent_changes 
                WHERE file_path = ? 
                ORDER BY created_at DESC 
                LIMIT ?
                """,
                (file_path, limit)
            )
            rows = cursor.fetchall()

            changes = []
            for row in rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else None
                changes.append(AgentChange(
                    id=row["id"],
                    thread_id=row["thread_id"],
                    message_id=row["message_id"],
                    session_id=row["session_id"],
                    file_path=row["file_path"],
                    change_type=row["change_type"],
                    original_content=row["original_content"],
                    new_content=row["new_content"],
                    backup_path=row["backup_path"],
                    lines_added=row["lines_added"],
                    lines_removed=row["lines_removed"],
                    validation_passed=bool(row["validation_passed"]),
                    applied=bool(row["applied"]),
                    rolled_back=bool(row["rolled_back"]),
                    user_confirmed=bool(row["user_confirmed"]),
                    metadata=meta,
                    created_at=row["created_at"]
                ))

            return changes

    def mark_change_applied(
        self,
        change_id: str,
        user_confirmed: bool = True
    ) -> bool:
        """
        Отмечает изменение как применённое.

        Args:
            change_id: ID записи об изменении
            user_confirmed: Было ли подтверждение пользователя

        Returns:
            True если обновление успешно
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE agent_changes 
                SET applied = 1, user_confirmed = ?
                WHERE id = ?
                """,
                (int(user_confirmed), change_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_change_rolled_back(self, change_id: str) -> bool:
        """
        Отмечает изменение как откаченное.

        Args:
            change_id: ID записи об изменении

        Returns:
            True если обновление успешно
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE agent_changes SET rolled_back = 1 WHERE id = ?",
                (change_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_session_changes(self, session_id: str) -> List[AgentChange]:
        """
        Получает все изменения для сессии бэкапов.

        Args:
            session_id: ID сессии

        Returns:
            Список AgentChange
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM agent_changes WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,)
            )
            rows = cursor.fetchall()

            changes = []
            for row in rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else None
                changes.append(AgentChange(
                    id=row["id"],
                    thread_id=row["thread_id"],
                    message_id=row["message_id"],
                    session_id=row["session_id"],
                    file_path=row["file_path"],
                    change_type=row["change_type"],
                    original_content=row["original_content"],
                    new_content=row["new_content"],
                    backup_path=row["backup_path"],
                    lines_added=row["lines_added"],
                    lines_removed=row["lines_removed"],
                    validation_passed=bool(row["validation_passed"]),
                    applied=bool(row["applied"]),
                    rolled_back=bool(row["rolled_back"]),
                    user_confirmed=bool(row["user_confirmed"]),
                    metadata=meta,
                    created_at=row["created_at"]
                ))

            return changes