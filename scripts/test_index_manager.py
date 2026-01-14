#!/usr/bin/env python3
# scripts/test_index_manager.py
"""
Тестовый скрипт для проверки работы index_manager.py

Проверяет:
1. Полную индексацию (semantic_index + project_map)
2. Инкрементальное обновление
3. Сжатие индекса при превышении лимита токенов
4. Корректность импортов и API подключений

Режимы:
1. Полная индексация с нуля
2. Инкрементальное обновление (sync)
3. Только проверка импортов и API
4. Статистика существующих индексов
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

# Добавляем корень проекта в путь для импортов
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============== ЦВЕТА ==============

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'


def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text:^60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_error(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def print_info(text: str):
    print(f"{Colors.CYAN}ℹ {text}{Colors.END}")


# ============== ЛОГГЕР ==============

class TestLogger:
    """Логгер для детального отслеживания процесса"""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.log_entries: List[Dict] = []
        self.start_time = None
        self.errors: List[str] = []
    
    def start(self, message: str):
        self.start_time = time.time()
        self._log("START", message, Colors.CYAN)
    
    def step(self, message: str):
        self._log("STEP", message, Colors.BLUE)
    
    def found(self, message: str):
        self._log("FOUND", message, Colors.GREEN)
    
    def warning(self, message: str):
        self._log("WARN", message, Colors.YELLOW)
    
    def error(self, message: str):
        self._log("ERROR", message, Colors.RED)
        self.errors.append(message)
    
    def detail(self, message: str):
        if self.verbose:
            self._log("DETAIL", message, Colors.DIM)
    
    def success(self, message: str):
        self._log("OK", message, Colors.GREEN)
    
    def finish(self, message: str):
        elapsed = time.time() - self.start_time if self.start_time else 0
        self._log("DONE", f"{message} ({elapsed:.2f}s)", Colors.CYAN)
    
    def _log(self, level: str, message: str, color: str):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = {
            "timestamp": timestamp,
            "level": level,
            "message": message
        }
        self.log_entries.append(entry)
        
        if self.verbose or level in ("ERROR", "WARN", "OK", "START", "DONE"):
            print(f"{Colors.DIM}[{timestamp}]{Colors.END} {color}[{level:6}]{Colors.END} {message}")
    
    def save_log(self, path: Path):
        """Сохраняет лог в файл"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write("LOG INDEX MANAGER TEST\n")
                f.write("=" * 60 + "\n\n")
                for entry in self.log_entries:
                    f.write(f"[{entry['timestamp']}] [{entry['level']:6}] {entry['message']}\n")
            return True
        except Exception as e:
            print_error(f"Не удалось сохранить лог: {e}")
            return False


# ============== ПРОГРЕСС-ТРЕКЕР ==============

class ProgressTracker:
    """Трекер прогресса для визуализации"""
    
    def __init__(self):
        self.total = 0
        self.current = 0
        self.message = ""
        self.last_update = 0
    
    def update(self, message: str, current: int, total: int):
        self.message = message
        self.current = current
        self.total = total
        self._print_status()
    
    def _print_status(self):
        now = time.time()
        if now - self.last_update < 0.1 and self.current < self.total:
            return
        self.last_update = now
        
        if self.total > 0:
            pct = (self.current / self.total * 100)
            bar_filled = int(pct / 5)
            bar_empty = 20 - bar_filled
            
            status = (
                f"\r{Colors.CYAN}[{self.current}/{self.total}]{Colors.END} "
                f"[{'█' * bar_filled}{'░' * bar_empty}] {pct:.0f}% "
                f"{Colors.DIM}{self.message[:40]}{Colors.END}   "
            )
        else:
            status = f"\r{Colors.CYAN}[...]{Colors.END} {self.message[:50]}   "
        
        print(status, end='', flush=True)
    
    def finish(self):
        print()


# ============== ИГНОРИРУЕМЫЕ ДИРЕКТОРИИ ==============

IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache",
    ".pytest_cache", ".tox", "eggs", "site-packages", ".ai-agent"
}


