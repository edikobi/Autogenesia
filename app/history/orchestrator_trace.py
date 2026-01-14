# app/history/orchestrator_trace.py
"""
Orchestrator Trace Storage - отдельное хранилище для шагов агента.
Нужно для отображения "мышления" Оркестратора на фронтенде.
"""

import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging
import time

# Импорт настроек, чтобы знать, где хранить БД
try:
    from config.settings import cfg
except ImportError:
    # Фолбек, если конфиг недоступен
    class Cfg:
        HISTORY_DB_PATH = "history.db"
    cfg = Cfg()

logger = logging.getLogger(__name__)

@dataclass
class TraceStep:
    """Один шаг работы Оркестратора"""
    tool_name: str
    tool_args: Dict[str, Any]
    tool_output: str
    success: bool
    timestamp: float
    thinking: str = ""       # Оригинальные мысли (EN)
    thinking_ru: str = ""    # Переведенные мысли (RU)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class OrchestratorTraceStorage:
    """
    Хранилище для детальных шагов Оркестратора.
    Использует отдельную SQLite базу данных, чтобы не засорять основную историю.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: Путь к БД трейсов. Если None, создается рядом с основной историей.
        """
        if db_path:
            self.db_path = Path(db_path)
        else:
            # Создаем traces.db в той же папке, что и history.db
            base_history_path = Path(getattr(cfg, 'HISTORY_DB_PATH', 'history.db'))
            self.db_path = base_history_path.parent / "traces.db"
            
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Создает таблицу, если её нет"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orchestrator_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_index INTEGER,  -- Позиция сообщения в диалоге (для связки)
                    step_number INTEGER,
                    tool_name TEXT,
                    tool_args TEXT,  -- JSON
                    tool_output TEXT,
                    success INTEGER,  -- 0/1
                    thinking TEXT DEFAULT '',
                    thinking_ru TEXT DEFAULT '',
                    timestamp REAL,
                    created_at REAL DEFAULT (julianday('now'))
                )
            """)
            
            # Индекс для быстрого поиска по сессии
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_message 
                ON orchestrator_traces(session_id, message_index)
            """)
            
            conn.commit()
            conn.close()
            logger.info(f"Orchestrator trace storage initialized: {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to init trace DB: {e}")
    
    def save_trace(
        self,
        session_id: str,
        message_index: int,
        steps: List[TraceStep]
    ):
        """
        Сохраняет шаги Оркестратора для конкретного сообщения.
        
        Args:
            session_id: ID сессии диалога (thread_id)
            message_index: Индекс сообщения в диалоге (порядковый номер)
            steps: Список шагов (tool calls)
        """
        if not steps:
            return
        
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Очищаем старые трейсы для этого сообщения (если были, например при ретрае)
            cursor.execute("""
                DELETE FROM orchestrator_traces 
                WHERE session_id = ? AND message_index = ?
            """, (session_id, message_index))
            
            for i, step in enumerate(steps):
                cursor.execute("""
                    INSERT INTO orchestrator_traces 
                    (session_id, message_index, step_number, tool_name, tool_args, 
                     tool_output, success, thinking, thinking_ru, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    message_index,
                    i,
                    step.tool_name,
                    json.dumps(step.tool_args, ensure_ascii=False),
                    step.tool_output,
                    1 if step.success else 0,
                    step.thinking,
                    step.thinking_ru,
                    step.timestamp
                ))
            
            conn.commit()
            conn.close()
            logger.debug(f"Saved {len(steps)} trace steps for session {session_id}, msg {message_index}")
        except Exception as e:
            logger.error(f"Failed to save trace: {e}")
    
    def get_trace(
        self,
        session_id: str,
        message_index: int
    ) -> List[Dict[str, Any]]:
        """
        Возвращает все шаги для конкретного сообщения (для UI).
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM orchestrator_traces
                WHERE session_id = ? AND message_index = ?
                ORDER BY step_number ASC
            """, (session_id, message_index))
            
            rows = cursor.fetchall()
            conn.close()
            
            results = []
            for row in rows:
                results.append({
                    "step_number": row["step_number"],
                    "tool_name": row["tool_name"],
                    "tool_args": json.loads(row["tool_args"]),
                    "tool_output": row["tool_output"],
                    "success": bool(row["success"]),
                    "thinking": row["thinking"],
                    "thinking_ru": row["thinking_ru"],
                    "timestamp": row["timestamp"]
                })
            
            return results
        except Exception as e:
            logger.error(f"Failed to get trace: {e}")
            return []
    
    def clear_session(self, session_id: str):
        """Удаляет все трейсы для сессии"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("DELETE FROM orchestrator_traces WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
            logger.info(f"Cleared traces for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to clear traces: {e}")
