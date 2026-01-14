# 🤖 AI Code Agent - Интеграционный Тест

**Дата выполнения:** 14.12.2025 23:06:04
**Проект:** `C:\Users\Admin\AI_Assistant_Pro`
**Время выполнения:** 157.32 сек.

---

## 📝 Запрос пользователя

> Создай модуль app/history/compressor.py, который выступает в роли "умного фильтра" для истории диалогов, генерируя оптимизированный контекст для LLM на лету без сохранения в БД ("Recompute"). Реализуй функцию compress_history_if_needed, которая проверяет порог токенов и, если он превышен, обрабатывает "старые" сообщения (кроме последних 3), делегируя сжатие отдельной быстрой нейросети (MODEL_GEMINI_2_FLASH) (через существующий app.llm.api_client): обычные ответы ассистента сжимай до 30% ("сохрани логику"), а выводы инструментов (role='tool') — до 20% ("оставь только факты, имена файлов и ошибки"), при этом строго запрещено трогать сообщения пользователя и блоки кода. Также реализуй функцию prune_irrelevant_context, которая анализирует текущий запрос пользователя, находит упомянутые файлы и заменяет старые выводы инструментов (role='tool') на заглушку [PRUNED], если они относятся к файлам, не упомянутым в текущем запросе. Все функции должны быть чистыми (возвращать новый список), использовать app.utils.token_counter, помечать сжатый текст префиксом [COMPRESSED] и корректно обрабатывать возможные сбои API сжимателя, возвращая оригинал.

---

## 🎯 Использованные модели

- **Orchestrator:** Claude Sonnet 4.5 (RouterAI)
- **Code Generator:** deepseek-chat

---

## 🔍 Анализ Оркестратора

Пользователь запрашивает создание нового модуля `app/history/compressor.py` для интеллектуального сжатия истории диалогов. Это компрессор истории, который работает в режиме "Recompute" (пересчитывает на лету без сохранения в БД).

**Ключевые требования:**

1. **Функция `compress_history_if_needed`:**
   - Проверяет порог токенов
   - Сжимает "старые" сообщения (все кроме последних 3)
   - Использует `MODEL_GEMINI_2_FLASH` через `app.llm.api_client`
   - Обычные ответы ассистента (`role='assistant'`) → 30% (промпт: "сохрани логику")
   - Выводы инструментов (`role='tool'`) → 20% (промпт: "факты, файлы, ошибки")
   - **Запрещено трогать:** сообщения пользователя (`role='user'`) и блоки кода
   - Помечает сжатый текст префиксом `[COMPRESSED]`

2. **Функция `prune_irrelevant_context`:**
   - Анализирует текущий запрос пользователя
   - Находит упомянутые файлы (регулярные выражения для путей)
   - Заменяет старые выводы `role='tool'` на `[PRUNED]`, если они относятся к файлам, не упомянутым в запросе

3. **Общие требования:**
   - Чистые функции (возвращают новый список, не мутируют)
   - Используют `app.utils.token_counter.TokenCounter`
   - Обрабатывают ошибки API (fallback к оригиналу)
   - Интеграция с существующими промптами из `app.llm.prompt_templates` (функции `format_compression_prompt`)

**Из контекста видно:**
- `app.llm.prompt_templates` содержит готовые функции для форматирования промптов сжатия
- `app.llm.api_client` имеет функцию `call_llm` для вызова LLM
- `app.utils.token_counter.TokenCounter` для подсчета токенов
- `app.history.storage.Message` — модель сообщения с полем `role`
- В `config.settings` есть константа `MODEL_GEMINI_2_FLASH`

Нужно создать новый файл с двумя основными функциями и вспомогательными утилитами.

---

## 📋 Инструкции для Code Generator

**Task:** Create a new history compression module with smart context filtering and on-the-fly compression using Gemini 2.0 Flash

**File:** CREATE: app/history/compressor.py

**Location:** New file structure

**Changes:**

1. Add imports at the top of the file:
   ```python
   import re
   import logging
   from typing import List, Dict, Any, Optional, Set
   from dataclasses import replace
   from app.history.storage import Message
   from app.utils.token_counter import TokenCounter
   from app.llm.api_client import call_llm
   from app.llm.prompt_templates import format_compression_prompt
   from config.settings import cfg
   ```

2. Create module-level logger and token counter instances:
   ```python
   logger = logging.getLogger(__name__)
   token_counter = TokenCounter()
   ```

3. Create helper function `_extract_file_paths(text: str) -> Set[str]`:
   - Use regex pattern to find file paths: `r'(?:^|[\s"\'\(])([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)(?:[\s"\'\)]|$)'`
   - Also match common path patterns like `app/`, `src/`, `config/`
   - Return a set of unique file paths found in text
   - Handle empty text gracefully (return empty set)

