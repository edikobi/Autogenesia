# app/tools/web_tools.py
"""
Web Tools - Tools for fetching and analyzing web pages.

Features:
- Raw HTML fetching (fetch_webpage)
- Structural analysis (analyze_webpage)
- Security checks (check_security)
- Media extraction (extract_media)
- User-Agent spoofing to avoid basic bot detection
"""

import logging
import httpx
import re
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse, urljoin
from datetime import datetime

try:
    from bs4 import BeautifulSoup
    BS_AVAILABLE = True
except ImportError:
    BS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Modern browser headers to avoid basic bot detection
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def _validate_url(url: str) -> bool:
    """Check if URL is safe and valid."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    host = parsed.hostname
    if not host:
        return False
    forbidden = ('localhost', '127.0.0.1', '::1', '0.0.0.0')
    if host in forbidden:
        return False
    return True

def _xml_escape(s: str) -> str:
    """Escape special characters for XML."""
    if not isinstance(s, str):
        s = str(s)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;"))

def _absolutize_url(src: str, base_url: str) -> str:
    """Convert relative URL to absolute based on base_url."""
    return urljoin(base_url, src)

def _fetch_html_internal(url: str, timeout: int = 10, max_length: int = 100000) -> Tuple[httpx.Response, str]:
    """Internal helper to fetch HTML with default headers."""
    if not _validate_url(url):
        raise ValueError(f"URL not allowed or invalid: {url}")
    
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
        resp = client.get(url)
        resp.raise_for_status()
        
        # NEW: Fix for Russian encodings (Windows-1251, etc.)
        # httpx uses headers by default, which can be wrong for legacy sites.
        # apparent_encoding uses charset-normalizer to detect real encoding from bytes.
        if resp.encoding != 'utf-8':
            resp.encoding = resp.apparent_encoding
            
        content = resp.text
        if len(content) > max_length:
            content = content[:max_length] + "\n... (truncated due to length limit)"
        return resp, content

def fetch_webpage_tool(url: str, max_length: int = 100000, timeout: int = 10) -> str:
    """Fetch raw HTML content of a webpage."""
    url = url.strip()
    if not url:
        return "<!-- ERROR: URL is required -->"
    
    try:
        resp, html = _fetch_html_internal(url, timeout=timeout, max_length=max_length)
    except Exception as e:
        return f"<!-- ERROR: Failed to fetch webpage: {str(e)} -->"

    # Escape CDATA end marker if present in HTML
    safe_html = html.replace("]]>", "]]]]><![CDATA[>")
    
    return f"""<!-- Fetched webpage: {url} -->
<webpage url="{_xml_escape(url)}" status_code="{resp.status_code}" content_type="{_xml_escape(resp.headers.get('content-type', ''))}">
    <content length="{len(html)}"><![CDATA[
{safe_html}
    ]]></content>
