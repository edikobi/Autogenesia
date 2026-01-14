# app/utils/xml_parser.py
"""
XML Parser для приёма и разбора ответов от ИИ.

Обратная операция к XMLWrapper:
- Извлекает код из XML-ответов ИИ
- Сохраняет форматирование и отступы
- Валидирует синтаксис (опционально)
- Поддерживает разные форматы ответов (XML, Markdown code blocks, plain)

Форматы ответов ИИ, которые поддерживаем:
1. XML: <code>, <file>, <chunk>, <content>
2. Markdown: ``````
3. Plain: просто код
"""

from __future__ import annotations
import re
import ast
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class CodeLanguage(Enum):
    PYTHON = "python"
    GO = "go"
    SQL = "sql"
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass
class ParsedCodeBlock:
    """Результат парсинга блока кода от ИИ."""
    content: str                    # Сам код
    language: CodeLanguage          # Язык
    file_path: Optional[str]        # Путь к файлу (если указан)
    chunk_kind: Optional[str]       # Тип чанка (class, method, function...)
    chunk_name: Optional[str]       # Имя чанка
    start_line: Optional[int]       # Начальная строка (для замены)
    end_line: Optional[int]         # Конечная строка
    base_indent: int                # Базовый отступ
    is_valid_syntax: bool           # Валидный ли синтаксис
    syntax_error: Optional[str]     # Ошибка синтаксиса (если есть)
    metadata: Dict[str, Any] = field(default_factory=dict)


