#!/usr/bin/env python3
# scripts/test_agents.py
"""
Комплексный тестовый скрипт для AI Code Agent.

Тестирует компоненты:
1. Проверка импортов
2. Проверка подключения к API
3. Тестирование отдельных агентов (Router, Pre-filter, Orchestrator, Code Generator)
4. Интеграционный тест (полный пайплайн)

Возможности:
- Интерактивное меню для выбора тестов
- Выбор директории проекта для тестирования
- Подробное логирование с цветным выводом
- Отслеживание прогресса и времени выполнения
"""

from __future__ import annotations
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Добавляем корень проекта в путь
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ЦВЕТНОГО ВЫВОДА
# ============================================================================

class Colors:
    """ANSI коды цветов для терминала"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Цвета
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    
    # Фоны
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


def print_header(text: str, char: str = "="):
    """Печатает заголовок с рамкой"""
    width = 70
    print(f"\n{Colors.CYAN}{char * width}")
    print(f"{Colors.BOLD}{text.center(width)}")
    print(f"{char * width}{Colors.RESET}\n")


def print_subheader(text: str):
    """Печатает подзаголовок"""
    print(f"\n{Colors.YELLOW}{'─' * 50}")
    print(f"{Colors.BOLD}  {text}")
    print(f"{'─' * 50}{Colors.RESET}\n")


def print_success(text: str):
    """Печатает сообщение об успехе"""
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def print_error(text: str):
    """Печатает сообщение об ошибке"""
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_warning(text: str):
    """Печатает предупреждение"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")


def print_info(text: str):
    """Печатает информационное сообщение"""
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")


def print_step(step: int, total: int, text: str):
    """Печатает прогресс шага"""
    print(f"{Colors.MAGENTA}[{step}/{total}]{Colors.RESET} {text}")


def print_result(label: str, value: str, success: bool = True):
    """Печатает строку результата"""
    color = Colors.GREEN if success else Colors.RED
    print(f"  {Colors.DIM}•{Colors.RESET} {label}: {color}{value}{Colors.RESET}")


def print_json(data: Any, indent: int = 2):
    """Печатает форматированный JSON"""
    print(f"{Colors.DIM}{json.dumps(data, indent=indent, ensure_ascii=False)}{Colors.RESET}")


def print_code_block(code: str, language: str = "python", filepath: Optional[str] = None):
    """Печатает блок кода с подсветкой"""
    if filepath:
        print(f"{Colors.CYAN}📁 {filepath}{Colors.RESET}")
    print(f"{Colors.DIM}```{language}")
    # Ограничиваем вывод первыми 30 строками
    lines = code.split('\n')
    if len(lines) > 30:
        print('\n'.join(lines[:30]))
        print(f"... ({len(lines) - 30} строк скрыто)")
    else:
        print(code)
    print(f"```{Colors.RESET}")


