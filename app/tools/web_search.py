# app/tools/web_search.py
"""
Web Search Tool - Search the internet using DuckDuckGo.

Features:
- DuckDuckGo search (no API key required)
- Full page content extraction
- Semantic relevance ranking
- Token limit enforcement (25,000 tokens max)
- XML-wrapped results
"""

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


logger = logging.getLogger(__name__)

# Constants
MAX_TOTAL_TOKENS = 25000
MAX_RESULTS = 10
REQUEST_TIMEOUT = 15.0
MAX_CONTENT_PER_PAGE = 5000  # Max tokens per single page


@dataclass
class WebPage:
    """Represents a fetched web page"""
    url: str
    title: str
    snippet: str  # Search result snippet
    content: str  # Full extracted content
    tokens: int
    relevance_score: float = 0.0
    error: Optional[str] = None


@dataclass 
class WebSearchResult:
    """Result of web search operation"""
    success: bool
    query: str
    pages: List[WebPage] = field(default_factory=list)
    total_tokens: int = 0
    error: Optional[str] = None


def web_search_tool(
    query: str,
    max_results: int = 10,
    region: str = "wt-wt",
) -> str:
    """
    Search the internet and return relevant page contents.
    
    Uses DuckDuckGo for search (no API key required).
    Fetches full page content for top results.
    Ranks by semantic relevance to query.
    Limits total output to 25,000 tokens.
    
    Args:
        query: Search query
        max_results: Maximum pages to return (max 10)
        region: DuckDuckGo region code (default: no region)
        
    Returns:
        XML-wrapped search results with page contents
    """
    logger.info(f"web_search_tool: Searching for '{query}'")
    
    if not query:
        return _format_error("query is required")
    
    # Limit max_results
    max_results = min(max_results, MAX_RESULTS)
    
    # Run async search
    try:
        # Проверяем, есть ли уже работающий event loop
        try:
            loop = asyncio.get_running_loop()
            # Loop работает - запускаем в отдельном потоке с НОВЫМ loop
            def run_in_new_loop():
                # Создаем новый loop в этом потоке
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(
                        _async_web_search(query, max_results, region)
                    )
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(run_in_new_loop)
                result = future.result(timeout=60)
                
        except RuntimeError:
            # Нет работающего loop - используем asyncio.run() напрямую
            result = asyncio.run(_async_web_search(query, max_results, region))
        
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return _format_error(f"Search failed: {e}")

    if not result.success:
        return _format_error(result.error or "Search failed")

    if not result.pages:
        return _format_no_results(query)

    return _format_results_xml(result)


async def _async_web_search(
    query: str,
    max_results: int,
    region: str
) -> WebSearchResult:
    """Async implementation of web search"""
    
    # Step 1: Get search results from DuckDuckGo
    search_results = await _duckduckgo_search(query, max_results * 2, region)  # Get extra for filtering
    
    if not search_results:
        return WebSearchResult(
            success=False,
            query=query,
            error="No search results found"
        )
    
    # Step 2: Fetch page contents in parallel
    pages = await _fetch_pages_parallel(search_results[:max_results * 2])
    
    # Step 3: Calculate semantic relevance scores
    pages = _calculate_relevance_scores(pages, query)
    
    # Step 4: Sort by relevance
    pages.sort(key=lambda p: p.relevance_score, reverse=True)
    
    # Step 5: Select top pages within token limit
    selected_pages = _select_within_token_limit(pages, max_results)
    
    total_tokens = sum(p.tokens for p in selected_pages)
    
    return WebSearchResult(
        success=True,
        query=query,
        pages=selected_pages,
        total_tokens=total_tokens
    )


async def _duckduckgo_search(
    query: str,
    num_results: int,
    region: str
) -> List[Dict[str, str]]:
    """
    Search DuckDuckGo and get result URLs.
    
    Uses DuckDuckGo HTML interface (no API needed).
    """
    results = []
    
    # DuckDuckGo HTML search URL
    search_url = "https://html.duckduckgo.com/html/"
    
    params = {
        "q": query,
        "kl": region,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(search_url, data=params, headers=headers)
            
            if response.status_code != 200:
                logger.warning(f"DuckDuckGo returned status {response.status_code}")
                return []
            
            html = response.text
            
            # Parse results from HTML
            results = _parse_duckduckgo_html(html, num_results)
            
    except Exception as e:
        logger.error(f"DuckDuckGo search error: {e}")
        return []
    
    return results


def _parse_duckduckgo_html(html: str, max_results: int) -> List[Dict[str, str]]:
    """Parse DuckDuckGo HTML search results"""
    results = []
    
    # Pattern to find result links and titles
    # DuckDuckGo HTML format: <a class="result__a" href="...">title</a>
    # and <a class="result__snippet">snippet</a>
    
    # Find all result blocks
    result_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>',
        re.IGNORECASE
    )
    
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>([^<]*(?:<[^>]*>[^<]*)*)</a>',
        re.IGNORECASE
    )
    
    # Extract URLs and titles
    matches = result_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    
    for i, (url, title) in enumerate(matches[:max_results]):
        # DuckDuckGo uses redirect URLs, extract actual URL
        actual_url = _extract_actual_url(url)
        
        if not actual_url or not _is_valid_url(actual_url):
            continue
        
        snippet = ""
        if i < len(snippets):
            # Clean HTML from snippet
            snippet = re.sub(r'<[^>]+>', '', snippets[i])
            snippet = snippet.strip()
        
        results.append({
            "url": actual_url,
            "title": title.strip(),
            "snippet": snippet
        })
    
    return results