class XMLResponseParser:
    """
    Парсер ответов от ИИ.
    
    Умеет извлекать код из:
    - XML тегов (<code>, <file>, <chunk>)
    - CDATA секций
    - Markdown code blocks (``````)
    - Чистого текста
    
    После извлечения:
    - Проверяет синтаксис (для Python)
    - Определяет базовый отступ
    - Сохраняет метаданные
    """

    # Паттерны для разных форматов
    PATTERNS = {
        # XML с CDATA
        'xml_cdata': re.compile(
            r'<(?P<tag>code|file|chunk|content)[^>]*>.*?<!\[CDATA\[\n?(?P<content>.*?)\n?\]\]>.*?</(?P=tag)>',
            re.DOTALL
        ),
        # XML без CDATA
        'xml_plain': re.compile(
            r'<(?P<tag>code|file|chunk|content)(?P<attrs>[^>]*)>(?P<content>.*?)</(?P=tag)>',
            re.DOTALL
        ),
        # Markdown code block
        'markdown': re.compile(
            r'``````',
            re.DOTALL
        ),
        # XML атрибуты
        'attr': re.compile(r'(\w+)=["\']([^"\']*)["\']'),
    }

    # Маппинг языков
    LANGUAGE_MAP = {
        'python': CodeLanguage.PYTHON,
        'py': CodeLanguage.PYTHON,
        'go': CodeLanguage.GO,
        'golang': CodeLanguage.GO,
        'sql': CodeLanguage.SQL,
        'json': CodeLanguage.JSON,
        'markdown': CodeLanguage.MARKDOWN,
        'md': CodeLanguage.MARKDOWN,
        'text': CodeLanguage.TEXT,
        'txt': CodeLanguage.TEXT,
        '': CodeLanguage.UNKNOWN,
    }

    def __init__(self, validate_syntax: bool = True):
        self.validate_syntax = validate_syntax

    def parse(self, response: str) -> List[ParsedCodeBlock]:
        """
        Парсит ответ от ИИ и извлекает все блоки кода.
        
        Args:
            response: Полный ответ от ИИ
            
        Returns:
            Список ParsedCodeBlock с извлечённым кодом
        """
        blocks = []
        
        # 1. Пробуем XML с CDATA
        for match in self.PATTERNS['xml_cdata'].finditer(response):
            block = self._parse_xml_match(match, has_cdata=True)
            if block:
                blocks.append(block)
        
        # 2. Пробуем XML без CDATA (если не нашли с CDATA)
        if not blocks:
            for match in self.PATTERNS['xml_plain'].finditer(response):
                block = self._parse_xml_match(match, has_cdata=False)
                if block:
                    blocks.append(block)
        
        # 3. Пробуем Markdown code blocks
        if not blocks:
            for match in self.PATTERNS['markdown'].finditer(response):
                block = self._parse_markdown_match(match)
                if block:
                    blocks.append(block)
        
        # 4. Если ничего не нашли — пробуем извлечь код эвристически
        if not blocks:
            block = self._parse_plain_code(response)
            if block:
                blocks.append(block)
        
        return blocks

    def parse_single(self, response: str) -> Optional[ParsedCodeBlock]:
        """Парсит ответ и возвращает первый блок кода."""
        blocks = self.parse(response)
        return blocks[0] if blocks else None

    def _parse_xml_match(self, match: re.Match, has_cdata: bool) -> Optional[ParsedCodeBlock]:
        """Парсит XML-совпадение."""
        tag = match.group('tag')
        content = match.group('content')
        
        # Восстанавливаем экранированные CDATA terminators
        if has_cdata:
            content = content.replace(']]]]><![CDATA[>', ']]>')
        else:
            # Декодируем HTML entities
            content = self._decode_html_entities(content)
        
        # Извлекаем атрибуты
        attrs = {}
        full_match = match.group(0)
        tag_match = re.search(rf'<{tag}([^>]*)>', full_match)
        if tag_match:
            attr_str = tag_match.group(1)
            for attr_match in self.PATTERNS['attr'].finditer(attr_str):
                attrs[attr_match.group(1)] = attr_match.group(2)
        
        # Определяем язык
        language = self._detect_language(
            attrs.get('type', ''),
            attrs.get('lang', ''),
            attrs.get('language', ''),
            content
        )
        
        # Базовый отступ
        base_indent = int(attrs.get('base_indent', 0)) or self._detect_base_indent(content)
        
        # Валидация синтаксиса
        is_valid, error = self._validate_syntax(content, language)
        
        return ParsedCodeBlock(
            content=content,
            language=language,
            file_path=attrs.get('file') or attrs.get('path'),
            chunk_kind=attrs.get('kind'),
            chunk_name=attrs.get('name'),
            start_line=int(attrs['lines'].split('-')[0]) if 'lines' in attrs else None,
            end_line=int(attrs['lines'].split('-')[1]) if 'lines' in attrs and '-' in attrs['lines'] else None,
            base_indent=base_indent,
            is_valid_syntax=is_valid,
            syntax_error=error,
            metadata=attrs,
        )

    def _parse_markdown_match(self, match: re.Match) -> Optional[ParsedCodeBlock]:
        """Парсит Markdown code block."""
        lang_str = match.group('lang').lower()
        content = match.group('content')
        
        # Убираем trailing newline если есть
        content = content.rstrip('\n')
        
        language = self.LANGUAGE_MAP.get(lang_str, CodeLanguage.UNKNOWN)
        base_indent = self._detect_base_indent(content)
        is_valid, error = self._validate_syntax(content, language)
        
        return ParsedCodeBlock(
            content=content,
            language=language,
            file_path=None,
            chunk_kind=None,
            chunk_name=None,
            start_line=None,
            end_line=None,
            base_indent=base_indent,
            is_valid_syntax=is_valid,
            syntax_error=error,
            metadata={'source': 'markdown'},
        )

    def _parse_plain_code(self, response: str) -> Optional[ParsedCodeBlock]:
        """
        Эвристический парсинг: пытаемся найти код в тексте.
        Ищем характерные паттерны (def, class, func, CREATE TABLE и т.д.)
        """
        # Убираем явный текст до/после кода
        lines = response.strip().split('\n')
        
        # Ищем начало кода
        code_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (stripped.startswith('def ') or 
                stripped.startswith('class ') or
                stripped.startswith('import ') or
                stripped.startswith('from ') or
                stripped.startswith('func ') or
                stripped.startswith('package ') or
                stripped.startswith('CREATE ') or
                stripped.startswith('SELECT ')):
                code_start = i
                break
        
        # Если не нашли характерных паттернов — возвращаем всё как есть
        content = '\n'.join(lines[code_start:])
        
        if not content.strip():
            return None
        
        # Пытаемся определить язык
        language = self._detect_language_from_content(content)
        base_indent = self._detect_base_indent(content)
        is_valid, error = self._validate_syntax(content, language)
        
        return ParsedCodeBlock(
            content=content,
            language=language,
            file_path=None,
            chunk_kind=None,
            chunk_name=None,
            start_line=None,
            end_line=None,
            base_indent=base_indent,
            is_valid_syntax=is_valid,
            syntax_error=error,
            metadata={'source': 'plain'},
        )

    def _detect_language(self, *hints: str, content: str = "") -> CodeLanguage:
        """Определяет язык по подсказкам и контенту."""
        for hint in hints:
            hint_lower = hint.lower()
            if 'python' in hint_lower or hint_lower == 'py':
                return CodeLanguage.PYTHON
            if 'go' in hint_lower:
                return CodeLanguage.GO
            if 'sql' in hint_lower:
                return CodeLanguage.SQL
            if 'json' in hint_lower:
                return CodeLanguage.JSON
        
        # Fallback: определяем по контенту
        return self._detect_language_from_content(content)

    def _detect_language_from_content(self, content: str) -> CodeLanguage:
        """Определяет язык по содержимому."""
        # Python
        if re.search(r'^(def |class |import |from |async def )', content, re.MULTILINE):
            return CodeLanguage.PYTHON
        
        # Go
        if re.search(r'^(package |func |type .* struct)', content, re.MULTILINE):
            return CodeLanguage.GO
        
        # SQL
        if re.search(r'^(CREATE |SELECT |INSERT |UPDATE |DELETE |ALTER )', content, re.MULTILINE | re.IGNORECASE):
            return CodeLanguage.SQL
        
        # JSON
        content_stripped = content.strip()
        if (content_stripped.startswith('{') and content_stripped.endswith('}')) or \
           (content_stripped.startswith('[') and content_stripped.endswith(']')):
            return CodeLanguage.JSON
        
        return CodeLanguage.UNKNOWN

    def _detect_base_indent(self, content: str) -> int:
        """Определяет минимальный отступ в коде."""
        lines = content.split('\n')
        min_indent = float('inf')
        
        for line in lines:
            if line.strip():
                indent = len(line) - len(line.lstrip())
                if indent < min_indent:
                    min_indent = indent
        
        return min_indent if min_indent != float('inf') else 0

    def _validate_syntax(self, content: str, language: CodeLanguage) -> tuple[bool, Optional[str]]:
        """Проверяет синтаксис кода."""
        if not self.validate_syntax:
            return True, None
        
        if language == CodeLanguage.PYTHON:
            try:
                ast.parse(content)
                return True, None
            except SyntaxError as e:
                return False, f"Line {e.lineno}: {e.msg}"
        
        if language == CodeLanguage.JSON:
            import json
            try:
                json.loads(content)
                return True, None
            except json.JSONDecodeError as e:
                return False, f"Line {e.lineno}: {e.msg}"
        
        # Для Go и SQL — просто возвращаем True (нет встроенного парсера)
        return True, None

    def _decode_html_entities(self, text: str) -> str:
        """Декодирует HTML entities."""
        return (text
                .replace('&amp;', '&')
                .replace('&lt;', '<')
                .replace('&gt;', '>')
                .replace('&quot;', '"')
                .replace('&#39;', "'"))


