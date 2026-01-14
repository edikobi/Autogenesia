# app/services/json_chunker.py
"""
Чанкер для JSON файлов.

Особенности JSON:
- Структурированные данные (объекты, массивы)
- Если файл < 50k токенов — передаём целиком
- Если > 50k токенов — чанкируем по размеру (не по структуре, т.к. это данные)

Для больших JSON (массивы объектов) пытаемся резать по элементам массива.
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Any

from app.utils.token_counter import TokenCounter

MAX_CHUNK_TOKENS = 50_000


@dataclass
class JSONChunk:
    file_path: str
    kind: str           # "file" | "chunk" | "array_slice"
    name: str           # имя или индекс
    chunk_index: int    # номер чанка (0, 1, 2...)
    total_chunks: int   # общее количество чанков
    start_index: Optional[int]  # для массивов: начальный индекс
    end_index: Optional[int]    # для массивов: конечный индекс
    tokens: int
    content: str


class SmartJSONChunker:
    """
    Чанкер JSON файлов.
    
    Стратегия:
    1. Если файл <= 50k токенов — один чанк (файл целиком)
    2. Если файл > 50k токенов:
       - Если это массив объектов — режем по элементам массива
       - Иначе — режем по размеру (грубая нарезка)
    """

    def __init__(self, token_counter: Optional[TokenCounter] = None, 
                 max_chunk_tokens: int = MAX_CHUNK_TOKENS):
        self.tokens = token_counter or TokenCounter()
        self.max_chunk_tokens = max_chunk_tokens

    def chunk_file(self, file_path: str) -> List[JSONChunk]:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        total_tokens = self.tokens.count(content)

        chunks: List[JSONChunk] = []

        # Если файл маленький — возвращаем целиком
        if total_tokens <= self.max_chunk_tokens:
            chunks.append(
                JSONChunk(
                    file_path=str(path),
                    kind="file",
                    name=path.name,
                    chunk_index=0,
                    total_chunks=1,
                    start_index=None,
                    end_index=None,
                    tokens=total_tokens,
                    content=content,
                )
            )
            return chunks

        # Файл большой — нужно чанкировать
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Невалидный JSON — режем по размеру текста
            return self._chunk_by_size(content, path)

        # Если корень — массив, режем по элементам
        if isinstance(data, list):
            return self._chunk_array(data, path)
        
        # Если корень — объект, пробуем разбить по ключам верхнего уровня
        if isinstance(data, dict):
            return self._chunk_object(data, path)

        # Fallback — по размеру
        return self._chunk_by_size(content, path)

    def _chunk_array(self, data: list, path: Path) -> List[JSONChunk]:
        """Чанкирует JSON-массив по элементам."""
        chunks = []
        current_batch = []
        current_tokens = 0
        chunk_index = 0
        start_index = 0

        for i, item in enumerate(data):
            item_json = json.dumps(item, ensure_ascii=False, indent=2)
            item_tokens = self.tokens.count(item_json)

            # Если один элемент больше лимита — добавляем как есть
            if item_tokens > self.max_chunk_tokens:
                # Сначала сохраняем накопленное
                if current_batch:
                    chunks.append(self._create_array_chunk(
                        current_batch, path, chunk_index, start_index, i - 1, current_tokens
                    ))
                    chunk_index += 1
                
                # Добавляем большой элемент отдельно
                chunks.append(
                    JSONChunk(
                        file_path=str(path),
                        kind="array_element",
                        name=f"[{i}]",
                        chunk_index=chunk_index,
                        total_chunks=-1,  # Заполним позже
                        start_index=i,
                        end_index=i,
                        tokens=item_tokens,
                        content=item_json,
                    )
                )
                chunk_index += 1
                current_batch = []
                current_tokens = 0
                start_index = i + 1
                continue

            # Проверяем, влезает ли в текущий батч
            if current_tokens + item_tokens > self.max_chunk_tokens:
                # Сохраняем текущий батч
                if current_batch:
                    chunks.append(self._create_array_chunk(
                        current_batch, path, chunk_index, start_index, i - 1, current_tokens
                    ))
                    chunk_index += 1
                
                current_batch = [item]
                current_tokens = item_tokens
                start_index = i
            else:
                current_batch.append(item)
                current_tokens += item_tokens

        # Остаток
        if current_batch:
            chunks.append(self._create_array_chunk(
                current_batch, path, chunk_index, start_index, len(data) - 1, current_tokens
            ))

        # Обновляем total_chunks
        total = len(chunks)
        for chunk in chunks:
            chunk.total_chunks = total

        return chunks

    def _create_array_chunk(self, items: list, path: Path, index: int, 
                            start: int, end: int, tokens: int) -> JSONChunk:
        content = json.dumps(items, ensure_ascii=False, indent=2)
        return JSONChunk(
            file_path=str(path),
            kind="array_slice",
            name=f"[{start}:{end+1}]",
            chunk_index=index,
            total_chunks=-1,
            start_index=start,
            end_index=end,
            tokens=tokens,
            content=content,
        )

    def _chunk_object(self, data: dict, path: Path) -> List[JSONChunk]:
        """Чанкирует JSON-объект по ключам верхнего уровня."""
        chunks = []
        current_obj = {}
        current_tokens = 0
        chunk_index = 0

        for key, value in data.items():
            item_json = json.dumps({key: value}, ensure_ascii=False, indent=2)
            item_tokens = self.tokens.count(item_json)

            if current_tokens + item_tokens > self.max_chunk_tokens:
                if current_obj:
                    content = json.dumps(current_obj, ensure_ascii=False, indent=2)
                    chunks.append(
                        JSONChunk(
                            file_path=str(path),
                            kind="object_slice",
                            name=f"keys_{chunk_index}",
                            chunk_index=chunk_index,
                            total_chunks=-1,
                            start_index=None,
                            end_index=None,
                            tokens=current_tokens,
                            content=content,
                        )
                    )
                    chunk_index += 1
                
                current_obj = {key: value}
                current_tokens = item_tokens
            else:
                current_obj[key] = value
                current_tokens += item_tokens

        # Остаток
        if current_obj:
            content = json.dumps(current_obj, ensure_ascii=False, indent=2)
            chunks.append(
                JSONChunk(
                    file_path=str(path),
                    kind="object_slice",
                    name=f"keys_{chunk_index}",
                    chunk_index=chunk_index,
                    total_chunks=-1,
                    start_index=None,
                    end_index=None,
                    tokens=current_tokens,
                    content=content,
                )
            )

        # Обновляем total_chunks
        total = len(chunks)
        for chunk in chunks:
            chunk.total_chunks = total

        return chunks

    def _chunk_by_size(self, content: str, path: Path) -> List[JSONChunk]:
        """Грубое чанкирование по размеру (fallback)."""
        chunks = []
        lines = content.split('\n')
        current_lines = []
        current_tokens = 0
        chunk_index = 0

        for line in lines:
            line_tokens = self.tokens.count(line)
            
            if current_tokens + line_tokens > self.max_chunk_tokens:
                if current_lines:
                    chunk_content = '\n'.join(current_lines)
                    chunks.append(
                        JSONChunk(
                            file_path=str(path),
                            kind="chunk",
                            name=f"part_{chunk_index}",
                            chunk_index=chunk_index,
                            total_chunks=-1,
                            start_index=None,
                            end_index=None,
                            tokens=current_tokens,
                            content=chunk_content,
                        )
                    )
                    chunk_index += 1
                
                current_lines = [line]
                current_tokens = line_tokens
            else:
                current_lines.append(line)
                current_tokens += line_tokens

        # Остаток
        if current_lines:
            chunk_content = '\n'.join(current_lines)
            chunks.append(
                JSONChunk(
                    file_path=str(path),
                    kind="chunk",
                    name=f"part_{chunk_index}",
                    chunk_index=chunk_index,
                    total_chunks=-1,
                    start_index=None,
                    end_index=None,
                    tokens=current_tokens,
                    content=chunk_content,
                )
            )

        # Обновляем total_chunks
        total = len(chunks)
        for chunk in chunks:
            chunk.total_chunks = total

        return chunks
