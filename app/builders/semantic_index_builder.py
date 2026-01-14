# app/services/semantic_indexer.py
"""
Incremental Semantic Indexer for Python projects.
Creates detailed project maps with AI-generated descriptions.
Tracks changes via content hashing for efficient updates.

Features:
- Dual output: Full index + Compact index for AI context
- Robust response parsing with fallback
- Automatic retry with model fallback (Qwen -> DeepSeek)
- Granular caching at file/class/function level
- [NEW] Incremental updates: sync only changed files
- [NEW] File watcher for automatic re-indexing
- [NEW] Incremental update of compressed index (if exists)
"""

from __future__ import annotations
import json
import asyncio
import hashlib
import re
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set, Callable
from enum import Enum
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import aiofiles
import multiprocessing
import httpx

# Правильные импорты относительно структуры проекта
from app.utils.token_counter import TokenCounter
from app.services.python_chunker import SmartPythonChunker, PythonTreeNode
from config.settings import cfg


# ============== LOGGING ==============

logger = logging.getLogger(__name__)


# ============== CONFIGURATION ==============

QWEN_TOKEN_THRESHOLD = 15_000
INDEX_FILENAME = "semantic_index.json"
COMPACT_INDEX_FILENAME = "compact_index.json"
COMPACT_INDEX_MD = "compact_index.md"
COMPRESSED_INDEX_FILENAME = "semantic_index_compressed.json"  # [NEW]
INDEX_VERSION = "1.2"

REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 3
CONCURRENT_REQUESTS = 5

IGNORE_DIRS: Set[str] = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache",
    ".pytest_cache", ".tox", "eggs", "site-packages"
}


class AnalyzerModel(Enum):
    QWEN = "qwen"
    DEEPSEEK = "deepseek"


class AnalysisError(Exception):
    """Ошибка анализа кода"""
    pass


# ============== PROMPTS ==============

QWEN_SYSTEM_PROMPT = """You are a code documentation assistant. Output ONLY a 1-2 sentence description.

Format: <what it does> + <key integrations if relevant>

Example input: A UserAuth class with login/logout methods using JWT
Example output: Handles user authentication via JWT tokens, integrates with UserRepository for credential validation.

IMPORTANT: Output ONLY the description. No thinking, no explanations, no markdown formatting."""

DEEPSEEK_SYSTEM_PROMPT = """You are a senior code analyst. Output ONLY a 1-2 sentence technical description.

Format: <primary purpose> + <system integration>

Example: ETL pipeline for CSV processing with pandas, supports parallel execution and checkpoint recovery.

Be precise. No preamble. Just the description."""

QWEN_CLASS_PROMPT = """Describe this class in 1-2 sentences. Output ONLY the description.

File: {context}

{code}

"""

QWEN_FUNCTION_PROMPT = """Describe this function in 1-2 sentences. Output ONLY the description.

File: {context}

{code}

"""

DEEPSEEK_CLASS_PROMPT = """Describe this Python class (1-2 sentences technical description).

File context: {context}

{code}

Description:"""

DEEPSEEK_FUNCTION_PROMPT = """Describe this Python function (1-2 sentences).

File context: {context}

{code}

Description:"""

FILE_SUMMARY_PROMPT = """Based on these components, write ONE sentence describing the file's purpose.

File: {filename}
Components:
{components}

Purpose:"""


# ============== DATA STRUCTURES ==============

@dataclass
class ImportInfo:
    lines: str
    modules: List[str]
    tokens: int = 0


@dataclass
class GlobalsInfo:
    lines: str
    names: List[str]
    tokens: int = 0


@dataclass
class ClassInfo:
    name: str
    lines: str
    tokens: int
    content_hash: str
    analyzed_by: str
    description: str
    references: List[str]
    methods: List[str]


@dataclass
class FunctionInfo:
    name: str
    lines: str
    tokens: int
    content_hash: str
    analyzed_by: str
    description: str
    references: List[str]


@dataclass
class FileIndex:
    name: str
    path: str  # Относительный путь от корня ИНДЕКСИРУЕМОЙ директории
    full_path: str  # Абсолютный путь
    file_hash: str
    tokens_total: int
    lines_total: int
    imports: Optional[ImportInfo]
    globals: Optional[GlobalsInfo]
    description: str
    classes: List[ClassInfo]
    functions: List[FunctionInfo]
    last_indexed: str


@dataclass
class IndexStats:
    qwen_calls: int = 0
    qwen_successes: int = 0
    qwen_failures: int = 0
    deepseek_calls: int = 0
    deepseek_successes: int = 0
    deepseek_failures: int = 0
    fallback_to_deepseek: int = 0
    total_analysis_tokens: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_updated: int = 0
    files_added: int = 0
    files_removed: int = 0
    parse_recoveries: int = 0
    indexing_duration_seconds: float = 0
    errors: List[str] = field(default_factory=list)


# ============== HASHING ==============

