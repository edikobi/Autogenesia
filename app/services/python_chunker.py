# app/services/python_chunker.py (дополнение)
from __future__ import annotations
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from collections import defaultdict

from app.utils.token_counter import TokenCounter

MAX_CHUNK_TOKENS = 120_000


@dataclass
class PythonChunk:
    file_path: str
    kind: str           # "file" | "imports" | "globals" | "class" | "method" | "function"
    name: str
    parent: Optional[str]
    start_line: int
    end_line: int
    tokens: int
    content: str


@dataclass
class PythonTreeNode:
    """Узел иерархического дерева Python кода"""
    id: str                      # Уникальный идентификатор: "module:auth", "class:User", "method:login"
    kind: str                    # "module" | "imports" | "globals" | "class" | "method" | "function"
    name: str                    # Имя узла
    parent_id: Optional[str]     # ID родителя
    file_path: str              # Путь к файлу
    start_line: int
    end_line: int
    tokens: int
    content: str                # Полный код узла
    children: List[PythonTreeNode] = field(default_factory=list)
    nested_depth: int = 0       # Уровень вложенности (0 для модуля)
    ast_node: Optional[ast.AST] = None  # Ссылка на AST узел
    
    def __post_init__(self):
        """Автоматически генерируем ID если не задан"""
        if not self.id:
            if self.parent_id:
                self.id = f"{self.parent_id}::{self.kind}:{self.name}"
            else:
                self.id = f"{self.kind}:{self.name}"
    
    @property
    def is_class(self) -> bool:
        return self.kind == "class"
    
    @property
    def is_method(self) -> bool:
        return self.kind == "method"
    
    @property
    def is_function(self) -> bool:
        return self.kind == "function"