# ==================== УТИЛИТЫ ====================

def extract_code_from_response(response: str, validate: bool = True) -> Optional[str]:
    """
    Быстрый способ извлечь код из ответа ИИ.
    Возвращает только контент (строку).
    """
    parser = XMLResponseParser(validate_syntax=validate)
    block = parser.parse_single(response)
    return block.content if block else None


def extract_and_validate(response: str, expected_language: CodeLanguage = CodeLanguage.PYTHON) -> tuple[Optional[str], bool, Optional[str]]:
    """
    Извлекает код и валидирует его.
    
    Returns:
        (code, is_valid, error_message)
    """
    parser = XMLResponseParser(validate_syntax=True)
    block = parser.parse_single(response)
    
    if not block:
        return None, False, "No code found in response"
    
    if block.language != expected_language and block.language != CodeLanguage.UNKNOWN:
        return block.content, False, f"Expected {expected_language.value}, got {block.language.value}"
    
    return block.content, block.is_valid_syntax, block.syntax_error


class CodeBlockBuilder:
    """
    Билдер для формирования ответа в формате, который поймёт XMLParser.
    
    Используется когда ИИ должен вернуть код в определённом формате,
    и мы хотим дать ему шаблон/инструкцию.
    """
    
    @staticmethod
    def get_response_format_instruction(language: str = "python") -> str:
        """
        Возвращает инструкцию для ИИ, как форматировать ответ с кодом.
        """
        return f'''When providing code, wrap it in XML tags with CDATA:

<code language="{language}" file="path/to/file.py" kind="method" name="method_name" lines="10-25">
<![CDATA[
    def method_name(self, arg):
        # Your code here
        pass
]]>
</code>

Or use Markdown code blocks:

def method_name(self, arg):
# Your code here
pass

Important:
- Preserve all indentation exactly
- Include complete, syntactically valid code
- Specify file path and line numbers when replacing existing code'''

    @staticmethod
    def format_code_for_ai(code: str, language: str, file_path: str = None,
                           kind: str = None, name: str = None, 
                           lines: str = None) -> str:
        """
        Форматирует код для отправки ИИ в качестве примера/контекста.
        """
        attrs = [f'language="{language}"']
        if file_path:
            attrs.append(f'file="{file_path}"')
        if kind:
            attrs.append(f'kind="{kind}"')
        if name:
            attrs.append(f'name="{name}"')
        if lines:
            attrs.append(f'lines="{lines}"')
        
        attrs_str = " ".join(attrs)
        
        # Экранируем ]]> в коде
        safe_code = code.replace(']]>', ']]]]><![CDATA[>')
        
        return f'''<code {attrs_str}>
<![CDATA[
{safe_code}
]]>
</code>'''
