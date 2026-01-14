import os
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

# Импорты для обработки файлов
try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import docx
except ImportError:
    docx = None

try:
    import pandas as pd
except ImportError:
    pd = None

from config.settings import cfg
from app.utils.token_counter import TokenCounter

logger = logging.getLogger(__name__)

@dataclass
class ParsedFile:
    filename: str
    content: str
    tokens: int
    file_type: str
    error: Optional[str] = None

class FileParser:
    """
    Утилита для чтения и конвертации пользовательских файлов в текст.
    Поддерживает: .txt, .py (и прочий код), .pdf, .docx, .xlsx, .csv.
    """
    
    def __init__(self):
        self.token_counter = TokenCounter()
        self.max_tokens = cfg.MAX_USER_FILES_TOKENS  # 55000

    async def parse_files(self, file_paths: List[str]) -> Tuple[List[ParsedFile], Optional[str]]:
        """
        Парсит список файлов, проверяет общий лимит токенов.
        Возвращает: (список обработанных файлов, сообщение об ошибке/предупреждении)
        """
        parsed_files = []
        total_tokens = 0
        warning_msg = None

        for path in file_paths:
            if not os.path.exists(path):
                logger.warning(f"File not found: {path}")
                continue
                
            try:
                # 1. Извлекаем текст
                content, file_type = self._read_file_content(path)
                
                if not content:
                    logger.warning(f"Empty content or unsupported file: {path}")
                    continue

                # 2. Считаем токены
                tokens = self.token_counter.count(content)
                
                # 3. Проверяем лимит (накопительно)
                if total_tokens + tokens > self.max_tokens:
                    remaining = self.max_tokens - total_tokens
                    if remaining > 100:
                        # Обрезаем последний файл, чтобы влезть
                        content = self._truncate_content(content, remaining)
                        tokens = remaining
                        parsed_files.append(ParsedFile(
                            filename=os.path.basename(path),
                            content=content + "\n\n[TRUNCATED DUE TO TOKEN LIMIT]",
                            tokens=tokens,
                            file_type=file_type
                        ))
                        warning_msg = f"Лимит токенов ({self.max_tokens}) превышен. Файл '{os.path.basename(path)}' был обрезан, остальные пропущены."
                    else:
                        warning_msg = f"Лимит токенов ({self.max_tokens}) превышен. Файл '{os.path.basename(path)}' и последующие пропущены."
                    
                    break # Прерываем цикл, так как лимит достигнут
                
                parsed_files.append(ParsedFile(
                    filename=os.path.basename(path),
                    content=content,
                    tokens=tokens,
                    file_type=file_type
                ))
                total_tokens += tokens
                
            except Exception as e:
                logger.error(f"Error parsing file {path}: {e}")
                parsed_files.append(ParsedFile(
                    filename=os.path.basename(path),
                    content="",
                    tokens=0,
                    file_type="unknown",
                    error=str(e)
                ))

        return parsed_files, warning_msg

    def _read_file_content(self, path: str) -> Tuple[str, str]:
        """Определяет тип файла и вызывает соответствующий парсер"""
        ext = os.path.splitext(path)[1].lower()
        
        if ext == '.pdf':
            return self._read_pdf(path), 'pdf'
        elif ext in ['.docx', '.doc']:
            return self._read_docx(path), 'docx'
        elif ext in ['.xlsx', '.xls']:
            return self._read_excel(path), 'excel'
        elif ext == '.csv':
            return self._read_csv(path), 'csv'
        else:
            # Пытаемся читать как текст (код, txt, md, json и т.д.)
            return self._read_text(path), 'text'

    def _read_text(self, path: str) -> str:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # Fallback для других кодировок
            try:
                with open(path, 'r', encoding='cp1251') as f:
                    return f.read()
            except:
                return "[Error: Binary or unsupported text encoding]"

    def _read_pdf(self, path: str) -> str:
        if not pypdf:
            return "[Error: pypdf library not installed]"
        try:
            text = []
            with open(path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    text.append(page.extract_text() or "")
            return "\n".join(text)
        except Exception as e:
            return f"[Error reading PDF: {e}]"

    def _read_docx(self, path: str) -> str:
        if not docx:
            return "[Error: python-docx library not installed]"
        try:
            doc = docx.Document(path)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            return f"[Error reading DOCX: {e}]"

    def _read_excel(self, path: str) -> str:
        if not pd:
            return "[Error: pandas library not installed]"
        try:
            # Читаем все листы
            dfs = pd.read_excel(path, sheet_name=None)
            text_parts = []
            for sheet_name, df in dfs.items():
                text_parts.append(f"Sheet: {sheet_name}")
                # Конвертируем в Markdown таблицу
                text_parts.append(df.to_markdown(index=False))
            return "\n\n".join(text_parts)
        except Exception as e:
            return f"[Error reading Excel: {e}]"

    def _read_csv(self, path: str) -> str:
        if not pd:
            return "[Error: pandas library not installed]"
        try:
            df = pd.read_csv(path)
            return df.to_markdown(index=False)
        except Exception as e:
            return f"[Error reading CSV: {e}]"

    def _truncate_content(self, content: str, max_tokens: int) -> str:
        """Грубая обрезка по символам (примерно 1 токен = 4 символа)"""
        # Точный подсчет дорог, поэтому режем с запасом и проверяем
        chars_limit = max_tokens * 4
        return content[:chars_limit]
