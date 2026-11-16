"""SQLite storage of TUI workspace sessions (SQLAlchemy 2.0 async + aiosqlite).

Хранит привязки вкладок воркспейса к (project_path, thread_id) в отдельной
таблице ``tui_workspace_sessions`` той же БД, что и история (history.db).
Существующие таблицы (threads, messages, agent_changes) не затрагиваются.

Конфигурация: путь БД берётся из ``cfg.HISTORY_DB_PATH`` (или "history.db"),
engine создаётся лениво и кэшируется на уровне модуля.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:
    from config.settings import cfg
except ImportError:  # pragma: no cover - defensive
    cfg = None

logger = logging.getLogger(__name__)


def _default_db_path() -> str:
    """Resolve the default database path from config (fallback: history.db)."""
    if cfg is not None:
        return getattr(cfg, "HISTORY_DB_PATH", None) or "history.db"
    return "history.db"


_db_path: str = _default_db_path()
_engine = None
_session_factory = None


class Base(DeclarativeBase):
    """Declarative base for TUI-only tables."""


def _utcnow() -> datetime:
    """Current local timestamp (naive, SQLite-friendly)."""
    return datetime.now()


class TuiWorkspaceSession(Base):
    """One persisted workspace session (tab -> project/thread binding)."""

    __tablename__ = "tui_workspace_sessions"

    id: Mapped[str] = mapped_column(primary_key=True)
    tab_key: Mapped[str] = mapped_column(default="ws-1")
    project_path: Mapped[Optional[str]] = mapped_column(default=None)
    thread_id: Mapped[Optional[str]] = mapped_column(default=None)
    title: Mapped[str] = mapped_column(default="Workspace")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


def set_db_path(path: str) -> None:
    """Point the module at another database file (resets the cached engine)."""
    global _db_path, _engine, _session_factory
    _db_path = path
    _engine = None
    _session_factory = None


def _get_engine():
    """Lazily create and cache the async engine + session factory."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(f"sqlite+aiosqlite:///{_db_path}", future=True)
        _session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def new_session(
    tab_key: str,
    project_path: Optional[str] = None,
    thread_id: Optional[str] = None,
    title: str = "Workspace",
) -> TuiWorkspaceSession:
    """Create a new unsaved TuiWorkspaceSession with a ``uuid4().hex`` id."""
    return TuiWorkspaceSession(
        id=uuid.uuid4().hex,
        tab_key=tab_key,
        project_path=project_path,
        thread_id=thread_id,
        title=title,
    )


async def init_db() -> None:
    """Create tables and enable foreign keys (PRAGMA via exec_driver_sql)."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    logger.info(f"tui sessions db ready: {_db_path}")


async def save_session(session: TuiWorkspaceSession) -> None:
    """Upsert a workspace session by primary key (merge)."""
    try:
        _get_engine()
        async with _session_factory() as db:
            await db.merge(session)
            await db.commit()
    except SQLAlchemyError as e:
        logger.warning(f"save_session failed: {e}")


async def load_sessions() -> List[TuiWorkspaceSession]:
    """Load all saved sessions ordered by ``updated_at`` desc (empty on error)."""
    try:
        _get_engine()
        async with _session_factory() as db:
            result = await db.execute(
                select(TuiWorkspaceSession).order_by(TuiWorkspaceSession.updated_at.desc())
            )
            return list(result.scalars().all())
    except SQLAlchemyError as e:
        logger.warning(f"load_sessions failed: {e}")
        return []


async def delete_session(session_id: str) -> bool:
    """Delete a session by id; False when missing or on error."""
    try:
        _get_engine()
        async with _session_factory() as db:
            obj = await db.get(TuiWorkspaceSession, session_id)
            if obj is None:
                return False
            await db.delete(obj)
            await db.commit()
            return True
    except SQLAlchemyError as e:
        logger.warning(f"delete_session failed: {e}")
        return False


async def get_session(session_id: str) -> Optional[TuiWorkspaceSession]:
    """Fetch one session by id (None when missing or on error)."""
    try:
        _get_engine()
        async with _session_factory() as db:
            return await db.get(TuiWorkspaceSession, session_id)
    except SQLAlchemyError as e:
        logger.warning(f"get_session failed: {e}")
        return None


async def dispose() -> None:
    """Dispose the cached engine (used on shutdown and in tests)."""
    global _engine, _session_factory
    if _engine is not None:
        try:
            await _engine.dispose()
        except Exception:  # pragma: no cover - defensive
            pass
    _engine = None
    _session_factory = None