# ============== ПРОВЕРКА ИМПОРТОВ ==============

def check_imports(logger: TestLogger) -> bool:
    """Проверяет все необходимые импорты"""
    
    logger.start("Проверка импортов...")
    
    imports_to_check = [
        # Core
        ("config.settings", "cfg", "Конфигурация"),
        ("app.utils.token_counter", "TokenCounter", "Счётчик токенов"),
        ("app.utils.file_types", "FileTypeDetector", "Детектор типов файлов"),
        
        # LLM
        ("app.llm.api_client", "call_llm", "API клиент LLM"),
        
        # Builders - ИСПРАВЛЕННЫЙ ПУТЬ
        ("app.builders.semantic_index_builder", "SemanticIndexer", "Semantic Index Builder"),
        
        # Services
        ("app.services.project_map_builder", "ProjectMapBuilder", "Project Map Builder"),
        ("app.services.python_chunker", "SmartPythonChunker", "Python Chunker"),
        
        # Index Manager
        ("app.services.index_manager", "FullIndexBuilder", "Index Manager (FullIndexBuilder)"),
        ("app.services.index_manager", "IncrementalIndexUpdater", "Index Manager (IncrementalIndexUpdater)"),
        ("app.services.index_manager", "IndexCompressor", "Index Manager (IndexCompressor)"),
        ("app.services.index_manager", "IndexStats", "Index Manager (IndexStats)"),
        ("app.services.index_manager", "SyncStats", "Index Manager (SyncStats)"),
    ]
    
    all_success = True
    success_count = 0
    
    for module_path, class_name, description in imports_to_check:
        try:
            module = __import__(module_path, fromlist=[class_name])
            obj = getattr(module, class_name)
            logger.success(f"{description}: {class_name}")
            success_count += 1
        except ImportError as e:
            logger.error(f"{description}: ImportError - {e}")
            all_success = False
        except AttributeError as e:
            logger.error(f"{description}: AttributeError - {e}")
            all_success = False
        except Exception as e:
            logger.error(f"{description}: {type(e).__name__} - {e}")
            all_success = False
    
    logger.finish(f"Импорты: {success_count}/{len(imports_to_check)} успешно")
    
    return all_success


# ============== ПРОВЕРКА API ==============

def check_api_qwen() -> Tuple[bool, str]:
    """Проверка Qwen API через OpenRouter"""
    try:
        from config.settings import cfg
        import httpx
        
        if not cfg.OPENROUTER_API_KEY:
            return False, "OPENROUTER_API_KEY не установлен"
        
        base = cfg.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1"
        model = cfg.MODEL_QWEN or "qwen/qwen-2.5-coder-32b-instruct"
        
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 10,
                }
            )
        
        if resp.status_code == 200:
            return True, f"OK (model: {model})"
        return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        return False, str(e)[:100]


def check_api_deepseek() -> Tuple[bool, str]:
    """Проверка DeepSeek API"""
    try:
        from config.settings import cfg
        import httpx
        
        if not cfg.DEEPSEEK_API_KEY:
            return False, "DEEPSEEK_API_KEY не установлен"
        
        base = cfg.DEEPSEEK_BASE_URL or "https://api.deepseek.com"
        
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 10,
                }
            )
        
        if resp.status_code == 200:
            return True, "OK"
        return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        return False, str(e)[:100]


def check_apis(logger: TestLogger) -> Dict[str, bool]:
    """Проверяет все API подключения"""
    
    logger.start("Проверка API подключений...")
    
    results = {}
    
    # Qwen
    logger.step("Проверка Qwen API (OpenRouter)...")
    qwen_ok, qwen_msg = check_api_qwen()
    results["qwen"] = qwen_ok
    if qwen_ok:
        logger.success(f"Qwen: {qwen_msg}")
    else:
        logger.error(f"Qwen: {qwen_msg}")
    
    # DeepSeek
    logger.step("Проверка DeepSeek API...")
    ds_ok, ds_msg = check_api_deepseek()
    results["deepseek"] = ds_ok
    if ds_ok:
        logger.success(f"DeepSeek: {ds_msg}")
    else:
        logger.warning(f"DeepSeek: {ds_msg}")
    
    logger.finish("Проверка API завершена")
    
    return results


