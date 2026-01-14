"""
Модуль для сжатия истории диалога на лету без сохранения в БД (режим Recompute).
Реализует две основные стратегии:
1. Токен-базированное сжатие: сжимает старые сообщения с помощью LLM,
   сохраняя сообщения пользователя и блоки кода.
2. Обрезка контекста: удаляет нерелевантные результаты инструментов на основе текущего запроса.
"""

import asyncio
import logging
import re
from typing import List, Optional
from dataclasses import dataclass
from app.llm.prompt_templates import format_compression_prompt
from app.history.storage import Message
from app.utils.token_counter import TokenCounter
from app.llm.api_client import call_llm
from config.settings import cfg

logger = logging.getLogger(__name__)

# Константы для управления сжатием
DEFAULT_THRESHOLD = 55000
COMPRESSION_MAX_TOKENS = 6000
KEEP_LAST_N = 3

@dataclass
class CompressionStats:
    """Статистика сжатия истории для отображения пользователю."""
    was_compressed: bool
    original_tokens: int
    compressed_tokens: int
    messages_before: int
    messages_after: int
    model_used: Optional[str]
    
    @property
    def tokens_saved(self) -> int:
        """Возвращает разницу токенов."""
        return self.original_tokens - self.compressed_tokens
    
    @property
    def compression_ratio(self) -> float:
        """Возвращает процент сжатия."""
        if self.original_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.original_tokens



def _contains_code_block(content: str) -> bool:
    """
    Безопасно проверяет, содержит ли текст блоки кода в формате Markdown (тройные обратные кавычки).
    Использует chr(96)*3 для избежания проблем с экранированием кавычек в коде.

    Args:
        content: Текст для проверки.

    Returns:
        True, если текст содержит блоки кода, иначе False.
    """
    triple_backticks = chr(96) * 3
    return triple_backticks in content


async def compress_history_if_needed(
    history: List[Message], 
    threshold: int = DEFAULT_THRESHOLD,
    active_model: str = None
) -> tuple[List[Message], CompressionStats]:
    """
    Сжимает историю сообщений, если общее количество токенов превышает пороговое значение.
    Сохраняет последние N сообщений неизменными, а старые сообщения сжимает с помощью LLM.

    Args:
        history: Список сообщений для обработки.
        threshold: Пороговое значение токенов для сжатия (по умолчанию 45000).
        active_model: Текущая модель оркестратора (влияет на лимит).

    Returns:
        Кортеж (сжатый список сообщений, статистика сжатия).
    """
    
    # ДИНАМИЧЕСКИЙ ПОРОГ В ЗАВИСИМОСТИ ОТ МОДЕЛИ
    current_threshold = threshold
    
    # Если явно передана Gemini 3.0 Pro, ставим огромный порог
    if active_model and active_model == cfg.MODEL_GEMINI_3_PRO:
        current_threshold = 200000 
        logger.info(f"Compressor: Using EXTENDED threshold for Gemini 3.0 Pro ({current_threshold} tokens)")
    elif active_model:
        # Для других моделей используем переданный порог
        logger.info(f"Compressor: Using threshold {current_threshold} tokens for model {active_model}")
    else:
        # Если модель не передана, используем более агрессивный порог
        current_threshold = 30000
        logger.info(f"Compressor: No model specified, using default threshold ({current_threshold} tokens)")
    
    token_counter = TokenCounter()
    total_tokens = sum(token_counter.count(msg.content) for msg in history)
    messages_before = len(history)
    
    logger.debug(f"Compressor: History has {total_tokens} tokens, threshold is {current_threshold}")
    
    # Используем current_threshold вместо исходного threshold
    if total_tokens <= current_threshold:
        logger.debug(f"Compressor: No compression needed ({total_tokens} <= {current_threshold})")
        stats = CompressionStats(
            was_compressed=False,
            original_tokens=total_tokens,
            compressed_tokens=total_tokens,
            messages_before=messages_before,
            messages_after=len(history),
            model_used=None
        )
        return history, stats
    
    logger.info(f"Compressor: Starting compression ({total_tokens} > {current_threshold})")
    
    compressed_history = []
    keep_last_n = KEEP_LAST_N
    
    # Разделяем историю на часть для сжатия и часть для сохранения
    to_compress = history[:-keep_last_n] if len(history) > keep_last_n else []
    keep_intact = history[-keep_last_n:] if len(history) > keep_last_n else history
    
    # Обрабатываем сообщения для сжатия
    for msg in to_compress:
        if msg.role == 'user':
            compressed_history.append(msg)
        elif msg.role == 'assistant':
            if _contains_code_block(msg.content):
                logger.debug(f"Skipping compression for assistant message {msg.id} due to code block")
                compressed_history.append(msg)
            else:
                compressed_msg = await _compress_message(msg, "reasoning")
                compressed_history.append(compressed_msg)
        elif msg.role == 'tool':
            compressed_msg = await _compress_message(msg, "tool_result")
            compressed_history.append(compressed_msg)
        else:
            compressed_history.append(msg)
    
    # Добавляем неизмененные последние сообщения
    compressed_history.extend(keep_intact)
    
    # Логируем статистику сжатия
    compressed_tokens = sum(token_counter.count(msg.content) for msg in compressed_history)
    logger.info(f"History compressed: {total_tokens} → {compressed_tokens} tokens "
                f"({compressed_tokens/total_tokens*100:.1f}% of original)")
    
    stats = CompressionStats(
        was_compressed=True,
        original_tokens=total_tokens,
        compressed_tokens=compressed_tokens,
        messages_before=messages_before,
        messages_after=len(compressed_history),
        model_used=cfg.AGENT_MODELS.get("history_compressor", "deepseek/deepseek-chat")
    )
    return compressed_history, stats