class ContentHasher:
    @staticmethod
    def hash_file(path: Path) -> str:
        hasher = hashlib.md5()
        try:
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (PermissionError, OSError):
            return "access_denied"
    
    @staticmethod
    def hash_content(content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()
    
    @staticmethod
    def hash_code_block(code: str) -> str:
        normalized = "\n".join(line.rstrip() for line in code.splitlines())
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()


# ============== RESPONSE PARSER ==============

class ResponseParser:
    """
    Robust parser for AI responses.
    Handles various edge cases: thinking blocks, markdown, malformed output.
    """
    
    THINKING_PATTERNS = [
        r"<think>.*?</think>",
        r"<thinking>.*?</thinking>",
        r"\*\*Thinking:?\*\*.*?(?=\n\n|\Z)",
        r"Let me (?:think|analyze|look).*?(?=\n\n|\n[A-Z]|\Z)",
        r"I'll (?:analyze|examine|look).*?(?=\n\n|\n[A-Z]|\Z)",
        r"Looking at (?:this|the).*?(?=\n\n|\n[A-Z]|\Z)",
        r"First,.*?(?=\n\n|\Z)",
        r"Okay,.*?(?=\n\n|\Z)",
        r"Sure,.*?(?=\n\n|\Z)",
    ]
    
    STRIP_PREFIXES = [
        "Description:", "Output:", "Answer:", "Result:", "Summary:",
        "Here's", "Here is", "The description:", "Brief description:",
        "→", "->", "•", "-", "*",
    ]
    
    @classmethod
    def parse(cls, response: str, stats: Optional[IndexStats] = None) -> str:
        if not response:
            return "[Empty response]"
        
        original = response
        cleaned = response.strip()
        
        # 1. Remove thinking blocks
        for pattern in cls.THINKING_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        
        # 2. Remove markdown
        cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
        
        # 3. Strip prefixes
        for prefix in cls.STRIP_PREFIXES:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
        
        # 4. Extract first meaningful content
        cleaned = cls._extract_first_meaningful(cleaned)
        
        # 5. Final cleanup
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.strip("\"'")
        
        if stats and cleaned != original.strip():
            stats.parse_recoveries += 1
        
        if len(cleaned) < 10:
            return "[Parse error: response too short]"
        
        if len(cleaned) > 500:
            truncated = cleaned[:500]
            last_period = truncated.rfind(".")
            if last_period > 200:
                cleaned = truncated[:last_period + 1]
            else:
                cleaned = truncated + "..."
        
        return cleaned
    
    @classmethod
    def _extract_first_meaningful(cls, text: str) -> str:
        lines = text.strip().split("\n")
        
        meaningful_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                if meaningful_lines:
                    break
                continue
            
            skip_patterns = [
                r"^(let me|i'll|i will|looking at|analyzing|first|okay|sure)",
                r"^(the class|this class|the function|this function)\s+(is|appears|seems)",
                r"^(based on|from the code|in this code)",
            ]
            
            should_skip = False
            for pattern in skip_patterns:
                if re.match(pattern, line.lower()):
                    should_skip = True
                    break
            
            if not should_skip:
                meaningful_lines.append(line)
        
        if not meaningful_lines:
            for line in lines:
                line = line.strip()
                if line and len(line) > 10:
                    return line
            return text[:200]
        
        return " ".join(meaningful_lines[:3])
    
    @classmethod
    def is_valid_description(cls, text: str) -> bool:
        if not text or len(text) < 10:
            return False
        
        if text.startswith("[") and text.endswith("]"):
            return False
        
        invalid_starts = [
            "let me", "i'll", "i will", "sure,", "okay,",
            "error:", "failed:", "exception:",
        ]
        
        lower = text.lower()
        for start in invalid_starts:
            if lower.startswith(start):
                return False
        
        return True


# ============== AI CLIENT ==============

class AIClient:
    """AI client with robust error handling and automatic fallback."""
    
    def __init__(self):
        self.token_counter = TokenCounter()
        self.stats = IndexStats()
        self._semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.parser = ResponseParser()
    
    async def analyze_class(self, code: str, context: str, tokens: int) -> Tuple[str, str]:
        if tokens >= QWEN_TOKEN_THRESHOLD:
            desc = await self._analyze_with_deepseek(
                DEEPSEEK_CLASS_PROMPT.format(context=context, code=code),
                "class"
            )
            return desc, AnalyzerModel.DEEPSEEK.value
        
        desc, success = await self._analyze_with_qwen_fallback(
            QWEN_CLASS_PROMPT.format(context=context, code=code),
            DEEPSEEK_CLASS_PROMPT.format(context=context, code=code),
            "class"
        )
        
        model = AnalyzerModel.QWEN.value if success else AnalyzerModel.DEEPSEEK.value
        return desc, model
    
    async def analyze_function(self, code: str, context: str, tokens: int) -> Tuple[str, str]:
        if tokens >= QWEN_TOKEN_THRESHOLD:
            desc = await self._analyze_with_deepseek(
                DEEPSEEK_FUNCTION_PROMPT.format(context=context, code=code),
                "function"
            )
            return desc, AnalyzerModel.DEEPSEEK.value
        
        desc, success = await self._analyze_with_qwen_fallback(
            QWEN_FUNCTION_PROMPT.format(context=context, code=code),
            DEEPSEEK_FUNCTION_PROMPT.format(context=context, code=code),
            "function"
        )
        
        model = AnalyzerModel.QWEN.value if success else AnalyzerModel.DEEPSEEK.value
        return desc, model
    
    async def summarize_file(self, components: List[str], filename: str) -> str:
        if not components:
            return "Utility module with minimal significant logic."
        
        prompt = FILE_SUMMARY_PROMPT.format(
            filename=filename,
            components="\n".join(f"- {c}" for c in components[:10])
        )
        
        return await self._analyze_with_deepseek(prompt, "file_summary")
    
    async def _analyze_with_qwen_fallback(
        self, 
        qwen_prompt: str, 
        deepseek_prompt: str,
        task_type: str
    ) -> Tuple[str, bool]:
        result = await self._call_qwen(qwen_prompt)
        
        if result and self.parser.is_valid_description(result):
            return result, True
        
        logger.warning(f"Qwen failed for {task_type}, falling back to DeepSeek")
        self.stats.fallback_to_deepseek += 1
        self.stats.qwen_failures += 1
        
        result = await self._call_deepseek(deepseek_prompt)
        return result, False
    
    async def _analyze_with_deepseek(self, prompt: str, task_type: str) -> str:
        return await self._call_deepseek(prompt)
    
    async def _call_qwen(self, prompt: str) -> Optional[str]:
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                for attempt in range(MAX_RETRIES):
                    try:
                        response = await client.post(
                            f"{cfg.OPENROUTER_BASE_URL}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": cfg.MODEL_QWEN,
                                "messages": [
                                    {"role": "system", "content": QWEN_SYSTEM_PROMPT},
                                    {"role": "user", "content": prompt}
                                ],
                                "temperature": 0.1,
                                "max_tokens": 200,
                                "top_p": 0.9,
                            }
                        )
                        
                        self.stats.qwen_calls += 1
                        self.stats.total_analysis_tokens += self.token_counter.count(prompt)
                        
                        if response.status_code != 200:
                            error_msg = f"Qwen HTTP {response.status_code}: {response.text[:100]}"
                            logger.warning(error_msg)
                            self.stats.errors.append(error_msg)
                            
                            if response.status_code in (429, 500, 502, 503):
                                await asyncio.sleep(2 ** attempt)
                                continue
                            return None
                        
                        data = response.json()
                        raw_content = data["choices"][0]["message"]["content"]
                        
                        parsed = self.parser.parse(raw_content, self.stats)
                        
                        if self.parser.is_valid_description(parsed):
                            self.stats.qwen_successes += 1
                            return parsed
                        
                        logger.warning(f"Qwen invalid response (attempt {attempt + 1}): {parsed[:50]}")
                        
                    except httpx.TimeoutException:
                        logger.warning(f"Qwen timeout (attempt {attempt + 1})")
                        self.stats.errors.append("qwen_timeout")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Qwen JSON error: {e}")
                        self.stats.errors.append(f"qwen_json_error: {str(e)[:50]}")
                    except Exception as e:
                        logger.error(f"Qwen unexpected error: {e}")
                        self.stats.errors.append(f"qwen_error: {str(e)[:50]}")
                    
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(2 ** attempt)
        
        return None
    
    async def _call_deepseek(self, prompt: str) -> str:
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                for attempt in range(MAX_RETRIES):
                    try:
                        response = await client.post(
                            f"{cfg.DEEPSEEK_BASE_URL}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {cfg.DEEPSEEK_API_KEY}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": cfg.MODEL_NORMAL,
                                "messages": [
                                    {"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT},
                                    {"role": "user", "content": prompt}
                                ],
                                "temperature": 0.1,
                                "max_tokens": 300,
                            }
                        )
                        
                        self.stats.deepseek_calls += 1
                        self.stats.total_analysis_tokens += self.token_counter.count(prompt)
                        
                        if response.status_code != 200:
                            error_msg = f"DeepSeek HTTP {response.status_code}"
                            logger.warning(error_msg)
                            self.stats.errors.append(error_msg)
                            
                            if response.status_code in (429, 500, 502, 503):
                                await asyncio.sleep(2 ** attempt)
                                continue
                            
                            self.stats.deepseek_failures += 1
                            return "[DeepSeek API error]"
                        
                        data = response.json()
                        raw_content = data["choices"][0]["message"]["content"]
                        
                        parsed = self.parser.parse(raw_content, self.stats)
                        
                        if self.parser.is_valid_description(parsed):
                            self.stats.deepseek_successes += 1
                            return parsed
                        
                        logger.warning(f"DeepSeek invalid response: {parsed[:50]}")
                        
                    except httpx.TimeoutException:
                        logger.warning(f"DeepSeek timeout (attempt {attempt + 1})")
                        self.stats.errors.append("deepseek_timeout")
                    except json.JSONDecodeError as e:
                        logger.warning(f"DeepSeek JSON error: {e}")
                        self.stats.errors.append(f"deepseek_json_error: {str(e)[:50]}")
                    except Exception as e:
                        logger.error(f"DeepSeek unexpected error: {e}")
                        self.stats.errors.append(f"deepseek_error: {str(e)[:50]}")
                    
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(2 ** attempt)
        
        self.stats.deepseek_failures += 1
        return "[Analysis failed after retries]"


# ============== REFERENCE EXTRACTOR ==============

class ReferenceExtractor:
    BUILTINS = {
        'print', 'len', 'str', 'int', 'float', 'list', 'dict', 'set',
        'tuple', 'bool', 'range', 'enumerate', 'zip', 'map', 'filter',
        'open', 'type', 'isinstance', 'hasattr', 'getattr', 'setattr',
        'super', 'property', 'staticmethod', 'classmethod', 'any', 'all',
        'min', 'max', 'sum', 'sorted', 'reversed', 'abs', 'round',
        'None', 'True', 'False', 'self', 'cls', 'id', 'repr', 'hash',
        'callable', 'iter', 'next', 'slice', 'object', 'Exception'
    }
    
    def extract(self, code: str) -> List[str]:
        import ast
        
        references = set()
        
        try:
            tree = ast.parse(code)
            
            class Visitor(ast.NodeVisitor):
                def visit_Call(self, node):
                    if isinstance(node.func, ast.Name):
                        references.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        chain = self._get_attr_chain(node.func)
                        if chain:
                            references.add(chain)
                    self.generic_visit(node)
                
                def visit_Attribute(self, node):
                    if isinstance(node.value, ast.Name) and node.value.id not in ('self', 'cls'):
                        references.add(f"{node.value.id}.{node.attr}")
                    self.generic_visit(node)
                
                def _get_attr_chain(self, node) -> Optional[str]:
                    parts = []
                    current = node
                    while isinstance(current, ast.Attribute):
                        parts.append(current.attr)
                        current = current.value
                    if isinstance(current, ast.Name):
                        parts.append(current.id)
                        parts.reverse()
                        return ".".join(parts[:2])
                    return None
            
            Visitor().visit(tree)
            
        except SyntaxError:
            patterns = [
                r'\b([A-Z][a-zA-Z0-9_]*)\s*\(',
                r'\b([a-z_][a-zA-Z0-9_]*)\s*\.\s*([a-zA-Z_][a-zA-Z0-9_]*)',
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, code):
                    references.add(match.group(0).rstrip('(').strip())
        
        return sorted(ref for ref in references if ref.split('.')[0] not in self.BUILTINS)


# ============== MAIN INDEXER ==============

