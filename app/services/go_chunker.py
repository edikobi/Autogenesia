# app/services/go_chunker.py
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.utils.token_counter import TokenCounter

MAX_CHUNK_TOKENS = 50_000


@dataclass
class GoChunk:
    file_path: str
    kind: str           # "file" | "package" | "imports" | "const" | "var" | "type" | "struct" | "interface" | "function" | "method"
    name: str
    receiver: Optional[str]  # для методов: имя структуры-получателя
    start_line: int
    end_line: int
    tokens: int
    content: str


class SmartGoChunker:
    """
    Иерархический чанкер Go-файлов.
    
    Выделяет:
      - package (объявление пакета)
      - imports (все импорты)
      - const (блоки констант)
      - var (глобальные переменные)
      - type/struct (структуры)
      - type/interface (интерфейсы)
      - function (функции)
      - method (методы структур)
    """

    # Regex паттерны для Go
    PATTERNS = {
        'package': re.compile(r'^package\s+(\w+)', re.MULTILINE),
        'import_single': re.compile(r'^import\s+"[^"]+"', re.MULTILINE),
        'import_block': re.compile(r'^import\s*\([\s\S]*?\)', re.MULTILINE),
        'const_single': re.compile(r'^const\s+\w+', re.MULTILINE),
        'const_block': re.compile(r'^const\s*\([\s\S]*?\)', re.MULTILINE),
        'var_single': re.compile(r'^var\s+\w+', re.MULTILINE),
        'var_block': re.compile(r'^var\s*\([\s\S]*?\)', re.MULTILINE),
        'struct': re.compile(r'^type\s+(\w+)\s+struct\s*\{', re.MULTILINE),
        'interface': re.compile(r'^type\s+(\w+)\s+interface\s*\{', re.MULTILINE),
        'type_alias': re.compile(r'^type\s+(\w+)\s+\w+', re.MULTILINE),
        'function': re.compile(r'^func\s+(\w+)\s*\(', re.MULTILINE),
        'method': re.compile(r'^func\s*\((\w+)\s+\*?(\w+)\)\s*(\w+)\s*\(', re.MULTILINE),
    }

    def __init__(self, token_counter: Optional[TokenCounter] = None, max_chunk_tokens: int = MAX_CHUNK_TOKENS):
        self.tokens = token_counter or TokenCounter()
        self.max_chunk_tokens = max_chunk_tokens

    def chunk_file(self, file_path: str) -> List[GoChunk]:
        path = Path(file_path)
        code = path.read_text(encoding="utf-8")
        lines = code.splitlines()
        total_tokens = self.tokens.count(code)

        chunks: List[GoChunk] = []

        # 0. Чанк уровня файла
        chunks.append(
            GoChunk(
                file_path=str(path),
                kind="file",
                name=path.name,
                receiver=None,
                start_line=1,
                end_line=len(lines),
                tokens=total_tokens,
                content=code,
            )
        )

        # 1. Package
        pkg_chunk = self._extract_package(code, lines, path)
        if pkg_chunk:
            chunks.append(pkg_chunk)

        # 2. Imports
        imports_chunk = self._extract_imports(code, lines, path)
        if imports_chunk:
            chunks.append(imports_chunk)

        # 3. Const blocks
        chunks.extend(self._extract_const_var(code, lines, path, "const"))

        # 4. Var blocks
        chunks.extend(self._extract_const_var(code, lines, path, "var"))

        # 5. Types (structs, interfaces)
        chunks.extend(self._extract_types(code, lines, path))

        # 6. Functions and Methods
        chunks.extend(self._extract_functions(code, lines, path))

        return chunks

    def _find_block_end(self, code: str, start_pos: int) -> int:
        """Находит конец блока с фигурными скобками."""
        brace_count = 0
        in_string = False
        string_char = None
        i = start_pos
        
        while i < len(code):
            char = code[i]
            
            # Обработка строк
            if char in ('"', '`') and (i == 0 or code[i-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    in_string = False
            
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return i + 1
            i += 1
        
        return len(code)

    def _pos_to_line(self, code: str, pos: int) -> int:
        """Конвертирует позицию в номер строки."""
        return code[:pos].count('\n') + 1

    def _extract_package(self, code: str, lines: list[str], path: Path) -> Optional[GoChunk]:
        match = self.PATTERNS['package'].search(code)
        if not match:
            return None
        
        start_line = self._pos_to_line(code, match.start())
        content = match.group(0)
        
        return GoChunk(
            file_path=str(path),
            kind="package",
            name=match.group(1),
            receiver=None,
            start_line=start_line,
            end_line=start_line,
            tokens=self.tokens.count(content),
            content=content,
        )

    def _extract_imports(self, code: str, lines: list[str], path: Path) -> Optional[GoChunk]:
        # Сначала ищем блочный import
        match = self.PATTERNS['import_block'].search(code)
        if not match:
            # Ищем одиночные импорты
            matches = list(self.PATTERNS['import_single'].finditer(code))
            if not matches:
                return None
            
            contents = [m.group(0) for m in matches]
            start_line = self._pos_to_line(code, matches[0].start())
            end_line = self._pos_to_line(code, matches[-1].end())
            content = '\n'.join(contents)
        else:
            start_line = self._pos_to_line(code, match.start())
            end_line = self._pos_to_line(code, match.end())
            content = match.group(0)
        
        return GoChunk(
            file_path=str(path),
            kind="imports",
            name="[imports]",
            receiver=None,
            start_line=start_line,
            end_line=end_line,
            tokens=self.tokens.count(content),
            content=content,
        )

    def _extract_const_var(self, code: str, lines: list[str], path: Path, kind: str) -> List[GoChunk]:
        chunks = []
        
        # Блочные объявления
        block_pattern = self.PATTERNS[f'{kind}_block']
        for match in block_pattern.finditer(code):
            start_line = self._pos_to_line(code, match.start())
            end_line = self._pos_to_line(code, match.end())
            content = match.group(0)
            
            chunks.append(GoChunk(
                file_path=str(path),
                kind=kind,
                name=f"[{kind} block]",
                receiver=None,
                start_line=start_line,
                end_line=end_line,
                tokens=self.tokens.count(content),
                content=content,
            ))
        
        return chunks

    def _extract_types(self, code: str, lines: list[str], path: Path) -> List[GoChunk]:
        chunks = []
        
        # Структуры
        for match in self.PATTERNS['struct'].finditer(code):
            name = match.group(1)
            start_pos = match.start()
            end_pos = self._find_block_end(code, match.end() - 1)
            
            start_line = self._pos_to_line(code, start_pos)
            end_line = self._pos_to_line(code, end_pos)
            content = code[start_pos:end_pos]
            
            chunks.append(GoChunk(
                file_path=str(path),
                kind="struct",
                name=name,
                receiver=None,
                start_line=start_line,
                end_line=end_line,
                tokens=self.tokens.count(content),
                content=content,
            ))
        
        # Интерфейсы
        for match in self.PATTERNS['interface'].finditer(code):
            name = match.group(1)
            start_pos = match.start()
            end_pos = self._find_block_end(code, match.end() - 1)
            
            start_line = self._pos_to_line(code, start_pos)
            end_line = self._pos_to_line(code, end_pos)
            content = code[start_pos:end_pos]
            
            chunks.append(GoChunk(
                file_path=str(path),
                kind="interface",
                name=name,
                receiver=None,
                start_line=start_line,
                end_line=end_line,
                tokens=self.tokens.count(content),
                content=content,
            ))
        
        return chunks

    def _extract_functions(self, code: str, lines: list[str], path: Path) -> List[GoChunk]:
        chunks = []
        
        # Методы (func (r Receiver) Name())
        method_positions = set()
        for match in self.PATTERNS['method'].finditer(code):
            receiver_var = match.group(1)
            receiver_type = match.group(2)
            name = match.group(3)
            
            start_pos = match.start()
            end_pos = self._find_block_end(code, code.find('{', match.end()))
            method_positions.add(start_pos)
            
            start_line = self._pos_to_line(code, start_pos)
            end_line = self._pos_to_line(code, end_pos)
            content = code[start_pos:end_pos]
            
            chunks.append(GoChunk(
                file_path=str(path),
                kind="method",
                name=name,
                receiver=receiver_type,
                start_line=start_line,
                end_line=end_line,
                tokens=self.tokens.count(content),
                content=content,
            ))
        
        # Функции (func Name())
        for match in self.PATTERNS['function'].finditer(code):
            start_pos = match.start()
            
            # Пропускаем, если это метод
            if start_pos in method_positions:
                continue
            
            name = match.group(1)
            brace_pos = code.find('{', match.end())
            if brace_pos == -1:
                continue
                
            end_pos = self._find_block_end(code, brace_pos)
            
            start_line = self._pos_to_line(code, start_pos)
            end_line = self._pos_to_line(code, end_pos)
            content = code[start_pos:end_pos]
            
            chunks.append(GoChunk(
                file_path=str(path),
                kind="function",
                name=name,
                receiver=None,
                start_line=start_line,
                end_line=end_line,
                tokens=self.tokens.count(content),
                content=content,
            ))
        
        return chunks


def build_go_context(chunks: List[GoChunk], target_chunk: GoChunk) -> str:
    """
    Собирает контекст для отправки Go-чанка в ИИ.
    Добавляет package, imports и связанную структуру (если это метод).
    """
    parts = []
    
    # 1. Package
    pkg = next((c for c in chunks if c.kind == "package"), None)
    if pkg:
        parts.append(pkg.content)
        parts.append("")
    
    # 2. Imports
    imports = next((c for c in chunks if c.kind == "imports"), None)
    if imports:
        parts.append(imports.content)
        parts.append("")
    
    # 3. Если это метод — добавляем структуру-получатель
    if target_chunk.kind == "method" and target_chunk.receiver:
        struct = next((c for c in chunks if c.kind == "struct" and c.name == target_chunk.receiver), None)
        if struct:
            parts.append(f"// === Struct: {struct.name} ===")
            parts.append(struct.content)
            parts.append("")
    
    # 4. Целевой чанк
    parts.append(f"// === {target_chunk.kind.capitalize()}: {target_chunk.name} ===")
    parts.append(target_chunk.content)
    
    return "\n".join(parts)