# ============== УТИЛИТЫ ==============

def list_python_files(target: Path) -> List[Path]:
    """Список Python файлов с учётом игнорируемых директорий"""
    py_files = []
    for f in target.rglob("*.py"):
        try:
            rel_parts = f.relative_to(target).parts
        except ValueError:
            continue
        if any(part in IGNORE_DIRS or part.startswith('.') for part in rel_parts):
            continue
        py_files.append(f)
    return py_files


def count_all_files(target: Path) -> int:
    """Считает все файлы (не только Python)"""
    count = 0
    for f in target.rglob("*"):
        if f.is_file():
            try:
                rel_parts = f.relative_to(target).parts
            except ValueError:
                continue
            if any(part in IGNORE_DIRS or part.startswith('.') for part in rel_parts):
                continue
            count += 1
    return count


# ============== ВЫБОР ДИРЕКТОРИИ ==============

def select_directory() -> Optional[Path]:
    """Интерактивный выбор директории для индексации"""
    
    print_header("ВЫБОР ДИРЕКТОРИИ")
    
    cwd = Path.cwd()
    
    print(f"  {Colors.CYAN}1{Colors.END} - Текущая директория")
    print(f"      {Colors.DIM}{cwd}{Colors.END}")
    print()
    print(f"  {Colors.CYAN}2{Colors.END} - Директория проекта AI_Assistant_Pro")
    print(f"      {Colors.DIM}{PROJECT_ROOT}{Colors.END}")
    print()
    print(f"  {Colors.CYAN}3{Colors.END} - Ввести путь вручную")
    print()
    print(f"  {Colors.CYAN}q{Colors.END} - Выход")
    
    choice = input(f"\n{Colors.YELLOW}Выбор: {Colors.END}").strip().lower()
    
    if choice == "q":
        return None
    elif choice == "1":
        target = cwd
    elif choice == "2":
        target = PROJECT_ROOT
    elif choice == "3":
        custom = input(f"{Colors.YELLOW}Путь: {Colors.END}").strip().strip('"').strip("'")
        target = Path(custom).resolve()
    else:
        print_error("Неверный выбор")
        return None
    
    if not target.exists():
        print_error(f"Путь не существует: {target}")
        return None
    
    if not target.is_dir():
        print_error(f"Это не директория: {target}")
        return None
    
    # Статистика
    py_files = list_python_files(target)
    all_files = count_all_files(target)
    
    print()
    print_success(f"Директория: {target}")
    print_info(f"Python файлов: {len(py_files)}")
    print_info(f"Всего файлов: {all_files}")
    
    if len(py_files) == 0:
        print_warning("В директории нет Python файлов для semantic index")
    
    return target


# ============== ВЫБОР РЕЖИМА ==============

def select_mode() -> str:
    """Интерактивный выбор режима работы"""
    
    print()
    print(f"{Colors.BOLD}Режим работы:{Colors.END}")
    print()
    print(f"  {Colors.CYAN}1{Colors.END} - {Colors.GREEN}Полная индексация{Colors.END}")
    print(f"      {Colors.DIM}Создаёт semantic_index + project_map с нуля{Colors.END}")
    print()
    print(f"  {Colors.CYAN}2{Colors.END} - {Colors.GREEN}Инкрементальное обновление{Colors.END} (sync)")
    print(f"      {Colors.DIM}Обновляет только изменённые файлы{Colors.END}")
    print()
    print(f"  {Colors.CYAN}3{Colors.END} - {Colors.YELLOW}Только проверка{Colors.END}")
    print(f"      {Colors.DIM}Проверяет импорты и API без индексации{Colors.END}")
    print()
    print(f"  {Colors.CYAN}4{Colors.END} - {Colors.CYAN}Статистика индексов{Colors.END}")
    print(f"      {Colors.DIM}Показывает информацию о существующих индексах{Colors.END}")
    
    choice = input(f"\n{Colors.YELLOW}Выбор [1]: {Colors.END}").strip()
    
    if choice == "2":
        return "sync"
    elif choice == "3":
        return "check"
    elif choice == "4":
        return "stats"
    return "full"


