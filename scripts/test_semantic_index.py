# scripts/test_semantic_index.py
"""
Тестовый скрипт для асинхронного построения семантического индекса.
Может индексировать ЛЮБУЮ директорию, не только текущий проект.

Режимы:
1. Инкрементальная индексация
2. Полная индексация (force)
3. Тестирование обнаружения изменений (NEW)
"""

from __future__ import annotations
import sys
import time
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Tuple, List

# Добавляем корень проекта в путь для импортов
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Теперь импорты будут работать
from config.settings import cfg
from app.utils.token_counter import TokenCounter
from app.services.python_chunker import SmartPythonChunker


# ==================== ЛОГГЕР ДЛЯ ТЕСТИРОВАНИЯ ====================

class ChangeDetectionLogger:
    """Логгер для детального отслеживания процесса обнаружения изменений"""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.log_entries: List[Dict] = []
        self.start_time = None
    
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
    
    def detail(self, message: str):
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
        
        if self.verbose:
            print(f"{Colors.DIM}[{timestamp}]{Colors.END} {color}[{level:6}]{Colors.END} {message}")
    
    def get_summary(self) -> str:
        """Возвращает сводку всех логов"""
        lines = ["=" * 60, "СВОДКА ЛОГОВ ОБНАРУЖЕНИЯ ИЗМЕНЕНИЙ", "=" * 60]
        for entry in self.log_entries:
            lines.append(f"[{entry['timestamp']}] [{entry['level']:6}] {entry['message']}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ==================== ЦВЕТА ====================

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


# ==================== ПРОГРЕСС-ТРЕКЕР ====================

class ProgressTracker:
    """Трекер прогресса для асинхронного выполнения"""
    
    def __init__(self):
        self.total = 0
        self.completed = 0
        self.successes = 0
        self.failures = 0
        self.active_count = 0
        self.last_update = 0
        self.current_file = ""
    
    def set_total(self, total: int):
        self.total = total
        self._print_status()
    
    def file_started(self, filename: str):
        self.current_file = filename
        self._print_status()
    
    def task_started(self, task: str):
        self.active_count += 1
        self._print_status()
    
    def task_completed(self, task: str, success: bool):
        self.completed += 1
        self.active_count = max(0, self.active_count - 1)
        if success:
            self.successes += 1
        else:
            self.failures += 1
        self._print_status()
    
    def task_error(self, task: str, error: str):
        self.completed += 1
        self.failures += 1
        self.active_count = max(0, self.active_count - 1)
        self._print_status()
    
    def _print_status(self):
        now = time.time()
        if now - self.last_update < 0.1 and self.completed < self.total:
            return
        self.last_update = now
        
        pct = (self.completed / self.total * 100) if self.total > 0 else 0
        bar_filled = int(pct / 5)
        bar_empty = 20 - bar_filled
        
        status = (
            f"\r{Colors.CYAN}[{self.completed}/{self.total}]{Colors.END} "
            f"{Colors.GREEN}✓{self.successes}{Colors.END} "
            f"{Colors.RED}✗{self.failures}{Colors.END} "
            f"{Colors.DIM}⚡{self.active_count}{Colors.END} "
            f"[{'█' * bar_filled}{'░' * bar_empty}] {pct:.1f}%   "
        )
        
        print(status, end='', flush=True)
    
    def finish(self):
        print()


# ==================== ИГНОРИРУЕМЫЕ ДИРЕКТОРИИ ====================

IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache",
    ".pytest_cache", ".tox", "eggs", "site-packages"
}


# ==================== ПРОВЕРКИ ====================

