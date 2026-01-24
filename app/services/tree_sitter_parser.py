# app/services/tree_sitter_parser.py
"""
Fault-tolerant Python parser based on Tree-sitter.

В отличие от стандартного ast.parse(), продолжает парсить код даже
при наличии синтаксических ошибок. Это критично для модификации
файлов, созданных с ошибками (например, `catch` вместо `except`).

Используется в FileModifier для:
- Поиска классов, методов, функций
- Определения границ (start_line, end_line)
- Работы с невалидным Python-кодом
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Iterator, Any
from enum import Enum

logger = logging.getLogger(__name__)

# Ленивая инициализация Tree-sitter
_parser = None
_language = None


def _get_parser():
    """Ленивая инициализация Tree-sitter парсера."""
    global _parser, _language
    
    if _parser is None:
        try:
            import tree_sitter_python as tspython
            from tree_sitter import Language, Parser
            
            _language = Language(tspython.language())
            _parser = Parser(_language)
            logger.debug("Tree-sitter parser initialized successfully")
        except ImportError as e:
            logger.warning(
                f"Tree-sitter not available: {e}. "
                "Install with: pip install tree-sitter tree-sitter-python"
            )
            raise
    
    return _parser, _language


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class NodeType(Enum):
    """Типы узлов, которые мы ищем."""
    CLASS = "class_definition"
    FUNCTION = "function_definition"
    METHOD = "function_definition"  # В tree-sitter методы — те же function_definition
    DECORATED = "decorated_definition"
    ERROR = "ERROR"


@dataclass
class CodeSpan:
    """
    Границы кода в файле.
    
    Attributes:
        start_line: Начальная строка (1-indexed, как в AST)
        end_line: Конечная строка (inclusive)
        start_byte: Начальный байт
        end_byte: Конечный байт
    """
    start_line: int
    end_line: int
    start_byte: int = 0
    end_byte: int = 0
    
    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class ParsedImport:
    """Распарсенный импорт с позицией в файле."""
    text: str
    span: CodeSpan


@dataclass
class ParsedClass:
    """Распарсенный класс."""
    name: str
    span: CodeSpan
    methods: List['ParsedFunction'] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    decorator_spans: List[CodeSpan] = field(default_factory=list)  # Позиции декораторов для удаления
    has_errors: bool = False
    body_start_line: int = 0  # Первая строка тела класса
    indent: int = 0  # Отступ определения класса


@dataclass 
class ParsedFunction:
    """Распарсенная функция или метод."""
    name: str
    span: CodeSpan
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)
    decorator_spans: List[CodeSpan] = field(default_factory=list)  # Позиции декораторов для удаления
    parent_class: Optional[str] = None  # Имя класса, если это метод
    has_errors: bool = False
    body_start_line: int = 0  # Первая строка тела (после def ...:)
    indent: int = 0  # Отступ определения функции


@dataclass
class ParseResult:
    """Результат парсинга файла."""
    classes: List[ParsedClass] = field(default_factory=list)
    functions: List[ParsedFunction] = field(default_factory=list)  # Top-level функции
    parsed_imports: List[ParsedImport] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)  # Найденные импорты
    errors: List[Tuple[int, int, str]] = field(default_factory=list)  # (line, col, message)
    root_node: Any = None
    success: bool = True
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    def get_class(self, name: str) -> Optional[ParsedClass]:
        """Находит класс по имени."""
        for cls in self.classes:
            if cls.name == name:
                return cls
        return None
    
    def get_function(self, name: str) -> Optional[ParsedFunction]:
        """Находит top-level функцию по имени."""
        for func in self.functions:
            if func.name == name:
                return func
        return None
    
    def get_method(self, class_name: str, method_name: str) -> Optional[ParsedFunction]:
        """Находит метод в классе."""
        cls = self.get_class(class_name)
        if cls:
            for method in cls.methods:
                if method.name == method_name:
                    return method
        return None

# ============================================================================
# MAIN PARSER CLASS
# ============================================================================

class FaultTolerantParser:
    """
    Fault-tolerant парсер Python-кода на основе Tree-sitter.
    
    Преимущества перед ast.parse():
    - Продолжает работать при синтаксических ошибках
    - Помечает ошибочные узлы, но строит дерево для остального кода
    - Позволяет находить классы/методы в частично невалидном коде
    
    Example:
        >>> parser = FaultTolerantParser()
        >>> result = parser.parse('''
        ... class MyClass:
        ...     def valid_method(self):
        ...         pass
        ...     
        ...     def broken_method(self):
        ...         catch Exception:  # Ошибка!
        ...             pass
        ... ''')
        >>> result.has_errors
        True
        >>> result.get_class("MyClass") is not None
        True  # Класс найден несмотря на ошибку!
        >>> len(result.get_class("MyClass").methods)
        2  # Оба метода найдены
    """
    
    def __init__(self):
        """Инициализирует парсер."""
        self._parser = None
        self._language = None
    
    def _ensure_parser(self):
        """Ленивая инициализация."""
        if self._parser is None:
            self._parser, self._language = _get_parser()
    
    def parse(self, source_code: str) -> ParseResult:
        """
        Парсит Python код и возвращает структурированную информацию.
        
        Args:
            source_code: Исходный код для парсинга
            
        Returns:
            ParseResult с информацией о классах, функциях, импортах и ошибках
        """
        self._ensure_parser()
        
        result = ParseResult()
        
        try:
            source_bytes = source_code.encode('utf-8')
            tree = self._parser.parse(source_bytes)
            result.root_node = tree.root_node
            
            # Парсим дерево — используем существующий метод _parse_node
            self._parse_node(tree.root_node, source_bytes, result, parent_class=None)
            
            # Собираем синтаксические ошибки — используем существующий метод _collect_errors
            self._collect_errors(tree.root_node, source_bytes, result)
            
            result.success = len(result.errors) == 0
            
        except Exception as e:
            result.errors.append(f"Parse error: {str(e)}")
            result.success = False
        
        return result
    
    
    def _collect_errors(self, node, source_bytes: bytes, result: ParseResult):
        """Рекурсивно собирает все ERROR узлы."""
        if node.type == "ERROR" or node.is_missing:
            line = node.start_point[0] + 1  # tree-sitter использует 0-indexed
            col = node.start_point[1]
            
            # Пытаемся получить контекст ошибки
            error_text = source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')
            if len(error_text) > 50:
                error_text = error_text[:50] + "..."
            
            message = f"Syntax error: unexpected '{error_text}'" if error_text.strip() else "Syntax error"
            result.errors.append((line, col, message))
        
        for child in node.children:
            self._collect_errors(child, source_bytes, result)
    
    def _parse_node(
        self, 
        node, 
        source_bytes: bytes, 
        result: ParseResult,
        parent_class: Optional[str]
    ):
        """Рекурсивно парсит узлы дерева."""
        
        # Обработка импортов
        if node.type in ("import_statement", "import_from_statement"):
            import_text = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
            result.imports.append(import_text)
            
            # Create structured import object
            parsed_import = ParsedImport(
                text=import_text,
                span=CodeSpan(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte
                )
            )
            result.parsed_imports.append(parsed_import)
            return

        # Обработка decorated_definition (функция/класс с декораторами)
        if node.type == "decorated_definition":
            # Внутри есть definition (class или function)
            decorators = []
            decorator_spans = []
            definition_node = None
            
            for child in node.children:
                if child.type == "decorator":
                    dec_text = source_bytes[child.start_byte:child.end_byte].decode('utf-8')
                    decorators.append(dec_text.strip())
                    decorator_spans.append(CodeSpan(
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        start_byte=child.start_byte,
                        end_byte=child.end_byte,
                    ))
                elif child.type in ("class_definition", "function_definition"):
                    definition_node = child
            
            if definition_node:
                # Парсим definition, но используем span от decorated_definition
                self._parse_definition(
                    definition_node, 
                    source_bytes, 
                    result, 
                    parent_class,
                    decorators=decorators,
                    decorator_spans=decorator_spans,
                    outer_node=node  # Для правильного span
                )
            return
        
        # Обработка class_definition
        if node.type == "class_definition":
            self._parse_definition(node, source_bytes, result, parent_class)
            return
        
        # Обработка function_definition
        if node.type == "function_definition":
            self._parse_definition(node, source_bytes, result, parent_class)
            return
        
        # Рекурсия для остальных узлов
        for child in node.children:
            self._parse_node(child, source_bytes, result, parent_class)


    
    def _parse_definition(
        self,
        node,
        source_bytes: bytes,
        result: ParseResult,
        parent_class: Optional[str],
        decorators: List[str] = None,
        decorator_spans: List[CodeSpan] = None,
        outer_node = None
    ):
        """Парсит определение класса или функции."""
        decorators = decorators or []
        decorator_spans = decorator_spans or []
        
        # Используем outer_node для span если есть (для decorated definitions)
        span_node = outer_node or node
        
        span = CodeSpan(
            start_line=span_node.start_point[0] + 1,  # 1-indexed
            end_line=span_node.end_point[0] + 1,
            start_byte=span_node.start_byte,
            end_byte=span_node.end_byte,
        )
        
        # Отступ определения (колонка начала node, не outer_node)
        indent = node.start_point[1]
        
        # Получаем имя
        name = self._get_name(node, source_bytes)
        if not name:
            return
        
        # Проверяем есть ли ошибки внутри
        has_errors = self._has_errors_inside(node)
        
        # Находим body для определения body_start_line
        body = self._get_body(node)
        body_start_line = body.start_point[0] + 1 if body else span.start_line + 1
        
        if node.type == "class_definition":
            parsed_class = ParsedClass(
                name=name,
                span=span,
                decorators=decorators,
                decorator_spans=decorator_spans,
                has_errors=has_errors,
                body_start_line=body_start_line,
                indent=indent,
            )
            
            # Парсим методы внутри класса
            if body:
                for child in body.children:
                    self._parse_node(child, source_bytes, result, parent_class=name)
                
                # Собираем методы для этого класса
                parsed_class.methods = [
                    f for f in result.functions 
                    if f.parent_class == name
                ]
                # Убираем методы из top-level functions
                result.functions = [
                    f for f in result.functions 
                    if f.parent_class != name
                ]
            
            result.classes.append(parsed_class)
        
        else:  # function_definition
            is_async = self._is_async(node, source_bytes)
            
            parsed_func = ParsedFunction(
                name=name,
                span=span,
                is_async=is_async,
                decorators=decorators,
                decorator_spans=decorator_spans,
                parent_class=parent_class,
                has_errors=has_errors,
                body_start_line=body_start_line,
                indent=indent,
            )
            
            result.functions.append(parsed_func)

    
    def _get_name(self, node, source_bytes: bytes) -> Optional[str]:
        """Извлекает имя класса/функции."""
        for child in node.children:
            if child.type == "identifier":
                return source_bytes[child.start_byte:child.end_byte].decode('utf-8')
        return None
    
    def _get_body(self, node):
        """Находит тело класса/функции (block node)."""
        for child in node.children:
            if child.type == "block":
                return child
        return None
    
    def _is_async(self, node, source_bytes: bytes) -> bool:
        """Проверяет, является ли функция async."""
        # Проверяем предшествующий токен или ищем async в детях
        for child in node.children:
            if child.type == "async":
                return True
            text = source_bytes[child.start_byte:child.end_byte].decode('utf-8')
            if text == "async":
                return True
        return False
    
    def _has_errors_inside(self, node) -> bool:
        """Проверяет, есть ли ERROR узлы внутри."""
        if node.type == "ERROR" or node.is_missing:
            return True
        
        for child in node.children:
            if self._has_errors_inside(child):
                return True
        
        return False
    
    # ========================================================================
    # CONVENIENCE METHODS
    # ========================================================================
    
    def find_class(self, source_code: str, class_name: str) -> Optional[ParsedClass]:
        """Находит класс по имени."""
        result = self.parse(source_code)
        return result.get_class(class_name)
    
    def find_function(self, source_code: str, func_name: str) -> Optional[ParsedFunction]:
        """Находит top-level функцию по имени."""
        result = self.parse(source_code)
        return result.get_function(func_name)
        
    
    def find_method(
        self, 
        source_code: str, 
        class_name: str, 
        method_name: str
    ) -> Optional[ParsedFunction]:
        """Находит метод в классе."""
        result = self.parse(source_code)
        return result.get_method(class_name, method_name)
    
    def get_node_text(self, source_code: str, span: CodeSpan) -> str:
        """Извлекает текст по span."""
        lines = source_code.splitlines(keepends=True)
        
        # span.start_line и span.end_line — 1-indexed
        start_idx = span.start_line - 1
        end_idx = span.end_line  # exclusive для slice
        
        return ''.join(lines[start_idx:end_idx])


    def get_element_indent(self, source_code: str, element_name: str, parent_class: Optional[str] = None) -> int:
        """
        Определяет отступ элемента (класса, метода, функции) через Tree-sitter.
        
        Args:
            source_code: Исходный код
            element_name: Имя элемента (класс, метод, функция)
            parent_class: Имя родительского класса (для методов)
            
        Returns:
            Отступ в пробелах, или 0 если элемент не найден
        """
        result = self.parse(source_code)
        
        if parent_class:
            # Ищем метод в классе
            method = result.get_method(parent_class, element_name)
            if method:
                return method.indent
        else:
            # Ищем класс
            cls = result.get_class(element_name)
            if cls:
                return cls.indent
            
            # Ищем функцию
            func = result.get_function(element_name)
            if func:
                return func.indent
        
        return 0

# ============================================================================
# MODULE-LEVEL INSTANCE
# ============================================================================

# Глобальный экземпляр для удобства
_default_parser: Optional[FaultTolerantParser] = None


def get_parser() -> FaultTolerantParser:
    """Возвращает глобальный экземпляр парсера."""
    global _default_parser
    if _default_parser is None:
        _default_parser = FaultTolerantParser()
    return _default_parser


def parse(source_code: str) -> ParseResult:
    """Парсит код используя глобальный парсер."""
    return get_parser().parse(source_code)


def find_class(source_code: str, class_name: str) -> Optional[ParsedClass]:
    """Находит класс по имени."""
    return get_parser().find_class(source_code, class_name)


def find_method(
    source_code: str, 
    class_name: str, 
    method_name: str
) -> Optional[ParsedFunction]:
    """Находит метод в классе."""
    return get_parser().find_method(source_code, class_name, method_name)


def find_function(source_code: str, func_name: str) -> Optional[ParsedFunction]:
    """Находит функцию по имени."""
    return get_parser().find_function(source_code, func_name)


# ============================================================================
# FALLBACK: AST + TREE-SITTER HYBRID
# ============================================================================

# (This function is being DELETED - no replacement code needed)
# The function will be removed by the parser when it sees this marker