class SmartPythonChunker:
    """
    Иерархический чанкер Python-файлов с поддержкой вложенных классов.
    """
    
    def __init__(self, token_counter: Optional[TokenCounter] = None, 
                 max_chunk_tokens: int = MAX_CHUNK_TOKENS):
        self.tokens = token_counter or TokenCounter()
        self.max_chunk_tokens = max_chunk_tokens
    
    # СОХРАНЯЕМ СУЩЕСТВУЮЩИЙ МЕТОД ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ
    def chunk_file(self, file_path: str) -> List[PythonChunk]:
        """Существующий метод - возвращает плоский список чанков"""
        path = Path(file_path)
        code = path.read_text(encoding="utf-8")
        lines = code.splitlines()
        total_tokens = self.tokens.count(code)

        chunks: List[PythonChunk] = []

        # 0. Чанк уровня файла (общая статистика)
        chunks.append(
            PythonChunk(
                file_path=str(path),
                kind="file",
                name=path.name,
                parent=None,
                start_line=1,
                end_line=len(lines),
                tokens=total_tokens,
                content=code,
            )
        )

        # 1. Парсим AST
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return chunks

        # 2. Собираем импорты
        imports_chunk = self._extract_imports(tree, lines, path)
        if imports_chunk:
            chunks.append(imports_chunk)

        # 3. Собираем глобальные переменные/константы
        globals_chunk = self._extract_globals(tree, lines, path)
        if globals_chunk:
            chunks.append(globals_chunk)

        # 4. Проходим по классам и функциям
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                chunks.extend(self._chunk_class_flat(node, lines, path))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(self._chunk_function_flat(node, lines, path))

        return chunks
    
    # НОВЫЙ МЕТОД: построение дерева с вложенностью
    def chunk_file_to_tree(self, file_path: str) -> PythonTreeNode:
        """
        Строит иерархическое дерево узлов Python кода с поддержкой вложенных классов.
        
        Возвращает:
            PythonTreeNode: Корневой узел (модуль) со всей иерархией
        """
        path = Path(file_path)
        code = path.read_text(encoding="utf-8")
        lines = code.splitlines()
        
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            # В случае ошибки синтаксиса возвращаем узел модуля с ошибкой
            return PythonTreeNode(
                id="module:error",
                kind="module",
                name=path.name,
                parent_id=None,
                file_path=str(path),
                start_line=1,
                end_line=len(lines),
                tokens=self.tokens.count(code),
                content=code,
                children=[]
            )
        
        # Создаём корневой узел модуля
        module_node = PythonTreeNode(
            id="module:" + path.stem,
            kind="module",
            name=path.name,
            parent_id=None,
            file_path=str(path),
            start_line=1,
            end_line=len(lines),
            tokens=self.tokens.count(code),
            content=code,
            children=[]
        )
        
        # Собираем импорты и глобалы
        imports_content, imports_start, imports_end = self._extract_all_imports(tree, lines)
        globals_content, globals_start, globals_end = self._extract_all_globals(tree, lines)
        
        # Добавляем импорты как дочерний узел модуля
        if imports_content:
            imports_node = PythonTreeNode(
                id=f"{module_node.id}::imports",
                kind="imports",
                name="[imports]",
                parent_id=module_node.id,
                file_path=str(path),
                start_line=imports_start,
                end_line=imports_end,
                tokens=self.tokens.count(imports_content),
                content=imports_content,
                children=[]
            )
            module_node.children.append(imports_node)
        
        # Добавляем глобалы как дочерний узел модуля
        if globals_content:
            globals_node = PythonTreeNode(
                id=f"{module_node.id}::globals",
                kind="globals",
                name="[globals]",
                parent_id=module_node.id,
                file_path=str(path),
                start_line=globals_start,
                end_line=globals_end,
                tokens=self.tokens.count(globals_content),
                content=globals_content,
                children=[]
            )
            module_node.children.append(globals_node)
        
        # Рекурсивно обрабатываем AST для построения иерархии
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_node = self._process_class_node(node, lines, path, module_node.id, depth=0)
                if class_node:
                    module_node.children.append(class_node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_node = self._process_function_node(node, lines, path, module_node.id, is_method=False)
                if func_node:
                    module_node.children.append(func_node)
        
        return module_node
    
    def _process_class_node(self, node: ast.ClassDef, lines: List[str], 
                           path: Path, parent_id: str, depth: int) -> Optional[PythonTreeNode]:
        """Рекурсивно обрабатывает класс с вложенными классами"""
        # Извлекаем код класса
        class_code, start, end = self._get_source_segment(lines, node)
        
        # Создаём узел класса
        class_node = PythonTreeNode(
            id=f"{parent_id}::class:{node.name}",
            kind="class",
            name=node.name,
            parent_id=parent_id,
            file_path=str(path),
            start_line=start,
            end_line=end,
            tokens=self.tokens.count(class_code),
            content=class_code,
            children=[],
            nested_depth=depth,
            ast_node=node
        )
        
        # Обрабатываем тело класса
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                # Вложенный класс - рекурсивно обрабатываем
                nested_class = self._process_class_node(child, lines, path, 
                                                       class_node.id, depth + 1)
                if nested_class:
                    class_node.children.append(nested_class)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Метод класса
                method_node = self._process_function_node(child, lines, path, 
                                                         class_node.id, is_method=True)
                if method_node:
                    class_node.children.append(method_node)
            # Игнорируем другие узлы (переменные класса и т.д.) для простоты
        
        return class_node
    
    def _process_function_node(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], 
                              lines: List[str], path: Path, 
                              parent_id: str, is_method: bool) -> Optional[PythonTreeNode]:
        """Обрабатывает функцию или метод"""
        func_code, start, end = self._get_source_segment(lines, node)
        
        kind = "method" if is_method else "function"
        
        return PythonTreeNode(
            id=f"{parent_id}::{kind}:{node.name}",
            kind=kind,
            name=node.name,
            parent_id=parent_id,
            file_path=str(path),
            start_line=start,
            end_line=end,
            tokens=self.tokens.count(func_code),
            content=func_code,
            children=[],
            ast_node=node
        )
    
    def _extract_all_imports(self, tree: ast.AST, lines: List[str]) -> tuple[str, int, int]:
        """Извлекает все импорты в один блок"""
        import_nodes = [
            node for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        
        if not import_nodes:
            return "", 0, 0
        
        # Находим диапазон строк
        start_line = min(getattr(n, "lineno", 1) for n in import_nodes)
        end_line = max(getattr(n, "end_lineno", start_line) for n in import_nodes)
        
        # Собираем код импортов
        import_lines = []
        for node in import_nodes:
            node_start = getattr(node, "lineno", 1)
            node_end = getattr(node, "end_lineno", node_start)
            import_lines.extend(lines[node_start - 1 : node_end])
        
        return "\n".join(import_lines), start_line, end_line
    
    def _extract_all_globals(self, tree: ast.AST, lines: List[str]) -> tuple[str, int, int]:
        """Извлекает все глобальные переменные"""
        global_nodes = [
            node for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ]
        
        if not global_nodes:
            return "", 0, 0
        
        start_line = min(getattr(n, "lineno", 1) for n in global_nodes)
        end_line = max(getattr(n, "end_lineno", start_line) for n in global_nodes)
        
        global_lines = []
        for node in global_nodes:
            node_start = getattr(node, "lineno", 1)
            node_end = getattr(node, "end_lineno", node_start)
            global_lines.extend(lines[node_start - 1 : node_end])
        
        return "\n".join(global_lines), start_line, end_line
    
    # СУЩЕСТВУЮЩИЕ МЕТОДЫ ДЛЯ ПЛОСКОГО ЧАНКИРОВАНИЯ (сохраняем для обратной совместимости)
    def _extract_imports(self, tree: ast.AST, lines: list[str], path: Path) -> Optional[PythonChunk]:
        """Извлекает все импорты в один чанк (для плоского списка)."""
        import_nodes = [
            node for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]

        if not import_nodes:
            return None

        start_line = min(getattr(node, "lineno", 1) for node in import_nodes)
        end_line = max(getattr(node, "end_lineno", start_line) for node in import_nodes)

        import_lines = []
        for node in import_nodes:
            node_start = getattr(node, "lineno", 1)
            node_end = getattr(node, "end_lineno", node_start)
            import_lines.extend(lines[node_start - 1 : node_end])

        content = "\n".join(import_lines)
        
        return PythonChunk(
            file_path=str(path),
            kind="imports",
            name="[imports]",
            parent=None,
            start_line=start_line,
            end_line=end_line,
            tokens=self.tokens.count(content),
            content=content,
        )
    
    def _extract_globals(self, tree: ast.AST, lines: list[str], path: Path) -> Optional[PythonChunk]:
        """Извлекает глобальные переменные и константы (для плоского списка)."""
        global_nodes = [
            node for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ]

        if not global_nodes:
            return None

        start_line = min(getattr(node, "lineno", 1) for node in global_nodes)
        end_line = max(getattr(node, "end_lineno", start_line) for node in global_nodes)

        global_lines = []
        for node in global_nodes:
            node_start = getattr(node, "lineno", 1)
            node_end = getattr(node, "end_lineno", node_start)
            global_lines.extend(lines[node_start - 1 : node_end])

        content = "\n".join(global_lines)

        return PythonChunk(
            file_path=str(path),
            kind="globals",
            name="[globals]",
            parent=None,
            start_line=start_line,
            end_line=end_line,
            tokens=self.tokens.count(content),
            content=content,
        )
    
    def _chunk_class_flat(self, node: ast.ClassDef, lines: list[str], path: Path) -> List[PythonChunk]:
        """Чанкирует класс в плоский список (для обратной совместимости)."""
        chunks: List[PythonChunk] = []

        class_code, start, end = self._get_source_segment(lines, node)
        class_tokens = self.tokens.count(class_code)

        # Чанк класса целиком
        chunks.append(
            PythonChunk(
                file_path=str(path),
                kind="class",
                name=node.name,
                parent=None,
                start_line=start,
                end_line=end,
                tokens=class_tokens,
                content=class_code,
            )
        )

        # Чанки методов (только первого уровня, без вложенных классов)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_code, m_start, m_end = self._get_source_segment(lines, child)
                method_tokens = self.tokens.count(method_code)

                chunks.append(
                    PythonChunk(
                        file_path=str(path),
                        kind="method",
                        name=child.name,
                        parent=node.name,
                        start_line=m_start,
                        end_line=m_end,
                        tokens=method_tokens,
                        content=method_code,
                    )
                )

        return chunks
    
    def _chunk_function_flat(self, node: ast.AST, lines: list[str], path: Path) -> PythonChunk:
        """Чанкирует функцию в плоский список (для обратной совместимости)."""
        func_code, start, end = self._get_source_segment(lines, node)
        func_tokens = self.tokens.count(func_code)
        return PythonChunk(
            file_path=str(path),
            kind="function",
            name=getattr(node, "name", "<lambda>"),
            parent=None,
            start_line=start,
            end_line=end,
            tokens=func_tokens,
            content=func_code,
        )
    
    def _get_source_segment(self, lines: list[str], node: ast.AST) -> tuple[str, int, int]:
        """Извлекает сегмент кода по AST узлу."""
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        segment_lines = lines[start - 1 : end]
        return "\n".join(segment_lines), start, end


# === УТИЛИТЫ ДЛЯ РАБОТЫ С ДЕРЕВОМ ===

def flatten_tree_to_chunks(tree: PythonTreeNode) -> List[PythonChunk]:
    """
    Преобразует дерево узлов обратно в плоский список чанков.
    Полезно для обратной совместимости.
    """
    chunks = []
    
    def traverse(node: PythonTreeNode):
        # Пропускаем корневой узел модуля (он соответствует "file" чанку)
        if node.kind != "module":
            chunks.append(
                PythonChunk(
                    file_path=node.file_path,
                    kind=node.kind,
                    name=node.name,
                    parent=node.parent_id.split("::")[-1] if node.parent_id else None,
                    start_line=node.start_line,
                    end_line=node.end_line,
                    tokens=node.tokens,
                    content=node.content,
                )
            )
        
        for child in node.children:
            traverse(child)
    
    traverse(tree)
    return chunks


def find_node_in_tree(tree: PythonTreeNode, node_id: str) -> Optional[PythonTreeNode]:
    """Находит узел в дереве по ID."""
    if tree.id == node_id:
        return tree
    
    for child in tree.children:
        found = find_node_in_tree(child, node_id)
        if found:
            return found
    
    return None


def get_node_hierarchy(node: PythonTreeNode) -> List[PythonTreeNode]:
    """Возвращает путь от корня до узла (иерархию)."""
    hierarchy = []
    
    # Собираем родителей (нужно хранить ссылки на родителей в узлах)
    # Для простоты сделаем рекурсивный поиск
    def find_path(current: PythonTreeNode, target_id: str, path: List[PythonTreeNode]) -> bool:
        path.append(current)
        
        if current.id == target_id:
            return True
        
        for child in current.children:
            if find_path(child, target_id, path):
                return True
        
        path.pop()
        return False
    
    path = []
    find_path(tree, node.id, path)
    return path


# СОХРАНЯЕМ СУЩЕСТВУЮЩУЮ ФУНКЦИЮ ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ
def build_context_for_chunk(chunks: List[PythonChunk], target_chunk: PythonChunk) -> str:
    """
    Собирает контекст для отправки чанка в ИИ (для плоских чанков).
    """
    parts = []
    
    # 1. Добавляем импорты (если есть)
    imports = next((c for c in chunks if c.kind == "imports"), None)
    if imports:
        parts.append(f"# === Imports ({imports.tokens} tokens) ===")
        parts.append(imports.content)
        parts.append("")
    
    # 2. Добавляем глобальные переменные (если есть и если это не они сами)
    if target_chunk.kind != "globals":
        globals_chunk = next((c for c in chunks if c.kind == "globals"), None)
        if globals_chunk:
            parts.append(f"# === Globals ({globals_chunk.tokens} tokens) ===")
            parts.append(globals_chunk.content)
            parts.append("")
    
    # 3. Добавляем целевой чанк
    parts.append(f"# === {target_chunk.kind.capitalize()}: {target_chunk.name} ({target_chunk.tokens} tokens) ===")
    parts.append(target_chunk.content)
    
    return "\n".join(parts)


def build_context_for_tree_node(tree: PythonTreeNode, target_node: PythonTreeNode) -> str:
    """
    Собирает контекст для отправки узла дерева в ИИ.
    Учитывает иерархию: добавляет импорты, глобалы и контекст родительских классов.
    """
    parts = []
    
    # Находим путь от корня до целевого узла
    hierarchy = get_node_hierarchy(target_node)
    
    # 1. Добавляем импорты из корневого узла
    imports_node = next((n for n in hierarchy[0].children if n.kind == "imports"), None)
    if imports_node:
        parts.append(f"# === Imports ({imports_node.tokens} tokens) ===")
        parts.append(imports_node.content)
        parts.append("")
    
    # 2. Добавляем глобалы из корневого узла (если целевой узел не глобалы)
    if target_node.kind != "globals":
        globals_node = next((n for n in hierarchy[0].children if n.kind == "globals"), None)
        if globals_node:
            parts.append(f"# === Globals ({globals_node.tokens} tokens) ===")
            parts.append(globals_node.content)
            parts.append("")
    
    # 3. Добавляем контекст родительских классов (только заголовки)
    for node in hierarchy[1:]:  # Пропускаем корневой модуль
        if node.kind == "class" and node.id != target_node.id:
            # Добавляем только заголовок класса, не весь код
            class_header = _extract_class_header(node.content)
            parts.append(f"# === Class {node.name} (context) ===")
            parts.append(class_header)
            parts.append("")
    
    # 4. Добавляем целевой узел
    node_type = target_node.kind.capitalize()
    if target_node.kind == "method":
        node_type = "Method"
    
    parts.append(f"# === {node_type}: {target_node.name} ({target_node.tokens} tokens) ===")
    parts.append(target_node.content)
    
    return "\n".join(parts)


def _extract_class_header(class_content: str) -> str:
    """Извлекает заголовок класса (до первого метода)."""
    lines = class_content.splitlines()
    header_lines = []
    
    for line in lines:
        stripped = line.lstrip()
        # Если строка начинается с def или async def, значит это метод
        if stripped.startswith(('def ', 'async def ')):
            break
        header_lines.append(line)
    
    return "\n".join(header_lines)