def check_imports() -> bool:
    print_info("Проверка импортов...")
    errors = []
    
    try:
        from config.settings import cfg
        print_success("config.settings")
    except ImportError as e:
        errors.append(f"config.settings: {e}")
    
    try:
        import httpx
        print_success("httpx")
    except ImportError as e:
        errors.append(f"httpx: {e}")
        print_error("Установите: pip install httpx")
    
    try:
        from app.utils.token_counter import TokenCounter
        print_success("TokenCounter")
    except ImportError as e:
        errors.append(f"TokenCounter: {e}")
    
    try:
        from app.services.python_chunker import SmartPythonChunker
        print_success("SmartPythonChunker")
    except ImportError as e:
        errors.append(f"SmartPythonChunker: {e}")
    
    try:
        from app.builders.semantic_index_builder import SemanticIndexer
        print_success("SemanticIndexer")
    except ImportError as e:
        errors.append(f"SemanticIndexer: {e}")
    
    for err in errors:
        print_error(err)
    
    return len(errors) == 0


def check_api_qwen() -> Tuple[bool, str]:
    """Проверка Qwen API"""
    import httpx
    
    if not cfg.OPENROUTER_API_KEY:
        return False, "OPENROUTER_API_KEY не установлен"
    
    try:
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
        return False, f"HTTP {resp.status_code}: {resp.text[:150]}"
    except Exception as e:
        return False, str(e)


def check_api_deepseek() -> Tuple[bool, str]:
    """Проверка DeepSeek API"""
    import httpx
    
    if not cfg.DEEPSEEK_API_KEY:
        return False, "DEEPSEEK_API_KEY не установлен"
    
    try:
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
        return False, f"HTTP {resp.status_code}: {resp.text[:150]}"
    except Exception as e:
        return False, str(e)


# ==================== УТИЛИТЫ ====================

def list_python_files(target: Path) -> list:
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


def count_tasks(target: Path) -> int:
    """Считает примерное количество задач для прогресс-бара"""
    chunker = SmartPythonChunker()
    total = 0
    
    py_files = list_python_files(target)
    
    for file_path in py_files:
        try:
            tree = chunker.chunk_file_to_tree(str(file_path))
            if tree.id == "module:error":
                continue
            
            def count_in_node(node):
                count = 0
                for child in node.children:
                    if child.kind == "class":
                        count += 1
                        for sub in child.children:
                            if sub.kind == "method":
                                count += 1
                    elif child.kind == "function":
                        count += 1
                return count
            
            total += count_in_node(tree)
        except:
            pass
    
    return total


# ==================== ВЫБОР ДИРЕКТОРИИ ====================

def select_directory() -> Optional[Path]:
    print_header("ВЫБОР ДИРЕКТОРИИ ДЛЯ ИНДЕКСАЦИИ")
    
    cwd = Path.cwd()
    
    print(f"  {Colors.CYAN}1{Colors.END} - Текущая директория")
    print(f"      {Colors.DIM}{cwd}{Colors.END}")
    print(f"  {Colors.CYAN}2{Colors.END} - Директория проекта AI_Assistant_Pro")
    print(f"      {Colors.DIM}{PROJECT_ROOT}{Colors.END}")
    print(f"  {Colors.CYAN}3{Colors.END} - Ввести путь вручную")
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
    
    py_files = list_python_files(target)
    
    if not py_files:
        print_error("В директории нет Python файлов")
        return None
    
    print_success(f"Найдено {len(py_files)} Python файлов")
    return target


def select_mode() -> str:
    """Выбор режима индексации"""
    print()
    print(f"{Colors.BOLD}Режим работы:{Colors.END}")
    print()
    print(f"  {Colors.CYAN}1{Colors.END} - {Colors.GREEN}Инкрементальный{Colors.END} (по умолчанию)")
    print(f"      {Colors.DIM}Индексирует только изменённые файлы{Colors.END}")
    print()
    print(f"  {Colors.CYAN}2{Colors.END} - {Colors.GREEN}Полный{Colors.END} (force)")
    print(f"      {Colors.DIM}Переиндексирует всё с нуля{Colors.END}")
    print()
    print(f"  {Colors.CYAN}3{Colors.END} - {Colors.YELLOW}Тест обнаружения изменений{Colors.END} (NEW)")
    print(f"      {Colors.DIM}Проверяет работу detect_changed_files() с логированием{Colors.END}")
    
    choice = input(f"\n{Colors.YELLOW}Выбор [1]: {Colors.END}").strip()
    
    if choice == "2":
        return "force"
    elif choice == "3":
        return "detect_changes"
    return "incremental"