# ============== ПОЛНАЯ ИНДЕКСАЦИЯ ==============

async def run_full_indexing(target: Path, logger: TestLogger) -> Dict[str, Any]:
    """Запускает полную индексацию через IndexManager"""
    
    logger.start(f"Полная индексация: {target}")
    
    results = {
        "success": False,
        "stats": None,
        "errors": [],
        "duration": 0,
        "files_created": [],
    }
    
    start_time = time.time()
    tracker = ProgressTracker()
    
    try:
        from app.services.index_manager import FullIndexBuilder
        
        builder = FullIndexBuilder(str(target))
        
        # Progress callback
        def on_progress(message: str, current: int, total: int):
            tracker.update(message, current, total)
            logger.detail(f"[{current}/{total}] {message}")
        
        logger.step("Запуск FullIndexBuilder.build()...")
        
        stats = await builder.build(on_progress=on_progress)
        
        tracker.finish()
        
        results["success"] = True
        results["stats"] = stats.to_dict()
        
        logger.success("Индексация завершена успешно")
        
    except Exception as e:
        tracker.finish()
        logger.error(f"Ошибка: {type(e).__name__}: {e}")
        results["errors"].append(str(e))
        
        import traceback
        logger.detail(traceback.format_exc())
    
    results["duration"] = time.time() - start_time
    
    # Проверяем созданные файлы
    ai_agent_dir = target / ".ai-agent"
    expected_files = [
        "semantic_index.json",
        "semantic_index_compressed.json",
        "compact_index.json",
        "compact_index.md",
        "project_map.json",
        "project_map.md",
    ]
    
    for filename in expected_files:
        file_path = ai_agent_dir / filename
        if file_path.exists():
            results["files_created"].append(filename)
            logger.found(f"Создан: {filename}")
    
    logger.finish(f"Индексация завершена за {results['duration']:.1f}с")
    
    return results


# ============== ИНКРЕМЕНТАЛЬНОЕ ОБНОВЛЕНИЕ ==============

async def run_incremental_sync(target: Path, logger: TestLogger) -> Dict[str, Any]:
    """Запускает инкрементальное обновление"""
    
    logger.start(f"Инкрементальное обновление: {target}")
    
    results = {
        "success": False,
        "stats": None,
        "errors": [],
        "duration": 0,
    }
    
    start_time = time.time()
    tracker = ProgressTracker()
    
    try:
        from app.services.index_manager import IncrementalIndexUpdater
        
        updater = IncrementalIndexUpdater(str(target))
        
        # Progress callback
        def on_progress(message: str, current: int, total: int):
            tracker.update(message, current, total)
            logger.detail(f"[{current}/{total}] {message}")
        
        logger.step("Запуск IncrementalIndexUpdater.sync()...")
        
        stats = await updater.sync(on_progress=on_progress)
        
        tracker.finish()
        
        results["success"] = True
        results["stats"] = stats.to_dict()
        
        logger.success("Синхронизация завершена успешно")
        
    except Exception as e:
        tracker.finish()
        logger.error(f"Ошибка: {type(e).__name__}: {e}")
        results["errors"].append(str(e))
        
        import traceback
        logger.detail(traceback.format_exc())
    
    results["duration"] = time.time() - start_time
    
    logger.finish(f"Синхронизация завершена за {results['duration']:.1f}с")
    
    return results


# ============== СТАТИСТИКА ИНДЕКСОВ ==============

