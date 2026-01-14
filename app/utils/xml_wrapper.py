# app/utils/xml_wrapper.py
"""
XML Wrapper для передачи кода и текста в ИИ.

Особенности:
- Использует CDATA для сохранения форматирования (отступы, спецсимволы)
- Добавляет метаданные (путь, тип, токены, строки)
- Указывает base_indent для чанков, чтобы ИИ понимал уровень вложенности
- Поддерживает batch-обёртку нескольких файлов/чанков
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Optional, Union
from pathlib import Path

from app.services.python_chunker import PythonChunk
from app.services.go_chunker import GoChunk
from app.services.sql_chunker import SQLChunk

# Тип для любого чанка
AnyChunk = Union[PythonChunk, GoChunk, SQLChunk]


@dataclass
class FileContent:
    """Контейнер для содержимого файла."""
    path: str
    file_type: str
    content: str
    tokens: int
    encoding: str = "utf-8"


class XMLWrapper:
    """
    Центральный класс для XML-обёртки кода и текста.
    
    Гарантирует:
    - Сохранение всех отступов и форматирования
    - Читаемость для ИИ (метаданные о структуре)
    - Возможность восстановить оригинал из XML
    """

    # Теги для разных уровней
    TAG_FILE = "file"
    TAG_CHUNK = "chunk"
    TAG_CONTENT = "content"
    TAG_CONTEXT = "context"
    TAG_BATCH = "files"

    def __init__(self):
        pass

    # ==================== ФАЙЛЫ ====================

    def wrap_file(self, file: FileContent, include_line_numbers: bool = False) -> str:
        """
        Оборачивает полный файл в XML.
        
        Args:
            file: Контент файла
            include_line_numbers: Добавить номера строк (для отладки)
        
        Returns:
            XML-строка с файлом
        """
        content = file.content
        
        if include_line_numbers:
            content = self._add_line_numbers(content)
        
        # Экранируем CDATA-закрывающую последовательность если она есть в коде
        safe_content = self._escape_cdata(content)
        
        xml = f'''<{self.TAG_FILE} path="{self._escape_attr(file.path)}" type="{file.file_type}" tokens="{file.tokens}" encoding="{file.encoding}">
<{self.TAG_CONTENT}><![CDATA[
{safe_content}
]]></{self.TAG_CONTENT}>
</{self.TAG_FILE}>'''
        
        return xml

    def wrap_file_from_path(self, file_path: str, file_type: str, tokens: int) -> str:
        """Читает файл и оборачивает в XML."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        
        file = FileContent(
            path=str(path),
            file_type=file_type,
            content=content,
            tokens=tokens
        )
        
        return self.wrap_file(file)

    # ==================== ЧАНКИ ====================

    def wrap_chunk(self, chunk: AnyChunk, include_context: bool = False, 
                   context_chunks: Optional[List[AnyChunk]] = None) -> str:
        """
        Оборачивает чанк (класс, метод, функцию) в XML.
        
        Особенности:
        - Сохраняет оригинальные отступы
        - Добавляет base_indent для понимания уровня вложенности
        - Опционально включает контекст (импорты, родительский класс)
        
        Args:
            chunk: Чанк кода
            include_context: Добавить контекст (импорты/globals)
            context_chunks: Список чанков для извлечения контекста
        """
        # Определяем базовый отступ чанка
        base_indent = self._detect_base_indent(chunk.content)
        
        # Определяем тип чанка (для разных Chunk классов)
        chunk_kind = getattr(chunk, 'kind', 'unknown')
        chunk_name = getattr(chunk, 'name', '')
        chunk_parent = getattr(chunk, 'parent', None) or getattr(chunk, 'receiver', None)
        
        # Безопасный контент
        safe_content = self._escape_cdata(chunk.content)
        
        # Формируем атрибуты
        attrs = [
            f'file="{self._escape_attr(chunk.file_path)}"',
            f'kind="{chunk_kind}"',
            f'name="{self._escape_attr(chunk_name)}"',
            f'lines="{chunk.start_line}-{chunk.end_line}"',
            f'tokens="{chunk.tokens}"',
            f'base_indent="{base_indent}"',
        ]
        
        if chunk_parent:
            attrs.append(f'parent="{self._escape_attr(chunk_parent)}"')
        
        attrs_str = " ".join(attrs)
        
        # Собираем XML
        parts = []
        
        # Контекст (если запрошен)
        if include_context and context_chunks:
            context_xml = self._build_context(context_chunks, chunk)
            if context_xml:
                parts.append(context_xml)
        
        # Сам чанк
        parts.append(f'''<{self.TAG_CHUNK} {attrs_str}>
<{self.TAG_CONTENT}><![CDATA[
{safe_content}
]]></{self.TAG_CONTENT}>
</{self.TAG_CHUNK}>''')
        
        return "\n".join(parts)

    def wrap_chunks(self, chunks: List[AnyChunk], include_context: bool = True,
                    all_file_chunks: Optional[List[AnyChunk]] = None) -> str:
        """
        Оборачивает несколько чанков в XML (batch).
        
        Полезно для передачи нескольких методов одного класса
        или связанных функций.
        """
        parts = [f'<{self.TAG_BATCH} count="{len(chunks)}">']
        
        for chunk in chunks:
            # Пропускаем file-чанки
            if getattr(chunk, 'kind', '') == 'file':
                continue
            
            chunk_xml = self.wrap_chunk(
                chunk, 
                include_context=include_context,
                context_chunks=all_file_chunks
            )
            parts.append(chunk_xml)
            # Добавляем контекст только к первому чанку
            include_context = False
        
        parts.append(f'</{self.TAG_BATCH}>')
        
        return "\n".join(parts)

    # ==================== ТЕКСТ (MD, TXT) ====================

    def wrap_text(self, content: str, file_path: str, file_type: str = "text/plain") -> str:
        """
        Оборачивает обычный текст (.md, .txt) в XML.
        Передаём как есть, без модификаций.
        """
        safe_content = self._escape_cdata(content)
        
        # Считаем токены
        from app.utils.token_counter import TokenCounter
        tokens = TokenCounter().count(content)
        
        return f'''<{self.TAG_FILE} path="{self._escape_attr(file_path)}" type="{file_type}" tokens="{tokens}">
<{self.TAG_CONTENT}><![CDATA[
{safe_content}
]]></{self.TAG_CONTENT}>
</{self.TAG_FILE}>'''

    # ==================== КОНТЕКСТ ====================

    def _build_context(self, chunks: List[AnyChunk], target_chunk: AnyChunk) -> Optional[str]:
        """
        Строит XML-контекст для чанка.
        Включает: imports, globals, родительский класс (для методов).
        """
        context_parts = []
        
        # Ищем imports
        imports = next((c for c in chunks if getattr(c, 'kind', '') == 'imports'), None)
        if imports:
            safe_imports = self._escape_cdata(imports.content)
            context_parts.append(f'''  <imports tokens="{imports.tokens}"><![CDATA[
{safe_imports}
]]></imports>''')
        
        # Ищем globals
        globals_chunk = next((c for c in chunks if getattr(c, 'kind', '') == 'globals'), None)
        if globals_chunk:
            safe_globals = self._escape_cdata(globals_chunk.content)
            context_parts.append(f'''  <globals tokens="{globals_chunk.tokens}"><![CDATA[
{safe_globals}
]]></globals>''')
        
        # Для методов — добавляем сигнатуру класса (без тела методов)
        target_parent = getattr(target_chunk, 'parent', None) or getattr(target_chunk, 'receiver', None)
        if target_parent and getattr(target_chunk, 'kind', '') == 'method':
            parent_class = next(
                (c for c in chunks if getattr(c, 'kind', '') in ('class', 'struct') 
                 and getattr(c, 'name', '') == target_parent),
                None
            )
            if parent_class:
                # Извлекаем только заголовок класса (без полного тела)
                class_header = self._extract_class_header(parent_class.content)
                if class_header:
                    safe_header = self._escape_cdata(class_header)
                    context_parts.append(f'''  <parent_class name="{target_parent}"><![CDATA[
{safe_header}
]]></parent_class>''')
        
        if not context_parts:
            return None
        
        return f'''<{self.TAG_CONTEXT}>
{"".join(context_parts)}
</{self.TAG_CONTEXT}>'''

    def _extract_class_header(self, class_content: str) -> str:
        """
        Извлекает заголовок класса без полного тела методов.
        Для Python: class Name(Base):\n    '''docstring'''\n    attr = ...
        """
        lines = class_content.split('\n')
        header_lines = []
        in_docstring = False
        docstring_char = None
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Первая строка — всегда class definition
            if i == 0:
                header_lines.append(line)
                continue
            
            # Docstring
            if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
                docstring_char = stripped[:3]
                header_lines.append(line)
                if stripped.count(docstring_char) >= 2 and len(stripped) > 3:
                    continue
                in_docstring = True
                continue
            
            if in_docstring:
                header_lines.append(line)
                if docstring_char in stripped:
                    in_docstring = False
                continue
            
            # Атрибуты класса (присваивания на уровне класса)
            if stripped and not stripped.startswith('def ') and not stripped.startswith('async def '):
                # Проверяем уровень отступа (должен быть 1 уровень)
                indent = len(line) - len(line.lstrip())
                if indent <= 8:  # Примерно 1-2 уровня отступа
                    # Это атрибут или аннотация
                    if '=' in stripped or ':' in stripped:
                        header_lines.append(line)
                        continue
            
            # Встретили метод — останавливаемся, добавляем placeholder
            if stripped.startswith('def ') or stripped.startswith('async def '):
                header_lines.append("    # ... methods ...")
                break
        
        return '\n'.join(header_lines)

    # ==================== УТИЛИТЫ ====================

    def _detect_base_indent(self, content: str) -> int:
        """
        Определяет базовый отступ кода (минимальный отступ непустых строк).
        Важно для понимания уровня вложенности чанка.
        """
        lines = content.split('\n')
        min_indent = float('inf')
        
        for line in lines:
            if line.strip():  # Непустая строка
                indent = len(line) - len(line.lstrip())
                if indent < min_indent:
                    min_indent = indent
        
        return min_indent if min_indent != float('inf') else 0

    def _escape_cdata(self, content: str) -> str:
        """
        Экранирует последовательность ]]> внутри CDATA.
        Это единственная последовательность, которая может сломать CDATA.
        """
        return content.replace(']]>', ']]]]><![CDATA[>')

    def _escape_attr(self, value: str) -> str:
        """Экранирует значение для XML-атрибута."""
        return (value
                .replace('&', '&amp;')
                .replace('"', '&quot;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    def _add_line_numbers(self, content: str) -> str:
        """Добавляет номера строк (для отладки)."""
        lines = content.split('\n')
        max_width = len(str(len(lines)))
        numbered = [f"{i+1:>{max_width}} | {line}" for i, line in enumerate(lines)]
        return '\n'.join(numbered)

    # ==================== РАЗБОР XML (для тестов) ====================

    def unwrap_content(self, xml: str) -> str:
        """
        Извлекает содержимое из XML (для тестирования).
        Возвращает оригинальный код/текст.
        """
        # Ищем CDATA секцию
        match = re.search(r'<!\[CDATA\[\n?(.*?)\n?\]\]>', xml, re.DOTALL)
        if match:
            content = match.group(1)
            # Восстанавливаем экранированные ]]>
            return content.replace(']]]]><![CDATA[>', ']]>')
        return ""


# ==================== УДОБНЫЕ ФУНКЦИИ ====================

def wrap_python_chunk_with_context(chunk: PythonChunk, all_chunks: List[PythonChunk]) -> str:
    """
    Быстрая обёртка Python-чанка с контекстом.
    Добавляет imports и globals автоматически.
    """
    wrapper = XMLWrapper()
    return wrapper.wrap_chunk(chunk, include_context=True, context_chunks=all_chunks)


def wrap_go_chunk_with_context(chunk: GoChunk, all_chunks: List[GoChunk]) -> str:
    """Быстрая обёртка Go-чанка с контекстом."""
    wrapper = XMLWrapper()
    return wrapper.wrap_chunk(chunk, include_context=True, context_chunks=all_chunks)


def wrap_sql_chunk_with_context(chunk: SQLChunk, all_chunks: List[SQLChunk]) -> str:
    """Быстрая обёртка SQL-чанка с контекстом."""
    wrapper = XMLWrapper()
    return wrapper.wrap_chunk(chunk, include_context=True, context_chunks=all_chunks)


def wrap_file_simple(file_path: str) -> str:
    """
    Самый простой способ обернуть файл.
    Автоматически определяет тип и считает токены.
    """
    from app.utils.file_types import FileTypeDetector
    from app.utils.token_counter import TokenCounter
    
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    file_type = FileTypeDetector().detect(file_path)
    tokens = TokenCounter().count(content)
    
    wrapper = XMLWrapper()
    file = FileContent(
        path=str(path),
        file_type=file_type,
        content=content,
        tokens=tokens
    )
    
    return wrapper.wrap_file(file)

# app/utils/xml_wrapper.py - ДОБАВЛЯЕМ В КОНЕЦ ФАЙЛА

# ==================== ИЕРАРХИЧЕСКИЙ ОБЕРТЫВАТЕЛЬ ====================

@dataclass
class HierarchyChunk:
    """Контейнер для чанка с иерархической информацией"""
    chunk: AnyChunk
    hierarchy_path: List[str]  # ["OuterClass", "InnerClass", "method_name"]
    full_hierarchy: str        # "OuterClass.InnerClass.method_name"
    chunk_type: str           # "method", "class", "function"
    total_tokens: int         # Общее количество токенов
    is_nested: bool           # Вложенный ли элемент


class HierarchyXMLWrapper:
    """
    Специализированный XML Wrapper для построения подробной индексной карты.
    
    Особенности:
    - Поддерживает полные цепочки иерархии (вложенные классы)
    - Генерирует специализированные промпты для методов и классов
    - Учитывает размер токенов для выбора модели ИИ
    - Подготавливает данные для отдельных вызовов Qwen на каждый метод
    """
    
    def __init__(self):
        self.base_wrapper = XMLWrapper()
        self.token_counter = TokenCounter()
    
    # ==================== МЕТОДЫ ====================
    
    def wrap_method_for_analysis(self, method_chunk: HierarchyChunk, 
                                 class_context: Optional[str] = None) -> Dict[str, Any]:
        """
        Подготавливает метод для анализа Qwen.
        Каждый метод анализируется отдельным вызовом.
        
        Args:
            method_chunk: Иерархический чанк метода
            class_context: Контекст класса (если метод внутри класса)
            
        Returns:
            Словарь с XML и метаданными для отправки в Qwen
        """
        # 1. Подготавливаем XML с иерархической информацией
        xml_content = self.base_wrapper.wrap_chunk(
            method_chunk.chunk,
            include_context=True,
            context_chunks=None  # Контекст будет в промпте
        )
        
        # 2. Создаем специализированный промпт для метода
        prompt = self._create_method_prompt(method_chunk, class_context)
        
        # 3. Формируем полное сообщение
        full_message = f"{prompt}\n\n{xml_content}"
        
        return {
            "xml": full_message,
            "hierarchy": method_chunk.full_hierarchy,
            "chunk_type": "method",
            "tokens": method_chunk.total_tokens,
            "target": {
                "file": method_chunk.chunk.file_path,
                "hierarchy": method_chunk.full_hierarchy,
                "lines": f"{method_chunk.chunk.start_line}-{method_chunk.chunk.end_line}"
            },
            "model_choice": "qwen",  # Методы всегда анализирует Qwen
            "requires_response": True
        }
    
    def _create_method_prompt(self, method_chunk: HierarchyChunk, 
                            class_context: Optional[str]) -> str:
        """Создает промпт для анализа метода"""
        base_prompt = """Ты - опытный Python аналитик кода. Проанализируй следующий метод и ответь в формате JSON.

ТРЕБОВАНИЯ К АНАЛИЗУ:
1. Определи, является ли метод частью класса (укажи полную иерархию классов)
2. Найди ВСЕ вызовы других функций/методов внутри этого метода
3. Опиши, какую конкретную функцию выполняет этот метод
4. Укажи зависимости (импорты, которые используются)
5. Отметь потенциальные проблемы с синтаксисом

ВХОДНЫЕ ДАННЫЕ:
- Метод находится в файле: {file_path}
- Иерархия: {hierarchy}
- Строки: {lines}
- Токены: {tokens}

КОНТЕКСТ КЛАССА (если есть):
{class_context}

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "hierarchy": "полная.цепочка.классов.метод",
  "is_class_method": true/false,
  "parent_class": "имя класса или null",
  "calls": [
    {{
      "target": "имя_функции",
      "type": "internal/external/builtin",
      "module": "имя_модуля (если известно)"
    }}
  ],
  "description": "что делает этот метод (1-2 предложения)",
  "dependencies": ["список", "зависимостей"],
  "syntax_issues": ["список", "проблем"],
  "complexity": "low/medium/high",
  "estimated_purpose": "бизнес-логика/утилита/обработка данных/etc"
}}

ВАЖНО: Будь максимально конкретным. Не используй общие фразы."""
        
        return base_prompt.format(
            file_path=method_chunk.chunk.file_path,
            hierarchy=method_chunk.full_hierarchy,
            lines=f"{method_chunk.chunk.start_line}-{method_chunk.chunk.end_line}",
            tokens=method_chunk.total_tokens,
            class_context=class_context if class_context else "Метод не является частью класса (уровень модуля)"
        )
    
    # ==================== КЛАССЫ ====================
    
    def wrap_class_for_analysis(self, class_chunk: HierarchyChunk, 
                               methods_chunks: List[HierarchyChunk],
                               imports_context: Optional[str] = None) -> Dict[str, Any]:
        """
        Подготавливает класс для анализа ИИ.
        Анализируется весь класс целиком (со всеми методами).
        
        Args:
            class_chunk: Иерархический чанк класса
            methods_chunks: Список чанков методов этого класса
            imports_context: Контекст импортов
            
        Returns:
            Словарь с XML и метаданными для отправки в ИИ
        """
        # 1. Собираем полный код класса
        full_class_code = self._assemble_full_class(class_chunk, methods_chunks)
        
        # 2. Создаем FileContent для всего класса
        class_file = FileContent(
            path=class_chunk.chunk.file_path,
            file_type="python/class",
            content=full_class_code,
            tokens=self.token_counter.count(full_class_code)
        )
        
        # 3. Обертываем в XML
        xml_content = self.base_wrapper.wrap_file(class_file, include_line_numbers=False)
        
        # 4. Определяем модель ИИ на основе размера
        model_choice = self._select_model_for_class(class_file.tokens)
        
        # 5. Создаем специализированный промпт
        prompt = self._create_class_prompt(class_chunk, class_file.tokens, imports_context, model_choice)
        
        # 6. Формируем полное сообщение
        full_message = f"{prompt}\n\n{xml_content}"
        
        return {
            "xml": full_message,
            "hierarchy": class_chunk.full_hierarchy,
            "chunk_type": "class",
            "tokens": class_file.tokens,
            "target": {
                "file": class_chunk.chunk.file_path,
                "hierarchy": class_chunk.full_hierarchy,
                "lines": f"{class_chunk.chunk.start_line}-{class_chunk.chunk.end_line}",
                "total_methods": len(methods_chunks)
            },
            "model_choice": model_choice,
            "requires_response": True,
            "is_large_class": class_file.tokens > 15000
        }
    
    def _assemble_full_class(self, class_chunk: HierarchyChunk, 
                           methods_chunks: List[HierarchyChunk]) -> str:
        """Собирает полный код класса из чанков"""
        # Начинаем с заголовка класса
        lines = []
        
        # Добавляем сам класс
        lines.append(class_chunk.chunk.content.rstrip())
        
        # Добавляем методы в правильном порядке строк
        sorted_methods = sorted(methods_chunks, 
                              key=lambda m: m.chunk.start_line)
        
        for method in sorted_methods:
            lines.append("\n" + method.chunk.content)
        
        return "\n".join(lines)
    
    def _select_model_for_class(self, tokens: int) -> str:
        """Выбирает модель ИИ на основе размера класса"""
        if tokens <= 15000:
            return "qwen"
        else:
            return "deepseek"  # Используем DeepSeek V3 для больших классов
    
    def _create_class_prompt(self, class_chunk: HierarchyChunk, tokens: int,
                           imports_context: Optional[str], model_choice: str) -> str:
        """Создает промпт для анализа класса"""
        if model_choice == "qwen":
            model_name = "Qwen"
        else:
            model_name = "DeepSeek V3"
        
        base_prompt = """Ты - архитектор Python кода. Проанализируй следующий класс ЦЕЛИКОМ и ответь в формате JSON.

КЛАСС ДЛЯ АНАЛИЗА:
- Полная иерархия: {hierarchy}
- Файл: {file_path}
- Строки: {lines}
- Токены: {tokens}
- Модель анализа: {model_name}

КОНТЕКСТ ИМПОРТОВ:
{imports_context}

ТРЕБОВАНИЯ К АНАЛИЗУ (весь класс целиком):
1. Определи архитектурную роль класса в проекте
2. Опиши, какую общую функцию выполняет класс
3. Проанализируй взаимодействие методов между собой
4. Определи паттерны проектирования (если есть)
5. Оцени сложность и связность класса

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "hierarchy": "полная.цепочка.классов",
  "architectural_role": "MVC/Service/Model/Utility/etc",
  "overall_purpose": "общая функция класса (2-3 предложения)",
  "key_methods_summary": {{
    "total_methods": число,
    "public_methods": число,
    "private_methods": число,
    "main_methods": ["список", "ключевых", "методов"]
  }},
  "design_patterns": ["список", "паттернов", "или", "null"],
  "dependencies": ["список", "внешних", "зависимостей"],
  "complexity_analysis": {{
    "overall": "low/medium/high",
    "cohesion": "low/medium/high",
    "coupling": "low/medium/high"
  }},
  "recommendations": ["список", "рекомендаций", "по", "улучшению"]
}}

ВАЖНО: Анализируй класс как единое целое, а не как набор отдельных методов."""
        
        return base_prompt.format(
            hierarchy=class_chunk.full_hierarchy,
            file_path=class_chunk.chunk.file_path,
            lines=f"{class_chunk.chunk.start_line}-{class_chunk.chunk.end_line}",
            tokens=tokens,
            model_name=model_name,
            imports_context=imports_context if imports_context else "Информация об импортах недоступна"
        )
    
    # ==================== ФУНКЦИИ УРОВНЯ МОДУЛЯ ====================
    
    def wrap_function_for_analysis(self, function_chunk: HierarchyChunk) -> Dict[str, Any]:
        """
        Подготавливает функцию уровня модуля для анализа Qwen.
        Обрабатывается аналогично методу, но без контекста класса.
        """
        return self.wrap_method_for_analysis(function_chunk, class_context=None)
    
    # ==================== УТИЛИТЫ ДЛЯ СОЗДАНИЯ ИЕРАРХИИ ====================
    
    @staticmethod
    def create_hierarchy_chunk(chunk: AnyChunk, parent_hierarchy: List[str] = None) -> HierarchyChunk:
        """
        Создает иерархический чанк из обычного чанка.
        
        Args:
            chunk: Оригинальный чанк
            parent_hierarchy: Цепочка родительских элементов
            
        Returns:
            HierarchyChunk с полной иерархией
        """
        if parent_hierarchy is None:
            parent_hierarchy = []
        
        # Определяем тип чанка
        chunk_kind = getattr(chunk, 'kind', 'unknown')
        chunk_name = getattr(chunk, 'name', '')
        
        # Строим полную иерархию
        if chunk_name:
            current_hierarchy = parent_hierarchy + [chunk_name]
        else:
            current_hierarchy = parent_hierarchy
        
        # Определяем тип для анализа
        if chunk_kind == 'method':
            chunk_type = "method"
        elif chunk_kind == 'class':
            chunk_type = "class"
        elif chunk_kind == 'function':
            chunk_type = "function"
        else:
            chunk_type = "unknown"
        
        return HierarchyChunk(
            chunk=chunk,
            hierarchy_path=current_hierarchy,
            full_hierarchy=".".join(current_hierarchy) if current_hierarchy else "",
            chunk_type=chunk_type,
            total_tokens=getattr(chunk, 'tokens', 0),
            is_nested=bool(parent_hierarchy)
        )
    
    @staticmethod
    def extract_imports_context(tree_chunks: List[AnyChunk]) -> str:
        """Извлекает контекст импортов из всех чанков файла"""
        imports = []
        for chunk in tree_chunks:
            if getattr(chunk, 'kind', '') == 'imports' and hasattr(chunk, 'content'):
                imports.append(chunk.content)
        
        if imports:
            return "Импорты модуля:\n" + "\n".join(imports)
        return ""


# ==================== УДОБНЫЕ ФУНКЦИИ ДЛЯ НОВОГО WRAPPER ====================

def create_hierarchy_wrapper() -> HierarchyXMLWrapper:
    """Создает экземпляр HierarchyXMLWrapper"""
    return HierarchyXMLWrapper()


def prepare_method_for_ai(method_chunk: AnyChunk, parent_hierarchy: List[str] = None) -> Dict[str, Any]:
    """
    Быстрая подготовка метода для анализа ИИ.
    
    Args:
        method_chunk: Чанк метода
        parent_hierarchy: Иерархия родительских классов
    
    Returns:
        Готовые данные для отправки в ИИ
    """
    wrapper = create_hierarchy_wrapper()
    hierarchy_chunk = HierarchyXMLWrapper.create_hierarchy_chunk(method_chunk, parent_hierarchy)
    return wrapper.wrap_method_for_analysis(hierarchy_chunk)


def prepare_class_for_ai(class_chunk: AnyChunk, methods_chunks: List[AnyChunk],
                        parent_hierarchy: List[str] = None) -> Dict[str, Any]:
    """
    Быстрая подготовка класса для анализа ИИ.
    
    Args:
        class_chunk: Чанк класса
        methods_chunks: Чанки методов этого класса
        parent_hierarchy: Иерархия родительских классов
    
    Returns:
        Готовые данные для отправки в ИИ
    """
    wrapper = create_hierarchy_wrapper()
    
    # Создаем иерархические чанки
    class_hierarchy = HierarchyXMLWrapper.create_hierarchy_chunk(class_chunk, parent_hierarchy)
    method_hierarchies = [
        HierarchyXMLWrapper.create_hierarchy_chunk(method, class_hierarchy.hierarchy_path)
        for method in methods_chunks
    ]
    
    return wrapper.wrap_class_for_analysis(class_hierarchy, method_hierarchies)
