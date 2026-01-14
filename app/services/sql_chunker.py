# app/services/sql_chunker.py
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.utils.token_counter import TokenCounter

MAX_CHUNK_TOKENS = 50_000


@dataclass
class SQLChunk:
    file_path: str
    kind: str           # "file" | "create_table" | "create_index" | "create_view" | "procedure" | "function" | "trigger" | "insert" | "select" | "update" | "delete" | "alter" | "other"
    name: str           # имя таблицы/процедуры/и т.д.
    start_line: int
    end_line: int
    tokens: int
    content: str


class SmartSQLChunker:
    """
    Чанкер SQL-файлов.
    
    Распознаёт:
      - CREATE TABLE / VIEW / INDEX
      - CREATE PROCEDURE / FUNCTION / TRIGGER
      - INSERT / SELECT / UPDATE / DELETE
      - ALTER / DROP
      
    Разделители: ; GO или двойной перенос строки
    """

    # Паттерны для определения типа SQL-выражения
    STATEMENT_PATTERNS = [
        (re.compile(r'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'create_table'),
        (re.compile(r'^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'create_index'),
        (re.compile(r'^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'create_view'),
        (re.compile(r'^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:STORED\s+)?PROCEDURE\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'procedure'),
        (re.compile(r'^\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'function'),
        (re.compile(r'^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'trigger'),
        (re.compile(r'^\s*INSERT\s+INTO\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'insert'),
        (re.compile(r'^\s*SELECT\b', re.IGNORECASE | re.MULTILINE), 'select'),
        (re.compile(r'^\s*UPDATE\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'update'),
        (re.compile(r'^\s*DELETE\s+FROM\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'delete'),
        (re.compile(r'^\s*ALTER\s+TABLE\s+[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'alter'),
        (re.compile(r'^\s*DROP\s+(?:TABLE|INDEX|VIEW|PROCEDURE|FUNCTION|TRIGGER)\s+(?:IF\s+EXISTS\s+)?[`"\[]?(\w+)', re.IGNORECASE | re.MULTILINE), 'drop'),
    ]

    def __init__(self, token_counter: Optional[TokenCounter] = None, max_chunk_tokens: int = MAX_CHUNK_TOKENS):
        self.tokens = token_counter or TokenCounter()
        self.max_chunk_tokens = max_chunk_tokens

    def chunk_file(self, file_path: str) -> List[SQLChunk]:
        path = Path(file_path)
        code = path.read_text(encoding="utf-8")
        lines = code.splitlines()
        total_tokens = self.tokens.count(code)

        chunks: List[SQLChunk] = []

        # 0. Чанк уровня файла
        chunks.append(
            SQLChunk(
                file_path=str(path),
                kind="file",
                name=path.name,
                start_line=1,
                end_line=len(lines),
                tokens=total_tokens,
                content=code,
            )
        )

        # 1. Разбиваем на отдельные SQL-выражения
        statements = self._split_statements(code)
        
        # 2. Классифицируем каждое выражение
        for stmt_content, start_line, end_line in statements:
            kind, name = self._classify_statement(stmt_content)
            
            chunks.append(
                SQLChunk(
                    file_path=str(path),
                    kind=kind,
                    name=name,
                    start_line=start_line,
                    end_line=end_line,
                    tokens=self.tokens.count(stmt_content),
                    content=stmt_content,
                )
            )

        return chunks

    def _split_statements(self, code: str) -> List[tuple[str, int, int]]:
        """
        Разбивает SQL-код на отдельные выражения.
        Разделители: ; GO (на отдельной строке) или ;; для процедур.
        Возвращает: [(content, start_line, end_line), ...]
        """
        statements = []
        
        # Нормализуем разделители
        # GO на отдельной строке -> точка с запятой
        normalized = re.sub(r'^\s*GO\s*$', ';', code, flags=re.MULTILINE | re.IGNORECASE)
        
        # Разбиваем по ; но учитываем, что внутри процедур могут быть вложенные ;
        # Упрощённый подход: разбиваем по ; в конце строки или по ;;
        
        lines = code.splitlines()
        current_stmt_lines = []
        current_start = 1
        in_block = False  # Для BEGIN...END блоков
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip().upper()
            
            # Отслеживаем BEGIN/END блоки
            if 'BEGIN' in stripped:
                in_block = True
            if 'END' in stripped and in_block:
                # Проверяем, что это END блока, а не END IF и т.п.
                if stripped == 'END' or stripped.startswith('END;') or stripped.startswith('END '):
                    in_block = False
            
            current_stmt_lines.append(line)
            
            # Проверяем конец выражения
            is_end = False
            
            # GO на отдельной строке
            if stripped == 'GO':
                is_end = True
                current_stmt_lines = current_stmt_lines[:-1]  # Убираем GO
            # Точка с запятой в конце (не внутри блока)
            elif stripped.endswith(';') and not in_block:
                is_end = True
            # Двойная точка с запятой (для некоторых СУБД)
            elif stripped.endswith(';;'):
                is_end = True
            
            if is_end and current_stmt_lines:
                content = '\n'.join(current_stmt_lines).strip()
                if content and not content.upper() == 'GO':
                    statements.append((content, current_start, i))
                current_stmt_lines = []
                current_start = i + 1
        
        # Остаток
        if current_stmt_lines:
            content = '\n'.join(current_stmt_lines).strip()
            if content:
                statements.append((content, current_start, len(lines)))
        
        return statements

    def _classify_statement(self, content: str) -> tuple[str, str]:
        """
        Определяет тип SQL-выражения и извлекает имя объекта.
        Возвращает: (kind, name)
        """
        for pattern, kind in self.STATEMENT_PATTERNS:
            match = pattern.search(content)
            if match:
                # Извлекаем имя, если есть группа
                name = match.group(1) if match.lastindex and match.lastindex >= 1 else f"[{kind}]"
                return kind, name
        
        return 'other', '[statement]'


def build_sql_context(chunks: List[SQLChunk], target_chunk: SQLChunk, include_schema: bool = True) -> str:
    """
    Собирает контекст для отправки SQL-чанка в ИИ.
    
    Если target — это INSERT/UPDATE/DELETE/SELECT и include_schema=True,
    добавляет CREATE TABLE для связанной таблицы.
    """
    parts = []
    
    # Если это DML операция — добавляем схему таблицы
    if include_schema and target_chunk.kind in ('insert', 'update', 'delete', 'select'):
        table_name = target_chunk.name
        if table_name and table_name != '[statement]':
            # Ищем CREATE TABLE для этой таблицы
            table_def = next(
                (c for c in chunks if c.kind == 'create_table' and c.name.lower() == table_name.lower()),
                None
            )
            if table_def:
                parts.append(f"-- === Table Schema: {table_def.name} ===")
                parts.append(table_def.content)
                parts.append("")
    
    # Целевой чанк
    parts.append(f"-- === {target_chunk.kind.upper()}: {target_chunk.name} ===")
    parts.append(target_chunk.content)
    
    return "\n".join(parts)


def group_sql_by_table(chunks: List[SQLChunk]) -> dict[str, List[SQLChunk]]:
    """
    Группирует SQL-чанки по таблицам.
    Полезно для понимания, какие операции связаны с какой таблицей.
    """
    groups = {}
    
    for chunk in chunks:
        if chunk.kind == 'file':
            continue
        
        table_name = chunk.name if chunk.name != '[statement]' else '_misc_'
        
        if table_name not in groups:
            groups[table_name] = []
        groups[table_name].append(chunk)
    
    return groups