4. Create helper function `_contains_code_block(text: str) -> bool`:
   - Check if text contains markdown code blocks (triple backticks)
   - Check for common code patterns: `def `, `class `, `function `, `import `, `from `
   - Return True if any code patterns found, False otherwise

5. Create helper function `_compress_message_content(content: str, role: str) -> str`:
   - Parameters: `content` (message text), `role` (message role: 'assistant', 'tool', etc.)
   - If role is 'assistant', use `format_compression_prompt(content, "reasoning")` to get compression prompt
   - If role is 'tool', use `format_compression_prompt(content, "tool_result")` to get compression prompt
   - Call `await call_llm(model=cfg.MODEL_GEMINI_2_FLASH, messages=[{"role": "user", "content": compression_prompt}], temperature=0.3, max_tokens=4000)`
   - Extract compressed text from LLM response
   - If response starts with `[COMPRESSED]`, return as-is; otherwise prepend `[COMPRESSED] ` to the result
   - Wrap entire operation in try-except block: on any exception, log warning and return original content
   - Return compressed content

6. Create main function `compress_history_if_needed(messages: List[Message], max_tokens: int = 8000, keep_last_n: int = 3) -> List[Message]`:
   - Calculate total tokens using `token_counter.count(msg.content)` for all messages
   - If total tokens <= max_tokens, return messages unchanged (new list copy)
   - If total tokens > max_tokens:
     - Split messages into `old_messages` (all except last `keep_last_n`) and `recent_messages` (last `keep_last_n`)
     - Create new list `compressed_messages = []`
     - For each message in `old_messages`:
       - If `msg.role == 'user'`, append to compressed_messages unchanged
       - If `msg.role in ['assistant', 'tool']`:
         - Check if content contains code blocks using `_contains_code_block(msg.content)`
         - If contains code, append unchanged
         - If no code and role is 'assistant' or 'tool', call `_compress_message_content(msg.content, msg.role)` asynchronously
         - Create new Message with compressed content using `replace(msg, content=compressed_content, tokens=token_counter.count(compressed_content))`
         - Append to compressed_messages
       - For other roles, append unchanged
     - Return `compressed_messages + recent_messages`
   - Add docstring explaining parameters and return value

7. Create helper function `_file_mentioned_in_query(file_path: str, query: str) -> bool`:
   - Normalize both file_path and query to lowercase
   - Check if file_path (or its basename) appears in query
   - Return True if mentioned, False otherwise

8. Create function `prune_irrelevant_context(messages: List[Message], current_query: str) -> List[Message]`:
   - Extract mentioned files from current_query using `_extract_file_paths(current_query)`
   - Create new list `pruned_messages = []`
   - For each message in messages:
     - If `msg.role != 'tool'`, append to pruned_messages unchanged
     - If `msg.role == 'tool'`:
       - Extract file paths from message content using `_extract_file_paths(msg.content)`
       - Check if any of these files are in mentioned_files
       - If at least one file is mentioned, append message unchanged
       - If no files are mentioned, create new Message with content="[PRUNED: Irrelevant tool output]" and tokens=10
       - Use `replace(msg, content=..., tokens=...)` to create new Message
     - Append to pruned_messages
   - Return pruned_messages
   - Add docstring explaining the pruning logic

9. Make `compress_history_if_needed` async (change to `async def`) since it calls async `call_llm`

10. Make `_compress_message_content` async as well (change to `async def`)

11. Update step 6: when iterating old_messages, use `await _compress_message_content(...)` for async call

12. Add module-level docstring at the top explaining:
    - Purpose: On-the-fly history compression without DB persistence (Recompute mode)
    - Two main strategies: token-based compression and context pruning
    - Integration with Gemini 2.0 Flash for fast compression

**Why:** This module implements intelligent conversation history management to stay within LLM token limits. It uses two complementary strategies: (1) compressing old messages using a fast LLM while preserving user messages and code blocks, and (2) pruning irrelevant tool outputs based on current context. This is a "Recompute" approach that generates optimized context on-the-fly without modifying the database, ensuring conversations remain coherent while respecting token budgets.

---

## 💻 Сгенерированный код

**Файл:** `app/history/compressor.py`