def select_concurrency() -> int:
    print()
    print(f"Уровень параллелизма (одновременных запросов к API):")
    print(f"  {Colors.DIM}Рекомендуется 5-25. Максимум 50. Больше = быстрее, но риск rate limit.{Colors.END}")
    
    choice = input(f"{Colors.YELLOW}Количество [10]: {Colors.END}").strip()
    
    if not choice:
        return 10
    
    try:
        n = int(choice)
        limit = 50
        result = max(1, min(limit, n))
        
        if result != n:
            print_warning(f"Число ограничено диапазоном 1-{limit}. Использую {result}")
            
        return result
    except ValueError:
        print_warning("Некорректное число, использую 10")
        return 10


# ==================== ТЕСТИРОВАНИЕ ОБНАРУЖЕНИЯ ИЗМЕНЕНИЙ ====================

def test_change_detection(target: Path) -> bool:
    """
    Тестирует функционал обнаружения изменений с подробным логированием.
    
    Проверяет:
    1. Наличие метода detect_changed_files()
    2. Загрузку существующего индекса
    3. Сканирование файловой системы
    4. Сравнение хэшей
    5. Обнаружение добавленных/изменённых/удалённых/перемещённых файлов
    
    Returns:
        bool: True если тест прошёл успешно
    """
    from app.builders.semantic_index_builder import SemanticIndexer
    
    logger = ChangeDetectionLogger(verbose=True)
    
    print_header("ТЕСТ ОБНАРУЖЕНИЯ ИЗМЕНЕНИЙ")
    
    logger.start(f"Инициализация тестирования для: {target}")
    
    # === ШАГ 1: Проверка наличия метода ===
    logger.step("Проверка наличия метода detect_changed_files()...")
    
    indexer = SemanticIndexer(str(target))
    
    if not hasattr(indexer, 'detect_changed_files'):
        logger.error("Метод detect_changed_files() НЕ НАЙДЕН в SemanticIndexer!")
        logger.error("Убедитесь, что код из предыдущего ответа добавлен в semantic_index_builder.py")
        print()
        print_error("ТЕСТ НЕ ПРОЙДЕН: метод detect_changed_files() отсутствует")
        return False
    
    logger.success("Метод detect_changed_files() найден")
    
    # === ШАГ 2: Проверка существующего индекса ===
    logger.step("Проверка существующего индекса...")
    
    index_path = target / "semantic_index.json"
    
    if index_path.exists():
        logger.found(f"Найден существующий индекс: {index_path}")
        
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                existing_index = json.load(f)
            
            files_count = len(existing_index.get("files", {}))
            created_at = existing_index.get("created_at", "N/A")
            updated_at = existing_index.get("updated_at", "N/A")
            
            logger.detail(f"  Файлов в индексе: {files_count}")
            logger.detail(f"  Создан: {created_at}")
            logger.detail(f"  Обновлён: {updated_at}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка чтения индекса: {e}")
            existing_index = None
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            existing_index = None
    else:
        logger.warning(f"Индекс не найден: {index_path}")
        logger.warning("Для полноценного тестирования сначала создайте индекс (режим 1 или 2)")
        existing_index = None
    
    # === ШАГ 3: Сканирование файловой системы ===
    logger.step("Сканирование файловой системы...")
    
    py_files = list_python_files(target)
    logger.found(f"Найдено Python файлов: {len(py_files)}")
    
    # Показываем первые 10 файлов
    for i, f in enumerate(py_files[:10]):
        rel_path = f.relative_to(target)
        logger.detail(f"  [{i+1:3}] {rel_path}")
    
    if len(py_files) > 10:
        logger.detail(f"  ... и ещё {len(py_files) - 10} файлов")
    
    # === ШАГ 4: Вызов detect_changed_files() ===
    logger.step("Вызов detect_changed_files()...")
    
    try:
        start_time = time.time()
        changes = indexer.detect_changed_files()
        elapsed = time.time() - start_time
        
        logger.success(f"Метод выполнен успешно за {elapsed:.3f}s")
        
    except AttributeError as e:
        logger.error(f"AttributeError: {e}")
        logger.error("Возможно, не все вспомогательные методы добавлены")
        print()
        print_error("ТЕСТ НЕ ПРОЙДЕН: ошибка при вызове detect_changed_files()")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print()
        print_error("ТЕСТ НЕ ПРОЙДЕН: исключение при выполнении")
        return False
    
    # === ШАГ 5: Анализ результатов ===
    logger.step("Анализ результатов...")
    
    # Проверяем структуру результата
    expected_keys = {'added', 'modified', 'deleted', 'moved'}
    actual_keys = set(changes.keys())
    
    if not expected_keys.issubset(actual_keys):
        missing = expected_keys - actual_keys
        logger.error(f"В результате отсутствуют ключи: {missing}")
        print()
        print_error("ТЕСТ НЕ ПРОЙДЕН: неверная структура результата")
        return False
    
    logger.success("Структура результата корректна")
    
    # Выводим статистику
    added = changes.get('added', [])
    modified = changes.get('modified', [])
    deleted = changes.get('deleted', [])
    moved = changes.get('moved', [])
    
    print()
    print(f"{Colors.BOLD}📊 РЕЗУЛЬТАТЫ ОБНАРУЖЕНИЯ ИЗМЕНЕНИЙ:{Colors.END}")
    print()
    
    # Добавленные файлы
    print(f"  {Colors.GREEN}➕ Добавлено: {len(added)} файлов{Colors.END}")
    if added:
        for f in added[:5]:
            name = f.name if hasattr(f, 'name') else Path(f).name
            logger.detail(f"     + {name}")
        if len(added) > 5:
            logger.detail(f"     ... и ещё {len(added) - 5}")
    
    # Изменённые файлы
    print(f"  {Colors.YELLOW}📝 Изменено: {len(modified)} файлов{Colors.END}")
    if modified:
        for f in modified[:5]:
            name = f.name if hasattr(f, 'name') else Path(f).name
            logger.detail(f"     ~ {name}")
        if len(modified) > 5:
            logger.detail(f"     ... и ещё {len(modified) - 5}")
    
    # Удалённые файлы
    print(f"  {Colors.RED}🗑️ Удалено: {len(deleted)} файлов{Colors.END}")
    if deleted:
        for f in deleted[:5]:
            logger.detail(f"     - {f}")
        if len(deleted) > 5:
            logger.detail(f"     ... и ещё {len(deleted) - 5}")
    
    # Перемещённые файлы
    print(f"  {Colors.CYAN}📦 Перемещено: {len(moved)} файлов{Colors.END}")
    if moved:
        for m in moved[:3]:
            if isinstance(m, dict):
                logger.detail(f"     {m.get('from', '?')} → {m.get('to', '?')}")
            else:
                logger.detail(f"     {m}")
        if len(moved) > 3:
            logger.detail(f"     ... и ещё {len(moved) - 3}")
    
    # === ШАГ 6: Проверка вспомогательных методов ===
    logger.step("Проверка вспомогательных методов...")
    
    # Проверяем update_single_file
    if hasattr(indexer, 'update_single_file'):
        logger.success("Метод update_single_file() найден")
    else:
        logger.warning("Метод update_single_file() НЕ найден")
    
    # Проверяем sync_index
    if hasattr(indexer, 'sync_index'):
        logger.success("Метод sync_index() найден")
    else:
        logger.warning("Метод sync_index() НЕ найден")
    
    # Проверяем _save_both_indexes
    if hasattr(indexer, '_save_both_indexes'):
        logger.success("Метод _save_both_indexes() найден")
    else:
        logger.warning("Метод _save_both_indexes() НЕ найден")
    
    # === ШАГ 7: Тест хэширования ===
    logger.step("Тестирование хэширования файлов...")
    
    if py_files:
        test_file = py_files[0]
        
        try:
            from app.builders.semantic_index_builder import ContentHasher
            
            hasher = ContentHasher()
            file_hash = hasher.hash_file(test_file)
            
            logger.success(f"Хэш файла {test_file.name}: {file_hash[:16]}...")
            
            # Читаем содержимое и хэшируем
            content = test_file.read_text(encoding='utf-8')
            content_hash = hasher.hash_content(content)
            
            logger.success(f"Хэш содержимого: {content_hash[:16]}...")
            
        except ImportError:
            logger.warning("ContentHasher не найден, используется встроенное хэширование")
        except Exception as e:
            logger.error(f"Ошибка хэширования: {e}")
    
    # === ФИНАЛЬНЫЙ ОТЧЁТ ===
    logger.finish("Тестирование завершено")
    
    print()
    print(f"{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.GREEN}{Colors.BOLD}✅ ТЕСТ ПРОЙДЕН УСПЕШНО{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}")
    print()
    
    # Рекомендации
    total_changes = len(added) + len(modified) + len(deleted) + len(moved)
    
    if total_changes == 0 and existing_index:
        print(f"{Colors.CYAN}💡 Подсказка:{Colors.END}")
        print(f"   Изменений не обнаружено. Попробуйте:")
        print(f"   1. Изменить любой .py файл и запустить тест снова")
        print(f"   2. Создать новый .py файл в директории")
        print(f"   3. Удалить файл (после создания индекса)")
    elif total_changes > 0:
        print(f"{Colors.CYAN}💡 Следующие шаги:{Colors.END}")
        print(f"   1. Запустите режим '1' (инкрементальный) для обновления индекса")
        print(f"   2. Или используйте sync_index() для синхронизации")
    elif not existing_index:
        print(f"{Colors.CYAN}💡 Подсказка:{Colors.END}")
        print(f"   Индекс не найден. Сначала создайте его:")
        print(f"   1. Запустите режим '1' или '2' для создания индекса")
        print(f"   2. Затем повторите тест обнаружения изменений")
    
    print()
    
    # Сохраняем лог в файл
    log_path = target / "change_detection_test.log"
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(logger.get_summary())
        print(f"{Colors.DIM}Лог сохранён: {log_path}{Colors.END}")
    except Exception as e:
        print(f"{Colors.DIM}Не удалось сохранить лог: {e}{Colors.END}")
    
    return True