async def _compress_message(msg: Message, content_type: str) -> Message:
    """
    Сжимает отдельное сообщение с помощью LLM, используя шаблоны промптов.
    Не сжимает уже сжатые сообщения или сообщения с блоками кода.

    Args:
        msg: Сообщение для сжатия.
        content_type: Тип контента ('reasoning' или 'tool_result').

    Returns:
        Сжатое сообщение или оригинальное сообщение в случае ошибки.
    """
    # Проверяем, не сжато ли сообщение уже
    if msg.content.startswith("[COMPRESSED]"):
        return msg
    
    # Не сжимаем сообщения с блоками кода
    if _contains_code_block(msg.content):
        return msg
    
    try:
        # Форматируем промпт для сжатия
        prompt = format_compression_prompt(msg.content, content_type)
        logger.debug(f"Compression prompt preview: {prompt[:200]}...")
        
        # Получаем модель для сжатия
        model = cfg.AGENT_MODELS.get("history_compressor", "deepseek/deepseek-chat")
        
        # Вызываем LLM для сжатия
        compressed = await call_llm(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=COMPRESSION_MAX_TOKENS
        )
        
        # Создаем новое сжатое сообщение
        compressed_content = "[COMPRESSED] " + compressed.strip()
        
        # Копируем все поля из оригинального сообщения
        compressed_msg = Message(
            id=msg.id,
            thread_id=msg.thread_id,
            role=msg.role,
            content=compressed_content,
            tokens=TokenCounter().count(compressed_content),
            metadata=msg.metadata,
            created_at=msg.created_at
        )
        
        return compressed_msg
        
    except Exception as e:
        logger.warning(f"Compression failed for {msg.role} message: {e}")
        return msg


def prune_irrelevant_context(history: List[Message], current_query: str) -> List[Message]:
    """
    Удаляет нерелевантные результаты инструментов из истории на основе файлов,
    упомянутых в текущем запросе.

    Args:
        history: История сообщений для обработки.
        current_query: Текущий запрос пользователя.

    Returns:
        История с удаленными нерелевантными результатами инструментов.
    """
    # Улучшенный regex для поиска файлов
    file_pattern = r'[\w/\-]+\.(?:py|js|ts|sql|json|md|txt|html|css|go|java|cpp|c|rs|rb)|\b(?:src|lib|app|tests|config)/[\w/.-]+'
    
    # Извлекаем упомянутые файлы из текущего запроса
    mentioned_files = set(re.findall(file_pattern, current_query))
    
    # Если файлы не упомянуты, возвращаем историю без изменений
    if not mentioned_files:
        return history
    
    pruned_history = []
    counter = TokenCounter()
    pruned_tokens = 0
    
    for msg in history:
        # Skip non-tool messages
        if msg.role != "tool":
            pruned_history.append(msg)
            continue
        
        # Skip already processed messages
        if msg.content.startswith("[COMPRESSED]") or msg.content.startswith("[PRUNED]"):
            pruned_history.append(msg)
            continue
        
        # Extract file names from tool content
        content_files = set(re.findall(r'(?:File:|file_path:|filepath:)\s*([^\s\n]+)', msg.content, re.IGNORECASE))
        
        # CASE 1: No files mentioned in tool output → PRUNE (not relevant anymore)
        if not content_files:
            original_tokens = counter.count(msg.content)
            msg.content = "[PRUNED: Tool output was analyzed earlier]"
            msg.tokens = counter.count(msg.content)
            pruned_tokens += original_tokens - msg.tokens
            pruned_history.append(msg)
            logger.debug(f"Pruned tool message (no files): saved {original_tokens - msg.tokens} tokens")
            continue
        
        # CASE 2: Files mentioned, but none overlap with current query → PRUNE
        if not content_files.intersection(mentioned_files):
            original_tokens = counter.count(msg.content)
            file_list = ", ".join(sorted(content_files)[:3])  # Show first 3 files
            if len(content_files) > 3:
                file_list += f" and {len(content_files) - 3} more"
            msg.content = f"[PRUNED: Analyzed {file_list}]"
            msg.tokens = counter.count(msg.content)
            pruned_tokens += original_tokens - msg.tokens
            pruned_history.append(msg)
            logger.debug(f"Pruned tool message (irrelevant files): saved {original_tokens - msg.tokens} tokens")
            continue
        
        # CASE 3: Files overlap with current query → KEEP
        pruned_history.append(msg)
    
    logger.info(f"Pruned {pruned_tokens} tokens from irrelevant tool outputs")
    return pruned_history
