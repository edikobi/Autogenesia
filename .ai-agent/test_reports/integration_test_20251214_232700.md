# 🤖 AI Code Agent - Интеграционный Тест

**Дата выполнения:** 14.12.2025 23:27:00
**Проект:** `C:\Users\Admin\AI_Assistant_Pro`
**Время выполнения:** 130.72 сек.

---

## 📝 Запрос пользователя

> # ЗАДАЧА: Исправить app/history/compressor.py (Final Integration)Мы обнаружили, что в проекте уже есть готовые шаблоны промптов в `app/llm/prompt_templates.py`.Тебе нужно переписать `compressor.py`, используя их, вместо того чтобы писать промпты вручную. Это решит проблему с кавычками и дублированием кода.**Требования к реализации:**1.  **Импорты:**    *   `from app.llm.prompt_templates import format_compression_prompt`    *   `from app.history.storage import Message`    *   `from app.utils.token_counter import TokenCounter`    *   `from app.llm.api_client import call_llm`    *   `from config.settings import cfg`    *   Стандартные: `asyncio`, `logging`, `typing`, `dataclasses`.2.  **Функция `compress_history_if_needed(history, threshold)`:**    *   Логика та же: считаем токены, оставляем последние 3 сообщения без изменений.    *   Для сжатия используй `await call_llm(...)`.    *   **ВАЖНО:** Для формирования prompt'а используй импортированную функцию:        *   Для `role='assistant'`: `prompt = format_compression_prompt(msg.content, "reasoning")`        *   Для `role='tool'`: `prompt = format_compression_prompt(msg.content, "tool_result")`    *   Обрабатывай ошибки (try/except), возвращая оригинал при сбое.    *   Добавляй префикс `[COMPRESSED]`.3.  **Функция `prune_irrelevant_context(history, current_query)`:**    *   Реализуй логику поиска файлов в `current_query` (используй регулярки).    *   Если файлы найдены, проходи по `role='tool'` и заменяй контент на `[PRUNED: ...]`, если в нем нет упоминаний этих файлов.4.  **Безопасность (Safe Mode):**    *   В функции проверки блоков кода (`_contains_code_block`) **НЕ ИСПОЛЬЗУЙ** тройные кавычки в коде явно.    *   Используй `chr(96)*3` для проверки наличия маркдаун-блоков кода.**Выдай ПОЛНЫЙ код файла `compressor.py`.**

---

## 🎯 Использованные модели

- **Orchestrator:** Claude Sonnet 4.5 (RouterAI)
- **Code Generator:** deepseek-chat

---

## 🔍 Анализ Оркестратора

The user wants to refactor `app/history/compressor.py` to use existing prompt templates from `app/llm/prompt_templates.py` instead of manually writing prompts. This will solve issues with quotes and code duplication.

Looking at the selected chunks, I can see:
- `format_compression_prompt` function exists in `app/llm/prompt_templates.py` and accepts `content` and `content_type` ("tool_result" or defaults to "reasoning")
- `call_llm` function is available in `app/llm/api_client.py`
- `Message` class is defined in `app/history/storage.py`
- `TokenCounter` class exists in `app/utils/token_counter.py`

The current implementation needs to be completely rewritten to:
1. Use the template functions instead of manual prompts
2. Implement proper history compression with token counting
3. Implement context pruning based on file mentions
4. Use safe string handling for code block detection

---

## 📋 Инструкции для Code Generator

**Task:** Completely rewrite the history compression module to use existing prompt templates and implement proper compression logic

**File:** `app/history/compressor.py`

**Location:** Replace entire file content

**Changes:**

1. **Add all required imports at the top of the file:**
   - `import asyncio`
   - `import logging`
   - `import re`
   - `from typing import List, Optional`
   - `from dataclasses import dataclass`
   - `from app.llm.prompt_templates import format_compression_prompt`
   - `from app.history.storage import Message`
   - `from app.utils.token_counter import TokenCounter`
   - `from app.llm.api_client import call_llm`
   - `from config.settings import cfg`

2. **Create logger instance:**
   - Add: `logger = logging.getLogger(__name__)`

3. **Create helper function `_contains_code_block(content: str) -> bool`:**
   - Use `chr(96)*3` to safely check for markdown code blocks (triple backticks)
   - Return `True` if `chr(96)*3` is found in content, `False` otherwise
   - This avoids using literal triple quotes in the code