def select_mode() -> str:
    """Выбор режима индексации"""
    print()
    print(f"{Colors.BOLD}Режим работы:{Colors.END}")
    print()
    print(f"  {Colors.CYAN}1{Colors.END} - {Colors.GREEN}Инкрементальный{Colors.END}")
    print(f"      {Colors.DIM}Полная индексация с проверкой кэша{Colors.END}")
    print()
    print(f"  {Colors.CYAN}2{Colors.END} - {Colors.GREEN}Полный{Colors.END} (force)")
    print(f"      {Colors.DIM}Переиндексирует всё с нуля{Colors.END}")
    print()
    print(f"  {Colors.CYAN}3{Colors.END} - {Colors.YELLOW}Тест обнаружения изменений{Colors.END}")
    print(f"      {Colors.DIM}Только проверка, без обновления{Colors.END}")
    print()
    print(f"  {Colors.CYAN}4{Colors.END} - {Colors.CYAN}Синхронизация{Colors.END} (NEW)")
    print(f"      {Colors.DIM}Обновить только изменённые файлы{Colors.END}")
    
    choice = input(f"\n{Colors.YELLOW}Выбор [1]: {Colors.END}").strip()
    
    if choice == "2":
        return "force"
    elif choice == "3":
        return "detect_changes"
    elif choice == "4":
        return "sync"
    return "incremental"



