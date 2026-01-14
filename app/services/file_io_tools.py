# app/services/file_io_tools.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, List, Dict, Any

from app.utils.file_types import FileTypeDetector
from app.utils.token_counter import TokenCounter
from app.utils.xml_wrapper import XMLWrapper, FileContent
from app.services.project_scanner import ProjectScanner
from app.services.python_chunker import SmartPythonChunker, PythonChunk
from app.services.go_chunker import SmartGoChunker, GoChunk
from app.services.sql_chunker import SmartSQLChunker, SQLChunk
from app.services.json_chunker import SmartJSONChunker, JSONChunk

ReadMode = Literal["auto", "full", "chunks"]

MAX_MODEL_CONTEXT = 50_000  # общий лимит по проекту
MAX_FILE_TOKENS = 50_000    # файл считается "слишком большим" выше этого
QWEN_CONTEXT_LIMIT = 20_000 # для индексации Qwen


@dataclass
class ReadRequest:
    project_root: str          # корень проекта
    path: str                  # относительный путь к файлу
    mode: ReadMode = "auto"    # auto/full/chunks
    available_tokens: int = QWEN_CONTEXT_LIMIT  # сколько токенов можно использовать под файл/чанк
    with_context: bool = True  # добавлять ли контекст (imports/globals)


@dataclass
class ReadResponse:
    ok: bool
    reason: str
    file_type: str
    tokens_total: int
    xml: Optional[str] = None
    chunks_xml: Optional[List[str]] = None
    too_large: bool = False
    suggested_mode: Optional[str] = None
    meta: Dict[str, Any] = None


