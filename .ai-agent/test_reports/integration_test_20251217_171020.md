# 🤖 AI Code Agent - Интеграционный Тест

**Дата выполнения:** 17.12.2025 17:10:20
**Проект:** `C:\Users\Admin\AI_Assistant_Pro`
**Время выполнения:** 200.10 сек.

---

## 📝 Запрос пользователя

> Проанализируй файл general_web_search.py на предмет неправильной асинхронности, в случае, если проблема есть, напиши код всех методов и функций, где проблема

---

## 🎯 Использованные модели

- **Orchestrator:** Claude Opus 4.5 (RouterAI)
- **Code Generator:** deepseek-chat

---

## 🔍 Анализ Оркестратора

[Анализ не выполнен]

---

## 📋 Инструкции для Code Generator

**Task:** Исправить проблему с асинхронностью в функции `general_web_search_tool`, которая может вызывать конфликты при работе в уже существующем async-контексте.

**File:** `app/tools/general_web_search.py`

**Location:** Функция `general_web_search_tool` (строки 39-73)

**Changes:**

1. **Добавить вспомогательную функцию `_run_async` после импортов (после строки 13, перед константами на строке 15)**:
   
   Создать функцию `_run_async(coro)`, которая безопасно запускает корутину:
   - Сначала пытается получить текущий event loop через `asyncio.get_running_loop()`
   - Если loop уже запущен (`RuntimeError` не возникает), использует `concurrent.futures.ThreadPoolExecutor` для запуска корутины в отдельном потоке с новым event loop
   - Если loop не запущен, использует `asyncio.run(coro)` (Python 3.7+) — это более чистый способ, чем ручное создание/закрытие loop

   Код функции:
   ```python
   def _run_async(coro):
       """
       Безопасно запускает корутину из синхронного контекста.
       Работает корректно как из обычного кода, так и из уже запущенного event loop.
       """
       try:
           loop = asyncio.get_running_loop()
       except RuntimeError:
           # Нет запущенного loop — можно использовать asyncio.run()
           return asyncio.run(coro)
       
       # Если loop уже запущен, выполняем в отдельном потоке
       with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
           future = executor.submit(asyncio.run, coro)
           return future.result()
   ```

2. **Заменить содержимое функции `general_web_search_tool` (строки 39-73)**:
   
   Заменить блок try с ручным управлением event loop на вызов `_run_async`:
   
   ```python
   def general_web_search_tool(query: str, max_results: int = 10, time_limit: str = "w", region: str = "ru-ru") -> str:
       """
       Выполняет поиск в интернете для общих, юридических и новостных запросов.
       
       Args:
           query: Поисковый запрос.
           max_results: Максимум результатов (до 10).
           time_limit: Фильтр по времени ('d' - день, 'w' - неделя, 'm' - месяц, 'y' - год, None - все время).
           region: Регион поиска (по умолчанию 'ru-ru' для РФ).
       """
       if not query:
           return format_error("Query is required")

       max_results = min(max_results, 10)
       
       try:
           result = _run_async(async_general_web_search(query, max_results, time_limit, region))
               
           if not result.success:
               return format_error(result.error or "Search failed")
               
           if not result.pages:
               return format_no_results(query)
               
           return format_results_xml(result)
           
       except Exception as e:
           logger.error(f"General web search error: {e}")
           return format_error(f"Search failed: {e}")
   ```

**Why:** Текущая реализация создаёт новый event loop и устанавливает его глобально через `asyncio.set_event_loop()`, что вызывает конфликты при вызове из уже работающего async-контекста (например, из FastAPI, aiohttp, или async-функций оркестратора). Новая реализация корректно определяет контекст выполнения и использует подходящий метод запуска корутины.

---

## 💻 Сгенерированный код

**Файл:** `app/tools/general_web_search.py`