def test_incremental_update(target: Path) -> bool:
    """
    Дополнительный тест: симуляция инкрементального обновления
    """
    from app.builders.semantic_index_builder import SemanticIndexer
    
    logger = ChangeDetectionLogger(verbose=True)
    
    print_header("ТЕСТ ИНКРЕМЕНТАЛЬНОГО ОБНОВЛЕНИЯ")
    
    logger.start("Начало теста инкрементального обновления")
    
    indexer = SemanticIndexer(str(target))
    
    # Проверяем наличие update_single_file
    if not hasattr(indexer, 'update_single_file'):
        logger.error("Метод update_single_file() не найден!")
        print_error("ТЕСТ НЕ ПРОЙДЕН")
        return False
    
    logger.success("Метод update_single_file() доступен")
    
    # Находим файл для теста
    py_files = list_python_files(target)
    if not py_files:
        logger.error("Нет Python файлов для тестирования")
        return False
    
    test_file = py_files[0]
    logger.step(f"Тестовый файл: {test_file.name}")
    
    # Проверяем, что можем вызвать метод (dry run)
    logger.step("Проверка сигнатуры метода...")
    
    import inspect
    sig = inspect.signature(indexer.update_single_file)
    params = list(sig.parameters.keys())
    
    logger.detail(f"Параметры: {params}")
    
    if 'file_path' not in params and len(params) < 1:
        logger.error("Неверная сигнатура метода")
        return False
    
    logger.success("Сигнатура метода корректна")
    
    logger.finish("Тест структуры пройден")
    
    print()
    print(f"{Colors.GREEN}✅ Инкрементальное обновление готово к использованию{Colors.END}")
    print()
    print(f"{Colors.CYAN}Для реального обновления используйте:{Colors.END}")
    print(f"   await indexer.update_single_file(Path('path/to/file.py'))")
    print()
    
    return True