def show_index_stats(target: Path, logger: TestLogger):
    """Показывает подробную статистику существующих индексов"""
    
    logger.start(f"Статистика индексов: {target}")
    
    ai_agent_dir = target / ".ai-agent"
    
    if not ai_agent_dir.exists():
        logger.warning("Директория .ai-agent не найдена")
        print_warning("Индексы не созданы. Запустите полную индексацию (режим 1)")
        return
    
    try:
        from app.utils.token_counter import TokenCounter
        token_counter = TokenCounter()
    except ImportError:
        logger.error("Не удалось импортировать TokenCounter")
        return
    
    print()
    print(f"{Colors.BOLD}📊 СТАТИСТИКА ИНДЕКСОВ{Colors.END}")
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
    
    # Файлы для проверки
    files_to_check = [
        ("semantic_index.json", "Semantic Index (полный)", True),
        ("semantic_index_compressed.json", "Semantic Index (сжатый)", True),
        ("compact_index.json", "Compact Index", True),
        ("compact_index.md", "Compact Index (MD)", False),
        ("project_map.json", "Project Map", True),
        ("project_map.md", "Project Map (MD)", False),
    ]
    
    total_tokens = 0
    
    for filename, description, is_json in files_to_check:
        file_path = ai_agent_dir / filename
        
        if not file_path.exists():
            print(f"  {Colors.DIM}⚪ {description}: не существует{Colors.END}")
            continue
        
        try:
            content = file_path.read_text(encoding="utf-8")
            tokens = token_counter.count(content)
            size_kb = file_path.stat().st_size / 1024
            
            total_tokens += tokens
            
            # Дополнительная информация для JSON
            extra_info = ""
            if is_json:
                try:
                    data = json.loads(content)
                    
                    if "files" in data:
                        if isinstance(data["files"], dict):
                            extra_info = f"{len(data['files'])} файлов"
                        elif isinstance(data["files"], list):
                            extra_info = f"{len(data['files'])} файлов"
                    
                    if "classes" in data:
                        extra_info += f", {len(data['classes'])} классов"
                    
                    if "functions" in data:
                        extra_info += f", {len(data['functions'])} функций"
                    
                    if data.get("compressed"):
                        extra_info += f" {Colors.YELLOW}[СЖАТ]{Colors.END}"
                    
                    if data.get("errors"):
                        extra_info += f" {Colors.RED}[{len(data['errors'])} ошибок]{Colors.END}"
                        
                except json.JSONDecodeError:
                    extra_info = f"{Colors.RED}[JSON ошибка]{Colors.END}"
            
            print(f"  {Colors.GREEN}✅ {description}{Colors.END}")
            print(f"     {Colors.CYAN}{tokens:,} токенов{Colors.END} | {size_kb:.1f} KB")
            if extra_info:
                print(f"     {extra_info}")
            
            logger.found(f"{filename}: {tokens:,} токенов, {size_kb:.1f} KB")
            
        except Exception as e:
            print(f"  {Colors.RED}❌ {description}: ошибка - {e}{Colors.END}")
            logger.error(f"Ошибка чтения {filename}: {e}")
    
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
    print(f"  {Colors.BOLD}📊 ВСЕГО ТОКЕНОВ: {total_tokens:,}{Colors.END}")
    
    # Проверка лимита
    LIMIT = 60000
    if total_tokens > LIMIT:
        print(f"  {Colors.RED}⚠️ Превышен лимит {LIMIT:,} токенов!{Colors.END}")
    else:
        pct = (total_tokens / LIMIT) * 100
        print(f"  {Colors.GREEN}✅ В пределах лимита ({pct:.0f}% от {LIMIT:,}){Colors.END}")
    
    print()
    
    logger.finish("Статистика собрана")


# ============== ВЫВОД РЕЗУЛЬТАТОВ ==============