class AsyncFileProcessor:
    """Обработчик файлов с поддержкой 25+ одновременных задач"""
    
    def __init__(self, max_workers: int = 25):
        self.max_workers = max_workers
        self._semaphore = asyncio.Semaphore(max_workers)
        
    async def process_files_batch(
        self,
        files: List[Path],
        process_func: Callable,
        progress_callback: Optional[Callable] = None
    ) -> List[Any]:
        """
        Обрабатывает пакет файлов асинхронно с ограничением параллелизма
        
        Args:
            files: Список путей к файлам
            process_func: Асинхронная функция обработки
            progress_callback: Callback для прогресса
        """
        tasks = []
        results = []
        
        for i, file_path in enumerate(files):
            task = asyncio.create_task(
                self._process_file_with_limit(
                    file_path, 
                    process_func,
                    index=i,
                    total=len(files)
                )
            )
            tasks.append(task)
        
        # Собираем результаты по мере готовности
        for future in asyncio.as_completed(tasks):
            try:
                result = await future
                if result:
                    results.append(result)
                    
                if progress_callback:
                    progress_callback(len(results), len(files), "Processing files")
                    
            except Exception as e:
                logger.error(f"Error processing file batch: {e}")
                
        return results
    
    async def _process_file_with_limit(
        self,
        file_path: Path,
        process_func: Callable,
        index: int,
        total: int
    ) -> Optional[Any]:
        """Обрабатывает файл с ограничением через семафор"""
        async with self._semaphore:
            try:
                return await process_func(file_path)
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")
                return None
    
    async def read_file_content(self, file_path: Path) -> str:
        """Асинхронное чтение файла"""
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                return await f.read()
        except Exception as e:
            logger.error(f"Cannot read {file_path}: {e}")
            raise


class ParallelChunkProcessor:
    """Параллельная обработка чанкирования в отдельных процессах"""
    
    def __init__(self, max_processes: int = None):
        self.max_processes = max_processes or multiprocessing.cpu_count()
        
    async def chunk_files_parallel(
        self,
        file_paths: List[Path],
        chunker: SmartPythonChunker
    ) -> Dict[Path, PythonTreeNode]:
        """
        Параллельное чанкирование файлов в процессах
        """
        # Используем ThreadPoolExecutor для I/O-bound операций
        with ThreadPoolExecutor(max_workers=self.max_processes) as executor:
            loop = asyncio.get_event_loop()
            
            # Подготовка задач
            tasks = []
            for file_path in file_paths:
                task = loop.run_in_executor(
                    executor,
                    self._chunk_single_file,
                    str(file_path),
                    chunker
                )
                tasks.append((file_path, task))
            
            # Ждем завершения всех задач
            results = {}
            for file_path, task in tasks:
                try:
                    tree = await task
                    results[file_path] = tree
                except Exception as e:
                    logger.error(f"Failed to chunk {file_path}: {e}")
                    
            return results
    
    def _chunk_single_file(
        self,
        file_path: str,
        chunker: SmartPythonChunker
    ) -> PythonTreeNode:
        """Синхронная функция для выполнения в потоке"""
        return chunker.chunk_file_to_tree(file_path)