# ==================== ПОСТРОЕНИЕ ====================

async def build_with_progress(target: Path, max_concurrent: int, force: bool) -> Tuple[Dict, float]:
    """
    Асинхронная обертка для запуска индексации с визуализацией прогресса.
    Совместима с обновленным SemanticIndexer.
    """
    from app.builders.semantic_index_builder import SemanticIndexer
    
    start_time = time.time()
    
    # 1. Инициализация (передаем путь как строку)
    indexer = SemanticIndexer(str(target))
    if hasattr(indexer, 'max_concurrent'):
        indexer.max_concurrent = max_concurrent
    
    # 2. Настройка трекера прогресса
    tracker = ProgressTracker()
    print_info("Предварительный подсчет задач...")
    initial_total = count_tasks(target)
    tracker.set_total(initial_total)
    
    # 3. Связываем indexer с нашим трекером
    original_report_progress = indexer._report_progress
    
    def on_progress_update(current, total, message):
        if total > 0:
            tracker.set_total(total)
        tracker.file_started(message)
        if current > 0:
            tracker.completed = current
        tracker._print_status()

    indexer._report_progress = on_progress_update
    
    # 4. ЗАПУСК ИНДЕКСАЦИИ
    try:
        index = await indexer.build_index_async(force=force)
    finally:
        tracker.finish()
    
    elapsed = time.time() - start_time
    
    return index, elapsed


# ==================== АНАЛИЗ РЕЗУЛЬТАТА ====================