</webpage>"""

def analyze_webpage_tool(
    url: str, 
    extract_links: bool = True,
    extract_metadata: bool = True,
    extract_forms: bool = True,
    extract_media: bool = True,
    extract_technologies: bool = False,
    max_links: int = 100
) -> str:
    """Perform structural analysis of a webpage."""
    url = url.strip()
    if not url:
        return "<!-- ERROR: URL is required -->"
    
    if not BS_AVAILABLE:
        return "<!-- ERROR: BeautifulSoup is not installed. Install with: pip install beautifulsoup4 -->"

    try:
        resp, html = _fetch_html_internal(url, timeout=15, max_length=200000)
    except Exception as e:
        return f"<!-- ERROR: Failed to fetch webpage: {str(e)} -->"

    soup = BeautifulSoup(html, 'html.parser')
    max_links = min(max_links, 500)

    metadata_xml = ""
    if extract_metadata:
        title = soup.title.string if soup.title else ""
        metadata_xml += f"  <title>{_xml_escape(title)}</title>\n"
        for meta in soup.find_all("meta"):
            name = meta.get("name") or meta.get("property")
            content = meta.get("content", "")
            if name and content:
                metadata_xml += f'  <meta name="{_xml_escape(name)}" content="{_xml_escape(content)}" />\n'

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
            links_xml += f'  <link href="{_xml_escape(href)}">{_xml_escape(text)}</link>\n'

    forms_xml = ""
    if extract_forms:
        for form in soup.find_all("form"):
            action = form.get("action", "")
            method = form.get("method", "get").upper()
            inputs = []
            for inp in form.find_all(["input", "select", "textarea"]):
                name = inp.get("name")
                if name:
                    inputs.append(f'{name} ({inp.name}/{inp.get("type", "text")})')
            forms_xml += f'  <form action="{_xml_escape(action)}" method="{method}">\n'
            for inp_desc in inputs:
                forms_xml += f'    <input>{_xml_escape(inp_desc)}</input>\n'
            forms_xml += '  </form>\n'

    media_xml = ""
    if extract_media:
        images = []
        for img in soup.find_all("img", src=True):
            src = img["src"]
            alt = img.get("alt", "")[:100]
            images.append((src, alt))
        for src, alt in images[:50]:
            media_xml += f'  <image src="{_xml_escape(src)}" alt="{_xml_escape(alt)}" />\n'

        for video in soup.find_all("video"):
            sources = video.find_all("source")
            if sources:
                for src in sources:
                    media_xml += f'  <video src="{_xml_escape(src.get("src", ""))}" />\n'
            elif video.get("src"):
                media_xml += f'  <video src="{_xml_escape(video.get("src"))}" />\n'
        
        for audio in soup.find_all("audio"):
            sources = audio.find_all("source")
            if sources:
                for src in sources:
                    media_xml += f'  <audio src="{_xml_escape(src.get("src", ""))}" />\n'
            elif audio.get("src"):
                media_xml += f'  <audio src="{_xml_escape(audio.get("src"))}" />\n'

    tech_xml = ""
    if extract_technologies:
        techs = []
        if soup.find("meta", attrs={"name": "generator"}):
            gen = soup.find("meta", attrs={"name": "generator"}).get("content", "")
            techs.append(f"Generator: {gen}")
        if soup.find("script", src=lambda x: x and "jquery" in x.lower()):
            techs.append("jQuery")
        for t in techs:
            tech_xml += f"  <technology>{_xml_escape(t)}</technology>\n"

    return f"""<!-- Analyzed webpage: {url} -->
<webpage_analysis url="{_xml_escape(url)}" timestamp="{datetime.now().isoformat()}" status_code="{resp.status_code}">
<metadata>
{metadata_xml}</metadata>
<links count="{links_count}">
{links_xml}</links>
<forms>
{forms_xml}</forms>
<media>
{media_xml}</media>
<technologies>
{tech_xml}</technologies>
</webpage_analysis>"""

def check_security_tool(url: str, check_certificate: bool = True, follow_redirects: bool = True) -> str:
    """Evaluate basic security posture of a website."""
    url = url.strip()
    if not url:
        return "<!-- ERROR: URL is required -->"

    try:
        with httpx.Client(timeout=10, follow_redirects=follow_redirects, headers=DEFAULT_HEADERS) as client:
            resp = client.get(url)
            final_url = str(resp.url)
    except Exception as e:
        return f"<!-- ERROR: Failed to fetch {url}: {str(e)} -->"

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
            headers_xml += f'  <header name="{header}" present="true">{_xml_escape(value)}</header>\n'
        else:
            headers_xml += f'  <header name="{header}" present="false" />\n'
            recommendations.append(f"Missing {desc} header ({header}).")

    if final_url.startswith("https://"):
        headers_xml += '  <https>enabled</https>\n'
    else:
        headers_xml += '  <https>disabled</https>\n'
        recommendations.append("Site is served over HTTP. Consider using HTTPS.")

    cookies_xml = ""
    set_cookie = headers.get("set-cookie")
    if set_cookie:
        if "Secure" not in set_cookie:
            recommendations.append("Cookies missing 'Secure' flag.")
        if "HttpOnly" not in set_cookie:
            recommendations.append("Cookies missing 'HttpOnly' flag.")
        cookies_xml = f"  <set-cookie>{_xml_escape(set_cookie[:200])}</set-cookie>\n"
    else:
        cookies_xml = "  <set-cookie>None</set-cookie>\n"

    cert_xml = ""
    if check_certificate and final_url.startswith("https://"):
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
            
            # Simple expiry check
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
            now = datetime.utcnow()
            days_left = (expiry - now).days
            
            if days_left < 30:
                recommendations.append(f"SSL certificate expires in {days_left} days.")
            
            cert_xml = f"""