4. **Implement main function `async def compress_history_if_needed(history: List[Message], threshold: int = 8000) -> List[Message]`:**
   - Initialize `TokenCounter()` instance
   - Calculate total tokens: sum of `token_counter.count(msg.content)` for all messages in history
   - If total tokens <= threshold, return history unchanged
   - Create new list `compressed_history = []`
   - Keep last 3 messages unchanged: `keep_last_n = 3`
   - Split history into: `to_compress = history[:-keep_last_n]` and `keep_intact = history[-keep_last_n:]`
   - For each message in `to_compress`:
     - If `msg.role == 'user'`: append to compressed_history unchanged
     - If `msg.role == 'assistant'`: call `await _compress_message(msg, "reasoning")`
     - If `msg.role == 'tool'`: call `await _compress_message(msg, "tool_result")`
     - Otherwise: append unchanged
   - Extend compressed_history with keep_intact messages
   - Log compression stats: original vs compressed token count
   - Return compressed_history

5. **Implement helper function `async def _compress_message(msg: Message, content_type: str) -> Message`:**
   - Check if message already compressed: if `msg.content.startswith("[COMPRESSED]")`, return msg unchanged
   - Check if contains code blocks: if `_contains_code_block(msg.content)`, return msg unchanged (don't compress code)
   - Use try-except block for compression:
     - In try block:
       - Get prompt: `prompt = format_compression_prompt(msg.content, content_type)`
       - Get model: `model = cfg.AGENT_MODELS.get("history_compressor", "deepseek/deepseek-chat")`
       - Call LLM: `compressed = await call_llm(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.0, max_tokens=2000)`
       - Create new Message with compressed content: `content="[COMPRESSED] " + compressed.strip()`
       - Copy all other fields from original message (id, thread_id, role, tokens, metadata, created_at)
       - Update tokens field with new token count
       - Return new compressed message
     - In except block:
       - Log warning with exception details: `logger.warning(f"Compression failed for {msg.role} message: {e}")`
       - Return original message unchanged

6. **Implement function `def prune_irrelevant_context(history: List[Message], current_query: str) -> List[Message]`:**
   - Extract file mentions from current_query using regex: `file_pattern = r'(?:^|[\s,\'"(])([a-zA-Z0-9_/\\.-]+\.(?:py|js|ts|sql|json|md|txt|html|css))(?:[\s,\'")\]]|$)'`
   - Find all matches: `mentioned_files = set(re.findall(file_pattern, current_query))`
   - If no files mentioned, return history unchanged
   - Create `pruned_history = []`
   - For each message in history:
     - If `msg.role != 'tool'`: append unchanged
     - If `msg.role == 'tool'`: check if any mentioned_file is in msg.content
       - If yes: append unchanged
       - If no: create new Message with `content=f"[PRUNED: Tool output not relevant to {', '.join(mentioned_files)}]"`
       - Copy all other fields, update tokens count
   - Return pruned_history

7. **Add docstrings to all functions:**
   - For `compress_history_if_needed`: Explain it compresses old messages using LLM while keeping last N intact
   - For `_compress_message`: Explain it uses prompt templates to compress individual messages
   - For `prune_irrelevant_context`: Explain it removes tool outputs not related to files mentioned in query
   - For `_contains_code_block`: Explain it safely checks for markdown code blocks without using literal triple quotes

**Why:** This refactoring eliminates code duplication by using centralized prompt templates, improves maintainability, fixes quote escaping issues, and implements proper history management with compression and pruning logic to reduce token usage while preserving important context.

---

## 💻 Сгенерированный код

### Блок 1

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


async def compress_history_if_needed(history: List[Message], threshold: int = 8000) -> List[Message]:
    """
    Сжимает историю сообщений, если общее количество токенов превышает пороговое значение.
    Сохраняет последние N сообщений неизменными, а старые сообщения сжимает с помощью LLM.

    Args:
        history: Список сообщений для обработки.
        threshold: Пороговое значение токенов для сжатия (по умолчанию 8000).

    Returns:
        Сжатый список сообщений или оригинальный список, если сжатие не требуется.
    """
    token_counter = TokenCounter()
    total_tokens = sum(token_counter.count(msg.content) for msg in history)
    
    if total_tokens <= threshold:
        return history
    
    compressed_history = []
    keep_last_n = 3
    
    # Разделяем историю на часть для сжатия и часть для сохранения
    to_compress = history[:-keep_last_n] if len(history) > keep_last_n else []
    keep_intact = history[-keep_last_n:] if len(history) > keep_last_n else history
    
    # Обрабатываем сообщения для сжатия
    for msg in to_compress:
        if msg.role == 'user':
            compressed_history.append(msg)
        elif msg.role == 'assistant':
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
        
        # Получаем модель для сжатия
        model = cfg.AGENT_MODELS.get("history_compressor", "deepseek/deepseek-chat")
        
        # Вызываем LLM для сжатия
        compressed = await call_llm(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2000
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
    # Извлекаем упомянутые файлы из текущего запроса
    file_pattern = r'(?:^|[\s,\'"(])([a-zA-Z0-9_/\\.-]+\.(?:py|js|ts|sql|json|md|txt|html|css))(?:[\s,\'")\]]|$)'
    mentioned_files = set(re.findall(file_pattern, current_query))
    
    # Если файлы не упомянуты, возвращаем историю без изменений
    if not mentioned_files:
        return history
    
    pruned_history = []
    
    for msg in history:
        if msg.role != 'tool':
            pruned_history.append(msg)
        else:
            # Проверяем, содержит ли результат инструмента упомянутые файлы
            if any(mentioned_file in msg.content for mentioned_file in mentioned_files):
                pruned_history.append(msg)
            else:
                # Заменяем нерелевантный результат инструмента
                pruned_content = f"[PRUNED: Tool output not relevant to {', '.join(mentioned_files)}]"
                pruned_msg = Message(
                    id=msg.id,
                    thread_id=msg.thread_id,
                    role=msg.role,
                    content=pruned_content,
                    tokens=TokenCounter().count(pruned_content),
                    metadata=msg.metadata,
                    created_at=msg.created_at
                )
                pruned_history.append(pruned_msg)
    
    return pruned_history
```

### Блок 2

**Файл:** `app/history/compressor.py`

```python
compressed_history = await compress_history_if_needed(history, threshold=8000)
```

### Блок 3

**Файл:** `app/history/compressor.py`

```python
pruned_history = prune_irrelevant_context(history, current_query)
```

---

## 📖 Пояснения к коду

Этот код полностью переписывает модуль сжатия истории диалога, реализуя улучшенную архитектуру с использованием существующих шаблонов промптов и правильной логики сжатия.

**Что делает код:**

1. **Импорты и настройка**:
   - Добавлены все необходимые импорты, включая `asyncio` для асинхронных операций
   - Создан логгер для отслеживания операций сжатия

2. **Вспомогательная функция `_contains_code_block`**:
   - Безопасно проверяет наличие блоков кода в формате Markdown (тройные обратные кавычки)
   - Использует `chr(96)*3` вместо литеральных кавычек для избежания проблем с экранированием

3. **Основная функция `compress_history_if_needed`**:
   - Рассчитывает общее количество токенов в истории
   - Если токены не превышают порог (по умолчанию 8000), возвращает историю без изменений
   - Сохраняет последние 3 сообщения неизменными для поддержания контекста
   - Разделяет историю на часть для сжатия и часть для сохранения
   - Обрабатывает разные типы сообщений:
     - Сообщения пользователя остаются неизменными
     - Сообщения ассистента сжимаются как "reasoning"
     - Результаты инструментов сжимаются как "tool_result"
   - Логирует статистику сжатия (исходные и сжатые токены)

4. **Функция `_compress_message`**:
   - Проверяет, не сжато ли сообщение уже (по префиксу "[COMPRESSED]")
   - Не сжимает сообщения с блоками кода для сохранения важной информации
   - Использует централизованные шаблоны промптов через `format_compression_prompt`
   - Вызывает LLM для сжатия контента с низкой температурой (0.0) для детерминированных результатов
   - Обрабатывает ошибки сжатия, возвращая оригинальное сообщение в случае проблем

5. **Функция `prune_irrelevant_context`**:
   - Извлекает упомянутые файлы из текущего запроса пользователя с помощью регулярного выражения
   - Удаляет нерелевантные результаты инструментов, которые не содержат упомянутых файлов
   - Заменяет удаленный контент информативным сообщением о причине удаления
   - Сохраняет все другие типы сообщений без изменений

**Почему этот подход работает:**

1. **Использование существующих промптов**: Устраняет дублирование кода и обеспечивает согласованность форматов сжатия
2. **Асинхронная архитектура**: Позволяет эффективно обрабатывать сжатие без блокировки основного потока
3. **Сохранение контекста**: Последние сообщения остаются неизменными для поддержания актуального диалога
4. **Безопасная обработка кода**: Блоки кода не сжимаются, что предотвращает потерю важной технической информации
5. **Адаптивная обрезка**: Удаляет только нерелевантные результаты инструментов на основе текущего запроса

**Как использовать:**

1. Для сжатия истории при превышении лимита токенов:
   

2. Для удаления нерелевантного контекста:
   

**Важные замечания:**

- Модуль использует асинхронные вызовы LLM, поэтому функции должны вызываться с `await`
- Сжатые сообщения помечаются префиксом "[COMPRESSED]" для предотвращения повторного сжатия
- Порог сжатия (8000 токенов) можно настраивать в зависимости от требований модели
- Функция обрезки работает только с результатами инструментов, сохраняя все другие типы сообщений
- В случае ошибок сжатия модуль возвращает оригинальные сообщения, обеспечивая отказоустойчивость

---

*Отчет сгенерирован автоматически: 2025-12-14T23:27:00.302726*