def save_json_report(
    project_dir: str,
    user_query: str,
    orchestrator_analysis: str,
    orchestrator_instruction: str,
    code_blocks: List[Dict[str, Any]],
    code_explanation: str,
    frontend_json: Dict[str, Any],
    model_info: Dict[str, Any],
    duration: float
) -> Path:
    """
    Сохраняет полный отчет теста в JSON файл.
    
    Args:
        project_dir: Путь к проекту
        user_query: Запрос пользователя
        orchestrator_analysis: Анализ Оркестратора
        orchestrator_instruction: Инструкции для генератора
        code_blocks: Сгенерированные блоки кода
        code_explanation: Пояснения к коду
        frontend_json: JSON для фронтенда
        model_info: Информация о моделях
        duration: Время выполнения
        
    Returns:
        Path to saved report
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(project_dir) / ".ai-agent" / "test_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = report_dir / f"integration_test_{timestamp}.json"
    
    report = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "project_dir": project_dir,
            "user_query": user_query,
            "duration_seconds": duration,
            "models_used": model_info
        },
        "orchestrator": {
            "analysis": orchestrator_analysis,
            "instruction": orchestrator_instruction
        },
        "code_generator": {
            "code_blocks": code_blocks,
            "explanation": code_explanation,
            "frontend_json": frontend_json
        }
    }
    
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    return report_path


def save_markdown_report(
    project_dir: str,
    user_query: str,
    orchestrator_analysis: str,
    orchestrator_instruction: str,
    code_blocks: List[Dict[str, Any]],
    code_explanation: str,
    model_info: Dict[str, Any],
    tool_calls: Optional[List[Any]] = None,
    duration: float = 0
) -> Path:
    """
    Сохраняет полный отчет теста в Markdown файл (человеко-читаемый формат).
    
    Осторожный подход: проверяет все входные данные на None/пустоту.
    
    Args:
        project_dir: Путь к проекту
        user_query: Запрос пользователя
        orchestrator_analysis: Анализ Оркестратора
        orchestrator_instruction: Инструкции для генератора
        code_blocks: Сгенерированные блоки кода
        code_explanation: Пояснения к коду
        model_info: Информация о моделях
        tool_calls: Вызовы инструментов (опционально)
        duration: Время выполнения
        
    Returns:
        Path to saved report
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(project_dir) / ".ai-agent" / "test_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = report_dir / f"integration_test_{timestamp}.md"
    
    # Безопасное получение значений
    def safe_str(value, default="[Нет данных]"):
        """Безопасное преобразование в строку"""
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return str(value)
    
    # Формируем содержимое
    lines = []
    
    # === ЗАГОЛОВОК ===
    lines.append("# 🤖 AI Code Agent - Интеграционный Тест")
    lines.append("")
    lines.append(f"**Дата выполнения:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(f"**Проект:** `{project_dir}`")
    lines.append(f"**Время выполнения:** {duration:.2f} сек.")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === ЗАПРОС ПОЛЬЗОВАТЕЛЯ ===
    lines.append("## 📝 Запрос пользователя")
    lines.append("")
    lines.append(f"> {safe_str(user_query, '[Запрос отсутствует]')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === ИСПОЛЬЗОВАННЫЕ МОДЕЛИ ===
    lines.append("## 🎯 Использованные модели")
    lines.append("")
    if model_info:
        lines.append(f"- **Orchestrator:** {safe_str(model_info.get('orchestrator'), 'N/A')}")
        lines.append(f"- **Code Generator:** {safe_str(model_info.get('code_generator'), 'N/A')}")
    else:
        lines.append("[Информация о моделях отсутствует]")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === АНАЛИЗ ОРКЕСТРАТОРА ===
    lines.append("## 🔍 Анализ Оркестратора")
    lines.append("")
    analysis_text = safe_str(orchestrator_analysis, "[Анализ не выполнен]")
    lines.append(analysis_text)
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === ИНСТРУКЦИИ ДЛЯ ГЕНЕРАТОРА ===
    lines.append("## 📋 Инструкции для Code Generator")
    lines.append("")
    instruction_text = safe_str(orchestrator_instruction, "[Инструкции отсутствуют]")
    lines.append(instruction_text)
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === СГЕНЕРИРОВАННЫЙ КОД ===
    lines.append("## 💻 Сгенерированный код")
    lines.append("")
    
    if code_blocks and len(code_blocks) > 0:
        for i, block in enumerate(code_blocks, 1):
            # Безопасное извлечение данных
            filepath = safe_str(block.get("filepath"), "unknown_file")
            language = safe_str(block.get("language"), "python")
            code = safe_str(block.get("code"), "# [Код отсутствует]")
            context = block.get("context")
            
            # Заголовок блока
            if len(code_blocks) > 1:
                lines.append(f"### Блок {i}")
                lines.append("")
            
            # Метаданные
            lines.append(f"**Файл:** `{filepath}`")
            if context:
                lines.append(f"**Контекст:** `{context}`")
            lines.append("")
            
            # Код
            lines.append(f"```{language}")
            lines.append(code)
            lines.append("```")
            lines.append("")
    else:
        lines.append("[Код не был сгенерирован]")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # === ПОЯСНЕНИЯ К КОДУ ===
    lines.append("## 📖 Пояснения к коду")
    lines.append("")
    explanation_text = safe_str(code_explanation, "[Пояснения отсутствуют]")
    lines.append(explanation_text)
    lines.append("")
    
    # === ВЫЗОВЫ ИНСТРУМЕНТОВ (опционально) ===
    if tool_calls and len(tool_calls) > 0:
        lines.append("---")
        lines.append("")
        lines.append("## 🛠️ Выполненные вызовы инструментов")
        lines.append("")
        
        for i, tc in enumerate(tool_calls, 1):
            status_icon = "✅" if getattr(tc, 'success', True) else "❌"
            tool_name = safe_str(getattr(tc, 'name', 'unknown'), 'unknown')
            
            # Безопасное получение аргументов
            try:
                args = getattr(tc, 'arguments', {})
                args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
            except:
                args_str = "[args unavailable]"
            
            lines.append(f"{i}. {status_icon} **{tool_name}**")
            lines.append(f"   - Аргументы: `{args_str}`")
            
            # Если была ошибка, показываем её
            if hasattr(tc, 'success') and not tc.success:
                output = safe_str(getattr(tc, 'output', ''), '[no output]')
                lines.append(f"   - ⚠️ Ошибка: {output[:200]}...")
            
            lines.append("")
    
    # === FOOTER ===
    lines.append("---")
    lines.append("")
    lines.append(f"*Отчет сгенерирован автоматически: {datetime.now().isoformat()}*")
    
    # Сохранение
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
    except Exception as e:
        # Если не удалось сохранить, не ломаем тест
        print_warning(f"Не удалось сохранить Markdown отчет: {e}")
        return None
    
    return report_path


# ============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """Форматтер с цветами"""
    
    COLORS = {
        logging.DEBUG: Colors.DIM,
        logging.INFO: Colors.BLUE,
        logging.WARNING: Colors.YELLOW,
        logging.ERROR: Colors.RED,
        logging.CRITICAL: Colors.BG_RED + Colors.WHITE,
    }
    
    def format(self, record):
        color = self.COLORS.get(record.levelno, Colors.RESET)
        record.levelname = f"{color}{record.levelname}{Colors.RESET}"
        record.msg = f"{color}{record.msg}{Colors.RESET}"
        return super().format(record)


def setup_logging(verbose: bool = False):
    """Настройка логирования с цветами"""
    level = logging.DEBUG if verbose else logging.INFO
    
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter(
        fmt='%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s',
        datefmt='%H:%M:%S'
    ))
    
    logging.basicConfig(level=level, handlers=[handler])
    
    # Уменьшаем шум от httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================================================================
# РЕЗУЛЬТАТЫ ТЕСТОВ
# ============================================================================

@dataclass
class TestResult:
    """Результат одного теста"""
    name: str
    passed: bool
    duration_sec: float
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class TestSuite:
    """Набор результатов тестов"""
    name: str
    results: List[TestResult] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)
    
    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)
    
    @property
    def total(self) -> int:
        return len(self.results)
    
    @property
    def duration(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return sum(r.duration_sec for r in self.results)
    
    def add(self, result: TestResult):
        self.results.append(result)
    
    def print_summary(self):
        """Печатает сводку тестов"""
        print_subheader(f"Результаты: {self.name}")
        
        for r in self.results:
            status = f"{Colors.GREEN}УСПЕХ{Colors.RESET}" if r.passed else f"{Colors.RED}ОШИБКА{Colors.RESET}"
            print(f"  [{status}] {r.name} ({r.duration_sec:.2f}с)")
            if r.message:
                print(f"         {Colors.DIM}{r.message}{Colors.RESET}")
            if r.error:
                print(f"         {Colors.RED}Ошибка: {r.error}{Colors.RESET}")
        
        print()
        color = Colors.GREEN if self.failed == 0 else Colors.RED
        print(f"  {color}Итого: {self.passed}/{self.total} пройдено{Colors.RESET} за {self.duration:.2f}с")


# ============================================================================
# ТЕСТЫ ИМПОРТОВ
# ============================================================================

def test_imports() -> TestSuite:
    """Тестирует все необходимые импорты"""
    suite = TestSuite(name="Тесты импортов")
    suite.start_time = datetime.now()
    
    imports_to_test = [
        ("config.settings", "cfg", "Конфигурация"),
        ("app.llm.api_client", "call_llm", "LLM API клиент"),
        ("app.llm.api_client", "call_llm_with_tools", "LLM API с инструментами"),
        ("app.llm.api_client", "is_router_enabled", "Проверка роутера"),
        ("app.llm.prompt_templates", "ROUTER_SYSTEM_PROMPT", "Промпты роутера"),
        ("app.llm.prompt_templates", "PREFILTER_SYSTEM_PROMPT", "Промпты пре-фильтра"),
        ("app.llm.prompt_templates", "format_orchestrator_prompt_ask", "Промпты оркестратора"),
        ("app.llm.prompt_templates", "CODE_GENERATOR_SYSTEM_PROMPT", "Промпты генератора кода"),  # НОВОЕ
        ("app.llm.prompt_templates", "format_code_generator_prompt", "Форматирование промптов генератора"),  # НОВОЕ
        ("app.agents.router", "route_request", "Агент Router"),
        ("app.agents.router", "RouteResult", "Результат роутинга"),
        ("app.agents.pre_filter", "pre_filter_chunks", "Агент Pre-filter"),
        ("app.agents.pre_filter", "PreFilterResult", "Результат пре-фильтра"),
        ("app.agents.orchestrator", "orchestrate", "Агент Orchestrator"),
        ("app.agents.orchestrator", "OrchestratorResult", "Результат оркестратора"),
        ("app.agents.code_generator", "generate_code", "Агент Code Generator"),  # НОВОЕ
        ("app.agents.code_generator", "CodeGeneratorResult", "Результат генератора кода"),  # НОВОЕ
        ("app.agents.code_generator", "CodeBlock", "Блок кода"),  # НОВОЕ
        ("app.tools.tool_executor", "ToolExecutor", "Исполнитель инструментов"),
        ("app.tools.tool_executor", "parse_tool_call", "Парсер вызовов инструментов"),
        ("app.tools.tool_definitions", "ORCHESTRATOR_TOOLS", "Определения инструментов"),
        ("app.services.index_manager", "load_semantic_index", "Менеджер индексов"),
        ("app.services.project_map_builder", "get_project_map_for_prompt", "Построитель карты проекта"),
        ("app.utils.token_counter", "TokenCounter", "Счётчик токенов"),
        ("app.builders.semantic_index_builder", "create_chunks_list_auto", "Создание списка чанков"),
    ]
    
    for module_name, attr_name, description in imports_to_test:
        start = time.time()
        try:
            module = __import__(module_name, fromlist=[attr_name])
            obj = getattr(module, attr_name, None)
            
            if obj is None:
                suite.add(TestResult(
                    name=f"Импорт {description}",
                    passed=False,
                    duration_sec=time.time() - start,
                    error=f"Атрибут '{attr_name}' не найден в {module_name}"
                ))
            else:
                suite.add(TestResult(
                    name=f"Импорт {description}",
                    passed=True,
                    duration_sec=time.time() - start,
                    message=f"{module_name}.{attr_name}"
                ))
        except Exception as e:
            suite.add(TestResult(
                name=f"Импорт {description}",
                passed=False,
                duration_sec=time.time() - start,
                error=str(e)
            ))
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ТЕСТЫ ПОДКЛЮЧЕНИЯ К API
# ============================================================================

async def test_api_connectivity() -> TestSuite:
    """Тестирует подключение к API всех провайдеров"""
    suite = TestSuite(name="Тесты подключения к API")
    suite.start_time = datetime.now()
    
    from config.settings import cfg
    
    # Тест DeepSeek API
    start = time.time()
    try:
        from app.llm.api_client import call_llm
        
        response = await call_llm(
            model=cfg.MODEL_NORMAL,
            messages=[{"role": "user", "content": "Скажи 'OK' и ничего больше."}],
            temperature=0,
            max_tokens=10,
        )
        
        if response and len(response) > 0:
            suite.add(TestResult(
                name="DeepSeek API",
                passed=True,
                duration_sec=time.time() - start,
                message=f"Ответ: {response[:50]}..."
            ))
        else:
            suite.add(TestResult(
                name="DeepSeek API",
                passed=False,
                duration_sec=time.time() - start,
                error="Пустой ответ"
            ))
    except Exception as e:
        suite.add(TestResult(
            name="DeepSeek API",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
    
    # Тест OpenRouter API (Gemini)
    start = time.time()
    try:
        from app.llm.api_client import call_llm
        
        response = await call_llm(
            model=cfg.MODEL_GEMINI_2_FLASH,
            messages=[{"role": "user", "content": "Скажи 'OK' и ничего больше."}],
            temperature=0,
            max_tokens=10,
        )
        
        if response and len(response) > 0:
            suite.add(TestResult(
                name="OpenRouter API (Gemini 2.0 Flash)",
                passed=True,
                duration_sec=time.time() - start,
                message=f"Ответ: {response[:50]}..."
            ))
        else:
            suite.add(TestResult(
                name="OpenRouter API (Gemini 2.0 Flash)",
                passed=False,
                duration_sec=time.time() - start,
                error="Пустой ответ"
            ))
    except Exception as e:
        suite.add(TestResult(
            name="OpenRouter API (Gemini 2.0 Flash)",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ТЕСТ АГЕНТА ROUTER
# ============================================================================

async def test_router(test_queries: Optional[List[str]] = None) -> TestSuite:
    """Тестирует агент Router с примерами запросов"""
    suite = TestSuite(name="Тесты агента Router")
    suite.start_time = datetime.now()
    
    from app.agents.router import route_request
    
    if test_queries is None:
        test_queries = [
            # Простые задачи (должны направляться на Gemini 3 Pro)
            "Добавь логирование в главную функцию",
            "Исправь опечатку в docstring",
            "Добавь новый параметр в класс конфигурации",
            # Сложные задачи (должны направляться на Opus 4.5)
            "Есть race condition в асинхронной обработке файлов, вызывающий повреждение данных",
            "Проанализируй и исправь уязвимость SQL-инъекции в модуле авторизации",
            "Рефакторинг всей кодовой базы для использования паттерна dependency injection",
        ]
    
    for query in test_queries:
        start = time.time()
        try:
            result = await route_request(query)
            
            suite.add(TestResult(
                name=f"Маршрут: {query[:40]}...",
                passed=True,
                duration_sec=time.time() - start,
                message=f"→ {result.orchestrator_model.split('/')[-1]}",
                details={
                    "model": result.orchestrator_model,
                    "reasoning": result.reasoning,
                    "confidence": result.confidence,
                    "risk_level": result.risk_level,
                }
            ))
        except Exception as e:
            suite.add(TestResult(
                name=f"Маршрут: {query[:40]}...",
                passed=False,
                duration_sec=time.time() - start,
                error=str(e)
            ))
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ТЕСТ АГЕНТА PRE-FILTER
# ============================================================================

async def test_pre_filter(project_dir: str) -> TestSuite:
    """Тестирует агент Pre-filter с реальным проектом"""
    suite = TestSuite(name="Тесты агента Pre-filter")
    suite.start_time = datetime.now()
    
    from app.agents.pre_filter import pre_filter_chunks
    from app.services.index_manager import load_semantic_index
    
    # Загрузка индекса
    start = time.time()
    try:
        index = load_semantic_index(project_dir)
        
        if index is None:
            suite.add(TestResult(
                name="Загрузка семантического индекса",
                passed=False,
                duration_sec=time.time() - start,
                error="Индекс не найден. Сначала запустите индексацию."
            ))
            suite.end_time = datetime.now()
            return suite
        
        is_compressed = index.get("compressed", False)
        suite.add(TestResult(
            name="Загрузка семантического индекса",
            passed=True,
            duration_sec=time.time() - start,
            message=f"Загружен {'сжатый' if is_compressed else 'полный'} индекс"
        ))
    except Exception as e:
        suite.add(TestResult(
            name="Загрузка семантического индекса",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        suite.end_time = datetime.now()
        return suite
    
    # Тестирование пре-фильтрации с примерами запросов
    test_queries = [
        "Как API клиент обрабатывает аутентификацию?",
        "Где находится главная точка входа приложения?",
        "Покажи логику подсчёта токенов",
    ]
    
    for query in test_queries:
        start = time.time()
        try:
            result = await pre_filter_chunks(
                user_query=query,
                index=index,
                project_dir=project_dir,
            )
            
            chunk_names = [c.name for c in result.selected_chunks[:3]]
            
            suite.add(TestResult(
                name=f"Пре-фильтр: {query[:35]}...",
                passed=len(result.selected_chunks) > 0,
                duration_sec=time.time() - start,
                message=f"Выбрано {len(result.selected_chunks)} чанков, {result.total_tokens} токенов",
                details={
                    "chunks": chunk_names,
                    "total_tokens": result.total_tokens,
                    "pruned": result.pruned,
                }
            ))
        except Exception as e:
            suite.add(TestResult(
                name=f"Пре-фильтр: {query[:35]}...",
                passed=False,
                duration_sec=time.time() - start,
                error=str(e)
            ))
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ТЕСТ АГЕНТА ORCHESTRATOR
# ============================================================================

async def test_orchestrator(project_dir: str) -> TestSuite:
    """Тестирует агент Orchestrator с реальным проектом"""
    suite = TestSuite(name="Тесты агента Orchestrator")
    suite.start_time = datetime.now()
    
    from app.agents.pre_filter import pre_filter_chunks
    from app.agents.orchestrator import orchestrate
    from app.services.index_manager import load_semantic_index
    from app.services.project_map_builder import get_project_map_for_prompt
    from app.builders.semantic_index_builder import create_chunks_list_auto
    from config.settings import cfg
    
    # Загрузка необходимых данных
    start = time.time()
    try:
        index = load_semantic_index(project_dir)
        if index is None:
            raise ValueError("Индекс не найден")
        
        project_map = get_project_map_for_prompt(project_dir)
        compact_index = create_chunks_list_auto(index)
        
        suite.add(TestResult(
            name="Загрузка данных проекта",
            passed=True,
            duration_sec=time.time() - start,
            message="Индекс, карта проекта и компактный индекс загружены"
        ))
    except Exception as e:
        suite.add(TestResult(
            name="Загрузка данных проекта",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        suite.end_time = datetime.now()
        return suite
    
    # Тестирование оркестрации
    test_query = "Объясни как работает счётчик токенов и предложи улучшения"
    
    # Шаг 1: Пре-фильтр
    start = time.time()
    try:
        prefilter_result = await pre_filter_chunks(
            user_query=test_query,
            index=index,
            project_dir=project_dir,
        )
        
        suite.add(TestResult(
            name="Пре-фильтр для оркестратора",
            passed=len(prefilter_result.selected_chunks) > 0,
            duration_sec=time.time() - start,
            message=f"Выбрано {len(prefilter_result.selected_chunks)} чанков"
        ))
    except Exception as e:
        suite.add(TestResult(
            name="Пре-фильтр для оркестратора",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        suite.end_time = datetime.now()
        return suite
    
    # Шаг 2: Оркестрация
    start = time.time()
    try:
        # Используем простую модель для тестирования
        orchestrator_model = cfg.ORCHESTRATOR_SIMPLE_MODEL
        
        result = await orchestrate(
            user_query=test_query,
            selected_chunks=prefilter_result.selected_chunks,
            compact_index=compact_index,
            history=[],
            orchestrator_model=orchestrator_model,
            project_dir=project_dir,
            index=index,
            project_map=project_map,
        )
        
        has_analysis = len(result.analysis) > 50
        has_instruction = len(result.instruction) > 20
        
        suite.add(TestResult(
            name="Анализ оркестратора",
            passed=has_analysis and has_instruction,
            duration_sec=time.time() - start,
            message=f"Анализ: {len(result.analysis)} симв., Инструкция: {len(result.instruction)} симв.",
            details={
                "tool_calls": len(result.tool_calls),
                "target_file": result.target_file,
                "web_searches_used": result.tool_usage.web_search_count if result.tool_usage else 0,
            }
        ))
        
        # Показываем превью анализа
        if has_analysis:
            print_info("Превью анализа:")
            print(f"{Colors.DIM}{result.analysis[:500]}...{Colors.RESET}")
        
    except Exception as e:
        suite.add(TestResult(
            name="Анализ оркестратора",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ТЕСТ АГЕНТА CODE GENERATOR (НОВОЕ)
# ============================================================================

async def test_code_generator() -> TestSuite:
    """Тестирует агент Code Generator с примерами инструкций"""
    suite = TestSuite(name="Тесты агента Code Generator")
    suite.start_time = datetime.now()
    
    from app.agents.code_generator import generate_code, format_result_for_display
    
    # Тестовые инструкции разной сложности
    test_cases = [
        {
            "name": "Простая функция",
            "instruction": """
**Task:** Create a helper function to validate email addresses
**File:** app/utils/validators.py
**Changes:**
- Create a function `validate_email(email: str) -> bool`
- Use regex pattern for basic email validation
- Return True if valid, False otherwise
**Why:** Need email validation for user registration
            """,
            "file_code": None,
        },
        {
            "name": "Метод класса",
            "instruction": """
**Task:** Add a method to calculate total cost
**File:** app/services/order.py
**Location:** Inside OrderService class
**Changes:**
- Add method `calculate_total(self, items: List[Item]) -> float`
- Sum up item prices with quantity
- Apply 10% discount if total > 100
**Why:** Business logic for order processing
            """,
            "file_code": """
class OrderService:
    def __init__(self, db):
        self.db = db
    
    def get_order(self, order_id: int):
        return self.db.get(order_id)
    
    # Add new method here
            """,
        },
        {
            "name": "Исправление бага",
            "instruction": """
**Task:** Fix the division by zero bug in calculate_average
**File:** app/utils/math_helpers.py
**Location:** calculate_average function
**Changes:**
- Add check for empty list
- Return 0 or raise ValueError for empty input
- Add type hints
**Why:** Prevents crash when processing empty data
            """,
            "file_code": """
def calculate_average(numbers):
    total = sum(numbers)
    return total / len(numbers)
            """,
        },
    ]
    
    for test_case in test_cases:
        start = time.time()
        try:
            result = await generate_code(
                instruction=test_case["instruction"],
                file_code=test_case["file_code"],
            )
            
            # Проверяем результат
            has_code = len(result.code_blocks) > 0
            has_explanation = len(result.explanation) > 10
            
            suite.add(TestResult(
                name=f"Генерация: {test_case['name']}",
                passed=result.success and has_code,
                duration_sec=time.time() - start,
                message=f"Блоков кода: {len(result.code_blocks)}, Пояснение: {len(result.explanation)} симв.",
                details={
                    "success": result.success,
                    "code_blocks_count": len(result.code_blocks),
                    "primary_filepath": result.primary_filepath,
                    "model_used": result.model_used,
                    "has_explanation": has_explanation,
                }
            ))
            
            # Показываем превью кода
            if has_code:
                print_info(f"Превью сгенерированного кода ({test_case['name']}):")
                for block in result.code_blocks[:2]:  # Показываем максимум 2 блока
                    print_code_block(
                        code=block.code[:500] + ("..." if len(block.code) > 500 else ""),
                        language=block.language,
                        filepath=block.filepath
                    )
            
        except Exception as e:
            suite.add(TestResult(
                name=f"Генерация: {test_case['name']}",
                passed=False,
                duration_sec=time.time() - start,
                error=str(e)
            ))
    
    # Тест форматирования для отображения
    start = time.time()
    try:
        from app.agents.code_generator import CodeGeneratorResult, CodeBlock
        
        # Создаём тестовый результат
        test_result = CodeGeneratorResult(
            code_blocks=[
                CodeBlock(
                    code="def hello():\n    print('Hello, World!')",
                    filepath="test.py",
                    language="python"
                )
            ],
            explanation="Простая функция приветствия.",
            success=True,
        )
        
        formatted = format_result_for_display(test_result)
        
        suite.add(TestResult(
            name="Форматирование результата",
            passed=len(formatted) > 0 and "```python" in formatted,
            duration_sec=time.time() - start,
            message=f"Форматированный вывод: {len(formatted)} симв."
        ))
    except Exception as e:
        suite.add(TestResult(
            name="Форматирование результата",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
    
    # Тест сериализации в JSON (для API)
    start = time.time()
    try:
        from app.agents.code_generator import CodeGeneratorResult, CodeBlock
        
        test_result = CodeGeneratorResult(
            code_blocks=[
                CodeBlock(code="print('test')", filepath="test.py")
            ],
            explanation="Test explanation",
            success=True,
            model_used="deepseek-chat",
        )
        
        json_output = test_result.to_dict()
        
        # Проверяем структуру JSON
        required_keys = ["success", "code_blocks", "combined_code", "explanation"]
        has_all_keys = all(key in json_output for key in required_keys)
        
        suite.add(TestResult(
            name="Сериализация в JSON (для фронтенда)",
            passed=has_all_keys,
            duration_sec=time.time() - start,
            message=f"Ключи: {list(json_output.keys())}",
            details=json_output
        ))
    except Exception as e:
        suite.add(TestResult(
            name="Сериализация в JSON (для фронтенда)",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ИНТЕГРАЦИОННЫЙ ТЕСТ (ОБНОВЛЕНО - добавлен шаг Code Generator)
# ============================================================================

async def test_integration(project_dir: str, user_query: str) -> TestSuite:
    """Полный интеграционный тест: Router → Pre-filter → Orchestrator → Code Generator"""
    suite = TestSuite(name="Интеграционный тест (полный пайплайн)")
    suite.start_time = datetime.now()
    
    from config.settings import cfg
    from app.llm.api_client import is_router_enabled
    from app.agents.router import route_request
    from app.agents.pre_filter import pre_filter_chunks
    from app.agents.orchestrator import orchestrate
    from app.agents.code_generator import generate_code, format_result_for_display  # НОВОЕ
    from app.services.index_manager import load_semantic_index
    from app.services.project_map_builder import get_project_map_for_prompt
    from app.builders.semantic_index_builder import create_chunks_list_auto
    
    print_header("ИНТЕГРАЦИОННЫЙ ТЕСТ", "═")
    print_info(f"Проект: {project_dir}")
    print_info(f"Запрос: {user_query}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # ШАГ 1: Загрузка данных проекта
    # ─────────────────────────────────────────────────────────────────────────
    print_step(1, 6, "Загрузка данных проекта...")  # Изменено: 6 шагов вместо 5
    start = time.time()
    
    try:
        index = load_semantic_index(project_dir)
        if index is None:
            raise ValueError("Семантический индекс не найден. Сначала запустите индексацию.")
        
        project_map = get_project_map_for_prompt(project_dir)
        compact_index = create_chunks_list_auto(index)
        
        suite.add(TestResult(
            name="Шаг 1: Загрузка данных проекта",
            passed=True,
            duration_sec=time.time() - start,
            message=f"Индекс: {'сжатый' if index.get('compressed') else 'полный'}"
        ))
        print_success(f"Данные проекта загружены ({time.time() - start:.2f}с)")
    except Exception as e:
        suite.add(TestResult(
            name="Шаг 1: Загрузка данных проекта",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        print_error(f"Не удалось загрузить данные проекта: {e}")
        suite.end_time = datetime.now()
        return suite
    
    # ─────────────────────────────────────────────────────────────────────────
    # ШАГ 2: Маршрутизация (с возможностью ручного выбора)
    # ─────────────────────────────────────────────────────────────────────────
    
    # === [MODIFIED] START: Добавлено меню выбора режима ===
    print_subheader("Настройка Orchestrator")
    print(f"{Colors.CYAN}Выберите режим определения модели:{Colors.RESET}")
    print(" 1. 🤖 Автоматический Router (рекомендуется)")
    print(" 2. 👤 Ручной выбор модели")
    
    mode_choice = input(f"\n{Colors.DIM}Ваш выбор (1-2) [1]:{Colors.RESET} ").strip()
    
    manual_model_selected = None
    
    if mode_choice == "2":
        print(f"\n{Colors.CYAN}Доступные модели (из settings.py):{Colors.RESET}")
        # Получаем список доступных моделей через метод конфига
        # Фильтруем None значения, если они есть
        available_models = [m for m in cfg.get_available_orchestrator_models() if m]
        
        for i, model_id in enumerate(available_models, 1):
            display_name = cfg.get_model_display_name(model_id)
            print(f" {i}. {display_name}")
            
        try:
            m_input = input(f"\n{Colors.DIM}Выберите модель (1-{len(available_models)}):{Colors.RESET} ").strip()
            if m_input.isdigit() and 1 <= int(m_input) <= len(available_models):
                manual_model_selected = available_models[int(m_input) - 1]
                print_info(f"Выбрана модель: {cfg.get_model_display_name(manual_model_selected)}")
            else:
                print_warning("Неверный выбор. Будет использован автоматический роутер.")
        except Exception:
            print_warning("Ошибка выбора. Будет использован автоматический роутер.")
    # === [MODIFIED] END ===

    print_step(2, 6, "Маршрутизация задачи...")
    start = time.time()
    
    try:
        # Логика с учетом ручного выбора
        if manual_model_selected:
            orchestrator_model = manual_model_selected
            
            suite.add(TestResult(
                name="Шаг 2: Маршрутизация (Ручной выбор)",
                passed=True,
                duration_sec=time.time() - start,
                message=f"Пользователь выбрал: {cfg.get_model_display_name(orchestrator_model)}"
            ))
            print_success(f"Используется модель (ручной выбор): {cfg.get_model_display_name(orchestrator_model)}")
            
        elif is_router_enabled():
            router_result = await route_request(user_query)
            orchestrator_model = router_result.orchestrator_model
            
            suite.add(TestResult(
                name="Шаг 2: Маршрутизация",
                passed=True,
                duration_sec=time.time() - start,
                message=f"Направлено на {cfg.get_model_display_name(orchestrator_model)}",
                details={
                    "model": orchestrator_model,
                    "reasoning": router_result.reasoning,
                    "confidence": router_result.confidence,
                    "risk_level": router_result.risk_level,
                }
            ))
            print_success(f"Направлено на: {cfg.get_model_display_name(orchestrator_model)}")
            print_result("Обоснование", router_result.reasoning)
            print_result("Уверенность", f"{router_result.confidence:.2f}")
            print_result("Уровень риска", router_result.risk_level)
        else:
            orchestrator_model = cfg.ORCHESTRATOR_FIXED_MODEL or cfg.ORCHESTRATOR_SIMPLE_MODEL
            suite.add(TestResult(
                name="Шаг 2: Маршрутизация",
                passed=True,
                duration_sec=time.time() - start,
                message=f"Маршрутизатор отключён, используется {cfg.get_model_display_name(orchestrator_model)}"
            ))
            print_warning(f"Маршрутизатор отключён, используется фиксированная модель: {cfg.get_model_display_name(orchestrator_model)}")
    except Exception as e:
        suite.add(TestResult(
            name="Шаг 2: Маршрутизация",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        print_error(f"Ошибка маршрутизации: {e}")
        # Откат на простую модель
        orchestrator_model = cfg.ORCHESTRATOR_SIMPLE_MODEL
        print_warning(f"Откат на {cfg.get_model_display_name(orchestrator_model)}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # ШАГ 3: Пре-фильтр
    # ─────────────────────────────────────────────────────────────────────────
    print_step(3, 6, "Пре-фильтрация релевантных чанков кода...")
    start = time.time()
    
    try:
        prefilter_result = await pre_filter_chunks(
            user_query=user_query,
            index=index,
            project_dir=project_dir,
        )
        
        suite.add(TestResult(
            name="Шаг 3: Пре-фильтр",
            passed=len(prefilter_result.selected_chunks) > 0,
            duration_sec=time.time() - start,
            message=f"Выбрано {len(prefilter_result.selected_chunks)} чанков ({prefilter_result.total_tokens} токенов)",
            details={
                "chunks": [c.name for c in prefilter_result.selected_chunks],
                "total_tokens": prefilter_result.total_tokens,
                "pruned": prefilter_result.pruned,
            }
        ))
        
        print_success(f"Выбрано {len(prefilter_result.selected_chunks)} чанков ({prefilter_result.total_tokens} токенов)")
        for i, chunk in enumerate(prefilter_result.selected_chunks, 1):
            print_result(f"Чанк {i}", f"{chunk.name} ({chunk.chunk_type}) - {chunk.relevance_score:.2f}")
    except Exception as e:
        suite.add(TestResult(
            name="Шаг 3: Пре-фильтр",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        print_error(f"Ошибка пре-фильтра: {e}")
        suite.end_time = datetime.now()
        return suite
    
    # ─────────────────────────────────────────────────────────────────────────
    # ШАГ 4: Оркестратор
    # ─────────────────────────────────────────────────────────────────────────
    print_step(4, 6, f"Анализ с помощью {cfg.get_model_display_name(orchestrator_model)}...")
    start = time.time()
    
    orchestrator_result = None  # Для использования в шаге 5
    
    try:
        orchestrator_result = await orchestrate(
            user_query=user_query,
            selected_chunks=prefilter_result.selected_chunks,
            compact_index=compact_index,
            history=[],
            orchestrator_model=orchestrator_model,
            project_dir=project_dir,
            index=index,
            project_map=project_map,
        )
        
        has_analysis = len(orchestrator_result.analysis) > 50
        has_instruction = len(orchestrator_result.instruction) > 20
        
        suite.add(TestResult(
            name="Шаг 4: Оркестратор",
            passed=has_analysis,
            duration_sec=time.time() - start,
            message=f"Анализ: {len(orchestrator_result.analysis)} симв., Инструкции: {len(orchestrator_result.instruction)} симв.",
            details={
                "tool_calls": len(orchestrator_result.tool_calls),
                "target_file": orchestrator_result.target_file,
                "web_searches": orchestrator_result.tool_usage.web_search_count if orchestrator_result.tool_usage else 0,
            }
        ))
        
        print_success(f"Анализ завершён ({time.time() - start:.2f}с)")
        print_result("Длина анализа", f"{len(orchestrator_result.analysis)} симв.")
        print_result("Длина инструкции", f"{len(orchestrator_result.instruction)} симв.")
        print_result("Вызовы инструментов", str(len(orchestrator_result.tool_calls)))
        if orchestrator_result.target_file:
            print_result("Целевой файл", orchestrator_result.target_file)
        
    except Exception as e:
        suite.add(TestResult(
            name="Шаг 4: Оркестратор",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        print_error(f"Ошибка оркестратора: {e}")
        suite.end_time = datetime.now()
        return suite
    
    # ─────────────────────────────────────────────────────────────────────────
    # ШАГ 5: Генератор кода (НОВОЕ)
    # ─────────────────────────────────────────────────────────────────────────
    print_step(5, 6, "Генерация кода по инструкции оркестратора...")
    start = time.time()
    
    code_result = None

    # === НОВОЕ: Валидация инструкции ===
    instruction = orchestrator_result.instruction

    def validate_instruction(instr: str) -> tuple[bool, str]:
        """Validate instruction before sending to Code Generator"""
        if not instr:
            return False, "Empty instruction"
        
        if len(instr) < 100:
            return False, f"Instruction too short ({len(instr)} chars)"
        
        invalid_markers = [
            "[No separate instruction section found]",
            "[No instruction",
            "[Инструкции отсутствуют]",
        ]
        if any(marker in instr for marker in invalid_markers):
            return False, "Instruction parsing failed"
        
        # Should have file reference
        has_file = any([
            "**File:**" in instr,
            "### FILE:" in instr,
            "FILE:" in instr,
            "app/" in instr,
            "src/" in instr,
        ])
        if not has_file:
            return False, "Missing file specification"
        
        return True, ""

    is_valid, validation_error = validate_instruction(instruction)

    if not is_valid:
        print_warning(f"Инструкция невалидна: {validation_error}")
        print_warning("Попытка извлечь инструкцию из raw_response...")
        
        # Fallback: попробуем извлечь из сырого ответа
        raw = orchestrator_result.raw_response
        if raw and "**Task:**" in raw:
            # Найдем начало инструкции
            task_idx = raw.find("**Task:**")
            instruction = raw[task_idx:].strip()
            print_info(f"Извлечена инструкция из raw_response ({len(instruction)} символов)")
        else:
            suite.add(TestResult(
                name="Шаг 5: Генератор кода",
                passed=False,
                duration_sec=time.time() - start,
                error=f"Invalid instruction: {validation_error}"
            ))
            print_error(f"Не удалось извлечь валидную инструкцию")
            suite.end_time = datetime.now()
            return suite
    
    
    try:
        # Получаем код целевого файла, если он указан
        file_code = None
        if orchestrator_result.target_file:
            target_path = Path(project_dir) / orchestrator_result.target_file
            if target_path.exists():
                file_code = target_path.read_text(encoding='utf-8')
                print_info(f"Загружен целевой файл: {orchestrator_result.target_file}")
        
        code_result = await generate_code(
            instruction=orchestrator_result.instruction,
            file_code=file_code,
            target_file=orchestrator_result.target_file,
        )
        
        suite.add(TestResult(
            name="Шаг 5: Генератор кода",
            passed=code_result.success and len(code_result.code_blocks) > 0,
            duration_sec=time.time() - start,
            message=f"Блоков кода: {len(code_result.code_blocks)}, Модель: {code_result.model_used}",
            details={
                "success": code_result.success,
                "code_blocks": len(code_result.code_blocks),
                "primary_filepath": code_result.primary_filepath,
                "explanation_length": len(code_result.explanation),
            }
        ))
        
        if code_result.success:
            print_success(f"Код сгенерирован ({time.time() - start:.2f}с)")
            print_result("Блоков кода", str(len(code_result.code_blocks)))
            if code_result.primary_filepath:
                print_result("Целевой файл", code_result.primary_filepath)
        else:
            print_error(f"Ошибка генерации: {code_result.error}")
        
    except Exception as e:
        suite.add(TestResult(
            name="Шаг 5: Генератор кода",
            passed=False,
            duration_sec=time.time() - start,
            error=str(e)
        ))
        print_error(f"Ошибка генератора кода: {e}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # ШАГ 6: Отображение результатов (ОБНОВЛЕНО)
    # ─────────────────────────────────────────────────────────────────────────
    print_step(6, 6, "Отображение результатов...")
    
    suite.add(TestResult(
        name="Шаг 6: Отображение результатов",
        passed=True,
        duration_sec=0,
        message="Результаты показаны ниже"
    ))
    
    print_header("АНАЛИЗ ОРКЕСТРАТОРА", "─")
    print(orchestrator_result.analysis)
    
    print_header("ИНСТРУКЦИИ ДЛЯ ГЕНЕРАТОРА КОДА", "─")
    print(orchestrator_result.instruction)
    
    # НОВОЕ: Отображение сгенерированного кода
    if code_result and code_result.success:
        print_header("СГЕНЕРИРОВАННЫЙ КОД", "─")
        for i, block in enumerate(code_result.code_blocks, 1):
            if len(code_result.code_blocks) > 1:
                print(f"\\n{Colors.CYAN}--- Блок {i} ---{Colors.RESET}")
            print_code_block(block.code, block.language, block.filepath)
        
        if code_result.explanation:
            print_header("ПОЯСНЕНИЯ К КОДУ", "─")
            print(code_result.explanation)
        
        # Показываем JSON-представление для фронтенда
        print_header("JSON ДЛЯ ФРОНТЕНДА (превью)", "─")
        json_preview = code_result.to_dict()
        # Укорачиваем код для превью
        for block in json_preview.get("code_blocks", []):
            if len(block.get("code", "")) > 200:
                block["code"] = block["code"][:200] + "..."
        if len(json_preview.get("combined_code", "")) > 200:
            json_preview["combined_code"] = json_preview["combined_code"][:200] + "..."
        print_json(json_preview)
    
    if orchestrator_result.tool_calls:
        print_header("ВЫПОЛНЕННЫЕ ВЫЗОВЫ ИНСТРУМЕНТОВ", "─")
        for i, tc in enumerate(orchestrator_result.tool_calls, 1):
            status = "✅" if tc.success else "❌"
            print(f"{status} {i}. {tc.name}({', '.join(f'{k}={v}' for k, v in list(tc.arguments.items())[:2])})")
            if not tc.success:
                print(f"   Ошибка: {tc.output[:200]}...")
    
    if orchestrator_result and code_result and code_result.success:
        try:
            # Подготовка данных для отчета
            model_info = {
                "orchestrator": cfg.get_model_display_name(orchestrator_model),
                "code_generator": code_result.model_used
            }
            
            # Конвертация code_blocks в сериализуемый формат
            code_blocks_data = [
                {
                    "filepath": block.filepath,
                    "language": block.language,
                    "code": block.code,
                    "context": block.context
                }
                for block in code_result.code_blocks
            ]
            
            # Сохранение отчета
            total_duration = suite.duration if hasattr(suite, 'duration') else 0
            report_path = save_json_report(
                project_dir=project_dir,
                user_query=user_query,
                orchestrator_analysis=orchestrator_result.analysis,
                orchestrator_instruction=orchestrator_result.instruction,
                code_blocks=code_blocks_data,
                code_explanation=code_result.explanation,
                frontend_json=code_result.to_dict(),
                model_info=model_info,
                duration=total_duration
            )
            
            print_success(f"JSON отчет сохранен: {report_path}")
            
        except Exception as e:
            print_warning(f"Не удалось сохранить JSON отчет: {e}")    
    
        try:
            # Подготовка данных для отчета
            model_info = {
                "orchestrator": cfg.get_model_display_name(orchestrator_model),
                "code_generator": code_result.model_used
            }
            
            # Конвертация code_blocks в сериализуемый формат
            code_blocks_data = [
                {
                    "filepath": block.filepath,
                    "language": block.language,
                    "code": block.code,
                    "context": block.context
                }
                for block in code_result.code_blocks
            ]
            
            # Сохранение JSON отчета
            total_duration = suite.duration if hasattr(suite, 'duration') else 0
            report_path = save_json_report(
                project_dir=project_dir,
                user_query=user_query,
                orchestrator_analysis=orchestrator_result.analysis,
                orchestrator_instruction=orchestrator_result.instruction,
                code_blocks=code_blocks_data,
                code_explanation=code_result.explanation,
                frontend_json=code_result.to_dict(),
                model_info=model_info,
                duration=total_duration
            )
            
            print_success(f"JSON отчет сохранен: {report_path}")
            
            # === ДОБАВЛЯЕМ: Сохранение Markdown отчета ===
            try:
                markdown_path = save_markdown_report(
                    project_dir=project_dir,
                    user_query=user_query,
                    orchestrator_analysis=orchestrator_result.analysis,
                    orchestrator_instruction=orchestrator_result.instruction,
                    code_blocks=code_blocks_data,
                    code_explanation=code_result.explanation,
                    model_info=model_info,
                    tool_calls=orchestrator_result.tool_calls if orchestrator_result else None,
                    duration=total_duration
                )
                if markdown_path:
                    print_success(f"Markdown отчет сохранен: {markdown_path}")
            except Exception as md_error:
                # Если Markdown не сохранился, продолжаем работу (JSON уже сохранен)
                print_warning(f"Markdown отчет не создан: {md_error}")
            # ============================================\n            
        except Exception as e:
            print_warning(f"Не удалось сохранить отчеты: {e}")
    
    suite.end_time = datetime.now()
    return suite


# ============================================================================
# ИНТЕРАКТИВНОЕ МЕНЮ (ОБНОВЛЕНО)
# ============================================================================

def print_menu():
    """Печатает главное меню"""
    print_header("AI CODE AGENT - НАБОР ТЕСТОВ", "═")
    print(f"""
{Colors.CYAN}Выберите тест для запуска:{Colors.RESET}

  {Colors.BOLD}1.{Colors.RESET} Проверка импортов     - Проверка корректности всех импортов
  {Colors.BOLD}2.{Colors.RESET} Подключение к API     - Тест соединения с DeepSeek и OpenRouter
  {Colors.BOLD}3.{Colors.RESET} Агент Router          - Тест классификации сложности задач
  {Colors.BOLD}4.{Colors.RESET} Агент Pre-filter      - Тест выбора чанков (требуется проиндексированный проект)
  {Colors.BOLD}5.{Colors.RESET} Агент Orchestrator    - Тест анализа кода (требуется проиндексированный проект)
  {Colors.BOLD}6.{Colors.RESET} Агент Code Generator  - Тест генерации кода по инструкции (НОВОЕ)
  {Colors.BOLD}7.{Colors.RESET} Интеграционный тест   - Полный тест пайплайна (требуется проиндексированный проект)
  
  {Colors.BOLD}0.{Colors.RESET} Выход

{Colors.DIM}Для тестов 4, 5, 7 требуется директория проекта с существующим индексом.{Colors.RESET}
{Colors.DIM}Тест 6 (Code Generator) не требует проект - использует тестовые инструкции.{Colors.RESET}
""")


def select_directory() -> Optional[str]:
    """Интерактивный выбор директории"""
    print_subheader("Выбор директории проекта")
    
    # Предлагаем несколько распространённых путей
    suggestions = [
        str(PROJECT_ROOT),  # Текущий проект
        str(Path.home() / "projects"),
        str(Path.cwd()),
    ]
    
    print("Варианты:")
    for i, path in enumerate(suggestions, 1):
        exists = "✓" if Path(path).exists() else "✗"
        has_index = "📑" if (Path(path) / ".ai-agent" / "semantic_index.json").exists() else "  "
        print(f"  {i}. [{exists}] {has_index} {path}")
    
    print(f"\n{Colors.DIM}Введите номер (1-{len(suggestions)}) или полный путь:{Colors.RESET}")
    
    choice = input("> ").strip()
    
    if choice.isdigit() and 1 <= int(choice) <= len(suggestions):
        path = suggestions[int(choice) - 1]
    else:
        path = choice
    
    # Проверка
    path_obj = Path(path).resolve()
    
    if not path_obj.exists():
        print_error(f"Директория не существует: {path}")
        return None
    
    if not path_obj.is_dir():
        print_error(f"Это не директория: {path}")
        return None
    
    # Проверка наличия индекса
    index_path = path_obj / ".ai-agent" / "semantic_index.json"
    if not index_path.exists():
        print_warning(f"Семантический индекс не найден в {path}")
        print_info("Возможно, нужно сначала запустить индексацию: python scripts/test_semantic_index.py")
        
        confirm = input("Продолжить всё равно? (y/n): ").strip().lower()
        if confirm != 'y':
            return None
    else:
        print_success(f"Семантический индекс найден в {path}")
    
    return str(path_obj)


def get_user_query() -> str:
    """Получение запроса пользователя для интеграционного теста"""
    print_subheader("Введите ваш запрос")
    
    suggestions = [
        "Объясни как API клиент обрабатывает ошибки",
        "Как работает счётчик токенов?",
        "Найди и исправь потенциальные баги в главной функции",
        "Предложи улучшения для системы логирования",
    ]
    
    print("Варианты:")
    for i, q in enumerate(suggestions, 1):
        print(f"  {i}. {q}")
    
    print(f"\n{Colors.DIM}Введите номер (1-{len(suggestions)}) или свой запрос:{Colors.RESET}")
    
    choice = input("> ").strip()
    
    if choice.isdigit() and 1 <= int(choice) <= len(suggestions):
        return suggestions[int(choice) - 1]
    
    return choice if choice else suggestions[0]


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ (ОБНОВЛЕНО)
# ============================================================================

async def main():
    """Главная точка входа"""
    setup_logging(verbose=False)
    
    all_suites: List[TestSuite] = []
    
    while True:
        print_menu()
        
        choice = input(f"{Colors.CYAN}Введите выбор (0-7): {Colors.RESET}").strip()  # Изменено: 0-7
        
        if choice == "0":
            print_info("Выход...")
            break
        
        elif choice == "1":
            # Тесты импортов
            print_header("ПРОВЕРКА ИМПОРТОВ")
            suite = test_imports()
            suite.print_summary()
            all_suites.append(suite)
        
        elif choice == "2":
            # Подключение к API
            print_header("ТЕСТ ПОДКЛЮЧЕНИЯ К API")
            suite = await test_api_connectivity()
            suite.print_summary()
            all_suites.append(suite)
        
        elif choice == "3":
            # Тест Router
            print_header("ТЕСТ АГЕНТА ROUTER")
            suite = await test_router()
            suite.print_summary()
            all_suites.append(suite)
            
            # Показываем детальные результаты
            print_subheader("Детальные результаты Router")
            for r in suite.results:
                if r.details:
                    print(f"\n{Colors.BOLD}{r.name}{Colors.RESET}")
                    print_json(r.details)
        
        elif choice == "4":
            # Тест Pre-filter
            project_dir = select_directory()
            if project_dir:
                print_header("ТЕСТ АГЕНТА PRE-FILTER")
                suite = await test_pre_filter(project_dir)
                suite.print_summary()
                all_suites.append(suite)
        
        elif choice == "5":
            # Тест Orchestrator
            project_dir = select_directory()
            if project_dir:
                print_header("ТЕСТ АГЕНТА ORCHESTRATOR")
                suite = await test_orchestrator(project_dir)
                suite.print_summary()
                all_suites.append(suite)
        
        elif choice == "6":
            # НОВОЕ: Тест Code Generator
            print_header("ТЕСТ АГЕНТА CODE GENERATOR")
            suite = await test_code_generator()
            suite.print_summary()
            all_suites.append(suite)
            
            # Показываем детальные результаты
            print_subheader("Детальные результаты Code Generator")
            for r in suite.results:
                if r.details:
                    print(f"\n{Colors.BOLD}{r.name}{Colors.RESET}")
                    print_json(r.details)
        
        elif choice == "7":
            # Интеграционный тест (был 6, теперь 7)
            project_dir = select_directory()
            if project_dir:
                user_query = get_user_query()
                suite = await test_integration(project_dir, user_query)
                suite.print_summary()
                all_suites.append(suite)
        
        else:
            print_error("Неверный выбор. Введите число от 0 до 7.")  # Изменено: 0-7
        
        # Ожидание пользователя перед возвратом в меню
        input(f"\n{Colors.DIM}Нажмите Enter для продолжения...{Colors.RESET}")
    
    # Итоговая сводка
    if all_suites:
        print_header("ИТОГОВАЯ СВОДКА ТЕСТОВ", "═")
        
        total_passed = sum(s.passed for s in all_suites)
        total_failed = sum(s.failed for s in all_suites)
        total_duration = sum(s.duration for s in all_suites)
        
        for suite in all_suites:
            status = f"{Colors.GREEN}УСПЕХ{Colors.RESET}" if suite.failed == 0 else f"{Colors.RED}ОШИБКА{Colors.RESET}"
            print(f"  [{status}] {suite.name}: {suite.passed}/{suite.total} ({suite.duration:.2f}с)")
        
        print()
        color = Colors.GREEN if total_failed == 0 else Colors.RED
        print(f"  {color}{Colors.BOLD}ВСЕГО: {total_passed}/{total_passed + total_failed} тестов пройдено{Colors.RESET}")
        print(f"  {Colors.DIM}Общее время: {total_duration:.2f}с{Colors.RESET}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Прервано пользователем{Colors.RESET}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.RED}Критическая ошибка: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)