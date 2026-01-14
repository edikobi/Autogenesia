# 🤖 AI Code Agent - Интеграционный Тест

**Дата выполнения:** 14.12.2025 23:58:45
**Проект:** `C:\Users\Admin\AI_Assistant_Pro`
**Время выполнения:** 133.87 сек.

---

## 📝 Запрос пользователя

> # ЗАДАЧА: Финальная полировка app/history/compressor.pyТы почти справился, но в логике функции `prune_irrelevant_context` обнаружена логическая ошибка, которую нужно исправить.**Что нужно изменить:**1.  **Исправить `prune_irrelevant_context`:**    *   Текущая ошибка: Если в tool-сообщении нет имен файлов (`content_files` пусто), оно не удаляется.    *   Новая логика:        *   Если сообщение уже начинается с `[COMPRESSED]` или `[PRUNED]`, пропускаем его (не трогаем).        *   Если `content_files` ЕСТЬ, но не пересекается с `mentioned_files` → ПРУНИТЬ (`[PRUNED: {file}...]`).        *   Если `content_files` ПУСТО (файлов нет вообще) → ТОЖЕ ПРУНИТЬ (`[PRUNED: Tool output was analyzed earlier]`).        *   Иначе (файлы есть и они нужны) → ОСТАВИТЬ.2.  **Вынести магические числа в константы:**    *   В начале файла определи:        ```        DEFAULT_THRESHOLD = 30000        COMPRESSION_MAX_TOKENS = 2000        KEEP_LAST_N = 3        ```    *   Используй их в коде.3.  **Логирование:**    *   В `_compress_message` добавь `logger.debug` с первыми 200 символами промпта (для отладки).**Промпты в `prompt_templates.py` менять НЕ НУЖНО (они уже корректные).**Выдай **финальную, идеальную версию** файла `compressor.py`.

---

## 🎯 Использованные модели

- **Orchestrator:** Claude Sonnet 4.5 (RouterAI)
- **Code Generator:** deepseek-chat

---

## 🔍 Анализ Оркестратора

Проблема находится в функции `prune_irrelevant_context` в файле `app/history/compressor.py`. Текущая логика имеет **критическую ошибку**: если в tool-сообщении нет упоминаний файлов (`content_files` пусто), такое сообщение не удаляется и остается в истории, хотя оно уже не релевантно для текущего запроса.

**Корневая причина:**
Условие проверки построено так, что сообщения без файлов проходят через все проверки и остаются неизмененными. Это приводит к накоплению старых tool-результатов в контексте.

**Что нужно исправить:**

1. **Логика прунинга** - добавить явную обработку случая, когда `content_files` пусто
2. **Магические числа** - вынести хардкоженные значения (30000, 2000, 3) в константы
3. **Отладка** - добавить логирование промпта для LLM в `_compress_message`

**Дополнительно обнаружено:**
- Отсутствует проверка на уже обработанные сообщения с префиксами `[COMPRESSED]` и `[PRUNED]`
- Нет четкого разделения между "файлы есть, но не нужны" и "файлов вообще нет"

---

## 📋 Инструкции для Code Generator

**Task:** Fix logic error in prune_irrelevant_context, extract magic numbers to constants, add debug logging

**File:** app/history/compressor.py

**Location:** Entire file - multiple modifications across the module

**Changes:**

1. **Add constants at module level** (after imports, before any functions):
   - Add: `DEFAULT_THRESHOLD = 30000`
   - Add: `COMPRESSION_MAX_TOKENS = 2000`
   - Add: `KEEP_LAST_N = 3`

2. **In function `_contains_code_block`** (lines 23-35):
   - No changes needed (keep as is)

3. **In function `compress_history_if_needed`** (lines 38-88):
   - Replace hardcoded `30000` with `DEFAULT_THRESHOLD` constant
   - Replace hardcoded `3` with `KEEP_LAST_N` constant
   - Keep all other logic unchanged

4. **In function `_compress_message`** (lines 91-144):
   - Replace hardcoded `2000` with `COMPRESSION_MAX_TOKENS` constant
   - After line where `prompt` variable is created (around line 108-110), add debug logging:
     ```python
     logger.debug(f"Compression prompt preview: {prompt[:200]}...")
     ```
   - Keep all other logic unchanged

5. **In function `prune_irrelevant_context`** (lines 147-198):
   - **COMPLETE REWRITE of the main loop logic** (starting from `for msg in history:` around line 167):
   
   Replace the current loop body with this logic:
   
   ```python
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
   ```

