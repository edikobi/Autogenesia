# app/tools/tool_executor.py
"""
Tool Executor - Central dispatcher for all tools.

Routes tool calls to appropriate implementations and handles errors.
"""

from __future__ import annotations
import json
import logging
from typing import Dict, Any, Optional, Callable
from pathlib import Path
from app.advice.advice_loader import execute_get_advice

from app.tools.read_file import read_file_tool
from app.tools.search_code import search_code_tool
from app.tools.web_search import web_search_tool
from app.tools.run_project_tests import run_project_tests  # NEW
from app.tools.grep_search import grep_search_tool
from app.tools.file_relations import show_file_relations_tool
from app.services.python_chunker import SmartPythonChunker

from app.tools.dependency_manager import (
    list_installed_packages_tool,
    install_dependency_tool,
    search_pypi_tool,
)

import httpx
from urllib.parse import urlparse, urljoin
from datetime import datetime

try:
    from bs4 import BeautifulSoup
    BS_AVAILABLE = True
except ImportError:
    BS_AVAILABLE = False
    logger.warning("BeautifulSoup not installed. Webpage analysis tools will not work. Install with: pip install beautifulsoup4")
        
logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Executes tools by name with provided arguments.
    
    Supports:
    - read_file: Read project files
    - search_code: Search code in index
    - web_search: Search the internet
    
    Can be extended with custom tools.
    """
    
    def __init__(
        self,
        project_dir: str,
        index: Optional[Dict[str, Any]] = None,
        virtual_fs: Optional[Any] = None,  # NEW: VirtualFileSystem instance
    ):
        """
        Initialize tool executor.
        
        Args:
            project_dir: Path to project root (for file operations)
            index: Project semantic index (for code search)
        """
        self.project_dir = project_dir
        self.index = index or {}
        self.virtual_fs = virtual_fs  # NEW
        self._custom_tools: Dict[str, Callable] = {}
    
    
    def _validate_url(self, url: str) -> bool:
        """Проверяет, что URL безопасен для запроса (только http/https, не локальный)."""
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        host = parsed.hostname
        forbidden = ('localhost', '127.0.0.1', '::1', '0.0.0.0')
        if host in forbidden:
            return False
        return True

    def _fetch_html(self, url: str, timeout: int = 10, max_length: int = 50000) -> tuple[httpx.Response, str]:
        """Загружает HTML-страницу, обрезает до max_length символов."""
        if not self._validate_url(url):
            raise ValueError(f"URL not allowed: {url}")
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.text
            if len(content) > max_length:
                content = content[:max_length] + "\n... (truncated due to length limit)"
            return resp, content

    def _fetch_head(self, url: str, timeout: int = 5) -> httpx.Response:
        """Выполняет HEAD-запрос для получения заголовков (без тела)."""
        if not self._validate_url(url):
            raise ValueError(f"URL not allowed: {url}")
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            return client.head(url)

    def _xml_escape(self, s: str) -> str:
        """Экранирует спецсимволы для XML."""
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

    def _absolutize_url(self, src: str, base_url: str) -> str:
        """Преобразует относительный URL в абсолютный на основе base_url."""
        return urljoin(base_url, src)    
    
    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Execute a tool by name.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as dict
            
        Returns:
            Tool output as string (usually XML-formatted)
        """
        logger.info(f"Executing tool: {tool_name} with args: {list(arguments.keys())}")
        
        try:
            # Check custom tools first
            if tool_name in self._custom_tools:
                return self._custom_tools[tool_name](**arguments)
            
            
            # Built-in tools
            if tool_name == "read_code_chunk": # Добавили блок
                return self._execute_read_code_chunk(arguments)
            elif tool_name == "read_file":
                return self._execute_read_file(arguments)
           
            if tool_name == "read_file":
                return self._execute_read_file(arguments)
            
            elif tool_name == "search_code":
                return self._execute_search_code(arguments)
            
            elif tool_name == "web_search":
                return self._execute_web_search(arguments)
            
            elif tool_name == "get_advice":
                return self._execute_get_advice(arguments)

            elif tool_name == "run_project_tests":  # NEW
                return self._execute_run_project_tests(arguments)
            
            elif tool_name == "list_installed_packages":
                return self._execute_list_installed_packages(arguments)
            elif tool_name == "install_dependency":
                return self._execute_install_dependency(arguments)
            elif tool_name == "search_pypi":
                return self._execute_search_pypi(arguments)
            elif tool_name == "fetch_webpage":
                return self._execute_fetch_webpage(arguments)
            elif tool_name == "analyze_webpage":
                return self._execute_analyze_webpage(arguments)
            elif tool_name == "check_security":
                return self._execute_check_security(arguments)
            elif tool_name == "extract_media":
                return self._execute_extract_media(arguments)
            elif tool_name == "grep_search": 
                return self._execute_grep_search(arguments)
            elif tool_name == "show_file_relations":
                 return self._execute_show_file_relations(arguments)
            else:
                return self._format_error(f"Unknown tool: {tool_name}")
                
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            return self._format_error(f"Tool execution failed: {e}")
    
    
    def _execute_read_code_chunk(self, arguments: Dict[str, Any]) -> str:
        """
        Execute read_code_chunk tool using SmartPythonChunker.
        
        UPDATED: Checks VirtualFileSystem first for staged files.
        """
        file_path = arguments.get("file_path", "")
        chunk_name = arguments.get("chunk_name", "")
        
        # Check VFS first for staged files
        content = None
        source = "disk"
        
        if self.virtual_fs is not None:
            staged_content = self.virtual_fs.read_file(file_path)
            if staged_content is not None:
                content = staged_content
                source = "VFS"
                logger.info(f"read_code_chunk: Reading '{file_path}' from VFS (staged)")
        
        # Fall back to disk if not in VFS
        if content is None:
            full_path = Path(self.project_dir) / file_path
            if not full_path.exists():
                return self._format_error(f"File not found: {file_path}")
            try:
                content = full_path.read_text(encoding='utf-8')
                source = "disk"
            except Exception as e:
                return self._format_error(f"Failed to read file: {e}")
        
        try:
            # Parse content with chunker
            import tempfile
            import os
            
            # SmartPythonChunker needs a file path, so write to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(content)
                temp_path = f.name
            
            try:
                chunker = SmartPythonChunker()
                chunks = chunker.chunk_file(temp_path)
            finally:
                os.unlink(temp_path)
            
            # Find target chunk
            target_chunk = next((c for c in chunks if c.name == chunk_name), None)
            
            if not target_chunk:
                return self._format_error(f"Chunk '{chunk_name}' not found in {file_path}")
            
            # Format response
            return f"""<!-- Source: {source} -->
<code_chunk>
<file>{file_path}</file>
<name>{target_chunk.name}</name>
<type>{target_chunk.kind}</type>
<lines>{target_chunk.start_line}-{target_chunk.end_line}</lines>
<content>
{target_chunk.content}
</content>
</code_chunk>
"""
        except Exception as e:
            logger.error(f"Error reading chunk: {e}")
            return self._format_error(f"Failed to read chunk: {e}")
    
    
    
    def _execute_read_file(self, arguments: Dict[str, Any]) -> str:
        """
        Execute read_file tool.
        
        UPDATED: Checks VirtualFileSystem first for staged files.
        This ensures Orchestrator sees the latest generated code during feedback loops.
        """
        file_path = arguments.get("file_path", "")
        include_line_numbers = arguments.get("include_line_numbers", True)
        
        # Check VFS first for staged files
        if self.virtual_fs is not None:
            # Check if file is staged (created or modified in current session)
            staged_content = self.virtual_fs.read_file(file_path)
            if staged_content is not None:
                # File exists in VFS - return it with XML formatting
                logger.info(f"read_file: Reading '{file_path}' from VFS (staged)")
                
                # Count lines and tokens
                lines = staged_content.count('\n') + 1
                tokens = len(staged_content) // 4  # Approximate
                
                # Add line numbers if requested
                if include_line_numbers:
                    content_lines = staged_content.split('\n')
                    max_width = len(str(len(content_lines)))
                    numbered = [f"{i+1:>{max_width}} | {line}" for i, line in enumerate(content_lines)]
                    display_content = '\n'.join(numbered)
                else:
                    display_content = staged_content
                
                # Determine file type
                if file_path.endswith('.py'):
                    file_type = 'code/python'
                elif file_path.endswith('.json'):
                    file_type = 'data/json'
                else:
                    file_type = 'other'
                
                # Format as XML (matching read_file_tool output format)
                result = f"""<!-- File: {file_path} -->
<!-- Type: {file_type} | Lines: {lines} | Tokens: {tokens} -->
<!-- Source: VFS (staged changes) -->

<file path="{file_path}" type="{file_type}" tokens="{tokens}" encoding="utf-8">
<content><![CDATA[
{display_content}
]]></content>
</file>"""
                return result
        
        # Fall back to disk read
        logger.info(f"read_file: Reading '{file_path}' from disk")
        return read_file_tool(
            file_path=file_path,
            project_dir=self.project_dir,
            include_line_numbers=include_line_numbers
        )
    
    
    
    def _execute_search_code(self, arguments: Dict[str, Any]) -> str:
        """Execute search_code tool"""
        query = arguments.get("query", "")
        search_type = arguments.get("search_type", "all")
        
        return search_code_tool(
            query=query,
            index=self.index,
            project_dir=self.project_dir,
            search_type=search_type
        )
    
    
    def _execute_grep_search(self, arguments: Dict[str, Any]) -> str:
        """Execute grep_search tool with VFS support."""
        pattern = arguments.get("pattern", "")
        
        if not pattern:
            return self._format_error("pattern is required")
        
        return grep_search_tool(
            pattern=pattern,
            project_dir=self.project_dir,
            use_regex=arguments.get("use_regex", False),
            case_sensitive=arguments.get("case_sensitive", False),
            file_pattern=arguments.get("file_pattern"),
            path=arguments.get("path"),
            max_files=arguments.get("max_files", 100),
            max_matches_per_file=arguments.get("max_matches_per_file", 20),
            max_total_matches=arguments.get("max_total_matches", 50),
            context_lines=arguments.get("context_lines", 2),
            virtual_fs=self.virtual_fs  # Pass VFS for staged files
        )
    
    
    
    def _execute_show_file_relations(self, arguments: Dict[str, Any]) -> str:
        """Показывает связи файла в проекте."""
        file_path = arguments.get("file_path", "")
        
        if not file_path:
            return self._format_error("file_path is required")
        
        return show_file_relations_tool(
            file_path=file_path,
            project_dir=self.project_dir,
            virtual_fs=self.virtual_fs,
            include_tests=arguments.get("include_tests", True),
            include_siblings=arguments.get("include_siblings", True),
            max_relations=arguments.get("max_relations", 20)
        )    
        
    def _execute_web_search(self, arguments: Dict[str, Any]) -> str:
        """Execute web_search tool"""
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 10)
        region = arguments.get("region", "wt-wt")
        
        return web_search_tool(
            query=query,
            max_results=max_results,
            region=region
        )

    def _execute_get_advice(self, arguments: Dict[str, Any]) -> str:
        """Execute get_advice tool to load methodological thinking frameworks"""
        advice_ids = arguments.get("advice_ids", "")
        if not advice_ids:
            return self._format_error("advice_ids parameter is required")
        return execute_get_advice(advice_ids)
    
    def _execute_run_project_tests(self, arguments: Dict[str, Any]) -> str:
        """
        Execute run_project_tests tool.
        
        NEW: Runs unittest tests on VirtualFileSystem staged files.
        """
        test_path = arguments.get("test_path", "")
        chunk_name = arguments.get("chunk_name")
        timeout_sec = min(arguments.get("timeout_sec", 30), 60)  # Cap at 60 sec
        
        if not test_path:
            return self._format_error("test_path is required")
        
        return run_project_tests(
            project_dir=self.project_dir,
            test_path=test_path,
            virtual_fs=self.virtual_fs,
            chunk_name=chunk_name,
            timeout_sec=timeout_sec,
        )

    def _execute_list_installed_packages(self, arguments: Dict[str, Any]) -> str:
        """Execute list_installed_packages tool with VFS python_path."""
        python_path = None
        
        if self.virtual_fs is not None:
            python_path = self.virtual_fs.get_project_python()
        
        return list_installed_packages_tool(
            project_dir=self.project_dir,
            python_path=python_path
        )
    
    
    
    def _execute_install_dependency(self, arguments: Dict[str, Any]) -> str:
        """Execute install_dependency tool with VFS python_path."""
        import_name = arguments.get("import_name")
        
        if not import_name:
            return self._format_error("Missing required argument: import_name")
        
        version = arguments.get("version")
        python_path = None
        
        if self.virtual_fs is not None:
            python_path = self.virtual_fs.get_project_python()
        
        return install_dependency_tool(
            project_dir=self.project_dir,
            import_name=import_name,
            version=version,
            python_path=python_path
        )
    
    
    def _execute_search_pypi(self, arguments: Dict[str, Any]) -> str:
        """Execute search_pypi tool"""
        query = arguments.get("query", "")
        
        if not query:
            return self._format_error("query is required")
        
        return search_pypi_tool(query=query)

    
    
    
    # ------------------------------------------------------------------------
    # Инструмент fetch_webpage (сырой HTML)
    # ------------------------------------------------------------------------
    def _execute_fetch_webpage(self, arguments: Dict[str, Any]) -> str:
        url = arguments.get("url", "").strip()
        if not url:
            return self._format_error("URL is required")

        max_length = arguments.get("max_length", 100000)
        timeout = arguments.get("timeout", 10)

        try:
            resp, html = self._fetch_html(url, timeout=timeout, max_length=max_length)
        except Exception as e:
            return self._format_error(f"Failed to fetch webpage: {e}")

        safe_html = html.replace("]]>", "]]]]><![CDATA[>")

        result = f"""<!-- Fetched webpage: {url} -->
<webpage url="{self._xml_escape(url)}" status_code="{resp.status_code}" content_type="{self._xml_escape(resp.headers.get('content-type', ''))}">
    <content length="{len(html)}"><![CDATA[
{safe_html}
    ]]></content>
</webpage>"""
        return result

    # ------------------------------------------------------------------------
    # Инструмент analyze_webpage
    # ------------------------------------------------------------------------
    def _execute_analyze_webpage(self, arguments: Dict[str, Any]) -> str:
        url = arguments.get("url", "").strip()
        if not url:
            return self._format_error("URL is required")
        if not BS_AVAILABLE:
            return self._format_error("BeautifulSoup is not installed. Please install it: pip install beautifulsoup4")

        extract_links = arguments.get("extract_links", True)
        extract_metadata = arguments.get("extract_metadata", True)
        extract_forms = arguments.get("extract_forms", True)
        extract_media = arguments.get("extract_media", True)
        extract_technologies = arguments.get("extract_technologies", False)
        max_links = min(arguments.get("max_links", 100), 500)

        try:
            resp, html = self._fetch_html(url, timeout=10, max_length=200000)
        except Exception as e:
            return self._format_error(f"Failed to fetch webpage: {e}")

        soup = BeautifulSoup(html, 'html.parser')

        metadata_xml = ""
        if extract_metadata:
            title = soup.title.string if soup.title else ""
            metadata_xml += f"<title>{self._xml_escape(title)}</title>\n"
            for meta in soup.find_all("meta"):
                name = meta.get("name") or meta.get("property")
                content = meta.get("content", "")
                if name and content:
                    metadata_xml += f'<meta name="{self._xml_escape(name)}" content="{self._xml_escape(content)}" />\n'

        links_xml = ""
        links_count = 0
        if extract_links:
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)[:200]
                links.append((href, text))
                if len(links) >= max_links:
                    break
            links_count = len(links)
            for href, text in links:
                links_xml += f'<link href="{self._xml_escape(href)}">{self._xml_escape(text)}</link>\n'

        forms_xml = ""
        if extract_forms:
            for form in soup.find_all("form"):
                action = form.get("action", "")
                method = form.get("method", "get").upper()
                inputs = []
                for inp in form.find_all("input"):
                    name = inp.get("name")
                    if name:
                        inputs.append(f'{name} ({inp.get("type", "text")})')
                forms_xml += f'<form action="{self._xml_escape(action)}" method="{method}">\n'
                for inp_desc in inputs:
                    forms_xml += f'  <input>{self._xml_escape(inp_desc)}</input>\n'
                forms_xml += '</form>\n'

        media_xml = ""
        if extract_media:
            images = []
            for img in soup.find_all("img", src=True):
                src = img["src"]
                alt = img.get("alt", "")[:100]
                images.append((src, alt))
            for src, alt in images[:50]:
                media_xml += f'<image src="{self._xml_escape(src)}" alt="{self._xml_escape(alt)}" />\n'

            for video in soup.find_all("video"):
                for src in video.find_all("src"):
                    media_xml += f'<video src="{self._xml_escape(src.get("src", ""))}" />\n'
            for audio in soup.find_all("audio"):
                for src in audio.find_all("src"):
                    media_xml += f'<audio src="{self._xml_escape(src.get("src", ""))}" />\n'

        tech_xml = ""
        if extract_technologies:
            techs = []
            if soup.find("meta", attrs={"name": "generator"}):
                gen = soup.find("meta", attrs={"name": "generator"}).get("content", "")
                techs.append(f"Generator: {gen}")
            if soup.find("script", src=lambda x: x and "jquery" in x.lower()):
                techs.append("jQuery")
            for t in techs:
                tech_xml += f"<technology>{self._xml_escape(t)}</technology>\n"

        result = f"""<!-- Analyzed webpage: {url} -->
<webpage_analysis url="{self._xml_escape(url)}" timestamp="{datetime.now().isoformat()}" status_code="{resp.status_code}">
    <metadata>
{metadata_xml}
    </metadata>
    <links count="{links_count}">
{links_xml}
    </links>
    <forms>
{forms_xml}
    </forms>
    <media>
{media_xml}
    </media>
    <technologies>
{tech_xml}
    </technologies>
</webpage_analysis>"""
        return result

    # ------------------------------------------------------------------------
    # Инструмент check_security
    # ------------------------------------------------------------------------
    def _execute_check_security(self, arguments: Dict[str, Any]) -> str:
        url = arguments.get("url", "").strip()
        if not url:
            return self._format_error("URL is required")
        check_cert = arguments.get("check_certificate", True)
        follow_redirects = arguments.get("follow_redirects", True)

        try:
            with httpx.Client(timeout=10, follow_redirects=follow_redirects) as client:
                resp = client.get(url)
                final_url = str(resp.url)
        except Exception as e:
            return self._format_error(f"Failed to fetch {url}: {e}")

        headers = resp.headers
        security_headers = {
            "Strict-Transport-Security": "HSTS",
            "Content-Security-Policy": "CSP",
            "X-Frame-Options": "Clickjacking protection",
            "X-Content-Type-Options": "MIME sniffing prevention",
            "Referrer-Policy": "Referrer policy",
            "Permissions-Policy": "Permissions policy",
        }

        headers_xml = ""
        recommendations = []

        for header, desc in security_headers.items():
            value = headers.get(header)
            if value:
                headers_xml += f'<header name="{header}" present="true">{self._xml_escape(value)}</header>\n'
            else:
                headers_xml += f'<header name="{header}" present="false" />\n'
                recommendations.append(f"Missing {desc} header.")

        if final_url.startswith("https://"):
            headers_xml += '<https>enabled</https>\n'
        else:
            headers_xml += '<https>disabled</https>\n'
            recommendations.append("Site is served over HTTP. Consider using HTTPS.")

        cookies_xml = ""
        set_cookie = headers.get("set-cookie")
        if set_cookie:
            if "Secure" not in set_cookie:
                recommendations.append("Cookies missing 'Secure' flag.")
            if "HttpOnly" not in set_cookie:
                recommendations.append("Cookies missing 'HttpOnly' flag.")
            cookies_xml = f"<set-cookie>{self._xml_escape(set_cookie[:200])}</set-cookie>\n"
        else:
            cookies_xml = "<set-cookie>None</set-cookie>\n"

        cert_xml = ""
        if check_cert and final_url.startswith("https://"):
            try:
                import ssl
                import socket
                hostname = urlparse(final_url).hostname
                port = 443
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
                    s.connect((hostname, port))
                    cert = s.getpeercert()
                subject = dict(x[0] for x in cert['subject'])
                issuer = dict(x[0] for x in cert['issuer'])
                not_after = cert['notAfter']
                not_before = cert['notBefore']
                expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                now = datetime.utcnow()
                days_left = (expiry - now).days
                if days_left < 30:
                    recommendations.append(f"SSL certificate expires in {days_left} days.")
                cert_xml = f"""
    <certificate>
        <subject>{subject.get('commonName', '')}</subject>
        <issuer>{issuer.get('commonName', '')}</issuer>
        <not_before>{not_before}</not_before>
        <not_after>{not_after}</not_after>
        <days_left>{days_left}</days_left>
    </certificate>"""
            except Exception as e:
                cert_xml = f"<certificate error=\"{self._xml_escape(str(e))}\" />"

        rec_xml = ""
        for rec in recommendations[:10]:
            rec_xml += f"<recommendation>{self._xml_escape(rec)}</recommendation>\n"

        result = f"""<!-- Security check for: {url} -->
<security_report url="{self._xml_escape(url)}" final_url="{self._xml_escape(final_url)}" status_code="{resp.status_code}">
    <headers>
{headers_xml}
    </headers>
    <cookies>
{cookies_xml}
    </cookies>
    {cert_xml}
    <recommendations>
{rec_xml}
    </recommendations>
</security_report>"""
        return result

    # ------------------------------------------------------------------------
    # Инструмент extract_media
    # ------------------------------------------------------------------------
    def _execute_extract_media(self, arguments: Dict[str, Any]) -> str:
        url = arguments.get("url", "").strip()
        if not url:
            return self._format_error("URL is required")
        if not BS_AVAILABLE:
            return self._format_error("BeautifulSoup is not installed. Please install it: pip install beautifulsoup4")

        media_types = arguments.get("media_types", ["image", "video", "audio"])
        max_urls = min(arguments.get("max_urls", 50), 200)
        check_size = arguments.get("check_size", False)

        try:
            resp, html = self._fetch_html(url, timeout=10, max_length=200000)
        except Exception as e:
            return self._format_error(f"Failed to fetch webpage: {e}")

        soup = BeautifulSoup(html, 'html.parser')
        result_xml = f'<media_urls url="{self._xml_escape(url)}">\n'

        if "image" in media_types:
            images = []
            for img in soup.find_all("img", src=True):
                src = img["src"]
                abs_src = self._absolutize_url(src, url)
                images.append(abs_src)
            images = list(dict.fromkeys(images))[:max_urls]
            result_xml += "  <images>\n"
            for img in images:
                size_attr = ""
                if check_size:
                    try:
                        head = self._fetch_head(img, timeout=3)
                        cl = head.headers.get("content-length")
                        if cl:
                            size_attr = f' size="{cl}"'
                    except:
                        pass
                result_xml += f'    <image src="{self._xml_escape(img)}"{size_attr} />\n'
            result_xml += "  </images>\n"

        if "video" in media_types:
            videos = []
            for video in soup.find_all("video"):
                for src in video.find_all("src"):
                    videos.append(self._absolutize_url(src.get("src", ""), url))
            videos = list(dict.fromkeys(videos))[:max_urls]
            result_xml += "  <videos>\n"
            for v in videos:
                size_attr = ""
                if check_size:
                    try:
                        head = self._fetch_head(v, timeout=3)
                        cl = head.headers.get("content-length")
                        if cl:
                            size_attr = f' size="{cl}"'
                    except:
                        pass
                result_xml += f'    <video src="{self._xml_escape(v)}"{size_attr} />\n'
            result_xml += "  </videos>\n"

        if "audio" in media_types:
            audios = []
            for audio in soup.find_all("audio"):
                for src in audio.find_all("src"):
                    audios.append(self._absolutize_url(src.get("src", ""), url))
            audios = list(dict.fromkeys(audios))[:max_urls]
            result_xml += "  <audios>\n"
            for a in audios:
                size_attr = ""
                if check_size:
                    try:
                        head = self._fetch_head(a, timeout=3)
                        cl = head.headers.get("content-length")
                        if cl:
                            size_attr = f' size="{cl}"'
                    except:
                        pass
                result_xml += f'    <audio src="{self._xml_escape(a)}"{size_attr} />\n'
            result_xml += "  </audios>\n"

        result_xml += "</media_urls>"
        return result_xml    
    
    def register_tool(self, name: str, func: Callable) -> None:
        """
        Register a custom tool.
        
        Args:
            name: Tool name (must be unique)
            func: Tool function (must accept **kwargs and return str)
        """
        if name in ["read_file", "search_code", "web_search"]:
            raise ValueError(f"Cannot override built-in tool: {name}")
        
        self._custom_tools[name] = func
        logger.info(f"Registered custom tool: {name}")
    
    def update_index(self, index: Dict[str, Any]) -> None:
        """Update the project index"""
        self.index = index
    
    def update_virtual_fs(self, virtual_fs: Any) -> None:
        """Update the VirtualFileSystem instance"""
        self.virtual_fs = virtual_fs
    
    def _format_error(self, message: str) -> str:
        """Format error message"""
        return f"""<!-- ERROR -->
<error>
  <message>{message}</message>
</error>"""


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    project_dir: str,
    virtual_fs: Optional[Any] = None,  # NEW
    index: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Execute a tool (convenience function).
    
    Creates a ToolExecutor instance and executes the tool.
    For repeated calls, prefer creating a ToolExecutor instance directly.
    
    Args:
        tool_name: Name of the tool
        arguments: Tool arguments
        project_dir: Path to project root
        index: Project semantic index
        
    Returns:
        Tool output as string
    """
    executor = ToolExecutor(project_dir=project_dir, index=index)
    return executor.execute(tool_name, arguments)


def parse_tool_call(tool_call: Dict[str, Any]) -> tuple:
    """
    Parse a tool call from LLM response.
    
    Args:
        tool_call: Tool call dict from LLM response
        
    Returns:
        Tuple of (tool_name, arguments_dict, tool_call_id)
    """
    func_info = tool_call.get("function", {})
    tool_name = func_info.get("name", "")
    arguments_str = func_info.get("arguments", "{}")
    tool_call_id = tool_call.get("id", "")
    
    try:
        arguments = json.loads(arguments_str)
    except json.JSONDecodeError:
        arguments = {}
    
    return tool_name, arguments, tool_call_id