def print_full_results(results: Dict[str, Any]):
    """Выводит результаты полной индексации"""
    
    print()
    print(f"{Colors.BOLD}📊 РЕЗУЛЬТАТЫ ПОЛНОЙ ИНДЕКСАЦИИ{Colors.END}")
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
    
    if results["success"]:
        print(f"  {Colors.GREEN}✅ Индексация завершена успешно{Colors.END}")
        print(f"  ⏱️  Время: {results['duration']:.1f} сек")
        
        if results["stats"]:
            s = results["stats"]
            print()
            print(f"  {Colors.BOLD}Semantic Index:{Colors.END}")
            print(f"     📄 Файлов кода: {s.get('code_files_indexed', 0)}")
            print(f"     📦 Классов: {s.get('classes_found', 0)}")
            print(f"     ⚡ Функций: {s.get('functions_found', 0)}")
            print(f"     🔢 Токенов кода: {s.get('code_tokens_total', 0):,}")
            
            print()
            print(f"  {Colors.BOLD}Project Map:{Colors.END}")
            print(f"     📁 Всего файлов: {s.get('total_files', 0)}")
            print(f"     💻 Код-файлов: {s.get('code_files', 0)}")
            print(f"     📄 Не-код файлов: {s.get('non_code_files', 0)}")
            print(f"     🤖 AI описаний создано: {s.get('ai_descriptions_generated', 0)}")
            print(f"     ⚠️ AI описаний не удалось: {s.get('ai_descriptions_failed', 0)}")
            print(f"     ⏭️ AI пропущено (>30k): {s.get('ai_descriptions_skipped', 0)}")
            
            if s.get('index_compressed'):
                print()
                print(f"  {Colors.YELLOW}📦 Индекс был сжат:{Colors.END}")
                print(f"     До: {s.get('original_index_tokens', 0):,} токенов")
                print(f"     После: {s.get('compressed_index_tokens', 0):,} токенов")
            
            if s.get('errors_count', 0) > 0:
                print()
                print(f"  {Colors.RED}⚠️ Ошибки ({s.get('errors_count', 0)}):{Colors.END}")
                for err in s.get('errors', [])[:5]:
                    print(f"     • {err.get('file', 'unknown')}: {err.get('error', '')[:50]}")
        
        print()
        print(f"  {Colors.BOLD}Созданные файлы:{Colors.END}")
        for f in results["files_created"]:
            print(f"     ✅ {f}")
    else:
        print(f"  {Colors.RED}❌ Индексация не удалась{Colors.END}")
        for err in results["errors"]:
            print(f"     {Colors.RED}• {err}{Colors.END}")
    
    print()


def print_sync_results(results: Dict[str, Any]):
    """Выводит результаты синхронизации"""
    
    print()
    print(f"{Colors.BOLD}📊 РЕЗУЛЬТАТЫ СИНХРОНИЗАЦИИ{Colors.END}")
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
    
    if results["success"]:
        print(f"  {Colors.GREEN}✅ Синхронизация завершена успешно{Colors.END}")
        print(f"  ⏱️  Время: {results['duration']:.1f} сек")
        
        if results["stats"]:
            s = results["stats"]
            print()
            print(f"  {Colors.BOLD}Изменения:{Colors.END}")
            print(f"     ➕ Новых файлов: {s.get('new_files', 0)}")
            print(f"     📝 Изменённых: {s.get('modified_files', 0)}")
            print(f"     🗑️ Удалённых: {s.get('deleted_files', 0)}")
            print(f"     📦 Перемещённых: {s.get('moved_files', 0)}")
            print(f"     ⏭️ Без изменений: {s.get('unchanged_files', 0)}")
            
            print()
            print(f"  {Colors.BOLD}AI описания:{Colors.END}")
            print(f"     🤖 Создано: {s.get('ai_descriptions_generated', 0)}")
            print(f"     ⚠️ Не удалось: {s.get('ai_descriptions_failed', 0)}")
            
            if s.get('index_compressed'):
                print(f"     {Colors.YELLOW}📦 Индекс был сжат{Colors.END}")
    else:
        print(f"  {Colors.RED}❌ Синхронизация не удалась{Colors.END}")
        for err in results["errors"]:
            print(f"     {Colors.RED}• {err}{Colors.END}")
    
    print()


# ============== MAIN ==============

