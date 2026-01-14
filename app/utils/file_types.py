# app/utils/file_types.py
from pathlib import Path


class FileTypeDetector:
    """
    Определение типа файла по расширению.
    """

    CODE_EXTENSIONS = {
        ".py": "code/python",
        ".go": "code/go",
        ".js": "code/js",
        ".ts": "code/ts",
    }

    TEXT_EXTENSIONS = {
        ".md": "text/md",
        ".markdown": "text/md",
        ".txt": "text/txt",
        ".rst": "text/rst",
    }

    DATA_EXTENSIONS = {
        ".csv": "data/csv",
        ".tsv": "data/csv",
        ".xlsx": "data/xlsx",
        ".xls": "data/xlsx",
        ".json": "data/json",      # ДОБАВЛЕНО
        ".jsonl": "data/jsonl",    # ДОБАВЛЕНО
    }

    SQL_EXTENSIONS = {
        ".sql": "sql",
    }

    CONFIG_EXTENSIONS = {
        ".yaml": "config/yaml",
        ".yml": "config/yaml",
        ".ini": "config/ini",
        ".toml": "config/toml",
        ".env": "config/env",
    }

    def detect(self, path: str) -> str:
        p = Path(path)
        ext = p.suffix.lower()

        if ext in self.CODE_EXTENSIONS:
            return self.CODE_EXTENSIONS[ext]
        if ext in self.SQL_EXTENSIONS:
            return self.SQL_EXTENSIONS[ext]
        if ext in self.TEXT_EXTENSIONS:
            return self.TEXT_EXTENSIONS[ext]
        if ext in self.DATA_EXTENSIONS:
            return self.DATA_EXTENSIONS[ext]
        if ext in self.CONFIG_EXTENSIONS:
            return self.CONFIG_EXTENSIONS[ext]

        return "other"
    
    def is_chunkable_code(self, file_type: str) -> bool:
        """Можно ли применить AST/структурное чанкирование."""
        return file_type in ("code/python", "code/go", "sql")
    
    def is_text_based(self, file_type: str) -> bool:
        """Текстовый файл (можно читать как UTF-8)."""
        return (
            file_type.startswith("code/") or
            file_type.startswith("text/") or
            file_type.startswith("data/") or
            file_type.startswith("config/") or
            file_type == "sql"
        )