class FileReaderTool:
    """
    Универсальный инструмент чтения файлов:
    - Определяет тип (python, go, sql, json, text...)
    - Считает токены
    - Решает, отдавать файл целиком или по чанкам
    - Возвращает XML-обёртку (готовую для ИИ)
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.type_detector = FileTypeDetector()
        self.token_counter = TokenCounter()
        self.xml_wrapper = XMLWrapper()
        self.python_chunker = SmartPythonChunker(self.token_counter)
        self.go_chunker = SmartGoChunker(self.token_counter)
        self.sql_chunker = SmartSQLChunker(self.token_counter)
        self.json_chunker = SmartJSONChunker(self.token_counter)
        self.project_scanner = ProjectScanner(project_root)

    def _read_text(self, full_path: Path) -> str:
        return full_path.read_text(encoding="utf-8")

    def read(self, req: ReadRequest) -> ReadResponse:
        """
        Главный метод.
        Логика:
        - Определить тип
        - Посчитать токены
        - Если mode=full: отдать целиком, но если превышает лимит -> too_large
        - Если mode=chunks или auto: отдать чанки (для кода/JSON) или отказать для других
        """
        full_path = (self.project_root / req.path).resolve()
        if not full_path.exists():
            return ReadResponse(
                ok=False,
                reason=f"File not found: {req.path}",
                file_type="unknown",
                tokens_total=0,
                meta={}
            )

        file_type = self.type_detector.detect(str(full_path))
        text = ""
        tokens_total = 0

        if self.type_detector.is_text_based(file_type):
            try:
                text = self._read_text(full_path)
                tokens_total = self.token_counter.count(text)
            except UnicodeDecodeError:
                return ReadResponse(
                    ok=False,
                    reason="File is not UTF-8 text",
                    file_type=file_type,
                    tokens_total=0,
                    meta={}
                )
        else:
            # бинарные/прочие файлы пока не поддерживаем
            return ReadResponse(
                ok=False,
                reason="Unsupported file type for reading",
                file_type=file_type,
                tokens_total=0,
                meta={}
            )

        # Решение по mode
        mode = req.mode
        if mode == "auto":
            # Если файл маленький и влазит в доступные токены — отдать целиком
            if tokens_total <= min(req.available_tokens, MAX_FILE_TOKENS):
                mode = "full"
            else:
                mode = "chunks"

        if mode == "full":
            if tokens_total > req.available_tokens or tokens_total > MAX_FILE_TOKENS:
                # ИИ должен перейти к работе с чанками
                return ReadResponse(
                    ok=False,
                    reason="File is too large for full transfer",
                    file_type=file_type,
                    tokens_total=tokens_total,
                    too_large=True,
                    suggested_mode="chunks",
                    meta={"tokens_total": tokens_total}
                )

            file = FileContent(
                path=str(full_path.relative_to(self.project_root)),
                file_type=file_type,
                content=text,
                tokens=tokens_total
            )
            xml = self.xml_wrapper.wrap_file(file)
            return ReadResponse(
                ok=True,
                reason="Full file returned",
                file_type=file_type,
                tokens_total=tokens_total,
                xml=xml,
                chunks_xml=None,
                too_large=False,
                suggested_mode=None,
                meta={"mode": "full"}
            )

        if mode == "chunks":
            # Чанкирование зависит от типа
            chunks_xml: List[str] = []

            if file_type == "code/python":
                chunks = self.python_chunker.chunk_file(str(full_path))
                for ch in chunks:
                    if ch.kind == "file":
                        continue
                    # Для Qwen учитываем лимит: промпт + контекст + чанк <= available_tokens
                    if ch.tokens > req.available_tokens:
                        # пропускаем или даём ИИ информацию, что чанк слишком большой
                        continue
                    xml = self.xml_wrapper.wrap_chunk(
                        ch, 
                        include_context=req.with_context,
                        context_chunks=chunks
                    )
                    chunks_xml.append(xml)

            elif file_type == "code/go":
                chunks = self.go_chunker.chunk_file(str(full_path))
                for ch in chunks:
                    if ch.kind == "file":
                        continue
                    if ch.tokens > req.available_tokens:
                        continue
                    xml = self.xml_wrapper.wrap_chunk(
                        ch,
                        include_context=req.with_context,
                        context_chunks=chunks
                    )
                    chunks_xml.append(xml)

            elif file_type == "sql":
                chunks = self.sql_chunker.chunk_file(str(full_path))
                for ch in chunks:
                    if ch.kind == "file":
                        continue
                    if ch.tokens > req.available_tokens:
                        continue
                    xml = self.xml_wrapper.wrap_chunk(
                        ch,
                        include_context=req.with_context,
                        context_chunks=chunks
                    )
                    chunks_xml.append(xml)

            elif file_type == "data/json":
                chunks = self.json_chunker.chunk_file(str(full_path))
                for ch in chunks:
                    if ch.tokens > req.available_tokens:
                        continue
                    # Для JSON используем wrap_file (это данные, не код)
                    file = FileContent(
                        path=str(full_path.relative_to(self.project_root)),
                        file_type="data/json-chunk",
                        content=ch.content,
                        tokens=ch.tokens,
                    )
                    xml = self.xml_wrapper.wrap_file(file)
                    chunks_xml.append(xml)

            else:
                # Текстовые файлы лучше отдавать целиком, а не чанками
                return ReadResponse(
                    ok=False,
                    reason="Chunk mode not supported for this file type",
                    file_type=file_type,
                    tokens_total=tokens_total,
                    too_large=tokens_total > req.available_tokens,
                    suggested_mode="full",
                    meta={"tokens_total": tokens_total}
                )

            return ReadResponse(
                ok=True,
                reason="Chunks returned",
                file_type=file_type,
                tokens_total=tokens_total,
                xml=None,
                chunks_xml=chunks_xml,
                too_large=False,
                suggested_mode=None,
                meta={"mode": "chunks", "chunks_count": len(chunks_xml)}
            )

        return ReadResponse(
            ok=False,
            reason=f"Unsupported read mode: {mode}",
            file_type=file_type,
            tokens_total=tokens_total,
            meta={}
        )


# ================== ИНСТРУМЕНТ ВНЕСЕНИЯ ИЗМЕНЕНИЙ ==================


@dataclass
class WriteRequest:
    project_root: str
    path: str
    new_content: str         # полный текст файла или фрагмент
    mode: Literal["replace_file", "replace_range"] = "replace_file"
    start_line: Optional[int] = None  # для replace_range
    end_line: Optional[int] = None
    file_type: Optional[str] = None   # можно подсказать тип
    validate_python_indent: bool = True


@dataclass
class WriteResponse:
    ok: bool
    reason: str
    file_type: str
    lines_affected: Optional[str] = None
    syntax_ok: Optional[bool] = None
    syntax_error: Optional[str] = None


class FileWriterTool:
    """
    Универсальный инструмент записи/изменения файлов.
    - Может заменить весь файл
    - Может заменить диапазон строк
    - Для Python умеет валидировать отступы и синтаксис
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.type_detector = FileTypeDetector()

    def _validate_python(self, code: str) -> tuple[bool, Optional[str]]:
        """
        Проверка синтаксиса Python и согласованности отступов.
        """
        import ast
        try:
            ast.parse(code)
            return True, None
        except IndentationError as e:
            return False, f"IndentationError: line {e.lineno}: {e.msg}"
        except SyntaxError as e:
            return False, f"SyntaxError: line {e.lineno}: {e.msg}"

    def write(self, req: WriteRequest) -> WriteResponse:
        full_path = (self.project_root / req.path).resolve()
        if not full_path.exists():
            return WriteResponse(
                ok=False,
                reason=f"File not found: {req.path}",
                file_type="unknown"
            )

        file_type = req.file_type or self.type_detector.detect(str(full_path))

        # Если это Python и требуется валидация — проверяем сначала
        syntax_ok = None
        syntax_error = None
        if file_type == "code/python" and req.validate_python_indent:
            syntax_ok, syntax_error = self._validate_python(req.new_content)
            if not syntax_ok:
                return WriteResponse(
                    ok=False,
                    reason="Python syntax/indentation validation failed",
                    file_type=file_type,
                    syntax_ok=False,
                    syntax_error=syntax_error
                )

        if req.mode == "replace_file":
            full_path.write_text(req.new_content, encoding="utf-8")
            return WriteResponse(
                ok=True,
                reason="File fully replaced",
                file_type=file_type,
                lines_affected="1-EOF",
                syntax_ok=syntax_ok,
                syntax_error=syntax_error
            )

        if req.mode == "replace_range":
            if req.start_line is None or req.end_line is None:
                return WriteResponse(
                    ok=False,
                    reason="start_line and end_line are required for replace_range",
                    file_type=file_type
                )
            original = full_path.read_text(encoding="utf-8")
            lines = original.splitlines()
            start = max(req.start_line, 1)
            end = min(req.end_line, len(lines))
            # заменяем диапазон (включительно)
            new_lines = (
                lines[: start - 1] +
                req.new_content.splitlines() +
                lines[end:]
            )
            full_path.write_text("\n".join(new_lines), encoding="utf-8")
            return WriteResponse(
                ok=True,
                reason="Lines replaced",
                file_type=file_type,
                lines_affected=f"{start}-{end}",
                syntax_ok=syntax_ok,
                syntax_error=syntax_error
            )

        return WriteResponse(
            ok=False,
            reason=f"Unsupported write mode: {req.mode}",
            file_type=file_type
        )