def main():
    # Настройка базового логирования
    logging.basicConfig(
        level=logging.WARNING,
        format='%(levelname)s: %(message)s'
    )
    
    print_header("INDEX MANAGER TEST")
    print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Проект: {PROJECT_ROOT}")
    
    # Создаём логгер
    logger = TestLogger(verbose=True)
    
    # === ЭТАП 1: Проверка импортов ===
    print_header("ЭТАП 1: ПРОВЕРКА ИМПОРТОВ")
    
    if not check_imports(logger):
        print()
        print_error("Критические импорты не удались!")
        print_info("Исправьте ошибки и перезапустите скрипт")
        print()
        input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
        return 1
    
    # === Выбор режима ===
    mode = select_mode()
    
    # === РЕЖИМ: Только проверка ===
    if mode == "check":
        print_header("ПРОВЕРКА API")
        check_apis(logger)
        
        print()
        print_success("Проверка завершена!")
        
        # Сохраняем лог
        log_path = PROJECT_ROOT / "logs" / f"index_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_path.parent.mkdir(exist_ok=True)
        logger.save_log(log_path)
        print_info(f"Лог сохранён: {log_path}")
        
        print()
        input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
        return 0
    
    # === Выбор директории ===
    target = select_directory()
    if not target:
        return 0
    
    # === РЕЖИМ: Статистика ===
    if mode == "stats":
        show_index_stats(target, logger)
        
        print()
        input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
        return 0
    
    # === ЭТАП 2: Проверка API ===
    print_header("ЭТАП 2: ПРОВЕРКА API")
    api_results = check_apis(logger)
    
    if not api_results.get("deepseek") and not api_results.get("qwen"):
        print()
        print_error("Ни один API не доступен!")
        print_info("Проверьте API ключи в .env файле")
        print()
        input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
        return 1
    
    if not api_results.get("deepseek"):
        print_warning("DeepSeek недоступен, будут ограничения")
    
    # === ЭТАП 3: Индексация/Синхронизация ===
    if mode == "full":
        print_header("ЭТАП 3: ПОЛНАЯ ИНДЕКСАЦИЯ")
        
        print_info(f"Целевая директория: {target}")
        print_info("Индексы будут сохранены в: .ai-agent/")
        print()
        
        confirm = input(f"{Colors.YELLOW}Начать индексацию? [Y/n]: {Colors.END}").strip().lower()
        if confirm == 'n':
            print_info("Отменено")
            return 0
        
        print()
        
        try:
            results = asyncio.run(run_full_indexing(target, logger))
        except KeyboardInterrupt:
            print()
            print_warning("Прервано пользователем (Ctrl+C)")
            return 1
        
        print_full_results(results)
        
        # Показываем статистику индексов
        if results["success"]:
            show_index_stats(target, logger)
    
    elif mode == "sync":
        print_header("ЭТАП 3: ИНКРЕМЕНТАЛЬНОЕ ОБНОВЛЕНИЕ")
        
        print_info(f"Целевая директория: {target}")
        print()
        
        try:
            results = asyncio.run(run_incremental_sync(target, logger))
        except KeyboardInterrupt:
            print()
            print_warning("Прервано пользователем (Ctrl+C)")
            return 1
        
        print_sync_results(results)
        
        # Показываем статистику индексов
        if results["success"]:
            show_index_stats(target, logger)
    
    # === Сохранение лога ===
    log_path = PROJECT_ROOT / "logs" / f"index_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path.parent.mkdir(exist_ok=True)
    
    if logger.save_log(log_path):
        print_info(f"Лог сохранён: {log_path}")
    
    # === Завершение ===
    print()
    print_header("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    
    if logger.errors:
        print(f"{Colors.YELLOW}⚠️ Во время работы возникли ошибки ({len(logger.errors)}):{Colors.END}")
        for err in logger.errors[:5]:
            print(f"   • {err[:80]}")
        if len(logger.errors) > 5:
            print(f"   ... и ещё {len(logger.errors) - 5}")
    else:
        print_success("Ошибок не обнаружено!")
    
    print()
    input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
    return 0


if __name__ == "__main__":
    sys.exit(main())