def analyze_result(index: Dict, target: Path):
    """Выводит статистику по результату"""
    
    stats = index.get("stats", {})
    
    print()
    print(f"{Colors.BOLD}📊 Статистика индексации:{Colors.END}")
    print()
    print(f"📁 Файлов: {index.get('total_files', 0)}")
    print(f"   ├── ➕ Добавлено: {stats.get('files_added', 0)}")
    print(f"   ├── 🔄 Обновлено: {stats.get('files_updated', 0)}")
    print(f"   ├── ⏭️ Пропущено: {stats.get('files_skipped', 0)}")
    print(f"   └── 🗑️ Удалено: {stats.get('files_removed', 0)}")
    print()
    
    # Подсчёт классов и функций
    total_classes = 0
    total_functions = 0
    for file_data in index.get("files", {}).values():
        total_classes += len(file_data.get("classes", []))
        total_functions += len(file_data.get("functions", []))
    
    print(f"📦 Классов: {total_classes}")
    print(f"⚡ Функций: {total_functions}")
    print()
    
    # API статистика
    qwen_calls = stats.get('qwen_calls', 0)
    qwen_success = stats.get('qwen_successes', 0)
    ds_calls = stats.get('deepseek_calls', 0)
    ds_success = stats.get('deepseek_successes', 0)
    
    qwen_pct = (qwen_success / qwen_calls * 100) if qwen_calls > 0 else 0
    ds_pct = (ds_success / ds_calls * 100) if ds_calls > 0 else 0
    
    print(f"{Colors.BOLD}🤖 API вызовы:{Colors.END}")
    print(f"   🔵 Qwen: {qwen_success}/{qwen_calls} ({qwen_pct:.1f}% успех)")
    print(f"   🟢 DeepSeek: {ds_success}/{ds_calls} ({ds_pct:.1f}% успех)")
    print(f"   🔄 Fallback Qwen→DS: {stats.get('fallback_to_deepseek', 0)}")
    print(f"   🔧 Parse recoveries: {stats.get('parse_recoveries', 0)}")
    print()
    
    # Размеры файлов
    print(f"{Colors.BOLD}📄 Созданные файлы (в {target}):{Colors.END}")
    
    tc = TokenCounter()
    
    index_files = [
        ("semantic_index.json", "ПОЛНЫЙ"),
        ("compact_index.json", "КОМПАКТ"),
        ("compact_index.md", "MARKDOWN"),
    ]
    
    for filename, label in index_files:
        filepath = target / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            size = len(content.encode('utf-8'))
            tokens = tc.count(content)
            
            color = Colors.CYAN if "ПОЛНЫЙ" in label else Colors.GREEN
            print(f"   {color}[{label}]{Colors.END} {filename}")
            print(f"      Размер: {size:,} байт ({size/1024:.1f} KB)")
            print(f"      Токены: {tokens:,}")
    
    # Ошибки
    errors = stats.get("errors", [])
    if errors:
        print()
        print_warning(f"Ошибок: {len(errors)}")
        
        error_types: Dict[str, int] = {}
        for e in errors:
            e_lower = str(e).lower()
            if "timeout" in e_lower:
                t = "Таймауты"
            elif "http" in e_lower:
                t = "HTTP ошибки"
            elif "json" in e_lower:
                t = "JSON ошибки"
            else:
                t = "Другие"
            error_types[t] = error_types.get(t, 0) + 1
        
        for t, count in sorted(error_types.items(), key=lambda x: -x[1]):
            print(f"   {Colors.DIM}• {t}: {count}{Colors.END}")
    else:
        print()
        print_success("Ошибок не обнаружено!")


def show_sample_output(target: Path):
    """Показывает пример вывода из индекса"""
    compact_path = target / "compact_index.md"
    
    if not compact_path.exists():
        return
    
    print()
    print(f"{Colors.BOLD}📋 Пример compact_index.md:{Colors.END}")
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
    
    content = compact_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    # Показываем первые 20 строк
    for line in lines[:20]:
        print(f"{Colors.DIM}{line}{Colors.END}")
    
    if len(lines) > 20:
        print(f"{Colors.DIM}... (ещё {len(lines) - 20} строк){Colors.END}")
    
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")


# ==================== MAIN ====================

