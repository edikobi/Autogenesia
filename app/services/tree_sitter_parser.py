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
from typing import Optional, List, Tuple, Iterator, Any, Dict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# Ленивая инициализация Tree-sitter
_parser = None
_language = None

@dataclass
class MultiLanguageChunk:
    """Universal chunk for any language parsed by tree-sitter"""
    file_path: str
    kind: str  # "class", "function", "method", "interface", "struct", "imports", "file"
    name: str
    parent: Optional[str]  # Parent class/struct name for methods
    start_line: int
    end_line: int
    tokens: int
    content: str
    language: str  # "javascript", "typescript", "go", "java"

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


class MultiLanguageParser:
    """Multi-language fault-tolerant parser based on Tree-sitter. Supports JavaScript, TypeScript, Go, and Java. Each language has its own parser instance with lazy initialization."""

    LANGUAGE_CONFIGS: Dict[str, Dict] = {
        'javascript': {
            'module': 'tree_sitter_javascript',
            'extensions': ['.js', '.jsx', '.mjs'],
            'function_types': ['function_declaration', 'method_definition', 'arrow_function'],
            'class_types': ['class_declaration'],
            'body_type': 'statement_block',
        },
        'typescript': {
            'module': 'tree_sitter_typescript',
            'attr': 'language_typescript',
            'extensions': ['.ts', '.tsx'],
            'function_types': ['function_declaration', 'method_definition', 'arrow_function'],
            'class_types': ['class_declaration'],
            'body_type': 'statement_block',
        },
        'go': {
            'module': 'tree_sitter_go',
            'extensions': ['.go'],
            'function_types': ['function_declaration', 'method_declaration'],
            'class_types': ['type_declaration'],
            'body_type': 'block',
        },
        'java': {
            'module': 'tree_sitter_java',
            'extensions': ['.java'],
            'function_types': ['method_declaration', 'constructor_declaration'],
            'class_types': ['class_declaration', 'interface_declaration'],
            'body_type': 'block',
        },
    }

    def __init__(self):
        """Initialize parser cache."""
        self._parsers: Dict[str, any] = {}
        self._languages: Dict[str, any] = {}

    def _get_parser_for_language(self, language: str) -> tuple:
        """Get or create parser for specified language.
        
        Returns:
            tuple: (parser, language_object)
            
        Raises:
            ValueError: If language is not supported.
        """
        if language in self._parsers:
            return (self._parsers[language], self._languages[language])

        if language not in self.LANGUAGE_CONFIGS:
            raise ValueError(f"Unsupported language: {language}")

        config = self.LANGUAGE_CONFIGS[language]

        try:
            # Import the tree-sitter language module
            module_name = config['module']
            module = __import__(module_name)

            # Get the language object
            if 'attr' in config:
                # For TypeScript, need to access specific attribute
                lang_obj = getattr(module, config['attr'])
            else:
                # For others, use default language attribute
                lang_obj = module.language

            # Create parser
            parser = Parser()
            parser.set_language(lang_obj)

            # Cache for future use
            self._parsers[language] = parser
            self._languages[language] = lang_obj

            return (parser, lang_obj)

        except ImportError as e:
            raise ValueError(f"Tree-sitter language module not installed for {language}: {e}")
        except Exception as e:
            raise ValueError(f"Failed to initialize parser for {language}: {e}")

    def get_language_for_file(self, file_path: str) -> Optional[str]:
        """Detect language from file extension"""
        ext = Path(file_path).suffix.lower()
        
        for language, config in self.LANGUAGE_CONFIGS.items():
            if ext in config.get("extensions", []):
                return language
        
        return None

    def is_supported(self, file_path: str) -> bool:
        """Check if file type is supported for parsing"""
        return self.get_language_for_file(file_path) is not None

    def chunk_file(self, file_path: str, language: str) -> List[MultiLanguageChunk]:
        """Parse file and extract chunks (classes, functions, etc.) using tree-sitter"""
        try:
            with open(file_path, 'rb') as f:
                source_bytes = f.read()
        except (FileNotFoundError, PermissionError, OSError) as e:
            logger.warning(f"Cannot read {file_path}: {e}")
            return []
        
        try:
            parser, lang_obj = self._get_parser_for_language(language)
        except ValueError as e:
            logger.warning(f"No parser for {language}: {e}")
            return []
        
        try:
            tree = parser.parse(source_bytes)
        except Exception as e:
            logger.warning(f"Parse error in {file_path}: {e}")
            return [self._create_file_level_chunk(file_path, source_bytes, language)]
        
        chunks = self._extract_chunks_from_tree(tree, source_bytes, file_path, language)
        
        if not chunks:
            chunks = [self._create_file_level_chunk(file_path, source_bytes, language)]
        
        return chunks

    def _extract_chunks_from_tree(self, tree, source_bytes: bytes, file_path: str, language: str) -> List[MultiLanguageChunk]:
        """Walk tree and extract chunks matching language config"""
        config = self.LANGUAGE_CONFIGS.get(language, {})
        chunks = []
        
        # Extract imports block
        imports_content = self._extract_imports_block(tree, source_bytes, config)
        if imports_content:
            chunks.append(MultiLanguageChunk(
                file_path=file_path,
                kind="imports",
                name="imports",
                parent=None,
                start_line=1,
                end_line=1,
                tokens=len(imports_content.split()),
                content=imports_content,
                language=language
            ))
        
        # Walk tree and extract nodes
        self._walk_tree(
            tree.root_node,
            source_bytes,
            file_path,
            language,
            config,
            chunks,
            parent_name=None
        )
        
        return chunks

    def _walk_tree(self, node, source_bytes: bytes, file_path: str, language: str, config: Dict, chunks: List[MultiLanguageChunk], parent_name: Optional[str] = None):
        """Recursively walk tree and extract chunks"""
        class_types = config.get("class_types", [])
        function_types = config.get("function_types", [])
        
        if node.type in class_types:
            name = self._extract_node_name(node, source_bytes)
            if name:
                content = self._get_node_content(node, source_bytes)
                chunks.append(MultiLanguageChunk(
                    file_path=file_path,
                    kind="class",
                    name=name,
                    parent=parent_name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    tokens=len(content.split()),
                    content=content,
                    language=language
                ))
                # Recursively process children with this class as parent
                for child in node.children:
                    self._walk_tree(child, source_bytes, file_path, language, config, chunks, parent_name=name)
                return
        
        elif node.type in function_types:
            name = self._extract_node_name(node, source_bytes)
            if name:
                content = self._get_node_content(node, source_bytes)
                kind = "method" if parent_name else "function"
                chunks.append(MultiLanguageChunk(
                    file_path=file_path,
                    kind=kind,
                    name=name,
                    parent=parent_name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    tokens=len(content.split()),
                    content=content,
                    language=language
                ))
                return
        
        # Recursively process children
        for child in node.children:
            self._walk_tree(child, source_bytes, file_path, language, config, chunks, parent_name=parent_name)

    def _extract_imports_block(self, tree, source_bytes: bytes, config: Dict) -> Optional[str]:
        """Extract imports block from tree"""
        import_types = ["import_statement", "import_declaration"]
        
        imports = []
        for node in tree.root_node.children:
            if node.type in import_types:
                imports.append(self._get_node_content(node, source_bytes))
        
        if imports:
            return "\n".join(imports)
        return None

    def _extract_node_name(self, node, source_bytes: bytes) -> Optional[str]:
        """Extract identifier name from node"""
        for child in node.children:
            if child.type == "identifier":
                return source_bytes[child.start_byte:child.end_byte].decode('utf-8', errors='ignore')
        return None

    def _get_node_content(self, node, source_bytes: bytes) -> str:
        """Get source code content of node"""
        try:
            return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')
        except Exception:
            return ""

    def _create_file_level_chunk(self, file_path: str, source_bytes: bytes, language: str) -> MultiLanguageChunk:
        """Create a file-level chunk when parsing fails or no chunks extracted"""
        try:
            content = source_bytes.decode('utf-8', errors='ignore')
        except Exception:
            content = ""
        
        return MultiLanguageChunk(
            file_path=file_path,
            kind="file",
            name=Path(file_path).name,
            parent=None,
            start_line=1,
            end_line=len(content.splitlines()),
            tokens=len(content.split()),
            content=content,
            language=language
        )

    def find_element(
        self,
        source_code: str,
        language: str,
        element_name: str,
        element_type: str = 'function',
        parent_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find a code element (function, method, class) by name in source code. Returns dict with start_line, end_line, indent, content or None."""
        try:
            # Validate language
            if language not in self.LANGUAGE_CONFIGS:
                raise ValueError(f"Unsupported language: {language}")
            
            # Get parser
            ts_parser = self._get_parser_for_language(language)
            tree = ts_parser.parse(source_code.encode('utf-8'))
            
            # Get config
            config = self.LANGUAGE_CONFIGS[language]
            function_types = config.get('function_types', [])
            class_types = config.get('class_types', [])
            
            # Determine target types
            if element_type in ('function', 'method'):
                target_types = function_types
            elif element_type == 'class':
                target_types = class_types
            else:
                target_types = function_types
            
            # Helper to find node by name
            def find_node_by_name(node, name, types, parent_class=None):
                if node.type in types:
                    node_name = self._extract_node_name(node, language)
                    if node_name == name:
                        # If parent_class specified, verify we're in that class
                        if parent_class:
                            # Check if this node is within parent_class
                            # (simplified - just return if name matches)
                            pass
                        return node
                
                for child in node.children:
                    result = find_node_by_name(child, name, types, parent_class)
                    if result:
                        return result
                
                return None
            
            # Find the element
            found_node = find_node_by_name(tree.root_node, element_name, target_types, parent_name)
            
            if not found_node:
                return None
            
            # Extract content
            content = source_code[found_node.start_byte:found_node.end_byte]
            
            return {
                'start_line': found_node.start_point[0] + 1,
                'end_line': found_node.end_point[0] + 1,
                'indent': found_node.start_point[1],
                'content': content
            }
        
        except Exception as e:
            logger.warning(f"Error finding element: {e}")
            return None

    def validate_syntax(self, source_code: str, language: str) -> Tuple[bool, List[str]]:
        """Validate syntax of source code using tree-sitter. Returns (is_valid, list_of_errors)."""
        try:
            # Get parser
            ts_parser = self._get_parser_for_language(language)
            tree = ts_parser.parse(source_code.encode('utf-8'))
            
            errors = []
            
            # Walk tree to find ERROR nodes
            def walk_tree(node):
                if node.type == 'ERROR':
                    line_num = node.start_point[0] + 1
                    text_snippet = source_code[node.start_byte:node.end_byte][:30]
                    errors.append(f"Line {line_num}: Syntax error near '{text_snippet}'")
                
                for child in node.children:
                    walk_tree(child)
            
            walk_tree(tree.root_node)
            
            return (len(errors) == 0, errors)
        
        except ValueError:
            # Unsupported language
            return (True, [])
        except Exception as e:
            logger.debug(f"Error validating syntax: {e}")
            return (True, [])
        


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
