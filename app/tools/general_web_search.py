"""
Инструмент для поиска в Интернете для режима общего чата.
Использует логику general_web_search (псевдосемантика) на надежном каркасе.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse, unquote

import httpx
# ВАЖНО: Используем nest_asyncio для починки сети при вложенных вызовах (DeepSeek)
import nest_asyncio
nest_asyncio.apply()

from app.utils.token_counter import TokenCounter
from config.settings import cfg

logger = logging.getLogger(__name__)

# Constants (из general_web_search)
MAX_TOTAL_TOKENS = 25000
MAX_RESULTS = 10
REQUEST_TIMEOUT = 15.0
MAX_CONTENT_PER_PAGE = 4000 

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

# === ГЛАВНАЯ ФУНКЦИЯ (СИНХРОННАЯ ОБЕРТКА) ===
# Каркас взят из надежного web_search.py, но имена функций сохранены для совместимости

def general_web_search_tool(query: str, max_results: int = 10, time_limit: str = "w", region: str = "ru-ru") -> str:
    """
    Выполняет поиск в интернете для общих запросов.
    Безопасна для вызова из любого контекста (sync/async/thread).
    """
    if not query:
        return format_error("Query is required")

    max_results = min(max_results, 10)

    # === НАДЕЖНЫЙ КАРКАС ЗАПУСКА ===
    # Вместо ThreadPoolExecutor и ручных потоков используем nest_asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
             loop = asyncio.new_event_loop()
             asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        # Запускаем асинхронную логику поиска (nest_asyncio позволяет это делать безопасно)
        result = loop.run_until_complete(
            async_general_web_search(query, max_results, time_limit, region)
        )
    except Exception as e:
        logger.error(f"General web search execution error: {e}")
        return format_error(f"Search failed: {e}")

    # === ФОРМАТИРОВАНИЕ (ЛОГИКА GENERAL SEARCH) ===
    if not result.success:
        return format_error(result.error or "Search failed")

    if not result.pages:
        return format_no_results(query)

    return format_results_xml(result)


def general_web_search_tool_sync(query: str, max_results: int = 10, time_limit: str = "w", region: str = "ru-ru") -> str:
    """Алиас для обратной совместимости, если где-то вызывается явно"""
    return general_web_search_tool(query, max_results, time_limit, region)


# === АСИНХРОННАЯ ЛОГИКА (СМЕСЬ) ===

async def async_general_web_search(query: str, max_results: int, time_limit: str, region: str) -> GeneralWebSearchResult:
    # 1. Получаем ссылки (используем httpx из каркаса web_search, но с параметрами general)
    search_results = await duckduckgo_search(query, max_results * 2, time_limit, region)

    if not search_results:
        return GeneralWebSearchResult(success=False, query=query, error="No search results found")

    # 2. Скачиваем контент параллельно
    pages = await fetch_pages_parallel(search_results, max_results)

    # 3. Рассчитываем релевантность (Логика General Search)
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
    """Поиск через HTML DDG (надежный httpx запрос)"""
    search_url = "https://html.duckduckgo.com/html/"
    
    # Параметры из General Search (поддержка time_limit)
    params = {'q': query, 'kl': region}
    if time_limit and time_limit in ['d', 'w', 'm', 'y']:
        params['df'] = time_limit

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        # Важно для русских результатов
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7" 
    }

    try:
        # Используем httpx без прокси (nest_asyncio должен решить проблему с DNS)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(search_url, data=params, headers=headers)
            
            if response.status_code != 200:
                logger.warning(f"DDG returned status {response.status_code}")
                return []
            
            return parse_ddg_html(response.text, num_results)
            
    except Exception as e:
        logger.error(f"DDG search error: {e}")
        return []

def parse_ddg_html(html: str, max_results: int) -> List[Dict[str, str]]:
    """Парсинг HTML (из general_web_search)"""
    results = []
    result_pattern = re.compile(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE)
    snippet_pattern = re.compile(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.IGNORECASE)
    
    matches = result_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    
    for i, (url, title) in enumerate(matches):
        if i >= max_results: break
        
        actual_url = extract_actual_url(url)
        if not is_valid_url(actual_url): continue
        
        snippet = snippets[i] if i < len(snippets) else ""
        
        # Очистка тегов
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
        if match: return unquote(match.group(1))
    return ddg_url

def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc: return False
        if any(url.lower().endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.zip']):
            return False
        return True
    except: return False


async def fetch_pages_parallel(search_results: List[Dict[str, str]], max_results: int) -> List[GeneralWebPage]:
    tasks = [fetch_single_page(r) for r in search_results]
    pages = await asyncio.gather(*tasks)
    # Фильтруем пустые
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
        
        # Обрезаем контент
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
            if response.status_code != 200: return ""
            return extract_text_from_html(response.text)
    except: return ""

def extract_text_from_html(html: str) -> str:
    """Улучшенная очистка текста (из general_web_search)"""
    html = re.sub(r'<(script|style|svg|nav|footer|header|aside)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    html = re.sub(r'<(p|div|br|h[1-6]|li|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', html)
    # Декодинг и очистка пробелов
    text = text.replace(' ', ' ').replace('&', '&').replace('"', '"').replace('<', '<').replace('>', '>')
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    return text.strip()

def truncate_content(content: str, max_tokens: int, counter: TokenCounter) -> str:
    """Бинарный поиск для точной обрезки (из general_web_search)"""
    if counter.count(content) <= max_tokens:
        return content
    
    low, high = 0, len(content)
    result = content
    
    while low < high:
        mid = (low + high + 1) // 2
        truncated = content[:mid]
        if counter.count(truncated) <= max_tokens:
            result = truncated
            low = mid
        else:
            high = mid - 1
            
    return result

def calculate_relevance_scores(pages: List[GeneralWebPage], query: str) -> List[GeneralWebPage]:
    """Псевдосемантика (Jaccard Similarity) из general_web_search"""
    query_words = set(re.findall(r'\w+', query.lower()))
    if not query_words: return pages
    
    for page in pages:
        score = 0.0
        # 1. Title Score
        title_words = set(re.findall(r'\w+', page.title.lower()))
        if title_words:
            intersection = query_words.intersection(title_words)
            score += (len(intersection) / len(query_words)) * 3.0
            
        # 2. Snippet Score
        snippet_words = set(re.findall(r'\w+', page.snippet.lower()))
        if snippet_words:
            intersection = query_words.intersection(snippet_words)
            score += (len(intersection) / len(query_words)) * 1.5
            
        # 3. Content Score (preview)
        content_preview = page.content[:1000].lower()
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
            remaining = limit - current_tokens
            if remaining > 500:
                page.content = page.content[:remaining * 4] # Грубая оценка
                page.tokens = remaining
                selected.append(page)
            break
    return selected

def format_results_xml(result: GeneralWebSearchResult) -> str:
    """Форматирование XML (из general_web_search)"""
    parts = []
    parts.append(f'<web_results query="{result.query}">')
    parts.append(f'<total_tokens>{result.total_tokens}</total_tokens>')
    
    for i, page in enumerate(result.pages, 1):
        parts.append(f'  <page id="{i}" relevance="{page.relevance_score:.2f}">')
        parts.append(f'    <title>{page.title}</title>')
        parts.append(f'    <url>{page.url}</url>')
        parts.append(f'    <content>{page.content}</content>')
        parts.append(f'  </page>')
        
    parts.append('</web_results>')
    return "\n".join(parts)

def format_error(msg: str) -> str:
    return f'<error>{msg}</error>'

def format_no_results(query: str) -> str:
    return f'<no_results>No results found for "{query}"</no_results>'