def _extract_actual_url(duckduckgo_url: str) -> str:
    """Extract actual URL from DuckDuckGo redirect URL"""
    # DuckDuckGo format: //duckduckgo.com/l/?uddg=https%3A%2F%2Factual-url.com
    if "uddg=" in duckduckgo_url:
        match = re.search(r'uddg=([^&]+)', duckduckgo_url)
        if match:
            return unquote(match.group(1))
    
    # Direct URL
    if duckduckgo_url.startswith("//"):
        return "https:" + duckduckgo_url
    
    return duckduckgo_url


def _is_valid_url(url: str) -> bool:
    """Check if URL is valid and should be fetched"""
    try:
        parsed = urlparse(url)
        
        # Must have scheme and netloc
        if not parsed.scheme or not parsed.netloc:
            return False
        
        # Skip certain domains
        skip_domains = [
            "youtube.com", "youtu.be",
            "twitter.com", "x.com",
            "facebook.com", "instagram.com",
            "tiktok.com",
        ]
        
        for domain in skip_domains:
            if domain in parsed.netloc:
                return False
        
        return True
        
    except Exception:
        return False


async def _fetch_pages_parallel(
    search_results: List[Dict[str, str]]
) -> List[WebPage]:
    """Fetch multiple pages in parallel"""
    
    async def fetch_single(result: Dict[str, str]) -> WebPage:
        url = result["url"]
        title = result["title"]
        snippet = result["snippet"]
        
        try:
            content = await _fetch_page_content(url)
            
            if not content:
                return WebPage(
                    url=url,
                    title=title,
                    snippet=snippet,
                    content="",
                    tokens=0,
                    error="Failed to fetch content"
                )
            
            # Count tokens
            counter = TokenCounter()
            tokens = counter.count(content)
            
            # Truncate if too long
            if tokens > MAX_CONTENT_PER_PAGE:
                content = _truncate_content(content, MAX_CONTENT_PER_PAGE, counter)
                tokens = MAX_CONTENT_PER_PAGE
            
            return WebPage(
                url=url,
                title=title,
                snippet=snippet,
                content=content,
                tokens=tokens
            )
            
        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
            return WebPage(
                url=url,
                title=title,
                snippet=snippet,
                content="",
                tokens=0,
                error=str(e)
            )
    
    # Fetch all pages in parallel
    tasks = [fetch_single(r) for r in search_results]
    pages = await asyncio.gather(*tasks)
    
    # Filter out failed pages
    return [p for p in pages if p.content and not p.error]


