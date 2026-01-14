# отвечает за менеджмент всех файлов по истории бесед
import logging
import time
import re
from typing import List, Optional, Dict, Any
from dataclasses import asdict
import asyncio
from app.history.storage import HistoryStorage, Message, Thread, AgentChange
from app.history.compressor import compress_history_if_needed, prune_irrelevant_context, CompressionStats
from app.history.orchestrator_trace import OrchestratorTraceStorage, TraceStep
from app.llm.api_client import call_llm
from config.settings import cfg


logger = logging.getLogger(__name__)


class HistoryManager:
    """
    Единая точка входа для работы с историей диалогов.
    Объединяет HistoryStorage и модуль compressor для прозрачной работы с историей.
    """
    DEFAULT_DB_PATH = "history.db"
    DEFAULT_COMPRESSION_THRESHOLD = 30000
    DEFAULT_FALLBACK_MESSAGE_COUNT = 10

    def __init__(
        self,
        db_path: Optional[str] = None,
        compression_threshold: Optional[int] = None
    ):
        """
        Инициализирует HistoryManager с настройками из config.settings или значениями по умолчанию.
        
        Args:
            db_path: Путь к файлу БД SQLite (если None, берется из cfg или используется DEFAULT_DB_PATH)
            compression_threshold: Порог токенов для сжатия (если None, используется DEFAULT_COMPRESSION_THRESHOLD)
        """
        # Получаем путь к БД из настроек или используем значение по умолчанию
        self.db_path = db_path or getattr(cfg, 'HISTORY_DB_PATH', self.DEFAULT_DB_PATH)
        
        # Получаем порог сжатия из настроек или используем значение по умолчанию
        self.compression_threshold = compression_threshold or getattr(
            cfg, 'HISTORY_COMPRESSION_THRESHOLD', self.DEFAULT_COMPRESSION_THRESHOLD
        )
        
        # Инициализируем хранилище
        self.storage = HistoryStorage(db_path=self.db_path)
        
        self.trace_storage = OrchestratorTraceStorage()
        
        logger.info(
            f"HistoryManager initialized: db_path={self.db_path}, "
            f"compression_threshold={self.compression_threshold}"
        )

    async def list_user_threads(self, user_id: str, limit: int = 20) -> List[Thread]:
        """
        Получает список диалогов пользователя.

        Args:
            user_id: ID пользователя
            limit: Максимальное количество возвращаемых диалогов (по умолчанию 20)

        Returns:
            Список объектов Thread
        """
        logger.info(f"Listing threads for user_id={user_id}, limit={limit}")
        try:
            threads = await asyncio.to_thread(
                self.storage.list_threads, user_id
            )
            # Применяем limit после получения результата
            threads = threads[:limit]
            logger.debug(f"Found {len(threads)} threads for user_id={user_id}")
            return threads
        except Exception as e:
            logger.error(f"Failed to list threads: {e}", exc_info=True)
            return []

    async def get_thread(self, thread_id: str) -> Optional[Thread]:
        """
        Получает диалог по ID.

        Args:
            thread_id: ID диалога

        Returns:
            Объект Thread или None, если не найден
        """
        logger.debug(f"Getting thread: thread_id={thread_id}")
        try:
            thread = await asyncio.to_thread(
                self.storage.get_thread, thread_id
            )
            if thread:
                logger.debug(f"Thread found: {thread_id}")
                return thread
            else:
                logger.warning(f"Thread not found: {thread_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to get thread: {e}", exc_info=True)
            raise

    async def get_thread_stats(self, thread_id: str) -> Dict[str, Any]:
        """
        Получает статистику диалога по ID.

        Args:
            thread_id: ID диалога

        Returns:
            Словарь со статистикой диалога или пустой словарь, если не найден
        """
        logger.debug(f"Getting thread stats for thread_id={thread_id}")
        try:
            thread = await asyncio.to_thread(
                self.storage.get_thread, thread_id
            )
            if thread:
                return {
                    "id": thread.id,
                    "title": thread.title,
                    "message_count": thread.message_count,
                    "total_tokens": thread.total_tokens,
                    "created_at": thread.created_at,
                    "updated_at": thread.updated_at
                }
            else:
                logger.warning(f"Thread not found for stats: thread_id={thread_id}")
                return {}
        except Exception as e:
            logger.error(f"Failed to get thread stats: {e}", exc_info=True)
            return {}


    async def get_messages(
        self,
        thread_id: str,
        limit: Optional[int] = None
    ) -> List[Message]:
        """
        Получает сообщения диалога напрямую из БД БЕЗ сжатия.
        
        Используется для:
        - Просмотра истории пользователем (UI)
        - Отображения превью при выборе диалога
        - Экспорта диалога
        
        Для подготовки контекста LLM используйте get_session_history().
        
        Args:
            thread_id: ID диалога
            limit: Ограничение количества сообщений (опционально).
                Если указано, возвращает последние N сообщений.
            
        Returns:
            Список объектов Message в хронологическом порядке
        """
        logger.debug(f"Getting raw messages for thread_id={thread_id}, limit={limit}")
        try:
            messages = await asyncio.to_thread(
                self.storage.get_messages, thread_id, limit
            )
            logger.debug(f"Retrieved {len(messages)} messages for thread_id={thread_id}")
            return messages
        except Exception as e:
            logger.error(f"Failed to get messages: {e}", exc_info=True)
            return []

    async def get_last_user_message(self, thread_id: str) -> Optional[Message]:
        """
        Получает последнее сообщение пользователя в диалоге.
        Используется для превью при выборе диалога.
        
        Args:
            thread_id: ID диалога
            
        Returns:
            Последнее сообщение пользователя или None
        """
        try:
            messages = await asyncio.to_thread(
                self.storage.get_messages, thread_id
            )
            # Ищем последнее сообщение с role="user"
            for msg in reversed(messages):
                if msg.role == "user":
                    return msg
            return None
        except Exception as e:
            logger.error(f"Failed to get last user message: {e}", exc_info=True)
            return None


    async def get_session_history(
        self, 
        thread_id: str, 
        current_query: str, 
        active_model: Optional[str] = None
    ) -> tuple[List[Message], Optional[CompressionStats]]:
        """
        Загружает историю сессии, сжимает её при необходимости и удаляет нерелевантный контекст.

        Args:
            thread_id: ID потока для загрузки истории.
            current_query: Текущий запрос пользователя для определения релевантности.
            active_model: Текущая активная модель.

        Returns:
            Кортеж (список сообщений, статистика сжатия или None).
        """
        try:
            # Загружаем сырую историю
            raw_history = await asyncio.to_thread(self.storage.get_messages, thread_id)
            logger.info(f"Loaded {len(raw_history)} messages from storage for thread {thread_id}")
            
            if not raw_history:
                logger.debug(f"No history found for thread {thread_id}")
                return [], None
            
            # Сжимаем историю если нужно
            try:
                compressed_history, compression_stats = await compress_history_if_needed(
                    raw_history,
                    threshold=self.compression_threshold,
                    active_model=active_model
                )
                logger.info(f"Compression complete: {len(compressed_history)} messages after compression")
            except Exception as e:
                logger.error(f"Compression failed: {e}, using raw history")
                compressed_history = raw_history
                compression_stats = None
            
            # Удаляем нерелевантный контекст
            try:
                pruned_history = prune_irrelevant_context(compressed_history, current_query)
                logger.info(f"Pruning complete: {len(pruned_history)} messages after pruning")
            except Exception as e:
                logger.error(f"Pruning failed: {e}, using compressed history")
                pruned_history = compressed_history
            
            return pruned_history, compression_stats
            
        except Exception as e:
            logger.error(f"Failed to load session history: {e}")
            fallback_history = await asyncio.to_thread(self.storage.get_messages, thread_id)
            if fallback_history:
                return fallback_history, None
            return [], None
   
   
    
    async def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        tokens: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Message:
        """
        Добавляет сообщение в диалог (делегирует в HistoryStorage).
        
        Args:
            thread_id: ID диалога
            role: Роль отправителя ('user', 'assistant', 'tool', 'system')
            content: Текст сообщения
            tokens: Количество токенов
            metadata: Дополнительные метаданные
            
        Returns:
            Созданный объект Message
        """
        logger.info(f"Adding {role} message to thread_id={thread_id}, tokens={tokens}")
        try:
            message = await asyncio.to_thread(
                self.storage.add_message, thread_id, role, content, tokens, metadata
            )
            logger.debug(f"Message added successfully: message_id={message.id}")
            return message
        except Exception as e:
            logger.error(f"Failed to add message: {e}", exc_info=True)
            raise

    async def create_thread(
        self,
        user_id: str,
        project_path: Optional[str] = None,
        title: str = "Новый диалог"
    ) -> Thread:
        """
        Создает новый диалог (делегирует в HistoryStorage).
        
        Args:
            user_id: ID пользователя
            project_path: Путь к проекту (опционально)
            title: Заголовок диалога
            
        Returns:
            Созданный объект Thread
        """
        logger.info(f"Creating new thread for user_id={user_id}, project_path={project_path}")
        try:
            thread = await asyncio.to_thread(
                self.storage.create_thread, user_id, project_path, title
            )
            logger.info(f"Thread created successfully: thread_id={thread.id}")
            return thread
        except Exception as e:
            logger.error(f"Failed to create thread: {e}", exc_info=True)
            raise
        
        
    async def update_thread_title(self, thread_id: str, new_title: str) -> bool:
        """
        Обновляет название диалога.
        
        Args:
            thread_id: ID диалога
            new_title: Новое название
            
        Returns:
            True если успешно обновлено
        """
        logger.info(f"Updating thread title: thread_id={thread_id}, new_title={new_title[:50]}")
        try:
            result = await asyncio.to_thread(
                self.storage.update_thread_title, thread_id, new_title
            )
            if result:
                logger.debug(f"Thread title updated successfully: thread_id={thread_id}")
            else:
                logger.warning(f"Thread not found for title update: thread_id={thread_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to update thread title: {e}", exc_info=True)
            return False        
        
        # === МЕТОДЫ ДЛЯ ТРЕЙСИНГА (МЫШЛЕНИЕ АГЕНТА) ===

    async def save_orchestration_trace(
        self, 
        thread_id: str,
        tool_calls: List[Any],  # List[ToolCall] from orchestrator
        message_index: Optional[int] = None
    ):
        """
        Сохраняет детали работы Оркестратора (мысли + вызовы инструментов).
        Если message_index не передан, берет индекс последнего сообщения + 1.
        """
        if not tool_calls:
            return

        # Если индекс не передан, считаем, что это будет следующее сообщение
        if message_index is None:
            thread_stats = await self.get_thread_stats(thread_id)
            message_index = thread_stats.get("message_count", 0) + 1

        steps = []
        for tc in tool_calls:
            # Пробуем перевести thinking, если он есть и на английском
            thinking_ru = ""
            thinking_en = getattr(tc, "thinking", "")
            
            if thinking_en and self._is_english(thinking_en):
                try:
                    thinking_ru = await self._translate_thinking(thinking_en)
                except Exception as e:
                    logger.warning(f"Thinking translation failed: {e}")
                    thinking_ru = thinking_en # Fallback
            else:
                thinking_ru = thinking_en

            steps.append(TraceStep(
                tool_name=tc.name,
                tool_args=tc.arguments,
                tool_output=tc.output,
                success=tc.success,
                timestamp=time.time(),
                thinking=thinking_en,
                thinking_ru=thinking_ru
            ))
        
        # Сохраняем в отдельную БД
        await asyncio.to_thread(
            self.trace_storage.save_trace,
            session_id=thread_id,
            message_index=message_index,
            steps=steps
        )

    async def get_message_trace(self, thread_id: str, message_index: int) -> List[Dict]:
        """Возвращает трейс (шаги) для конкретного сообщения"""
        return await asyncio.to_thread(
            self.trace_storage.get_trace, thread_id, message_index
        )

    # === ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ===

    async def _translate_thinking(self, text: str) -> str:
        """Быстрый перевод мыслей на русский"""
        # Используем быструю модель (Gemini Flash или что есть в конфиге)
        model_id = getattr(cfg, 'MODEL_GEMINI_2_FLASH', 'gpt-3.5-turbo')
        
        response = await call_llm(
            model=model_id,
            messages=[{
                "role": "user",
                "content": f"Translate this internal thought process to Russian (keep technical terms):\n\n{text}"
            }],
            temperature=0.3,
            max_tokens=500
        )
        return response  # call_llm возвращает str, не dict

    def _is_english(self, text: str) -> bool:
        """Простая проверка, что текст в основном на английском"""
        if not text:
            return False
        # Если есть русские буквы - считаем, что переводить не надо
        if bool(re.search('[а-яА-ЯёЁ]', text)):
            return False
        # Считаем процент латиницы
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        total_chars = len(text.strip())
        if total_chars == 0: return False
        return (english_chars / total_chars) > 0.5

    async def record_file_change(
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
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentChange:
        """
        Записывает информацию об изменении файла в Agent Mode.
        
        Args:
            thread_id: ID диалога
            session_id: ID сессии бэкапов
            file_path: Путь к файлу
            change_type: Тип изменения ('create', 'modify', 'delete')
            original_content: Оригинальное содержимое
            new_content: Новое содержимое
            backup_path: Путь к бэкапу
            lines_added: Строк добавлено
            lines_removed: Строк удалено
            validation_passed: Прошла ли валидация
            metadata: Дополнительные данные
            
        Returns:
            AgentChange объект
        """
        logger.info(
            f"Recording file change: thread={thread_id}, file={file_path}, "
            f"type={change_type}, +{lines_added}/-{lines_removed}"
        )
        
        try:
            change = await asyncio.to_thread(
                self.storage.add_agent_change,
                thread_id=thread_id,
                session_id=session_id,
                file_path=file_path,
                change_type=change_type,
                original_content=original_content,
                new_content=new_content,
                backup_path=backup_path,
                lines_added=lines_added,
                lines_removed=lines_removed,
                validation_passed=validation_passed,
                metadata=metadata
            )
            logger.debug(f"Change recorded: change_id={change.id}")
            return change
        except Exception as e:
            logger.error(f"Failed to record file change: {e}", exc_info=True)
            raise

    async def get_thread_file_changes(
        self,
        thread_id: str,
        only_applied: bool = False,
        limit: Optional[int] = None
    ) -> List[AgentChange]:
        """
        Получает историю изменений файлов для диалога.
        
        Args:
            thread_id: ID диалога
            only_applied: Только применённые изменения
            limit: Ограничение количества
            
        Returns:
            Список AgentChange
        """
        logger.debug(f"Getting file changes for thread_id={thread_id}")
        try:
            changes = await asyncio.to_thread(
                self.storage.get_thread_changes,
                thread_id=thread_id,
                only_applied=only_applied,
                limit=limit
            )
            return changes
        except Exception as e:
            logger.error(f"Failed to get thread changes: {e}", exc_info=True)
            return []

    async def get_file_history(self, file_path: str, limit: int = 10) -> List[AgentChange]:
        """
        Получает историю изменений конкретного файла.
        
        Args:
            file_path: Путь к файлу
            limit: Максимум записей
            
        Returns:
            Список AgentChange
        """
        logger.debug(f"Getting history for file: {file_path}")
        try:
            return await asyncio.to_thread(
                self.storage.get_file_change_history,
                file_path=file_path,
                limit=limit
            )
        except Exception as e:
            logger.error(f"Failed to get file history: {e}", exc_info=True)
            return []

    async def mark_changes_applied(
        self,
        change_ids: List[str],
        user_confirmed: bool = True
    ) -> int:
        """
        Отмечает изменения как применённые.
        
        Args:
            change_ids: Список ID изменений
            user_confirmed: Было ли подтверждение пользователя
            
        Returns:
            Количество обновлённых записей
        """
        count = 0
        for change_id in change_ids:
            try:
                success = await asyncio.to_thread(
                    self.storage.mark_change_applied,
                    change_id=change_id,
                    user_confirmed=user_confirmed
                )
                if success:
                    count += 1
            except Exception as e:
                logger.error(f"Failed to mark change {change_id} as applied: {e}")
        
        logger.info(f"Marked {count}/{len(change_ids)} changes as applied")
        return count

    async def mark_changes_rolled_back(self, change_ids: List[str]) -> int:
        """
        Отмечает изменения как откаченные.
        
        Args:
            change_ids: Список ID изменений
            
        Returns:
            Количество обновлённых записей
        """
        count = 0
        for change_id in change_ids:
            try:
                success = await asyncio.to_thread(
                    self.storage.mark_change_rolled_back,
                    change_id=change_id
                )
                if success:
                    count += 1
            except Exception as e:
                logger.error(f"Failed to mark change {change_id} as rolled back: {e}")
        
        logger.info(f"Marked {count}/{len(change_ids)} changes as rolled back")
        return count

    async def add_agent_mode_message(
        self,
        thread_id: str,
        changes: List[AgentChange],
        action: str = "applied"  # "applied", "rolled_back", "pending"
    ) -> Message:
        """
        Добавляет сообщение в историю беседы о действиях Agent Mode.
        
        Это позволяет пользователю видеть в истории чата,
        какие файлы были изменены.
        
        Args:
            thread_id: ID диалога
            changes: Список изменений
            action: Тип действия
            
        Returns:
            Message объект
        """
        if action == "applied":
            emoji = "✅"
            action_text = "Применены изменения"
        elif action == "rolled_back":
            emoji = "↩️"
            action_text = "Откачены изменения"
        else:
            emoji = "📝"
            action_text = "Подготовлены изменения"
        
        # Формируем читаемое сообщение
        lines = [f"{emoji} **{action_text}:**\n"]
        
        for change in changes:
            type_emoji = {"create": "🆕", "modify": "📝", "delete": "🗑️"}.get(change.change_type, "📄")
            stats = f"+{change.lines_added}/-{change.lines_removed}" if change.change_type != "delete" else ""
            lines.append(f"  {type_emoji} `{change.file_path}` {stats}")
        
        content = "\n".join(lines)
        
        # Метаданные для программного доступа
        metadata = {
            "type": "agent_change_summary",
            "action": action,
            "change_ids": [c.id for c in changes],
            "session_id": changes[0].session_id if changes else None,
            "files": [
                {
                    "path": c.file_path,
                    "type": c.change_type,
                    "lines_added": c.lines_added,
                    "lines_removed": c.lines_removed
                }
                for c in changes
            ]
        }
        
        return await self.add_message(
            thread_id=thread_id,
            role="assistant",
            content=content,
            tokens=0,  # Системное сообщение, не считаем токены
            metadata=metadata)
