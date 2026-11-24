"""Адаптеры совместимости: типизированные фасады над AgentPipeline,
HistoryManager и состоянием воркспейса.

Правила:
- все обращения TUI к AgentPipeline и HistoryManager идут ТОЛЬКО через эти
  адаптеры (единственная точка правки при дрейфе сигнатур);
- ни одного нового поля в существующих таблицах истории;
- параллельные вкладки работают каждая со своим ``thread_id``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Константа пользователя (копия из main.py; сам main.py НЕ импортируется).
DEFAULT_USER_ID = "default_user"


# ============================================================================
# STATE ADAPTER
# ============================================================================

@dataclass
class StateAdapter:
    """Per-workspace аналог глобального состояния main.py (AppState)."""

    mode: str = "agent"                      # "agent" | "ask" | "general"
    project_dir: Optional[str] = None
    project_index: Dict[str, Any] = field(default_factory=dict)
    is_new_project: bool = False
    is_legal_mode: bool = False
    use_router: bool = True
    prefilter_mode: str = "normal"           # "normal" | "advanced"
    prefilter_model: Optional[str] = None
    prefilter_provider: Optional[str] = None
    tester_model: Optional[str] = None
    tester_provider: Optional[str] = None
    enable_type_checking: bool = False
    generator_model: Optional[str] = None
    generator_provider: Optional[str] = None
    orchestrator_model: Optional[str] = None
    orchestrator_provider: Optional[str] = None
    thread_id: Optional[str] = None
    current_thread: Optional[Any] = None
    session_messages: List[Dict[str, str]] = field(default_factory=list)
    # Поля авто-тестирования и уровня reasoning (контрольные точки сессии)
    enable_tester: bool = True
    auto_translate: bool = True
    reasoning_effort: Optional[str] = None
    pending_stage: str = "IDLE"
    # Wizard state markers (setup flow progress)
    setup_complete: bool = False
    setup_stage: str = "IDLE"

    def get_current_orchestrator_model(self) -> Optional[str]:
        """Текущая модель оркестратора (None — работает роутер)."""
        if self.use_router:
            return None
        return self.orchestrator_model

    def get_current_generator_model(self) -> Optional[str]:
        """Текущая модель генератора."""
        return self.generator_model

    def get_current_orchestrator_provider(self) -> Optional[str]:
        """Текущий провайдер оркестратора (None — роутер/авто)."""
        if self.use_router:
            return None
        return self.orchestrator_provider

    def reset_session(self) -> None:
        """Сброс in-memory сессии (привязка к треду/проекту сохраняется)."""
        self.session_messages = []


# ============================================================================
# HISTORY ADAPTER
# ============================================================================

class HistoryAdapter:
    """Обёртка над HistoryManager: та же БД, тот же формат List[Dict[str, str]]."""

    def __init__(self, history_manager: Any) -> None:
        self.history_manager = history_manager

    # --- conversations -----------------------------------------------------

    async def new_conversation(self, project_path: Optional[str] = None, title: Optional[str] = None):
        """Создать новый тред для DEFAULT_USER_ID."""
        if title is None:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = f"Сессия {stamp}"
        return await self.history_manager.create_thread(DEFAULT_USER_ID, project_path, title)

    async def open_conversation(
        self,
        project_path: Optional[str],
        current_query: str = "",
        active_model: Optional[str] = None,
    ) -> Tuple[Optional[str], List[Dict[str, str]]]:
        """Выбрать существующий тред для проекта (или создать) → (thread_id, history)."""
        thread_id: Optional[str] = None
        try:
            threads = await self.history_manager.list_user_threads(DEFAULT_USER_ID)
            if project_path:
                matching = [t for t in threads if getattr(t, "project_path", None) == project_path]
            else:
                matching = [t for t in threads if not getattr(t, "project_path", None)]
            if matching:
                thread_id = matching[0].id
        except Exception as e:
            logger.warning(f"open_conversation: list threads failed: {e}")
        if thread_id is None:
            thread = await self.new_conversation(project_path)
            thread_id = thread.id
        history = await self.get_session_history(thread_id, current_query, active_model)
        return thread_id, history

    async def get_session_history(
        self,
        thread_id: str,
        current_query: str = "",
        active_model: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Загрузить историю сессии в формате List[Dict[str, str]]."""
        try:
            result = await self.history_manager.get_session_history(thread_id, current_query, active_model)
        except Exception as e:
            logger.warning(f"get_session_history failed: {e}")
            return []
        messages = result[0] if isinstance(result, tuple) else result
        history: List[Dict[str, str]] = []
        for msg in messages or []:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = "\n".join(str(item) for item in content)
            elif isinstance(content, dict):
                content = str(content)
            history.append({"role": str(getattr(msg, "role", "user")), "content": content})
        return history

    async def rename_conversation(self, thread_id: str, new_title: str) -> bool:
        """Переименовать тред."""
        try:
            return bool(await self.history_manager.update_thread_title(thread_id, new_title))
        except Exception as e:
            logger.warning(f"rename_conversation failed: {e}")
            return False

    async def auto_title(self, thread_id: str, query: str) -> None:
        """Название из первого запроса (если тред безымянный/«Новый диалог»/«Сессия …»)."""
        try:
            thread = await self.history_manager.get_thread(thread_id)
            if thread is None:
                return
            title = (getattr(thread, "title", "") or "").strip()
            unnamed = (not title) or title == "Новый диалог" or title.startswith("Сессия")
            if not unnamed:
                return
            messages = await self.history_manager.get_messages(thread_id)
            user_messages = [m for m in messages if getattr(m, "role", "") == "user"]
            if len(user_messages) > 1:
                return
            candidate = " ".join((query or "").strip().split())
            if len(candidate) > 50:
                cut = candidate[:50]
                space = cut.rfind(" ")
                if space > 30:
                    cut = cut[:space]
                candidate = cut + "..."
            if len(candidate) < 3:
                return
            await self.history_manager.update_thread_title(thread_id, candidate)
            try:
                thread.title = candidate
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"auto_title failed: {e}")

    async def view_history(self, thread_id: str, limit: Optional[int] = None) -> List[Any]:
        """Полная история сообщений треда (Message-объекты)."""
        try:
            return await self.history_manager.get_messages(thread_id, limit)
        except Exception as e:
            logger.warning(f"view_history failed: {e}")
            return []

    async def export_to_markdown(self, thread_id: str, path: str) -> str:
        """Экспорт треда в .md; возвращает абсолютный путь."""
        messages = await self.view_history(thread_id)
        title = thread_id
        try:
            thread = await self.history_manager.get_thread(thread_id)
            if thread is not None:
                title = getattr(thread, "title", "") or thread_id
        except Exception:
            pass
        lines: List[str] = [
            f"# {title}",
            "",
            f"**Экспорт:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
        ]
        for msg in messages:
            created = (getattr(msg, "created_at", "") or "")[:19].replace("T", " ")
            role = getattr(msg, "role", "user")
            who = {"user": "👤 Пользователь", "assistant": "🤖 Ассистент", "system": "⚙️ Система"}.get(
                role, f"🔧 {role}"
            )
            lines.append(f"## {who} ({created})")
            lines.append("")
            lines.append(str(getattr(msg, "content", "") or ""))
            lines.append("")
            lines.append("---")
            lines.append("")
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        target.write_text("\n".join(lines), encoding="utf-8")
        return str(target.resolve())

    async def delete_conversation(self, thread_id: str) -> bool:
        """Удалить тред: manager.delete_thread, иначе storage.delete_thread."""
        try:
            deleter = getattr(self.history_manager, "delete_thread", None)
            if callable(deleter):
                return bool(await deleter(thread_id))
            storage = getattr(self.history_manager, "storage", None)
            storage_deleter = getattr(storage, "delete_thread", None)
            if callable(storage_deleter):
                return bool(await asyncio.to_thread(storage_deleter, thread_id))
        except Exception as e:
            logger.warning(f"delete_conversation failed: {e}")
        return False

    # --- messages ----------------------------------------------------------

    async def save_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Сохранить сообщение с подсчётом токенов (TokenCounter, len//4 fallback)."""
        tokens = max(1, len(content or "") // 4)
        try:
            from app.utils.token_counter import TokenCounter

            tokens = TokenCounter().count(content or "")
        except Exception:
            pass
        return await self.history_manager.add_message(thread_id, role, content, tokens, metadata=metadata)

    async def attach_files(self, thread_id: str, paths: List[str]) -> List[str]:
        """Прикрепить файлы к треду как user-сообщения с метаданными файлов."""
        saved: List[str] = []
        for raw in paths or []:
            path = Path(os.path.expanduser(str(raw).strip().strip("\"'")))
            if not path.is_file():
                logger.warning(f"attach_files: not a file: {path}")
                continue
            parsed = await asyncio.to_thread(self._read_file_content_sync, path)
            if parsed is None:
                continue
            content, file_type = parsed
            filename = path.name
            message = f"[Прикреплённый файл: {filename}]\nТип: {file_type}\n---\n{content[:100000]}"
            metadata = {"type": "attached_file", "filename": filename, "file_type": file_type}
            try:
                await self.save_message(thread_id, "user", message, metadata=metadata)
                saved.append(filename)
            except Exception as e:
                logger.warning(f"attach_files: save failed for {filename}: {e}")
        return saved

    def _read_file_content_sync(self, path: Path) -> Optional[Tuple[str, str]]:
        """Прочитать файл → (content, type); парсер проекта → текстовый fallback."""
        suffix = path.suffix.lower()
        try:
            from app.utils import file_parser  # noqa: F401  (txt/pdf/docx/xlsx/csv)

            for fn_name in ("parse_file", "read_file", "extract_text"):
                fn = getattr(file_parser, fn_name, None)
                if callable(fn):
                    try:
                        result = fn(str(path))
                        if isinstance(result, tuple) and len(result) == 2:
                            return str(result[0]), str(result[1])
                        if isinstance(result, str) and result:
                            return result, suffix.lstrip(".") or "txt"
                    except Exception:
                        continue
        except ImportError:
            pass
        text_exts = {
            ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".xml", ".html",
            ".css", ".yaml", ".yml", ".ini", ".cfg", ".sh", ".sql", ".go", ".java",
        }
        try:
            if suffix in text_exts:
                try:
                    return path.read_text(encoding="utf-8"), "text"
                except UnicodeDecodeError:
                    return path.read_text(encoding="latin-1"), "text"
            if suffix == ".pdf":
                import pypdf

                reader = pypdf.PdfReader(str(path))
                return "\n\n".join((p.extract_text() or "") for p in reader.pages), "pdf"
            if suffix == ".docx":
                import docx

                doc = docx.Document(str(path))
                return "\n\n".join(p.text for p in doc.paragraphs), "docx"
            return path.read_text(encoding="utf-8", errors="replace"), "text"
        except Exception as e:
            logger.warning(f"attach_files: cannot read {path}: {e}")
            return None

    # --- misc passthroughs ---------------------------------------------------

    async def list_user_threads(self, limit: int = 20) -> List[Any]:
        """Список тредов пользователя."""
        try:
            return await self.history_manager.list_user_threads(DEFAULT_USER_ID, limit)
        except TypeError:
            return await self.history_manager.list_user_threads(DEFAULT_USER_ID)
        except Exception as e:
            logger.warning(f"list_user_threads failed: {e}")
            return []

    async def list_threads_paginated(self, user_id: str = DEFAULT_USER_ID, page: int = 1, per_page: int = 5):
        """Пагинированный список тредов (делегирует в HistoryManager)."""
        return await self.history_manager.list_threads_paginated(user_id, page, per_page)

    async def get_thread(self, thread_id: str) -> Optional[Any]:
        """Тред по id."""
        try:
            return await self.history_manager.get_thread(thread_id)
        except Exception as e:
            logger.warning(f"get_thread failed: {e}")
            return None

    async def thread_preview(self, thread_id: str, max_chars: int = 80) -> str:
        """Превью последнего сообщения треда (для списков)."""
        try:
            last = None
            getter = getattr(self.history_manager, "get_last_user_message", None)
            if callable(getter):
                last = await getter(thread_id)
            if last is None:
                messages = await self.history_manager.get_messages(thread_id)
                last = messages[-1] if messages else None
            content = getattr(last, "content", "") if last is not None else ""
            content = " ".join(str(content).split())
            if len(content) > max_chars:
                content = content[: max_chars - 3] + "..."
            return content or "—"
        except Exception as e:
            logger.debug(f"thread_preview failed: {e}")
            return "—"

    async def get_file_history(self, file_path: str, limit: int = 15) -> List[Any]:
        """История изменений конкретного файла."""
        try:
            return await self.history_manager.get_file_history(file_path, limit=limit)
        except Exception as e:
            logger.warning(f"get_file_history failed: {e}")
            return []

    async def get_thread_file_changes(self, thread_id: str, limit: int = 30) -> List[Any]:
        """История изменений файлов в треде."""
        try:
            return await self.history_manager.get_thread_file_changes(thread_id, limit=limit)
        except Exception as e:
            logger.warning(f"get_thread_file_changes failed: {e}")
            return []

    async def mark_changes_applied(self, change_ids: List[str]) -> int:
        """Отметить изменения применёнными."""
        try:
            return int(await self.history_manager.mark_changes_applied(change_ids))
        except Exception as e:
            logger.warning(f"mark_changes_applied failed: {e}")
            return 0


# ============================================================================
# PIPELINE ADAPTER
# ============================================================================

def create_pipeline(
    project_dir: Optional[str] = None,
    history_manager: Optional[Any] = None,
    thread_id: Optional[str] = None,
    project_index: Optional[Dict[str, Any]] = None,
    enable_type_checking: bool = False,
    generator_model: Optional[str] = None,
):
    """Создать AgentPipeline (единственная точка фиксации сигнатуры конструктора)."""
    from app.agents.agent_pipeline import AgentPipeline

    return AgentPipeline(
        project_dir=project_dir,
        history_manager=history_manager,
        thread_id=thread_id,
        project_index=project_index,
        enable_type_checking=enable_type_checking,
        generator_model=generator_model,
    )


class PipelineAdapter:
    """Фасад над AgentPipeline с точными сигнатурами из зафиксированных контрактов."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    # --- main entry points -------------------------------------------------

    async def process_request(
        self,
        user_request: str,
        history: List[Dict[str, str]],
        mode: Optional[Any] = None,
        on_thinking=None,
        on_tool_call=None,
        on_validation=None,
        on_status=None,
        on_stage=None,
        on_user_decision=None,
        on_token=None,
        prefilter_advice: str = "",
    ):
        """Полный прогон пайплайна (см. AgentPipeline.process_request)."""
        from app.agents.agent_pipeline import PipelineMode

        if mode is None:
            mode = PipelineMode.AGENT
        return await self._pipeline.process_request(
            user_request=user_request,
            history=history,
            mode=mode,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_validation=on_validation,
            on_status=on_status,
            on_stage=on_stage,
            on_user_decision=on_user_decision,
            on_token=on_token,
            prefilter_advice=prefilter_advice,
        )

    async def run_tester(
        self,
        user_additional_input: str = "",
        tester_model: Optional[str] = None,
        tester_provider: Optional[str] = None,
        on_tool_call=None,
        on_token=None,
    ):
        """Запустить Тестировщика → Optional[TesterReport]."""
        return await self._pipeline.run_tester(
            user_additional_input=user_additional_input,
            tester_model=tester_model,
            tester_provider=tester_provider,
            on_tool_call=on_tool_call,
            on_token=on_token,
        )

    async def ask_tester_followup(self, user_question: str):
        """Дополнительный вопрос Тестировщику."""
        return await self._pipeline.ask_tester_followup(user_question)

    def cleanup_tester(self) -> None:
        """Освободить ресурсы Тестировщика."""
        self._pipeline.cleanup_tester()

    async def run_feedback_cycle(
        self,
        user_feedback: str,
        history: List[Dict[str, str]],
        tester_report: Optional[str] = None,
        on_user_decision=None,
    ):
        """Цикл доработки по критике пользователя (опционально с отчётом тестера)."""
        return await self._pipeline.run_feedback_cycle(
            user_feedback=user_feedback,
            history=history,
            tester_report=tester_report,
            on_user_decision=on_user_decision,
        )

    async def apply_pending_changes(self):
        """Применить изменения → ApplyResult (доступ к полям через getattr)."""
        return await self._pipeline.apply_pending_changes()

    async def discard_pending_changes(self) -> None:
        """Отменить неподтверждённые изменения."""
        await self._pipeline.discard_pending_changes()

    async def handle_user_feedback(
        self,
        action: str,
        user_message: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        on_user_decision=None,
    ):
        """accept | override | replace | cancel → Optional[PipelineResult]."""
        return await self._pipeline.handle_user_feedback(
            action=action,
            user_message=user_message,
            history=history,
            on_user_decision=on_user_decision,
        )

    # --- model / provider settings ------------------------------------------

    def set_orchestrator_model(self, model: Optional[str]) -> None:
        """Фиксировать модель оркестратора (None — роутер)."""
        self._pipeline.set_orchestrator_model(model)

    def set_generator_model(self, model: Optional[str]) -> None:
        """Фиксировать модель генератора (None — из конфига)."""
        self._pipeline.set_generator_model(model)

    def set_orchestrator_provider(self, provider: Optional[str]) -> None:
        """Per-role провайдер оркестратора."""
        self._pipeline.set_orchestrator_provider(provider)

    def set_generator_provider(self, provider: Optional[str]) -> None:
        """Per-role провайдер генератора (если есть у пайплайна)."""
        setter = getattr(self._pipeline, "set_generator_provider", None)
        if callable(setter):
            setter(provider)

    # --- cancellation ---------------------------------------------------------

    def request_cancel(self) -> None:
        """Запросить кооперативную отмену (Emergency Stop)."""
        self._pipeline.request_cancel()

    def reset_cancel(self) -> None:
        """Сбросить флаг отмены."""
        self._pipeline.reset_cancel()

    @property
    def is_cancel_requested(self) -> bool:
        """Установлен ли флаг отмены."""
        try:
            return bool(self._pipeline.is_cancel_requested)
        except Exception:
            return False

    # --- attribute passthroughs ---------------------------------------------

    @property
    def project_index(self) -> Dict[str, Any]:
        """Индекс проекта пайплайна."""
        return getattr(self._pipeline, "project_index", {})

    @property
    def project_dir(self) -> Optional[str]:
        """Project directory bound to the underlying pipeline (or None)."""
        try:
            return getattr(self._pipeline, "project_dir", None)
        except Exception as e:
            logger.debug(f"project_dir get failed: {e}")
            return None

    @project_dir.setter
    def project_dir(self, value: Optional[str]) -> None:
        """Best-effort update of the underlying pipeline's project directory."""
        try:
            self._pipeline.project_dir = value
        except Exception as e:
            logger.debug(f"project_dir set failed: {e}")

    @project_index.setter
    def project_index(self, value: Dict[str, Any]) -> None:
        try:
            self._pipeline.project_index = value
        except Exception as e:
            logger.debug(f"project_index set failed: {e}")

    @property
    def backup_manager(self):
        """BackupManager пайплайна (или None)."""
        return getattr(self._pipeline, "backup_manager", None)

    @property
    def vfs(self):
        """VirtualFileSystem пайплайна (или None)."""
        return getattr(self._pipeline, "vfs", None)
    def set_orchestrator_reasoning_effort(self, effort: Optional[str]) -> None:
        """Best-effort проброс reasoning effort в AgentPipeline.

        Если у пайплайна есть метод ``set_orchestrator_reasoning_effort`` — вызов;
        иначе тихо пропускаем (log DEBUG).
        """
        setter = getattr(self._pipeline, "set_orchestrator_reasoning_effort", None)
        if callable(setter):
            try:
                setter(effort)
                return
            except Exception as e:
                logger.debug(f"set_orchestrator_reasoning_effort failed: {e}")
        else:
            logger.debug("AgentPipeline has no set_orchestrator_reasoning_effort method")

    @property
    def staged_files(self) -> List[str]:
        """Пути стейджированных в VFS файлов (для статус-бара)."""
        vfs = getattr(self._pipeline, "vfs", None)
        if vfs is None:
            return []
        try:
            lister = getattr(vfs, "list_staged", None)
            if callable(lister):
                result = lister()
                if isinstance(result, list):
                    return [str(x) for x in result]
            attr = getattr(vfs, "staged_files", None)
            if isinstance(attr, (list, tuple, set)):
                return [str(x) for x in attr]
            staged = getattr(vfs, "_staged_changes", None)
            if isinstance(staged, dict):
                return [str(k) for k in staged.keys()]
        except Exception as e:
            logger.debug(f"staged_files access failed: {e}")
        return []

    def estimate_context_tokens(self, history: List[Dict[str, str]]) -> int:
        """Суммарная оценка токенов истории (TokenCounter с fallback len//4)."""
        if not history:
            return 0
        try:
            from app.utils.token_counter import TokenCounter
            counter = TokenCounter()
        except Exception:
            counter = None
        total = 0
        for msg in history:
            content = str(msg.get("content", "")) if isinstance(msg, dict) else str(msg)
            if counter is not None:
                try:
                    total += int(counter.count(content))
                    continue
                except Exception:
                    pass
            total += max(0, len(content) // 4)
        return total