```python
"""
Модуль для сжатия истории диалога на лету без сохранения в БД (режим Recompute).
Реализует две основные стратегии:
1. Токен-базированное сжатие: сжимает старые сообщения с помощью быстрой LLM (Gemini 2.0 Flash),
   сохраняя сообщения пользователя и блоки кода.
2. Обрезка контекста: удаляет нерелевантные результаты инструментов на основе текущего запроса.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Set
from dataclasses import replace
from app.history.storage import Message
from app.utils.token_counter import TokenCounter
from app.llm.api_client import call_llm
from app.llm.prompt_templates import format_compression_prompt
from config.settings import cfg

logger = logging.getLogger(__name__)
token_counter = TokenCounter()


def _extract_file_paths(text: str) -> Set[str]:
    """
    Извлекает пути к файлам из текста с помощью регулярных выражений.

    Args:
        text: Текст для анализа.

    Returns:
        Множество уникальных путей к файлам, найденных в тексте.
    """
    if not text:
        return set()

    # Регулярное выражение для поиска путей к файлам
    pattern = r'(?:^|[\s"\'\(])([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)(?:[\s"\'\)]|$)'
    # Дополнительные паттерны для общих путей
    common_path_pattern = r'(?:^|[\s"\'\(])((?:app|src|config|tests|scripts)/[a-zA-Z0-9_\-./\\]+)(?:[\s"\'\)]|$)'

    file_paths = set()
    # Ищем файлы с расширениями
    for match in re.finditer(pattern, text):
        file_paths.add(match.group(1))
    # Ищем общие пути
    for match in re.finditer(common_path_pattern, text):
        file_paths.add(match.group(1))

    return file_paths


def _contains_code_block(text: str) -> bool:
    """
    Проверяет, содержит ли текст блоки кода или кодоподобные конструкции.

    Args:
        text: Текст для проверки.

    Returns:
        True, если текст содержит блоки кода или кодоподобные конструкции, иначе False.
    """
    # Проверка на блоки кода в markdown (тройные обратные кавычки)
    if '
```

---

## 📖 Пояснения к коду

Этот модуль реализует интеллектуальное управление историей диалога для работы в рамках лимитов токенов LLM. Он использует две взаимодополняющие стратегии:

**1. Токен-базированное сжатие (`compress_history_if_needed`):**
- Подсчитывает общее количество токенов во всей истории диалога
- Если превышен лимит (`max_tokens`, по умолчанию 8000), начинает сжатие
- Сохраняет последние `keep_last_n` сообщений (по умолчанию 3) без изменений для сохранения контекста
- Старые сообщения обрабатываются по-разному:
  - Сообщения пользователя (`role='user'`) не сжимаются, чтобы сохранить исходные запросы
  - Сообщения с кодом (обнаруженные через `_contains_code_block`) не сжимаются, чтобы не повредить синтаксис
  - Сообщения ассистента и инструментов сжимаются с помощью Gemini 2.0 Flash
- Сжатие выполняется асинхронно для эффективности

**2. Обрезка нерелевантного контекста (`prune_irrelevant_context`):**
- Анализирует текущий запрос пользователя для извлечения упомянутых файлов
- Проходит по истории и проверяет результаты работы инструментов (`role='tool'`)
- Если в выводе инструмента содержатся файлы, не упомянутые в текущем запросе, они помечаются как нерелевантные
- Нерелевантные выводы заменяются кратким сообщением `[PRUNED: Irrelevant tool output]`
- Это помогает фокусировать контекст на текущей задаче

**Ключевые особенности:**
- **Режим Recompute**: Сжатие выполняется на лету без сохранения в базу данных, что обеспечивает гибкость
- **Сохранение важной информации**: Не трогает сообщения пользователя и блоки кода
- **Быстрое сжатие**: Использует Gemini 2.0 Flash для эффективной обработки
- **Асинхронная архитектура**: Не блокирует основной поток выполнения
- **Умное определение кода**: Использует регулярные выражения для обнаружения кодоподобных конструкций
- **Извлечение путей к файлам**: Находит упоминания файлов в тексте для контекстной обрезки

**Как использовать:**
1. Импортируйте модуль: `from app.history.compressor import compress_history_if_needed, prune_irrelevant_context`
2. Перед отправкой истории в LLM вызовите `compressed_history = await compress_history_if_needed(history)`
3. Для фокусировки контекста: `focused_history = prune_irrelevant_context(history, current_query)`

**Важные замечания:**
- Модуль требует корректной настройки `cfg.MODEL_GEMINI_2_FLASH` в конфигурации
- Функции `format_compression_prompt` и `call_llm` должны быть правильно реализованы в соответствующих модулях
- Токенизатор `TokenCounter` должен поддерживать модель, используемую для сжатия
- Все операции сжатия логируются для отладки и мониторинга

---

*Отчет сгенерирован автоматически: 2025-12-14T23:06:04.849122*