class SemanticIndexer:
    """
    Incremental semantic indexer.
    
    ВАЖНО: Индексирует ЛЮБУЮ директорию, не только текущий проект.
    Пути в индексе - относительные от root_path (индексируемой директории).
    """
    
    def __init__(
        self, 
        root_path: str, 
        max_concurrent: int = 25,  # Увеличили до 25
        max_parallel_processes: int = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ):
        # root_path - директория которую индексируем (может быть любой!)
        self.root_path = Path(root_path).resolve()
        self.max_concurrent = max_concurrent
        self.progress_callback = progress_callback
        
        self.token_counter = TokenCounter()
        self.chunker = SmartPythonChunker(self.token_counter)
        self.hasher = ContentHasher()
        self.ai_client = AIClient()
        self.ai_client._semaphore = asyncio.Semaphore(max_concurrent)
        self.ref_extractor = ReferenceExtractor()
        
        self._existing_index: Optional[Dict] = None
        
        self.file_processor = AsyncFileProcessor(max_workers=max_concurrent)
        self.chunk_processor = ParallelChunkProcessor(
            max_processes=max_parallel_processes
        )
        self.max_concurrent = max_concurrent
        
        # Callbacks
        self.on_task_start: Optional[Callable] = None
        self.on_task_complete: Optional[Callable] = None
        self.on_task_error: Optional[Callable] = None
        self.on_file_start: Optional[Callable[[str], None]] = None
        self.on_file_complete: Optional[Callable[[str, bool], None]] = None
    
    def _report_progress(self, current: int, total: int, message: str):
        if self.progress_callback:
            self.progress_callback(current, total, message)
    
    async def index_files_batch(self, file_paths: List[Path], force: bool = False) -> List[Tuple[Path, Any]]:
        """
        Индексирует пакет файлов, используя SmartPythonChunker напрямую.
        """
        # Ограничиваем количество одновременных задач
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_file_wrapper(path: Path):
            async with semaphore:
                try:
                    # Уведомляем о начале обработки файла (для прогресс-бара)
                    # Если у вас есть механизм уведомления о начале задачи
                    if getattr(self, 'on_task_start', None):
                        self.on_task_start(f"Indexing {path.name}")
                    
                    # Основная работа по индексации файла
                    result = await self.index_file(path, force)
                    
                    # Уведомляем о завершении
                    if getattr(self, 'on_task_complete'):
                        self.on_task_complete(f"Indexing {path.name}", result is not None)
                        
                    return (path, result)
                except Exception as e:
                    logger.error(f"Error processing {path}: {e}")
                    if hasattr(self, 'on_task_error'):
                        self.on_task_error(f"Indexing {path.name}", str(e))
                    return (path, None)

        # Запускаем задачи параллельно
        tasks = [process_file_wrapper(p) for p in file_paths]
        results = await asyncio.gather(*tasks)
        
        # Возвращаем только успешные результаты
        return [r for r in results if r[1] is not None]
    
    def _collect_python_files(self) -> List[Path]:
        """Собирает Python файлы из root_path (индексируемой директории)"""
        files = []
        for path in self.root_path.rglob("*.py"):
            # Относительный путь от root_path
            try:
                rel_parts = path.relative_to(self.root_path).parts
            except ValueError:
                continue
            
            if any(part in IGNORE_DIRS or part.startswith('.') for part in rel_parts):
                continue
            files.append(path)
        return sorted(files)
    
    def _get_index_path(self) -> Path:
        """Путь к файлу индекса - в корне индексируемой директории"""
        ai_agent_dir = self.root_path / ".ai-agent"
        ai_agent_dir.mkdir(parents=True, exist_ok=True)
        return ai_agent_dir / INDEX_FILENAME
    
    def _get_compact_index_path(self) -> Path:
        """Путь к компактному индексу"""
        ai_agent_dir = self.root_path / ".ai-agent"
        ai_agent_dir.mkdir(parents=True, exist_ok=True)
        return ai_agent_dir / COMPACT_INDEX_FILENAME
    
    def _get_compact_md_path(self) -> Path:
        """Путь к MD версии индекса"""
        ai_agent_dir = self.root_path / ".ai-agent"
        ai_agent_dir.mkdir(parents=True, exist_ok=True)
        return ai_agent_dir / COMPACT_INDEX_MD
    
    # ============== [NEW] COMPRESSED INDEX METHODS ==============
    
    def _get_compressed_index_path(self) -> Path:
        """[NEW] Путь к сжатому индексу в .ai-agent"""
        ai_agent_dir = self.root_path / ".ai-agent"
        return ai_agent_dir / COMPRESSED_INDEX_FILENAME
    
    def _compressed_index_exists(self) -> bool:
        """[NEW] Проверяет, существует ли сжатый индекс"""
        return self._get_compressed_index_path().exists()
    
    def _load_compressed_index(self) -> Optional[Dict]:
        """[NEW] Загружает сжатый индекс если существует"""
        path = self._get_compressed_index_path()
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load compressed index: {e}")
            return None
    
    def _save_compressed_index(self, compressed: Dict) -> None:
        """[NEW] Сохраняет сжатый индекс"""
        path = self._get_compressed_index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with path.open("w", encoding="utf-8") as f:
            json.dump(compressed, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Compressed index saved: {path}")
    
    def _update_compressed_index(self, changes: Dict[str, List]) -> None:
        """
        [NEW] Инкрементально обновляет сжатый индекс на основе изменений.
        Вызывается только если сжатый индекс уже существует.
        
        Args:
            changes: словарь с ключами 'added', 'modified', 'deleted', 'moved'
        """
        if not self._compressed_index_exists():
            return  # Сжатого индекса нет — ничего не делаем
        
        compressed = self._load_compressed_index()
        if compressed is None:
            return
        
        logger.info("Updating compressed index...")
        
        # Полный индекс уже обновлён, берём из него актуальные данные
        full_index = self._load_existing_index()
        if full_index is None:
            return
        
        # Собираем множества путей для удаления и обновления
        deleted_files: Set[str] = set(changes.get('deleted', []))
        changed_files: Set[str] = set()
        
        # Добавляем пути из added (это Path объекты — конвертируем)
        for item in changes.get('added', []):
            if isinstance(item, Path):
                try:
                    rel_path = str(item.relative_to(self.root_path)).replace("\\", "/")
                    changed_files.add(rel_path)
                except ValueError:
                    pass
            else:
                changed_files.add(str(item))
        
        # Добавляем пути из modified (это тоже Path объекты)
        for item in changes.get('modified', []):
            if isinstance(item, Path):
                try:
                    rel_path = str(item.relative_to(self.root_path)).replace("\\", "/")
                    changed_files.add(rel_path)
                except ValueError:
                    pass
            else:
                changed_files.add(str(item))
        
        # Обрабатываем перемещённые файлы
        for move in changes.get('moved', []):
            if isinstance(move, dict):
                old_path = move.get('from', '')
                new_path = move.get('to', '')
                deleted_files.add(old_path)
                changed_files.add(new_path)
        
        # Удаляем записи для deleted файлов
        if deleted_files:
            compressed['files'] = [
                f for f in compressed.get('files', []) 
                if f.get('path') not in deleted_files
            ]
            compressed['classes'] = [
                c for c in compressed.get('classes', []) 
                if c.get('file') not in deleted_files
            ]
            compressed['functions'] = [
                f for f in compressed.get('functions', []) 
                if f.get('file') not in deleted_files
            ]
        
        # Удаляем старые записи для изменённых файлов (перед добавлением новых)
        if changed_files:
            compressed['files'] = [
                f for f in compressed.get('files', []) 
                if f.get('path') not in changed_files
            ]
            compressed['classes'] = [
                c for c in compressed.get('classes', []) 
                if c.get('file') not in changed_files
            ]
            compressed['functions'] = [
                f for f in compressed.get('functions', []) 
                if f.get('file') not in changed_files
            ]
        
        # Добавляем новые записи из полного индекса
        for file_path in changed_files:
            file_info = full_index.get('files', {}).get(file_path)
            if file_info is None:
                continue
            
            # Добавляем файл
            imports_info = file_info.get('imports', {})
            imports_list = []
            if imports_info:
                imports_list = imports_info.get('modules', []) if isinstance(imports_info, dict) else []
            
            compressed['files'].append({
                'path': file_path,
                'dir': str(Path(file_path).parent),
                'imports': ', '.join(imports_list[:20]),
                'hash': file_info.get('file_hash', ''),
            })
            
            # Добавляем классы
            for cls in file_info.get('classes', []):
                methods = cls.get('methods', [])
                compressed['classes'].append({
                    'name': cls.get('name', ''),
                    'file': file_path,
                    'lines': cls.get('lines', ''),
                    'methods': ', '.join(methods[:15]) if isinstance(methods, list) else methods,
                    'description': cls.get('description', ''),
                    'hash': cls.get('content_hash', ''),
                })
            
            # Добавляем функции
            for func in file_info.get('functions', []):
                compressed['functions'].append({
                    'name': func.get('name', ''),
                    'file': file_path,
                    'lines': func.get('lines', ''),
                    'description': func.get('description', ''),
                    'hash': func.get('content_hash', ''),
                })
        
        # Обновляем метаданные
        compressed['total_files'] = len(compressed['files'])
        compressed['updated_at'] = datetime.now(timezone.utc).isoformat() + 'Z'
        
        # Сохраняем
        self._save_compressed_index(compressed)
        logger.info(f"Compressed index updated: {len(compressed['files'])} files, "
                   f"{len(compressed.get('classes', []))} classes, "
                   f"{len(compressed.get('functions', []))} functions")
    
    # ============== END [NEW] COMPRESSED INDEX METHODS ==============
    
    def _load_existing_index(self) -> Optional[Dict]:
        index_path = self._get_index_path()
        if not index_path.exists():
            return None
        try:
            with index_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load existing index: {e}")
            return None
    
    def _get_existing_file_data(self, relative_path: str) -> Optional[Dict]:
        if self._existing_index is None:
            return None
        return self._existing_index.get("files", {}).get(relative_path)
    
    def _get_cached_class(self, file_data: Dict, class_name: str, content_hash: str) -> Optional[Dict]:
        if not file_data:
            return None
        for cls in file_data.get("classes", []):
            if cls.get("name") == class_name and cls.get("content_hash") == content_hash:
                return cls
        return None
    
    def _get_cached_function(self, file_data: Dict, func_name: str, content_hash: str) -> Optional[Dict]:
        if not file_data:
            return None
        for func in file_data.get("functions", []):
            if func.get("name") == func_name and func.get("content_hash") == content_hash:
                return func
        return None
    
    def _parse_imports(self, tree_node: PythonTreeNode) -> Tuple[Optional[ImportInfo], List[str]]:
        import ast
        
        imports_node = next(
            (c for c in tree_node.children if c.kind == "imports"), None
        )
        if not imports_node:
            return None, []
        
        modules = []
        try:
            tree = ast.parse(imports_node.content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    for alias in node.names:
                        modules.append(f"{base}.{alias.name}" if base else alias.name)
        except SyntaxError:
            pass
        
        return ImportInfo(
            lines=f"{imports_node.start_line}-{imports_node.end_line}",
            modules=modules,
            tokens=imports_node.tokens
        ), modules
    
    def _parse_globals(self, tree_node: PythonTreeNode) -> Optional[GlobalsInfo]:
        import ast
        
        globals_node = next(
            (c for c in tree_node.children if c.kind == "globals"), None
        )
        if not globals_node:
            return None
        
        names = []
        try:
            tree = ast.parse(globals_node.content)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            names.append(target.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.append(node.target.id)
        except SyntaxError:
            pass
        
        return GlobalsInfo(
            lines=f"{globals_node.start_line}-{globals_node.end_line}",
            names=names,
            tokens=globals_node.tokens
        )
    
    async def _analyze_class(
        self, 
        class_node: PythonTreeNode,
        imports: List[str],
        file_context: str,
        existing_file_data: Optional[Dict]
    ) -> ClassInfo:
        content_hash = self.hasher.hash_code_block(class_node.content)
        
        cached = self._get_cached_class(existing_file_data, class_node.name, content_hash)
        if cached:
            if self.on_task_complete:
                self.on_task_complete(f"class:{class_node.name}", True)
            return ClassInfo(
                name=cached["name"],
                lines=f"{class_node.start_line}-{class_node.end_line}",
                tokens=class_node.tokens,
                content_hash=content_hash,
                analyzed_by=cached["analyzed_by"],
                description=cached["description"],
                references=cached["references"],
                methods=cached["methods"]
            )
        
        if self.on_task_start:
            self.on_task_start(f"class:{class_node.name}")
        
        try:
            description, model = await self.ai_client.analyze_class(
                class_node.content, file_context, class_node.tokens
            )
            
            references = self.ref_extractor.extract(class_node.content)
            methods = [c.name for c in class_node.children if c.kind == "method"]
            
            if self.on_task_complete:
                self.on_task_complete(f"class:{class_node.name}", True)
            
            return ClassInfo(
                name=class_node.name,
                lines=f"{class_node.start_line}-{class_node.end_line}",
                tokens=class_node.tokens,
                content_hash=content_hash,
                analyzed_by=model,
                description=description,
                references=references,
                methods=methods
            )
        
        except Exception as e:
            logger.error(f"Failed to analyze class {class_node.name}: {e}")
            if self.on_task_error:
                self.on_task_error(f"class:{class_node.name}", str(e))
            
            return ClassInfo(
                name=class_node.name,
                lines=f"{class_node.start_line}-{class_node.end_line}",
                tokens=class_node.tokens,
                content_hash=content_hash,
                analyzed_by="error",
                description=f"[Analysis failed: {str(e)[:50]}]",
                references=[],
                methods=[c.name for c in class_node.children if c.kind == "method"]
            )
    
    async def _analyze_function(
        self,
        func_node: PythonTreeNode,
        imports: List[str],
        file_context: str,
        existing_file_data: Optional[Dict]
    ) -> FunctionInfo:
        content_hash = self.hasher.hash_code_block(func_node.content)
        
        cached = self._get_cached_function(existing_file_data, func_node.name, content_hash)
        if cached:
            if self.on_task_complete:
                self.on_task_complete(f"func:{func_node.name}", True)
            return FunctionInfo(
                name=cached["name"],
                lines=f"{func_node.start_line}-{func_node.end_line}",
                tokens=func_node.tokens,
                content_hash=content_hash,
                analyzed_by=cached["analyzed_by"],
                description=cached["description"],
                references=cached["references"]
            )
        
        if self.on_task_start:
            self.on_task_start(f"func:{func_node.name}")
        
        try:
            description, model = await self.ai_client.analyze_function(
                func_node.content, file_context, func_node.tokens
            )
            
            references = self.ref_extractor.extract(func_node.content)
            
            if self.on_task_complete:
                self.on_task_complete(f"func:{func_node.name}", True)
            
            return FunctionInfo(
                name=func_node.name,
                lines=f"{func_node.start_line}-{func_node.end_line}",
                tokens=func_node.tokens,
                content_hash=content_hash,
                analyzed_by=model,
                description=description,
                references=references
            )
        
        except Exception as e:
            logger.error(f"Failed to analyze function {func_node.name}: {e}")
            if self.on_task_error:
                self.on_task_error(f"func:{func_node.name}", str(e))
            
            return FunctionInfo(
                name=func_node.name,
                lines=f"{func_node.start_line}-{func_node.end_line}",
                tokens=func_node.tokens,
                content_hash=content_hash,
                analyzed_by="error",
                description=f"[Analysis failed: {str(e)[:50]}]",
                references=[]
            )
    
    async def index_file(self, file_path: Path, force: bool = False) -> Optional[FileIndex]:
        """
        Индексирует один файл.
        
        Args:
            file_path: Абсолютный путь к файлу
            force: Принудительная переиндексация
        """
        # Относительный путь от корня индексируемой директории
        try:
            relative_path = str(file_path.relative_to(self.root_path))
        except ValueError:
            logger.warning(f"File {file_path} is not under {self.root_path}")
            return None
        
        # Нормализуем путь (используем / для всех ОС)
        relative_path = relative_path.replace("\\", "/")
        
        try:
            code = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as e:
            logger.warning(f"Cannot read {relative_path}: {e}")
            return None
        
        file_hash = self.hasher.hash_file(file_path)
        
        existing_file_data = None if force else self._get_existing_file_data(relative_path)
        
        if existing_file_data and existing_file_data.get("file_hash") == file_hash:
            self.ai_client.stats.files_skipped += 1
            return self._dict_to_file_index(existing_file_data)
        
        lines = code.splitlines()
        tokens_total = self.token_counter.count(code)
        
        try:
            tree = self.chunker.chunk_file_to_tree(str(file_path))
        except Exception as e:
            logger.warning(f"Parse error {relative_path}: {e}")
            return None
        
        imports_info, import_modules = self._parse_imports(tree)
        globals_info = self._parse_globals(tree)
        
        file_context = f"{file_path.name} ({', '.join(import_modules[:5])})"
        
        classes = []
        class_descriptions = []
        
        for child in tree.children:
            if child.kind == "class":
                class_info = await self._analyze_class(
                    child, import_modules, file_context, existing_file_data
                )
                classes.append(class_info)
                class_descriptions.append(f"{class_info.name}: {class_info.description}")
        
        functions = []
        func_descriptions = []
        
        for child in tree.children:
            if child.kind == "function":
                func_info = await self._analyze_function(
                    child, import_modules, file_context, existing_file_data
                )
                functions.append(func_info)
                func_descriptions.append(f"{func_info.name}: {func_info.description}")
        
        all_descriptions = class_descriptions + func_descriptions
        file_description = await self.ai_client.summarize_file(all_descriptions, file_path.name)
        
        if existing_file_data:
            self.ai_client.stats.files_updated += 1
        else:
            self.ai_client.stats.files_added += 1
        
        return FileIndex(
            name=file_path.name,
            path=relative_path,  # Относительный путь
            full_path=str(file_path),  # Абсолютный путь
            file_hash=file_hash,
            tokens_total=tokens_total,
            lines_total=len(lines),
            imports=imports_info,
            globals=globals_info,
            description=file_description,
            classes=classes,
            functions=functions,
            last_indexed=datetime.now(timezone.utc).isoformat()
        )
    
    def _dict_to_file_index(self, data: Dict) -> FileIndex:
        return FileIndex(
            name=data["name"],
            path=data["path"],
            full_path=data["full_path"],
            file_hash=data["file_hash"],
            tokens_total=data["tokens_total"],
            lines_total=data["lines_total"],
            imports=ImportInfo(**data["imports"]) if data.get("imports") else None,
            globals=GlobalsInfo(**data["globals"]) if data.get("globals") else None,
            description=data["description"],
            classes=[ClassInfo(**c) for c in data.get("classes", [])],
            functions=[FunctionInfo(**f) for f in data.get("functions", [])],
            last_indexed=data.get("last_indexed", "")
        )
    
    def _generate_compact_index(self, full_index: Dict) -> Dict:
        compact_files = []
        
        for path, file_data in full_index.get("files", {}).items():
            compact_files.append({
                "name": file_data.get("name"),
                "path": path,
                "tokens": file_data.get("tokens_total", 0),
                "description": file_data.get("description", "")
            })
        
        compact_files.sort(key=lambda x: x["path"])
        
        return {
            "version": INDEX_VERSION,
            "updated_at": full_index.get("updated_at"),
            "root_path": full_index.get("root_path"),
            "total_files": len(compact_files),
            "total_tokens": full_index.get("total_tokens", 0),
            "files": compact_files
        }
    
    def _format_compact_for_context(self, compact_index: Dict) -> str:
        lines = [
            f"# Project Map ({compact_index['total_files']} files, {compact_index['total_tokens']:,} tokens)",
            f"# Root: {compact_index.get('root_path', 'N/A')}",
            ""
        ]
        
        by_dir: Dict[str, List[Dict]] = {}
        for f in compact_index["files"]:
            dir_path = str(Path(f["path"]).parent)
            if dir_path == ".":
                dir_path = "(root)"
            if dir_path not in by_dir:
                by_dir[dir_path] = []
            by_dir[dir_path].append(f)
        
        for dir_path in sorted(by_dir.keys()):
            files = by_dir[dir_path]
            lines.append(f"## {dir_path}/")
            
            for f in files:
                lines.append(f"- `{f['name']}` ({f['tokens']} tok): {f['description']}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    async def build_index_async(self, force: bool = False) -> Dict:
        """
        Полностью асинхронная версия build_index с отладкой
        """
        start_time = datetime.now(timezone.utc)
        
        if not force:
            self._existing_index = self._load_existing_index()
            if self._existing_index:
                logger.info(f"Loaded existing index ({len(self._existing_index.get('files', {}))} files)")
        
        # --- DEBUG START ---
        print(f"\n[DEBUG] Root path: {self.root_path}")
        print(f"[DEBUG] Checking files in {self.root_path}...")
        try:
             # Попробуем найти хоть что-то без фильтров
            raw_count = len(list(self.root_path.rglob("*.py")))
            print(f"[DEBUG] Total .py files found (raw rglob): {raw_count}")
        except Exception as e:
            print(f"[DEBUG] Error listing files: {e}")
        # --- DEBUG END ---

        python_files = self._collect_python_files()
        total_files = len(python_files)
        
        print(f"[DEBUG] Files after _collect_python_files filter: {total_files}")
        if total_files == 0 and raw_count > 0:
             print("[DEBUG] WARNING: All files were filtered out! Check IGNORE_DIRS or path logic.")

        # Берем настройки конкурентности
        concurrency = getattr(self, 'max_concurrent', 5)
        logger.info(f"Found {total_files} Python files, processing with {concurrency} concurrent tasks")
        
        # Разбиваем на батчи
        batch_size = concurrency * 2
        all_file_indices = []
        
        for i in range(0, total_files, batch_size):
            batch = python_files[i:i + batch_size]
            
            self._report_progress(
                min(i, total_files), 
                total_files,
                f"Processing batch {i//batch_size + 1}/{(total_files + batch_size - 1)//batch_size}"
            )
            
            # Обрабатываем батч
            batch_results = await self.index_files_batch(batch, force)
            all_file_indices.extend(batch_results)
        
        # --- НАЧАЛО ИСПРАВЛЕНИЯ: ЛОГИКА СБОРКИ (вместо _build_final_index) ---
        
        files_index: Dict[str, Dict] = {}
        total_tokens = 0
        current_files = set()
        
        # 1. Собираем результаты из списка в словарь
        for file_path, file_data in all_file_indices:
            if not file_data:
                continue
            
            try:
                relative_path = str(file_path.relative_to(self.root_path)).replace("\\", "/")
            except ValueError:
                 # Если вдруг путь не относительный (например, симлинк), сохраним как есть или пропустим
                 logger.warning(f"Could not determine relative path for {file_path}")
                 relative_path = file_path.name
            
            current_files.add(relative_path)
            
            # Конвертируем dataclass в dict
            if hasattr(file_data, 'tokens_total'):
                files_index[relative_path] = asdict(file_data)
                total_tokens += file_data.tokens_total
            else:
                files_index[relative_path] = file_data
                total_tokens += file_data.get('tokens_total', 0)
        
        # 2. Обрабатываем удаленные файлы
        if self._existing_index:
            existing_files = set(self._existing_index.get("files", {}).keys())
            deleted_files = existing_files - current_files
            
            if hasattr(self.ai_client.stats, 'files_removed'):
                self.ai_client.stats.files_removed = len(deleted_files)
                
            for deleted in deleted_files:
                logger.info(f"Removed from index: {deleted}")
        
        # 3. Финализируем статистику
        end_time = datetime.now(timezone.utc)
        if hasattr(self.ai_client.stats, 'indexing_duration_seconds'):
            self.ai_client.stats.indexing_duration_seconds = (end_time - start_time).total_seconds()
        if hasattr(self.ai_client.stats, 'files_indexed'):
            self.ai_client.stats.files_indexed = len(files_index)
        
        # 4. Формируем структуру полного индекса
        full_index = {
            "version": INDEX_VERSION,
            "created_at": (
                self._existing_index.get("created_at", start_time.isoformat()) 
                if self._existing_index else start_time.isoformat()
            ),
            "updated_at": end_time.isoformat(),
            "root_path": str(self.root_path),
            "total_files": len(files_index),
            "total_tokens": total_tokens,
            "files": files_index,
            "stats": asdict(self.ai_client.stats)
        }
        
        # 5. Сохраняем, используя существующие методы
        self._save_full_index(full_index)
        
        compact_index = self._generate_compact_index(full_index)
        self._save_compact_index(compact_index)
        
        return full_index
    
    def _save_full_index(self, index: Dict):
        output_path = self._get_index_path()
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"Full index saved: {output_path}")
    
    def _save_compact_index(self, compact_index: Dict):
        json_path = self._get_compact_index_path()
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(compact_index, f, ensure_ascii=False, indent=2)
        
        md_path = self._get_compact_md_path()
        md_content = self._format_compact_for_context(compact_index)
        with md_path.open("w", encoding="utf-8") as f:
            f.write(md_content)
        
        logger.info(f"Compact index saved: {json_path}, {md_path}")
    
    def get_compact_context(self) -> str:
        compact_path = self._get_compact_index_path()
        if not compact_path.exists():
            return "[Project index not found. Run indexing first.]"
        
        with compact_path.open("r", encoding="utf-8") as f:
            compact_index = json.load(f)
        
        return self._format_compact_for_context(compact_index)
    
    @property
    def stats(self) -> IndexStats:
        return self.ai_client.stats
    
    # ============================================================================
    # [NEW] INCREMENTAL UPDATE API - Добавленный функционал
    # ============================================================================
    
    def _recalculate_total_tokens(self):
        """[NEW] Пересчитывает общее количество токенов в индексе"""
        if self._existing_index is None:
            return
        total = sum(
            f.get("tokens_total", 0) 
            for f in self._existing_index.get("files", {}).values()
        )
        self._existing_index["total_tokens"] = total
    
    def _save_both_indexes(self):
        """[NEW] Сохраняет полный и компактный индекс"""
        if self._existing_index is None:
            logger.warning("Cannot save: no index loaded")
            return
            
        self._save_full_index(self._existing_index)
        compact_index = self._generate_compact_index(self._existing_index)
        self._save_compact_index(compact_index)
        
        logger.info(f"Saved both indexes to {self.root_path}")
    
    def _find_file_by_hash(
        self, 
        file_hash: str, 
        candidate_paths: Set[str],
        existing_files: Dict
    ) -> Optional[str]:
        """[NEW] Ищет файл с таким же хэшем среди кандидатов (для определения перемещений)"""
        for path in candidate_paths:
            if existing_files.get(path, {}).get("file_hash") == file_hash:
                return path
        return None
    
    def detect_changed_files(self) -> Dict[str, List]:
        """
        [NEW] Сканирует директорию и определяет изменения относительно существующего индекса.
        
        Returns:
            Dict с ключами:
                - 'added': List[Path] - новые файлы
                - 'modified': List[Path] - изменённые файлы (по хэшу)
                - 'deleted': List[str] - удалённые файлы (относительные пути)
                - 'moved': List[Dict] - перемещённые файлы {'from': str, 'to': str, 'path': Path}
        """
        # Загружаем индекс если ещё не загружен
        if self._existing_index is None:
            self._existing_index = self._load_existing_index()
        
        # Собираем текущие файлы
        current_files = self._collect_python_files()
        current_paths = {
            str(f.relative_to(self.root_path)).replace("\\", "/"): f 
            for f in current_files
        }
        
        # Получаем файлы из существующего индекса
        existing_files = self._existing_index.get("files", {}) if self._existing_index else {}
        existing_paths = set(existing_files.keys())
        current_path_set = set(current_paths.keys())
        
        result = {
            'added': [],
            'modified': [],
            'deleted': [],
            'moved': []
        }
        
        # Находим удалённые
        deleted_paths = existing_paths - current_path_set
        result['deleted'] = list(deleted_paths)
        
        # Находим добавленные и изменённые
        for rel_path, abs_path in current_paths.items():
            if rel_path not in existing_paths:
                # Проверяем, не перемещённый ли это файл (по хэшу)
                file_hash = self.hasher.hash_file(abs_path)
                moved_from = self._find_file_by_hash(file_hash, deleted_paths, existing_files)
                
                if moved_from:
                    result['moved'].append({
                        'from': moved_from,
                        'to': rel_path,
                        'path': abs_path
                    })
                    # Удаляем из deleted, так как это перемещение
                    if moved_from in result['deleted']:
                        result['deleted'].remove(moved_from)
                else:
                    result['added'].append(abs_path)
            else:
                # Проверяем изменился ли файл
                old_hash = existing_files[rel_path].get("file_hash")
                new_hash = self.hasher.hash_file(abs_path)
                
                if old_hash != new_hash:
                    result['modified'].append(abs_path)
        
        return result
    
    async def update_single_file(self, file_path: Path, force: bool = False) -> bool:
        """
        [NEW] Инкрементально обновляет индекс для одного файла.
        Автоматически сохраняет обе индексные карты.
        
        Args:
            file_path: Абсолютный или относительный путь к файлу
            force: Принудительная переиндексация даже если хэш не изменился
            
        Returns:
            bool: True если файл был обновлён, False если пропущен или ошибка
        """
        # Нормализуем путь
        file_path = Path(file_path).resolve()
        
        # Проверяем, что файл внутри root_path
        try:
            relative_path = str(file_path.relative_to(self.root_path)).replace("\\", "/")
        except ValueError:
            logger.error(f"File {file_path} is not under indexed directory {self.root_path}")
            return False
        
        # Загружаем существующий индекс если ещё не загружен
        if self._existing_index is None:
            self._existing_index = self._load_existing_index()
            if self._existing_index is None:
                self._existing_index = {
                    "version": INDEX_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "root_path": str(self.root_path),
                    "total_files": 0,
                    "total_tokens": 0,
                    "files": {},
                    "stats": asdict(IndexStats())
                }
        
        # Проверяем существование файла
        if not file_path.exists():
            # Файл удалён — удаляем из индекса
            if relative_path in self._existing_index.get("files", {}):
                del self._existing_index["files"][relative_path]
                self._existing_index["total_files"] = len(self._existing_index["files"])
                self._recalculate_total_tokens()
                self._existing_index["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save_both_indexes()
                logger.info(f"Removed deleted file from index: {relative_path}")
                return True
            return False
        
        # Индексируем файл
        file_index = await self.index_file(file_path, force=force)
        
        if file_index is None:
            return False
        
        # Проверяем, был ли файл реально обновлён (не из кэша)
        old_data = self._existing_index.get("files", {}).get(relative_path)
        new_data = asdict(file_index) if hasattr(file_index, '__dataclass_fields__') else file_index
        
        if old_data and old_data.get("file_hash") == new_data.get("file_hash") and not force:
            # Файл не изменился
            return False
        
        # Обновляем индекс
        self._existing_index["files"][relative_path] = new_data
        self._existing_index["total_files"] = len(self._existing_index["files"])
        self._recalculate_total_tokens()
        self._existing_index["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # Сохраняем обе карты
        self._save_both_indexes()
        
        logger.info(f"Updated index for: {relative_path}")
        return True
    
    async def update_files_batch(self, file_paths: List[Path], force: bool = False) -> Dict[str, bool]:
        """
        [NEW] Инкрементально обновляет индекс для списка файлов.
        Параллельная обработка с ограничением через семафор.

        Args:
            file_paths: Список путей к файлам
            force: Принудительная переиндексация
            
        Returns:
            Dict[str, bool]: Словарь {путь: успех_обновления}
        """
        results = {}
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_one(file_path: Path) -> Tuple[str, bool]:
            async with semaphore:
                try:
                    success = await self.update_single_file(file_path, force)
                    return (str(file_path), success)
                except Exception as e:
                    logger.error(f"Error updating {file_path}: {e}")
                    return (str(file_path), False)

        tasks = [process_one(fp) for fp in file_paths]
        task_results = await asyncio.gather(*tasks)

        for path, success in task_results:
            results[path] = success

        return results
    
    async def sync_index(self, force: bool = False) -> Dict[str, Any]:
        """
        [NEW] Синхронизирует индекс с текущим состоянием файловой системы.
        Обрабатывает добавленные, изменённые, удалённые и перемещённые файлы.
        
        Также обновляет сжатый индекс, если он существует.
        
        Args:
            force: Принудительная переиндексация всех файлов
            
        Returns:
            Статистика синхронизации: {'added': int, 'modified': int, 'deleted': int, 'moved': int, 'errors': list}
        """
        changes = self.detect_changed_files()
        
        stats = {
            'added': 0,
            'modified': 0,
            'deleted': 0,
            'moved': 0,
            'errors': []
        }
        
        # Загружаем индекс
        if self._existing_index is None:
            self._existing_index = self._load_existing_index() or {
                "version": INDEX_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "root_path": str(self.root_path),
                "total_files": 0,
                "total_tokens": 0,
                "files": {},
                "stats": asdict(IndexStats())
            }
        
        # Обрабатываем удалённые
        for deleted_path in changes['deleted']:
            if deleted_path in self._existing_index.get("files", {}):
                del self._existing_index["files"][deleted_path]
                stats['deleted'] += 1
                logger.info(f"Removed from index: {deleted_path}")
        
        # Обрабатываем перемещённые (сохраняем данные, меняем путь)
        for move_info in changes['moved']:
            old_path = move_info['from']
            new_path = move_info['to']
            
            if old_path in self._existing_index.get("files", {}):
                old_data = self._existing_index["files"].pop(old_path)
                old_data["path"] = new_path
                old_data["full_path"] = str(move_info['path'])
                old_data["name"] = move_info['path'].name
                self._existing_index["files"][new_path] = old_data
                stats['moved'] += 1
                logger.info(f"Moved in index: {old_path} -> {new_path}")
        
        # Обрабатываем добавленные и изменённые
        files_to_index = changes['added'] + changes['modified']
        
        if force:
            files_to_index = self._collect_python_files()
        
        total_to_process = len(files_to_index)
        
        if total_to_process == 0:
            # Только удаления/перемещения — сохраняем и выходим
            if stats['deleted'] > 0 or stats['moved'] > 0:
                self._existing_index["total_files"] = len(self._existing_index["files"])
                self._recalculate_total_tokens()
                self._existing_index["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save_both_indexes()
                
                # [NEW] Обновляем сжатый индекс, если он существует
                if self._compressed_index_exists():
                    self._update_compressed_index(changes)
            
            return stats
        
        # ============ ПАРАЛЛЕЛЬНАЯ ОБРАБОТКА С СЕМАФОРОМ ============
        semaphore = asyncio.Semaphore(self.max_concurrent)
        processed_count = 0
        results_lock = asyncio.Lock()
        
        async def process_file(file_path: Path) -> Optional[Tuple[str, Dict, bool]]:
            """Обрабатывает один файл с ограничением параллелизма"""
            nonlocal processed_count
            
            async with semaphore:
                try:
                    # Прогресс
                    async with results_lock:
                        processed_count += 1
                        current = processed_count
                    self._report_progress(current, total_to_process, f"Indexing {file_path.name}")
                    
                    # Индексируем через оригинальный index_file
                    file_index = await self.index_file(file_path, force=True)
                    
                    if file_index:
                        rel_path = str(file_path.relative_to(self.root_path)).replace("\\", "/")
                        new_data = asdict(file_index) if hasattr(file_index, '__dataclass_fields__') else file_index
                        is_new = rel_path not in self._existing_index.get("files", {})
                        return (rel_path, new_data, is_new)
                        
                except Exception as e:
                    async with results_lock:
                        stats['errors'].append(f"{file_path}: {str(e)}")
                    logger.error(f"Error indexing {file_path}: {e}")
                
                return None
        
        # Запускаем все задачи параллельно (семафор ограничит)
        tasks = [process_file(fp) for fp in files_to_index]
        results = await asyncio.gather(*tasks)
        
        # Собираем результаты
        for result in results:
            if result:
                rel_path, new_data, is_new = result
                self._existing_index["files"][rel_path] = new_data
                if is_new:
                    stats['added'] += 1
                else:
                    stats['modified'] += 1
                logger.info(f"Indexed: {rel_path}")
        
        # Обновляем метаданные
        self._existing_index["total_files"] = len(self._existing_index["files"])
        self._recalculate_total_tokens()
        self._existing_index["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # Сохраняем обе карты
        self._save_both_indexes()
        
        # [NEW] Обновляем сжатый индекс, если он существует
        if self._compressed_index_exists():
            self._update_compressed_index(changes)
        
        logger.info(f"Sync complete: +{stats['added']} ~{stats['modified']} -{stats['deleted']} moved:{stats['moved']}")
        
        return stats


# ============================================================================
# [NEW] FILE WATCHER - Автоматическое отслеживание изменений
# ============================================================================

class IndexFileWatcher:
    """
    [NEW] Наблюдатель за файловой системой для триггерного обновления индекса.
    Использует watchdog для отслеживания изменений.
    
    Требует установки: pip install watchdog
    """
    
    def __init__(
        self, 
        indexer: SemanticIndexer,
        debounce_seconds: float = 2.0,
        batch_delay_seconds: float = 5.0
    ):
        """
        Args:
            indexer: Экземпляр SemanticIndexer
            debounce_seconds: Задержка перед обработкой (для группировки быстрых изменений)
            batch_delay_seconds: Максимальная задержка для накопления батча изменений
        """
        self.indexer = indexer
        self.debounce_seconds = debounce_seconds
        self.batch_delay_seconds = batch_delay_seconds
        
        self._pending_changes: Dict[str, str] = {}  # path -> event_type
        self._last_change_time: float = 0
        self._observer = None
        self._running = False
        self._process_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    def start(self):
        """Запускает наблюдение за директорией"""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileSystemEvent
        except ImportError:
            logger.error("watchdog not installed. Run: pip install watchdog")
            raise ImportError("watchdog package required for file watching")
        
        watcher = self
        
        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent):
                if event.is_directory:
                    return
                
                # Фильтруем только Python файлы
                if not event.src_path.endswith('.py'):
                    return
                
                # Проверяем, не в игнорируемой ли директории
                path = Path(event.src_path)
                try:
                    rel_parts = path.relative_to(watcher.indexer.root_path).parts
                    if any(part in IGNORE_DIRS or part.startswith('.') for part in rel_parts):
                        return
                except ValueError:
                    return
                
                # Добавляем в очередь изменений
                asyncio.run_coroutine_threadsafe(
                    watcher._add_change(event.src_path, event.event_type),
                    asyncio.get_event_loop()
                )
        
        self._observer = Observer()
        self._observer.schedule(Handler(), str(self.indexer.root_path), recursive=True)
        self._observer.start()
        self._running = True
        
        # Запускаем обработчик изменений
        loop = asyncio.get_event_loop()
        self._process_task = loop.create_task(self._process_changes_loop())
        
        logger.info(f"Started watching: {self.indexer.root_path}")
    
    def stop(self):
        """Останавливает наблюдение"""
        self._running = False
        
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        
        if self._process_task:
            self._process_task.cancel()
            self._process_task = None
        
        logger.info("Stopped file watching")
    
    async def _add_change(self, path: str, event_type: str):
        """Добавляет изменение в очередь"""
        async with self._lock:
            self._pending_changes[path] = event_type
            self._last_change_time = asyncio.get_event_loop().time()
    
    async def _process_changes_loop(self):
        """Цикл обработки накопленных изменений"""
        while self._running:
            await asyncio.sleep(self.debounce_seconds)
            
            async with self._lock:
                if not self._pending_changes:
                    continue
                
                current_time = asyncio.get_event_loop().time()
                time_since_last = current_time - self._last_change_time
                
                # Ждём, пока изменения "устоятся" или превысим batch_delay
                if time_since_last < self.debounce_seconds:
                    if time_since_last < self.batch_delay_seconds:
                        continue
                
                # Забираем изменения
                changes = dict(self._pending_changes)
                self._pending_changes.clear()
            
            # Обрабатываем изменения
            await self._process_batch(changes)
    
    async def _process_batch(self, changes: Dict[str, str]):
        """Обрабатывает батч изменений"""
        logger.info(f"Processing {len(changes)} file changes...")
        
        deleted = []
        modified = []
        
        for path, event_type in changes.items():
            file_path = Path(path)
            
            if event_type == 'deleted' or not file_path.exists():
                deleted.append(path)
            else:
                modified.append(file_path)
        
        # Обрабатываем удаления
        for path in deleted:
            try:
                rel_path = str(Path(path).relative_to(self.indexer.root_path)).replace("\\", "/")
                if self.indexer._existing_index and rel_path in self.indexer._existing_index.get("files", {}):
                    del self.indexer._existing_index["files"][rel_path]
                    logger.info(f"Removed from index: {rel_path}")
            except Exception as e:
                logger.error(f"Error removing {path}: {e}")
        
        # Обрабатываем изменения/добавления
        for file_path in modified:
            try:
                await self.indexer.update_single_file(file_path)
            except Exception as e:
                logger.error(f"Error updating {file_path}: {e}")
        
        # Сохраняем если были удаления
        if deleted and self.indexer._existing_index:
            self.indexer._existing_index["total_files"] = len(self.indexer._existing_index["files"])
            self.indexer._recalculate_total_tokens()
            self.indexer._existing_index["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.indexer._save_both_indexes()
        
        logger.info(f"Processed: {len(modified)} updated, {len(deleted)} deleted")


# ============== CONVENIENCE FUNCTIONS ==============

def get_project_context(project_path: str) -> str:
    """Quick way to get compact project map for AI context."""
    indexer = SemanticIndexer(project_path)
    return indexer.get_compact_context()


def load_compact_index(project_path: str) -> Optional[Dict]:
    """Load compact index from specified directory."""
    compact_path = Path(project_path) / COMPACT_INDEX_FILENAME
    if not compact_path.exists():
        return None
    with compact_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def create_chunks_list_for_prefilter(index: Dict[str, Any]) -> str:
    """
    Creates JSON list of all chunks (classes/functions) for Pre-filter.
    
    Pre-filter uses this to select relevant code chunks based on:
    - Chunk names and types
    - AI-generated descriptions (semantic matching)
    - Method lists for classes
    
    Args:
        index: Full semantic index (from semantic_index_builder)
        
    Returns:
        JSON string with list of chunks
    """
    chunks = []
    
    for file_path, file_info in index.get("files", {}).items():
        # Process classes
        for cls in file_info.get("classes", []):
            chunks.append({
                "chunk_id": f"{file_path}:{cls['name']}",
                "file": file_path,
                "type": "class",
                "name": cls["name"],
                "lines": cls.get("lines", ""),
                "tokens": cls.get("tokens", 0),
                "description": cls.get("description", ""),
                "methods": cls.get("methods", []),
            })
        
        # Process standalone functions
        for func in file_info.get("functions", []):
            chunks.append({
                "chunk_id": f"{file_path}:{func['name']}",
                "file": file_path,
                "type": "function",
                "name": func["name"],
                "lines": func.get("lines", ""),
                "tokens": func.get("tokens", 0),
                "description": func.get("description", ""),
            })
    
    # Sort by file path and name for consistent output
    chunks.sort(key=lambda x: (x["file"], x["name"]))
    
    return json.dumps(chunks, indent=2, ensure_ascii=False)


def count_total_chunks(index: Dict[str, Any]) -> int:
    """Count total number of chunks (classes + functions) in index."""
    total = 0
    for file_data in index.get("files", {}).values():
        total += len(file_data.get("classes", []))
        total += len(file_data.get("functions", []))
    return total

def create_chunks_list_for_prefilter_compressed(index: Dict[str, Any]) -> str:
    """
    Creates JSON list of all chunks for Pre-filter from COMPRESSED index.
    
    Compressed index has flat structure:
    - index["classes"]: list of class dicts
    - index["functions"]: list of function dicts
    
    Args:
        index: Compressed semantic index
        
    Returns:
        JSON string with list of chunks
    """
    chunks = []
    
    # Process classes from flat list
    for cls in index.get("classes", []):
        # Parse methods from comma-separated string
        methods_str = cls.get("methods", "")
        methods = [m.strip() for m in methods_str.split(",") if m.strip()]
        
        chunks.append({
            "chunk_id": f"{cls['file']}:{cls['name']}",
            "file": cls.get("file", ""),
            "type": "class",
            "name": cls.get("name", ""),
            "lines": cls.get("lines", ""),
            "tokens": 0,  # Не храним в сжатом индексе
            "description": cls.get("description", ""),
            "methods": methods,
        })
    
    # Process functions from flat list
    for func in index.get("functions", []):
        chunks.append({
            "chunk_id": f"{func['file']}:{func['name']}",
            "file": func.get("file", ""),
            "type": "function",
            "name": func.get("name", ""),
            "lines": func.get("lines", ""),
            "tokens": 0,
            "description": func.get("description", ""),
        })
    
    # Sort by file path and name
    chunks.sort(key=lambda x: (x["file"], x["name"]))
    
    return json.dumps(chunks, indent=2, ensure_ascii=False)


def create_chunks_list_auto(index: Dict[str, Any]) -> str:
    """
    Automatically selects correct function based on index format.
    
    Args:
        index: Semantic index (regular or compressed)
        
    Returns:
        JSON string with list of chunks
    """
    if index.get("compressed", False):
        return create_chunks_list_for_prefilter_compressed(index)
    return create_chunks_list_for_prefilter(index)


def count_total_chunks_compressed(index: Dict[str, Any]) -> int:
    """Count total chunks in compressed index."""
    return len(index.get("classes", [])) + len(index.get("functions", []))


def count_total_chunks_auto(index: Dict[str, Any]) -> int:
    """Automatically count chunks based on index format."""
    if index.get("compressed", False):
        return count_total_chunks_compressed(index)
    return count_total_chunks(index)


# ============== CLI ==============

async def main():
    import sys
    import argparse
    import multiprocessing
    import logging
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='Semantic Index Builder')
    parser.add_argument('target_path', help='Path to index')
    parser.add_argument('--force', '-f', action='store_true', help='Force re-index')
    parser.add_argument('--concurrent', '-c', type=int, default=25, 
                        help='Max concurrent tasks (default: 25)')
    parser.add_argument('--processes', '-p', type=int, 
                        default=multiprocessing.cpu_count(),
                        help='Max parallel processes for chunking')
    parser.add_argument('--batch-size', '-b', type=int, default=50,
                        help='Batch size for processing')
    
    # [NEW] Дополнительные режимы работы
    parser.add_argument('--sync', '-s', action='store_true',
                        help='Sync mode: only process changed files')
    parser.add_argument('--watch', '-w', action='store_true',
                        help='Watch mode: monitor for changes and update automatically')
    parser.add_argument('--update-file', '-u', type=str,
                        help='Update single file in index')
    parser.add_argument('--detect-changes', '-d', action='store_true',
                        help='Only detect changes without updating')
    
    args = parser.parse_args()
    
    indexer = SemanticIndexer(
        args.target_path,
        max_concurrent=args.concurrent,
        max_parallel_processes=args.processes
    )
    
    def progress(current, total, msg):
        print(f"  [{current}/{total}] {msg}")
    
    indexer.progress_callback = progress
    
    # [NEW] Режим: только обнаружение изменений
    if args.detect_changes:
        print(f"\n🔍 Detecting changes in {args.target_path}...")
        changes = indexer.detect_changed_files()
        
        print(f"\n📊 Changes detected:")
        print(f"   ➕ Added: {len(changes['added'])} files")
        print(f"   📝 Modified: {len(changes['modified'])} files")
        print(f"   🗑️ Deleted: {len(changes['deleted'])} files")
        print(f"   📦 Moved: {len(changes['moved'])} files")
        
        if changes['added']:
            print(f"\n   New files:")
            for f in changes['added'][:10]:
                print(f"      + {f.name}")
            if len(changes['added']) > 10:
                print(f"      ... and {len(changes['added']) - 10} more")
        
        if changes['modified']:
            print(f"\n   Modified files:")
            for f in changes['modified'][:10]:
                print(f"      ~ {f.name}")
            if len(changes['modified']) > 10:
                print(f"      ... and {len(changes['modified']) - 10} more")
        
        if changes['moved']:
            print(f"\n   Moved files:")
            for m in changes['moved'][:5]:
                print(f"      {m['from']} -> {m['to']}")
        
        return
    
    # [NEW] Режим: обновление одного файла
    if args.update_file:
        file_path = Path(args.update_file)
        if not file_path.is_absolute():
            file_path = Path(args.target_path) / file_path
        
        print(f"\n📄 Updating single file: {file_path}")
        success = await indexer.update_single_file(file_path, force=args.force)
        
        if success:
            print(f"✅ File updated successfully")
        else:
            print(f"⏭️ File skipped (unchanged or error)")
        return
    
    # [NEW] Режим: наблюдение за изменениями
    if args.watch:
        print(f"\n👁️ Starting watch mode for {args.target_path}")
        print("   Press Ctrl+C to stop\n")
        
        try:
            watcher = IndexFileWatcher(indexer)
            watcher.start()
            
            # Бесконечный цикл
            while True:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Stopping watch mode...")
            watcher.stop()
        except ImportError as e:
            print(f"\n❌ {e}")
            print("   Install watchdog: pip install watchdog")
        return
    
    # [NEW] Режим: синхронизация (инкрементальное обновление)
    if args.sync:
        print(f"\n🔄 Syncing index for {args.target_path}...")
        sync_stats = await indexer.sync_index(force=args.force)
        
        print(f"\n{'='*50}")
        print(f"✅ Sync complete")
        print(f"   ➕ Added: {sync_stats['added']}")
        print(f"   📝 Modified: {sync_stats['modified']}")
        print(f"   🗑️ Deleted: {sync_stats['deleted']}")
        print(f"   📦 Moved: {sync_stats['moved']}")
        if sync_stats['errors']:
            print(f"   ⚠️ Errors: {len(sync_stats['errors'])}")
        print(f"{'='*50}")
        return
    
    # Режим по умолчанию: полная индексация
    await indexer.build_index_async(force=args.force)
    
    stats = indexer.stats
    print(f"\n{'='*50}")
    print(f"✅ Indexing complete in {stats.indexing_duration_seconds:.1f}s")
    print(f"   📄 Files: {stats.files_indexed}")
    print(f"   ➕ Added: {stats.files_added}")
    print(f"   🔄 Updated: {stats.files_updated}")
    print(f"   ⏭️ Skipped: {stats.files_skipped}")
    print(f"   🗑️ Removed: {stats.files_removed}")
    print(f"   🔵 Qwen: {stats.qwen_successes}/{stats.qwen_calls}")
    print(f"   🟢 DeepSeek: {stats.deepseek_successes}/{stats.deepseek_calls}")
    print(f"   🔄 Fallbacks: {stats.fallback_to_deepseek}")
    print(f"   🔧 Parse recoveries: {stats.parse_recoveries}")
    if stats.errors:
        print(f"   ⚠️ Errors: {len(stats.errors)}")
    print(f"{'='*50}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())