6. **Update the final log message** (around line 195):
   - Change to: `logger.info(f"Pruned {pruned_tokens} tokens from irrelevant tool outputs")`

7. **Verify all type hints and imports remain intact**:
   - Ensure `from app.utils.token_counter import TokenCounter` is present
   - Ensure `from app.history.storage import Message` is present
   - Ensure `import re` is present
   - Ensure `import logging` is present

**Why:** 
- **Bug fix (critical)**: Current logic fails to prune tool messages without file references, causing context bloat
- **Code quality**: Magic numbers make code hard to maintain and tune
- **Debugging**: Logging compression prompts helps diagnose LLM compression issues
- **Correctness**: Explicit handling of all three cases (no files, wrong files, right files) prevents edge case bugs

---

## 💻 Сгенерированный код

**Файл:** `app/history/compressor.py`

```python
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
DEFAULT_THRESHOLD = 30000
COMPRESSION_MAX_TOKENS = 2000
KEEP_LAST_N = 3


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


async def compress_history_if_needed(history: List[Message], threshold: int = DEFAULT_THRESHOLD) -> List[Message]:
    """
    Сжимает историю сообщений, если общее количество токенов превышает пороговое значение.
    Сохраняет последние N сообщений неизменными, а старые сообщения сжимает с помощью LLM.

    Args:
        history: Список сообщений для обработки.
        threshold: Пороговое значение токенов для сжатия (по умолчанию 30000).

    Returns:
        Сжатый список сообщений или оригинальный список, если сжатие не требуется.
    """
    token_counter = TokenCounter()
    total_tokens = sum(token_counter.count(msg.content) for msg in history)
    
    if total_tokens <= threshold:
        return history
    
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
    
    return compressed_history


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
```

---

## 📖 Пояснения к коду

Этот код вносит несколько важных улучшений в модуль сжатия истории диалога:

**1. Константы вместо "магических чисел":**
- `DEFAULT_THRESHOLD = 30000` - порог токенов для запуска сжатия
- `COMPRESSION_MAX_TOKENS = 2000` - максимальное количество токенов для сжатого ответа LLM
- `KEEP_LAST_N = 3` - количество последних сообщений, которые остаются неизменными
Эти константы упрощают настройку и делают код более читаемым.

**2. Исправление критической ошибки в `prune_irrelevant_context`:**
Старая логика имела серьезный недостаток - она пропускала tool-сообщения без упоминания файлов, что приводило к раздуванию контекста. Новая реализация обрабатывает три четких случая:
- **Случай 1**: Tool-сообщение без упоминания файлов → помечается как "[PRUNED: Tool output was analyzed earlier]"
- **Случай 2**: Tool-сообщение с файлами, но ни один не совпадает с текущим запросом → помечается как "[PRUNED: Analyzed file1, file2...]"
- **Случай 3**: Tool-сообщение с файлами, которые пересекаются с текущим запросом → сохраняется без изменений

**3. Добавление отладочного логирования:**
- В `_compress_message` добавлен лог с превью промпта для сжатия (первые 200 символов)
- В `prune_irrelevant_context` добавлены детальные логи о сохраненных токенах для каждого случая обрезки
- Финальное сообщение логирования теперь точно отражает количество удаленных токенов

**4. Улучшенное извлечение файлов:**
Используется более точный regex `(?:File:|file_path:|filepath:)\s*([^\s\n]+)` для поиска упоминаний файлов в tool-сообщениях, который ищет файлы после ключевых префиксов.

**5. Сохранение импортов и типов:**
Все необходимые импорты (`TokenCounter`, `Message`, `re`, `logging`) остаются на месте, что гарантирует корректную работу модуля.

**Как это работает:**
- При превышении порога `DEFAULT_THRESHOLD` токенов запускается сжатие истории
- Последние `KEEP_LAST_N` сообщений сохраняются без изменений
- Tool-сообщения анализируются на релевантность текущему запросу
- Нерелевантные tool-сообщения заменяются кратким описанием, экономя токены
- Все изменения логируются для отладки и мониторинга

**Важное замечание:** Новая логика `prune_irrelevant_context` более агрессивно удаляет нерелевантный контекст, что может значительно сократить использование токенов в длинных диалогах с множеством tool-вызовов.

---

*Отчет сгенерирован автоматически: 2025-12-14T23:58:45.513945*