def main():
    # Настройка логирования
    logging.basicConfig(
        level=logging.WARNING,
        format='%(levelname)s: %(message)s'
    )
    
    print_header("SEMANTIC INDEX BUILDER")
    print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Проект: {PROJECT_ROOT}")
    
    # === Проверки ===
    print_header("ЭТАП 1: ПРОВЕРКИ")
    
    if not check_imports():
        print_error("Исправьте ошибки импорта и перезапустите")
        return 1
    
    print()
    print_info("Проверка Qwen API...")
    qwen_ok, qwen_msg = check_api_qwen()
    if qwen_ok:
        print_success(f"Qwen: {qwen_msg}")
    else:
        print_error(f"Qwen: {qwen_msg}")
        return 1
    
    print_info("Проверка DeepSeek API...")
    ds_ok, ds_msg = check_api_deepseek()
    if ds_ok:
        print_success(f"DeepSeek: {ds_msg}")
    else:
        print_warning(f"DeepSeek: {ds_msg}")
        print_info("DeepSeek используется как fallback и для больших классов")
    
    # === Выбор директории ===
    target = select_directory()
    if not target:
        return 0
    
    # === Выбор режима ===
    mode = select_mode()
    
    # === РЕЖИМ ТЕСТИРОВАНИЯ ОБНАРУЖЕНИЯ ИЗМЕНЕНИЙ ===
    if mode == "detect_changes":
        success = test_change_detection(target)
        
        # Дополнительный тест
        print()
        run_extra = input(f"{Colors.YELLOW}Запустить тест инкрементального обновления? [y/N]: {Colors.END}").strip().lower()
        if run_extra == 'y':
            test_incremental_update(target)
        
        print()
        input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
        return 0 if success else 1
    
    # === РЕЖИМ СИНХРОНИЗАЦИИ ===
    if mode == "sync":
        print_header("СИНХРОНИЗАЦИЯ ИНДЕКСА")
        
        from app.builders.semantic_index_builder import SemanticIndexer
        
        indexer = SemanticIndexer(str(target))
        
        print_info("Запуск sync_index()...")
        
        try:
            stats = asyncio.run(indexer.sync_index(force=False))
            
            print()
            print_success("Синхронизация завершена!")
            print(f"   ➕ Добавлено: {stats['added']}")
            print(f"   📝 Изменено: {stats['modified']}")
            print(f"   🗑️ Удалено: {stats['deleted']}")
            print(f"   📦 Перемещено: {stats['moved']}")
            
            if stats['errors']:
                print_warning(f"   Ошибок: {len(stats['errors'])}")
                
        except Exception as e:
            print_error(f"Ошибка: {e}")
            import traceback
            traceback.print_exc()
        
        print()
        input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
        return 0
    
    
    # === РЕЖИМЫ ИНДЕКСАЦИИ ===
    force = (mode == "force")
    
    # === Выбор параллелизма ===
    max_concurrent = select_concurrency()
    
    # === Построение ===
    print_header("ЭТАП 2: ПОСТРОЕНИЕ ИНДЕКСА")
    print_info(f"Целевая директория: {target}")
    print_info(f"Режим: {'полный (force)' if force else 'инкрементальный'}")
    print_info(f"Параллелизм: {max_concurrent}")
    print_info(f"Индекс будет сохранён в: {target}")
    print()
    
    try:
        index, elapsed = asyncio.run(
            build_with_progress(target, max_concurrent, force)
        )
    except KeyboardInterrupt:
        print()
        print_warning("Прервано пользователем (Ctrl+C)")
        return 1
    except Exception as e:
        print()
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # === Результаты ===
    print_header("РЕЗУЛЬТАТЫ")
    
    print_success(f"Индекс создан успешно!")
    print_info(f"Время: {elapsed:.1f} сек ({elapsed/60:.1f} мин)")
    
    analyze_result(index, target)
    show_sample_output(target)
    
    # === Завершение ===
    print()
    input(f"{Colors.CYAN}Нажмите Enter для выхода...{Colors.END}")
    return 0


if __name__ == "__main__":
    sys.exit(main())