```python
from __future__ import annotations
import asyncio
import concurrent.futures
import logging
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse, unquote
import httpx
from app.utils.token_counter import TokenCounter
from config.settings import cfg

logger = logging.getLogger(__name__)

def _run_async(coro):
    """
    Безопасно запускает корутину из синхронного контекста.
    Работает корректно как из обычного кода, так и из уже запущенного event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Нет запущенного loop — можно использовать asyncio.run()
        return asyncio.run(coro)
    
    # Если loop уже запущен, выполняем в отдельном потоке
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()

# Используем лимит из settings или дефолтный
MAX_TOTAL_TOKENS = 25000  
REQUEST_TIMEOUT = 15.0
MAX_CONTENT_PER_PAGE = 4000 # Чуть меньше для статей, чтобы вместить больше источников

@dataclass
class GeneralWebPage:
    url: str
    title: str
    snippet: str
    content: str = ""
    tokens: int = 0
    relevance_score: float = 0.0
    error: Optional[str] = None
    published_date: Optional[str] = None # Полезно для новостей

@dataclass
class GeneralWebSearchResult:
    success: bool
    query: str
    pages: List[GeneralWebPage] = field(default_factory=list)
    total_tokens: int = 0
    error: Optional[str] = None

def general_web_search_tool(query: str, max_results: int = 10, time_limit: str = "w", region: str = "ru-ru") -> str:
    """
    Выполняет поиск в интернете для общих, юридических и новостных запросов.
    
    Args:
        query: Поисковый запрос.
        max_results: Максимум результатов (до 10).
        time_limit: Фильтр по времени ('d' - день, 'w' - неделя, 'm' - месяц, 'y' - год, None - все время).
        region: Регион поиска (по умолчанию 'ru-ru' для РФ).
    """
    if not query:
        return format_error("Query is required")

    max_results = min(max_results, 10)
    
    try:
        result = _run_async(async_general_web_search(query, max_results, time_limit, region))
            
        if not result.success:
            return format_error(result.error or "Search failed")
            
        if not result.pages:
            return format_no_results(query)
            
        return format_results_xml(result)
        
    except Exception as e:
        logger.error(f"General web search error: {e}")
        return format_error(f"Search failed: {e}")

async def async_general_web_search(query: str, max_results: int, time_limit: str, region: str) -> GeneralWebSearchResult:
    # 1. Получаем ссылки из DDG с учетом времени и региона
    search_results = await duckduckgo_search(query, max_results * 2, time_limit, region)
    
    if not search_results:
        return GeneralWebSearchResult(success=False, query=query, error="No search results found")

    # 2. Скачиваем контент страниц параллельно
    pages = await fetch_pages_parallel(search_results, max_results)

    # 3. Рассчитываем релевантность (Pseudo-Semantic Jaccard)
    pages = calculate_relevance_scores(pages, query)
    
    # 4. Сортируем: сначала самые релевантные
    pages.sort(key=lambda p: p.relevance_score, reverse=True)

    # 5. Отбираем лучшие, пока влезаем в лимит токенов
    selected_pages = select_within_token_limit(pages, MAX_TOTAL_TOKENS)
    
    total_tokens = sum(p.tokens for p in selected_pages)
    
    return GeneralWebSearchResult(
        success=True,
        query=query,
        pages=selected_pages,
        total_tokens=total_tokens
    )

async def duckduckgo_search(query: str, num_results: int, time_limit: str, region: str) -> List[Dict[str, str]]:
    """Поиск через HTML версию DDG с поддержкой фильтров"""
    search_url = "https://html.duckduckgo.com/html/"
    
    # Формируем параметры DDG
    # df: w (week), m (month), d (day), y (year)
    params = {
        'q': query,
        'kl': region, # region settings (ru-ru)
    }
    if time_limit and time_limit in ['d', 'w', 'm', 'y']:
        params['df'] = time_limit

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7" # Важно для русских результатов
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(search_url, data=params, headers=headers)
            
            if response.status_code != 200:
                logger.warning(f"DDG returned status {response.status_code}")
                return []

            return parse_ddg_html(response.text, num_results)
    except Exception as e:
        logger.error(f"DDG search error: {e}")
        return []

def parse_ddg_html(html: str, max_results: int) -> List[Dict[str, str]]:
    results = []
    # Чуть более строгий regex для парсинга
    result_pattern = re.compile(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE)
    snippet_pattern = re.compile(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.IGNORECASE)
    
    matches = result_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    
    for i, (url, title) in enumerate(matches):
        if i >= max_results:
            break
            
        actual_url = extract_actual_url(url)
        if not is_valid_url(actual_url):
            continue
            
        snippet = snippets[i] if i < len(snippets) else ""
        
        # Очистка HTML тегов из заголовка и сниппета
        title = remove_html_tags(title)
        snippet = remove_html_tags(snippet)
        
        results.append({
            "url": actual_url,
            "title": title.strip(),
            "snippet": snippet.strip()
        })
        
    return results

def remove_html_tags(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text)

def extract_actual_url(ddg_url: str) -> str:
    if "uddg=" in ddg_url:
        match = re.search(r'uddg=([^&]+)', ddg_url)
        if match:
            return unquote(match.group(1))
    return ddg_url

def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        # Игнорируем PDF и бинарники для скорости, если это не явно запрошено
        if any(url.lower().endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.zip']):
            return False 
        return True
    except:
        return False

async def fetch_pages_parallel(search_results: List[Dict[str, str]], max_results: int) -> List[GeneralWebPage]:
    tasks = [fetch_single_page(r) for r in search_results]
    pages = await asyncio.gather(*tasks)
    # Фильтруем пустые и ошибочные
    valid_pages = [p for p in pages if p.content and not p.error]
    return valid_pages[:max_results]

async def fetch_single_page(result: Dict[str, str]) -> GeneralWebPage:
    url = result['url']
    try:
        content = await fetch_page_content(url)
        if not content:
            return GeneralWebPage(url=url, title=result['title'], snippet=result['snippet'], error="Empty content")
            
        counter = TokenCounter()
        tokens = counter.count(content)
        
        # Если слишком длинно, обрезаем, но аккуратно
        if tokens > MAX_CONTENT_PER_PAGE:
            content = truncate_content(content, MAX_CONTENT_PER_PAGE, counter)
            tokens = MAX_CONTENT_PER_PAGE
            
        return GeneralWebPage(url=url, title=result['title'], snippet=result['snippet'], content=content, tokens=tokens)
    except Exception as e:
        return GeneralWebPage(url=url, title=result['title'], snippet=result['snippet'], error=str(e))

async def fetch_page_content(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                return ""
            return extract_text_from_html(response.text)
    except:
        return ""

def extract_text_from_html(html: str) -> str:
    """Улучшенная очистка текста для статей"""
    # Удаляем скрипты, стили, метрики
    html = re.sub(r'<(script|style|svg|nav|footer|header|aside)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    
    # Заменяем блочные теги на переносы строк
    html = re.sub(r'</?(p|div|br|h[1-6]|li|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    
    # Удаляем все остальные теги
    text = re.sub(r'<[^>]+>', '', html)
    
    # Декодируем HTML сущности (просто базовая очистка)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
    
    # Убираем множественные пробелы и переносы
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    
    return text.strip()

def truncate_content(content: str, max_tokens: int, counter: TokenCounter) -> str:
    # Простая обрезка, можно улучшить до обрезки по параграфам
    # Для скорости просто режем по символам (примерно 4 символа на токен) и проверяем
    approx_chars = max_tokens * 4
    if len(content) > approx_chars:
        content = content[:approx_chars]
    return content

def calculate_relevance_scores(pages: List[GeneralWebPage], query: str) -> List[GeneralWebPage]:
    """
    Рассчитывает релевантность на основе Jaccard Similarity (пересечение множеств слов).
    Это лучше, чем простой count, так как учитывает 'покрытие' запроса текстом.
    """
    query_words = set(re.findall(r'\w+', query.lower()))
    if not query_words:
        return pages

    for page in pages:
        score = 0.0
        
        # 1. Title Score (Очень важно)
        title_words = set(re.findall(r'\w+', page.title.lower()))
        if title_words:
            intersection = query_words.intersection(title_words)
            # Коэффициент Жаккара для заголовка
            score += (len(intersection) / len(query_words)) * 3.0 # Вес 3.0

        # 2. Snippet Score
        snippet_words = set(re.findall(r'\w+', page.snippet.lower()))
        if snippet_words:
            intersection = query_words.intersection(snippet_words)
            score += (len(intersection) / len(query_words)) * 1.5 # Вес 1.5

        # 3. Content Score (плотность в начале текста важнее)
        content_preview = page.content[:1000].lower() # Смотрим только начало для скорости и важности
        content_words = set(re.findall(r'\w+', content_preview))
        if content_words:
            intersection = query_words.intersection(content_words)
            score += (len(intersection) / len(query_words)) * 1.0

        page.relevance_score = score

    return pages

def select_within_token_limit(pages: List[GeneralWebPage], limit: int) -> List[GeneralWebPage]:
    selected = []
    current_tokens = 0
    for page in pages:
        if current_tokens + page.tokens <= limit:
            selected.append(page)
            current_tokens += page.tokens
        else:
            # Если страница не влезает целиком, но место еще есть (>500 токенов), берем кусок
            remaining = limit - current_tokens
            if remaining > 500:
                # Тут нужен truncate, но для простоты просто берем начало
                # В реальном коде лучше вызвать truncate_content
                page.content = page.content[:remaining * 4] 
                page.tokens = remaining
                selected.append(page)
            break
    return selected

def format_results_xml(result: GeneralWebSearchResult) -> str:
    parts = []
    parts.append(f"<!-- Web search results for '{result.query}' -->")
    parts.append(f"<websearch query='{result.query}' tokens='{result.total_tokens}'>")
    for i, page in enumerate(result.pages, 1):
        parts.append(f"  <page rank='{i}' relevance='{page.relevance_score:.2f}' url='{page.url}'>")
        parts.append(f"    <title>{page.title}</title>")
        parts.append(f"    <content><![CDATA[{page.content}]]></content>")
        parts.append(f"  </page>")
    parts.append("</websearch>")
    return "\n".join(parts)

def format_error(msg: str) -> str:
    return f"<!-- ERROR -->\n<error message='{msg}'/>"

def format_no_results(query: str) -> str:
    return f"<!-- No results -->\n<websearch query='{query}'><message>No results found</message></websearch>"
```