async def _fetch_page_content(url: str) -> str:
    """Fetch and extract text content from a web page"""
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            
            if response.status_code != 200:
                return ""
            
            html = response.text
            
            # Extract text content
            text = _extract_text_from_html(html)
            
            return text
            
    except Exception as e:
        logger.debug(f"Error fetching {url}: {e}")
        return ""


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML"""
    
    # Remove script and style elements
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove HTML comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    
    # Replace common block elements with newlines
    html = re.sub(r'<(p|div|br|h[1-6]|li|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    
    # Decode HTML entities
    text = _decode_html_entities(text)
    
    # Clean up whitespace
    text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces to single
    text = re.sub(r'\n\s*\n+', '\n\n', text)  # Multiple newlines to double
    text = text.strip()
    
    # Remove very short lines (likely navigation remnants)
    lines = text.split('\n')
    lines = [line.strip() for line in lines if len(line.strip()) > 20 or line.strip() == '']
    text = '\n'.join(lines)
    
    return text


def _decode_html_entities(text: str) -> str:
    """Decode common HTML entities"""
    entities = {
        '&nbsp;': ' ',
        '&amp;': '&',
        '&lt;': '<',
        '&gt;': '>',
        '&quot;': '"',
        '&apos;': "'",
        '&#39;': "'",
        '&mdash;': '—',
        '&ndash;': '–',
        '&bull;': '•',
        '&hellip;': '…',
        '&copy;': '©',
        '&reg;': '®',
        '&trade;': '™',
    }
    
    for entity, char in entities.items():
        text = text.replace(entity, char)
    
    # Decode numeric entities
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    text = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
    
    return text


def _calculate_relevance_scores(pages: List[WebPage], query: str) -> List[WebPage]:
    """
    Calculate semantic relevance scores for pages.
    
    Uses simple keyword matching and position-based scoring.
    Higher score = more relevant.
    """
    query_words = set(query.lower().split())
    
    for page in pages:
        score = 0.0
        
        # Title match (high weight)
        title_lower = page.title.lower()
        title_matches = sum(1 for w in query_words if w in title_lower)
        score += title_matches * 3.0
        
        # Snippet match (medium weight)
        snippet_lower = page.snippet.lower()
        snippet_matches = sum(1 for w in query_words if w in snippet_lower)
        score += snippet_matches * 2.0
        
        # Content match (lower weight but important)
        content_lower = page.content.lower()[:5000]  # First 5000 chars
        content_matches = sum(1 for w in query_words if w in content_lower)
        score += content_matches * 1.0
        
        # Exact phrase match bonus
        if query.lower() in title_lower:
            score += 5.0
        if query.lower() in content_lower:
            score += 2.0
        
        # Programming-related URL bonus
        programming_domains = ['stackoverflow.com', 'github.com', 'docs.python.org',
                              'developer.mozilla.org', 'medium.com', 'dev.to',
                              'realpython.com', 'geeksforgeeks.org']
        for domain in programming_domains:
            if domain in page.url.lower():
                score += 2.0
                break
        
        # Penalize very short content
        if len(page.content) < 500:
            score *= 0.5
        
        page.relevance_score = score
    
    return pages


def _select_within_token_limit(
    pages: List[WebPage],
    max_results: int
) -> List[WebPage]:
    """Select top pages within token limit"""
    selected = []
    total_tokens = 0
    
    for page in pages:
        if len(selected) >= max_results:
            break
        
        if total_tokens + page.tokens > MAX_TOTAL_TOKENS:
            # Try to truncate this page to fit
            remaining_tokens = MAX_TOTAL_TOKENS - total_tokens
            if remaining_tokens > 500:  # Worth including if at least 500 tokens
                page.content = _truncate_content(page.content, remaining_tokens, TokenCounter())
                page.tokens = remaining_tokens
                selected.append(page)
            break
        
        selected.append(page)
        total_tokens += page.tokens
    
    return selected


def _truncate_content(content: str, max_tokens: int, counter: TokenCounter) -> str:
    """Truncate content to fit within token limit"""
    # Simple approach: truncate by paragraphs
    paragraphs = content.split('\n\n')
    result = []
    current_tokens = 0
    
    for para in paragraphs:
        para_tokens = counter.count(para)
        if current_tokens + para_tokens > max_tokens:
            if result:  # Keep at least something
                result.append("... [content truncated] ...")
            break
        result.append(para)
        current_tokens += para_tokens
    
    return '\n\n'.join(result)


def _format_results_xml(result: WebSearchResult) -> str:
    """Format search results as XML"""
    parts = []
    
    # Header
    parts.append(f'<!-- Web search results for: "{_escape_xml(result.query)}" -->')
    parts.append(f'<!-- Pages: {len(result.pages)} | Total tokens: {result.total_tokens} -->')
    parts.append("")
    parts.append(f'<web_search query="{_escape_xml(result.query)}" '
                f'results="{len(result.pages)}" tokens="{result.total_tokens}">')
    
    for i, page in enumerate(result.pages, 1):
        parts.append(f'  <page rank="{i}" relevance="{page.relevance_score:.2f}" '
                    f'tokens="{page.tokens}">')
        parts.append(f'    <url>{_escape_xml(page.url)}</url>')
        parts.append(f'    <title>{_escape_xml(page.title)}</title>')
        parts.append(f'    <content><![CDATA[')
        parts.append(page.content)
        parts.append(']]></content>')
        parts.append('  </page>')
    
    parts.append('</web_search>')
    
    return "\n".join(parts)


def _format_no_results(query: str) -> str:
    """Format message when no results found"""
    return f"""<!-- Web search results for: "{_escape_xml(query)}" -->
<!-- No results found -->

<web_search query="{_escape_xml(query)}" results="0" tokens="0">
  <message>No results found for "{_escape_xml(query)}"</message>
  <suggestions>
    <suggestion>Try different keywords</suggestion>
    <suggestion>Make the query more specific</suggestion>
    <suggestion>Check spelling</suggestion>
  </suggestions>
</web_search>"""


def _format_error(message: str) -> str:
    """Format error message"""
    return f"""<!-- ERROR -->
<error>
  <message>{_escape_xml(message)}</message>
</error>"""


def _escape_xml(text: str) -> str:
    """Escape special XML characters"""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))