<certificate>
    <subject>{_xml_escape(subject.get('commonName', ''))}</subject>
    <issuer>{_xml_escape(issuer.get('commonName', ''))}</issuer>
    <not_before>{not_before}</not_before>
    <not_after>{not_after}</not_after>
    <days_left>{days_left}</days_left>
</certificate>"""
        except Exception as e:
            cert_xml = f'\n<certificate error="{_xml_escape(str(e))}" />'

    rec_xml = ""
    for rec in recommendations[:15]:
        rec_xml += f"  <recommendation>{_xml_escape(rec)}</recommendation>\n"

    return f"""<!-- Security report for: {url} -->
<security_report url="{_xml_escape(url)}" final_url="{_xml_escape(final_url)}" status_code="{resp.status_code}">
<headers>
{headers_xml}</headers>
<cookies>
{cookies_xml}</cookies>{cert_xml}
<recommendations>
{rec_xml}</recommendations>
</security_report>"""

def extract_media_tool(url: str, media_types: List[str] = None, max_urls: int = 50, check_size: bool = False) -> str:
    """Extract URLs of media resources (images, videos, audio) from a webpage."""
    url = url.strip()
    if not url:
        return "<!-- ERROR: URL is required -->"
    
    if not BS_AVAILABLE:
        return "<!-- ERROR: BeautifulSoup is not installed. -->"

    if not media_types:
        media_types = ["image", "video", "audio"]
    
    max_urls = min(max_urls, 200)

    try:
        resp, html = _fetch_html_internal(url, timeout=15, max_length=250000)
    except Exception as e:
        return f"<!-- ERROR: Failed to fetch webpage: {str(e)} -->"

    soup = BeautifulSoup(html, 'html.parser')
    result_xml = f'<media_urls url="{_xml_escape(url)}">\n'

    def _get_size(resource_url: str) -> str:
        if not check_size:
            return ""
        try:
            with httpx.Client(timeout=5, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
                head = client.head(resource_url)
                cl = head.headers.get("content-length")
                return f' size="{cl}"' if cl else ""
        except:
            return ""

    if "image" in media_types:
        images = []
        for img in soup.find_all("img", src=True):
            abs_src = _absolutize_url(img["src"], url)
            images.append(abs_src)
        images = list(dict.fromkeys(images))[:max_urls]
        result_xml += "  <images>\n"
        for img in images:
            result_xml += f'    <image src="{_xml_escape(img)}"{_get_size(img)} />\n'
        result_xml += "  </images>\n"

    if "video" in media_types:
        videos = []
        for video in soup.find_all("video"):
            sources = video.find_all("source")
            if sources:
                for src in sources:
                    videos.append(_absolutize_url(src.get("src", ""), url))
            elif video.get("src"):
                videos.append(_absolutize_url(video.get("src"), url))
        videos = list(dict.fromkeys(videos))[:max_urls]
        result_xml += "  <videos>\n"
        for v in videos:
            result_xml += f'    <video src="{_xml_escape(v)}"{_get_size(v)} />\n'
        result_xml += "  </videos>\n"

    if "audio" in media_types:
        audios = []
        for audio in soup.find_all("audio"):
            sources = audio.find_all("source")
            if sources:
                for src in sources:
                    audios.append(_absolutize_url(src.get("src", ""), url))
            elif audio.get("src"):
                audios.append(_absolutize_url(audio.get("src"), url))
        audios = list(dict.fromkeys(audios))[:max_urls]
        result_xml += "  <audios>\n"
        for a in audios:
            result_xml += f'    <audio src="{_xml_escape(a)}"{_get_size(a)} />\n'
        result_xml += "  </audios>\n"

    result_xml += "</media_urls>"
    return result_xml