---

## 📖 Пояснения к коду

Внесены изменения для решения проблемы с асинхронностью в функции `general_web_search_tool`. Основная проблема старой реализации заключалась в том, что она создавала новый event loop и устанавливала его глобально через `asyncio.set_event_loop()`, что вызывало конфликты при вызове из уже работающего async-контекста (например, из FastAPI, aiohttp или других async-функций).

**Что было сделано:**

1. **Добавлена вспомогательная функция `_run_async(coro)`**:
   - Функция размещена сразу после импортов, перед константами (как указано в инструкции)
   - Она определяет, запущен ли уже event loop в текущем контексте
   - Если loop не запущен (RuntimeError), используется `asyncio.run(coro)` — современный и безопасный способ
   - Если loop уже запущен, функция использует `ThreadPoolExecutor` для запуска корутины в отдельном потоке с новым event loop

2. **Изменена функция `general_web_search_tool`**:
   - Удален блок с ручным созданием и управлением event loop (`asyncio.new_event_loop()`, `asyncio.set_event_loop()`, `loop.run_until_complete()`, `loop.close()`)
   - Вместо этого используется вызов `_run_async(async_general_web_search(...))`
   - Вся остальная логика функции (обработка ошибок, форматирование результатов) оста

---

## 🛠️ Выполненные вызовы инструментов

1. ✅ **read_file**
   - Аргументы: `file_path=app/tools/general_web_search.py`

---

*Отчет сгенерирован автоматически: 2025-12-17T17:10:20.303181*