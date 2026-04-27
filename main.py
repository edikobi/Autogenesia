# main.py
"""
Autogenesia - Главная точка входа

Полнофункциональный CLI для AI-помощника в разработке кода:
- Режим Вопросов (Ask): Анализ кода и рекомендации
- Режим Агента (Agent): Автономная генерация кода с валидацией

В каждом режиме можно работать с:
- Существующим проектом (с автоматической индексацией)
- Новым проектом (индексация после применения изменений)

Также доступен:
- Общий Чат: Универсальный помощник без кодовой базы
  - Обычный режим
  - Legal режим (для юридических вопросов)

Возможности:
- Постоянная история диалогов с умным сжатием
- Продолжение существующих диалогов после выхода
- Отображение процесса мышления агента в реальном времени
- Выбор и управление проектами
- Красивый терминальный интерфейс на базе Rich
"""

from __future__ import annotations
from app.agents.pre_filter import analyze_query, PreFilterMode, PreFilterAdvice
from app.llm.prompt_templates import (
    _build_prefilter_advice_section,
    _build_prefilter_advice_section_agent,
    _build_prefilter_advice_section_new_project_agent,
)


import re
import os
import sys
if sys.platform == 'win32':
    # Set console code page to UTF-8
    os.system('chcp 65001 > nul 2>&1')
    
    # Environment variables for child processes (mypy, pip, pytest, etc.)
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    
    # Reconfigure stdout/stderr to UTF-8 with error handling
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

# [FIX] Патчим asyncio для работы вложенных циклов событий (проблема с web_search в General Chat)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

import asyncio
import logging
import signal
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from datetime import datetime

# Rich для красивого терминального интерфейса
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.tree import Tree
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.syntax import Syntax
from rich.layout import Layout
from rich import box

# Импорты проекта
from config.settings import cfg
from app.history.manager import HistoryManager
from app.history.storage import Thread, Message
from app.utils.token_counter import TokenCounter


from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.agent_pipeline import AgentPipeline





# ============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ С ДЕТАЛЬНЫМ ЗАХВАТОМ ОШИБОК
# ============================================================================

# Создаём директорию для логов
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


class DetailedFormatter(logging.Formatter):
    """Форматтер с детальной информацией об ошибках"""
    
    def format(self, record):
        # Базовое форматирование
        result = super().format(record)
        
        # Добавляем traceback для ошибок
        if record.exc_info:
            result += f"\n{'='*60}\nПОЛНЫЙ TRACEBACK:\n{'='*60}\n"
            result += ''.join(traceback.format_exception(*record.exc_info))
        
        return result


# Настройка логгера
log_formatter = DetailedFormatter(
    '%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Файловый хендлер для всех логов
file_handler = logging.FileHandler(
    LOG_DIR / f'agent_{datetime.now().strftime("%Y%m%d")}.log',
    encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(log_formatter)

# Отдельный файл для ошибок AI
ai_error_handler = logging.FileHandler(
    LOG_DIR / f'ai_errors_{datetime.now().strftime("%Y%m%d")}.log',
    encoding='utf-8'
)
ai_error_handler.setLevel(logging.ERROR)
ai_error_handler.setFormatter(log_formatter)

# Консольный хендлер (только если DEBUG)
console_handler = logging.StreamHandler() if os.getenv('DEBUG') else logging.NullHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(log_formatter)

# Корневой логгер
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[file_handler, console_handler]
)

# Специальный логгер для AI ошибок
ai_logger = logging.getLogger('ai_errors')
ai_logger.addHandler(ai_error_handler)
ai_logger.setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


# Импорт логгера валидации
try:
    from app.utils.validation_logger import ValidationLogger, VALIDATION_LOG_DIR
    VALIDATION_LOGGING_AVAILABLE = True
except ImportError:
    VALIDATION_LOGGING_AVAILABLE = False
    VALIDATION_LOG_DIR = Path("logs/validation")

# Консоль
console = Console()

# Импорт переводчика
try:
    from app.utils.translator import (
        translate_to_russian,
        translate_sync,
        translate_thinking,
        translate_validator_verdict,
        translate_analysis,
        is_mostly_russian,
        check_translation_available,
        reset_translation_state,
    )
    TRANSLATION_AVAILABLE = check_translation_available()
    if TRANSLATION_AVAILABLE:
        logger.info("Translation module loaded and available")
    else:
        logger.warning("Translation module loaded but not configured properly")
except ImportError as e:
    logger.warning(f"Translation module not available: {e}")
    TRANSLATION_AVAILABLE = False
    # Заглушки если модуль недоступен
    async def translate_to_russian(text, context=""): return text
    def translate_sync(text, context=""): return text
    async def translate_thinking(text): return text
    async def translate_validator_verdict(text): return text
    async def translate_analysis(text): return text
    def is_mostly_russian(text): return True
    def check_translation_available(): return False
    def reset_translation_state(): pass



# ============================================================================
# СПЕЦИАЛЬНЫЕ ИСКЛЮЧЕНИЯ ДЛЯ НАВИГАЦИИ
# ============================================================================

class BackToMenuException(Exception):
    """Исключение для возврата в главное меню"""
    pass


class BackException(Exception):
    """Исключение для возврата на шаг назад"""
    pass


class QuitException(Exception):
    """Исключение для выхода из программы"""
    pass


# ============================================================================
# ФУНКЦИЯ ЛОГИРОВАНИЯ ОШИБОК AI
# ============================================================================

def log_ai_error(
    error: Exception,
    context: str,
    model: str = None,
    request_data: Dict = None,
    response_data: Any = None
):
    """
    Логирует ошибки, связанные с AI операциями
    
    Args:
        error: Исключение
        context: Контекст операции (например, "router", "pre_filter", "orchestrator")
        model: Используемая модель
        request_data: Данные запроса (будут усечены)
        response_data: Данные ответа (будут усечены)
    """
    error_info = {
        "timestamp": datetime.now().isoformat(),
        "context": context,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "model": model or "unknown",
    }
    
    # Усекаем данные запроса для логирования
    if request_data:
        if isinstance(request_data, dict):
            truncated_request = {}
            for k, v in request_data.items():
                if isinstance(v, str) and len(v) > 500:
                    truncated_request[k] = v[:500] + "... [УСЕЧЕНО]"
                else:
                    truncated_request[k] = v
            error_info["request_preview"] = truncated_request
    
    # Усекаем ответ
    if response_data:
        resp_str = str(response_data)
        if len(resp_str) > 1000:
            error_info["response_preview"] = resp_str[:1000] + "... [УСЕЧЕНО]"
        else:
            error_info["response_preview"] = resp_str
    
    # Логируем в специальный файл
    ai_logger.error(
        f"AI ОШИБКА в {context}:\n"
        f"  Модель: {error_info['model']}\n"
        f"  Тип: {error_info['error_type']}\n"
        f"  Сообщение: {error_info['error_message']}\n"
        f"  Данные: {error_info}",
        exc_info=True
    )
    
    # Также в основной лог
    logger.error(f"AI ошибка [{context}]: {error}", exc_info=True)


def log_pipeline_stage(
    stage: str,
    message: str,
    data: Optional[Dict] = None,
    error: Optional[Exception] = None
):
    """
    Логирует этап pipeline в основной лог и в консоль (если DEBUG).
    
    Args:
        stage: Название этапа (ORCHESTRATOR, CODE_GEN, VALIDATION, etc.)
        message: Сообщение
        data: Дополнительные данные
        error: Исключение (если есть)
    """
    log_msg = f"[PIPELINE:{stage}] {message}"
    
    if data:
        # Усекаем большие данные
        truncated_data = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 500:
                truncated_data[k] = v[:500] + "...[TRUNCATED]"
            elif isinstance(v, (list, dict)) and len(str(v)) > 500:
                truncated_data[k] = f"[{type(v).__name__} with {len(v)} items]"
            else:
                truncated_data[k] = v
        log_msg += f" | Data: {truncated_data}"
    
    if error:
        logger.error(log_msg, exc_info=True)
        ai_logger.error(log_msg, exc_info=True)
    else:
        logger.info(log_msg)
    
    # В DEBUG режиме также выводим в консоль
    if os.getenv('DEBUG'):
        console.print(f"[dim][{stage}] {message}[/]")


# ============================================================================
# КОНСТАНТЫ
# ============================================================================

APP_NAME = "Autogenesia"
APP_VERSION = "0.0.1"
DEFAULT_USER_ID = "default_user"

# Специальные команды для навигации
BACK_COMMANDS = {'0', 'назад', 'back', 'b', 'н'}
MENU_COMMANDS = {'меню', 'menu', 'm', 'м'}
QUIT_COMMANDS = {'q', 'quit', 'exit', 'выход', 'в'}

# Цвета интерфейса
COLORS = {
    "primary": "cyan",
    "secondary": "blue",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "info": "white",
    "muted": "dim white",
    "accent": "magenta",
}

# Доступные модели оркестратора для выбора С ПОДРОБНЫМИ ОПИСАНИЯМИ
# Формат: (key, model_id, short_name, description)
AVAILABLE_ORCHESTRATOR_MODELS = [
    (
        "1",
        cfg.MODEL_GPT_5_2_Codex,
        "GPT-5.2 Codex",
        "Новая модель от OpenAI, отзывы очень хорошие."
    ),
    (
        "2",
        cfg.MODEL_SONNET_4_5,
        "Claude Sonnet 4.5",
        "Рабочая лошадка. Хорошо работает с инструментами, неплохо анализирует."
    ),
    (
        "3",
        cfg.MODEL_SONNET_4_6,
        "Claude Sonnet 4.6",
        "Новая рабочая лошадка, говорят, лучше прошлой."
    ),
    (
        "4",
        cfg.MODEL_OPUS_4_5,
        "Claude Opus 4.5",
        "Гигант мысли! Только для ОЧЕНЬ серьёзных задач. Очень дорогой! Также контекстное окно всего 200к токенов"
    ),
    (
        "5",
        cfg.MODEL_OPUS_4_6,
        "Claude Opus 4.6",
        "Гигант мысли! Только для ОЧЕНЬ серьёзных задач. Очень дорогой! Контекстное окно  1 млн. токенов"
    ),
    (
        "6",
        cfg.MODEL_GEMINI_3_PRO,
        "✨ Gemini 3.1 Pro",
        "Сложная модель, но исполнительная. Огромное окно 1 млн токенов. Не особо любит пользоваться инструментами. Относительно дешёвая."
    ),
    (
        "7",
        cfg.MODEL_DEEPSEEK_REASONER,
        "DeepSeek V3.2 Reasoning",
        "Неплохо думает, но маленькое контекстное окно и слегка 'туповат' для сложных задач. В новых проектах может быть хорош. ОЧЕНЬ дешёвый!"
    ),
    (
        "8",
        cfg.MODEL_Kimi_K_2_6,
        "Kimi K 2.6",
        "Очень неплохой китайский ИИ"
    ),
    (
        "9",
        cfg.MODEL_QWEN_3_6_Plus_Preview,
        "Qwen3.6 Plus",
        "ИИ от китайцев, дешевый с неплохим знанием кода"
    ),
    (
        "10",
        cfg.MODEL_Xiaomi_MiMo_V2_5_PRO,
        "Xiaomi: MiMo-V2.5-Pro",
        "Нахваливают эту модель, но должна быть хотя бы топ за свои деньги"
    ),
    
    (
        "11",
        cfg.MODEL_GLM_5_1,
        "GLM 5.1",
        "Маленький контекст, хз что еще сказать"
    ),
    
    (
        "12",
        cfg.MODEL_MiniMax_M2_7,
        "MiniMAX M2.7",
        "Надежд на это мало"
    ),
]

# 

# Доступные модели генератора для выбора С ПОДРОБНЫМИ ОПИСАНИЯМИ
# Формат: (key, model_id, short_name, description)
AVAILABLE_GENERATOR_MODELS = [
    (
        "1",
        cfg.MODEL_NORMAL,
        "DeepSeek Chat",
        "Базовая модель. Быстрая, дешёвая, хорошо справляется с простыми задачами генерации(Маленькое контекстное окно, особенно исходящее)."
    ),
    (
        "2",
        cfg.MODEL_GLM_5_Turbo,
        "GLM 5.0 Turbo",
        "Китайская модель от Zhipu AI."
    ),
    (
        "3",
        cfg.MODEL_HAIKU_4_5,
        "Claude Haiku 4.5",
        "Лёгкая модель от Anthropic. Самая лучшая и испольнительная, но и дороже всех."
    ),

    (
        "4",
        cfg.MODEL_GEMINI_3_FLASH,
        "Gemini 3.0 flash",
        "(крайне рекомендуется)Быстрая модель Google через OpenRouter. Хорошо подходит для генерации кода"
    ),

    (
        "5",
        cfg.MODEL_GPT_5_1_Codex_MINI,
        "GPT-5.1-Codex-Mini",
        "Младшая модель CODEX от OpenAI, минимально думает"
    ),
    
    (
        "6",
        cfg.MODEL_Grok_4_20,
        "Grok 4.20",
        "Контекстное окно 2 млн., хз что еще."
    ),

    (
        "7",
        cfg.MODEL_Nemotron_3_Plus_Super,
        "Nemotron 3 Super",
        "Халява от NVIDIA, но вроде обещают много агентов внутри одной модели"
    ),

    (
        "8",
        cfg.MODEL_QWEN3_MAX_THINKING,
        "Qwen3 Coder Next",
        "Это открытая причинно-следственная языковая модель, оптимизированная для программистов и локальных рабочих процессов разработки"
    ),

]


# ============================================================================
# СОСТОЯНИЕ ПРИЛОЖЕНИЯ
# ============================================================================

class AppState:
    """Глобальное состояние приложения"""
    
    def __init__(self):
        self.user_id: str = DEFAULT_USER_ID
        self.current_thread: Optional[Thread] = None
        self.project_dir: Optional[str] = None
        self.project_index: Optional[Dict[str, Any]] = None
        self.history_manager: Optional[HistoryManager] = None
        self.pipeline: Optional[Any] = None
        self.mode: str = "ask"  # ask, agent, general
        self.is_new_project: bool = False  # Новый проект (без индексации до применения)
        self.is_legal_mode: bool = False  # Legal режим для General Chat
        self.running: bool = True
        
        # Настройки выбора модели оркестратора
        self.use_router: bool = cfg.ROUTER_ENABLED  # Использовать роутер или фикс. модель
        self.fixed_orchestrator_model: Optional[str] = None  # Фиксированная модель
        
        # NEW: Настройки модели генератора
        self.generator_model: str = cfg.AGENT_MODELS.get("code_generator", cfg.MODEL_NORMAL)
        
        # Кэш сообщений сессии в памяти
        self.session_messages: List[Dict[str, str]] = []
        
        # Статистика
        self.total_tokens_used: int = 0
        self.messages_count: int = 0
        
        # Прикреплённые файлы для General Chat
        self.attached_files: List[Dict[str, Any]] = []
        
        # NEW: Настройка проверки типов
        self.enable_type_checking: bool = False
        
                # NEW: Настройки Pre-filter
        self.prefilter_mode: str = "normal"  # "normal" или "advanced"
        self.prefilter_model: Optional[str] = None  # Модель для Pre-filter (None = из конфига)

        self._saved_file_names: set = set()  # Отслеживание сохранённых файлов

    
    def reset_session(self):
        """Сброс состояния сессии (сохраняет тред)"""
        self.session_messages = []
        self.total_tokens_used = 0
        self.messages_count = 0
        self.attached_files = []
        self._saved_file_names = set()
    
    def reset_project(self):
        """Полный сброс проекта"""
        self.project_dir = None
        self.project_index = None
        self.is_new_project = False
        self.is_legal_mode = False
        self.pipeline = None
        self.reset_session()
    
    def get_current_orchestrator_model(self) -> Optional[str]:
        """Возвращает текущую модель оркестратора (или None если роутер)"""
        if self.use_router:
            return None
        return self.fixed_orchestrator_model or cfg.ORCHESTRATOR_SIMPLE_MODEL
    
    def get_current_generator_model(self) -> str:
        """Возвращает текущую модель генератора"""
        return self.generator_model


# Глобальное состояние
state = AppState()

def load_user_settings() -> Dict[str, Any]:
    """
    Загружает пользовательские настройки из файла data/user_settings.json.
    Если файл не существует, возвращает настройки по умолчанию.
    
    Returns:
        Dict с настройками пользователя
    """
    settings_path = Path("data/user_settings.json")
    
    default_settings = {
        "prefilter_mode": cfg.PREFILTER_DEFAULT_MODE,
        "prefilter_model": cfg.AGENT_MODELS.get("pre_filter", cfg.MODEL_NORMAL),
        "generator_model": cfg.AGENT_MODELS.get("code_generator", cfg.MODEL_NORMAL),
        "use_router": cfg.ROUTER_ENABLED,
        "fixed_orchestrator_model": None,
        "enable_type_checking": False,
    }
    
    if not settings_path.exists():
        return default_settings
    
    try:
        import json
        with open(settings_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        
        # Мержим с дефолтами (на случай новых полей)
        for key, value in default_settings.items():
            if key not in saved:
                saved[key] = value
        
        logger.info(f"Loaded user settings from {settings_path}")
        return saved
        
    except Exception as e:
        logger.warning(f"Failed to load user settings: {e}")
        return default_settings

def save_user_settings(settings: Dict[str, Any]) -> bool:
    """
    Сохраняет пользовательские настройки в файл data/user_settings.json.
    
    Args:
        settings: Словарь с настройками
        
    Returns:
        True если сохранение успешно
    """
    settings_path = Path("data/user_settings.json")
    
    try:
        # Создаём директорию если не существует
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        
        import json
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved user settings to {settings_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save user settings: {e}")
        return False

def get_current_user_settings() -> Dict[str, Any]:
    """
    Возвращает текущие настройки из state в формате для сохранения.
    
    Returns:
        Dict с текущими настройками
    """
    return {
        "prefilter_mode": state.prefilter_mode,
        "prefilter_model": state.prefilter_model,
        "generator_model": state.generator_model,
        "use_router": state.use_router,
        "fixed_orchestrator_model": state.fixed_orchestrator_model,
        "enable_type_checking": state.enable_type_checking,
    }


def apply_user_settings(settings: Dict[str, Any]) -> None:
    """
    Применяет загруженные настройки к глобальному state.
    
    Args:
        settings: Словарь с настройками
    """
    state.prefilter_mode = settings.get("prefilter_mode", "normal")
    state.prefilter_model = settings.get("prefilter_model")
    state.generator_model = settings.get("generator_model", cfg.MODEL_NORMAL)
    state.use_router = settings.get("use_router", True)
    state.fixed_orchestrator_model = settings.get("fixed_orchestrator_model")
    state.enable_type_checking = settings.get("enable_type_checking", False)
    
    logger.info(f"Applied user settings: prefilter_mode={state.prefilter_mode}, prefilter_model={state.prefilter_model}")

# ============================================================================
# УТИЛИТЫ НАВИГАЦИИ
# ============================================================================

def check_navigation(user_input: str) -> None:
    """
    Проверяет ввод на команды навигации и выбрасывает соответствующее исключение.
    
    Args:
        user_input: Введённая пользователем строка
        
    Raises:
        BackException: если пользователь хочет вернуться назад
        BackToMenuException: если пользователь хочет в главное меню
        QuitException: если пользователь хочет выйти
    """
    cleaned = user_input.strip().lower()
    
    if cleaned in BACK_COMMANDS:
        raise BackException()
    
    if cleaned in MENU_COMMANDS:
        raise BackToMenuException()
    
    if cleaned in QUIT_COMMANDS:
        raise QuitException()


def print_navigation_hint(show_back: bool = True, show_menu: bool = True):
    """Выводит подсказку по навигации"""
    hints = []
    if show_back:
        hints.append("[0] Назад")
    if show_menu:
        hints.append("[меню] Главное меню")
    hints.append("[q] Выход")
    
    console.print(f"[dim]{' │ '.join(hints)}[/]\n")


def prompt_with_navigation(
    prompt_text: str,
    choices: List[str] = None,
    default: str = None,
    show_back: bool = True,
    show_menu: bool = True,
    show_quit: bool = True
) -> str:
    """
    Обёртка над Prompt.ask с поддержкой навигации.
    
    Args:
        prompt_text: Текст приглашения
        choices: Допустимые варианты ответа
        default: Значение по умолчанию
        show_back: Показывать подсказку "Назад"
        show_menu: Показывать подсказку "Меню"
        show_quit: Показывать подсказку "Выход"
        
    Returns:
        Введённая пользователем строка
        
    Raises:
        BackException, BackToMenuException, QuitException при соответствующих командах
    """
    print_navigation_hint(show_back, show_menu)
    
    if choices:
        # Добавляем навигационные команды к допустимым (но не показываем их)
        all_choices = list(choices)
        if show_back:
            all_choices.extend(['0', 'назад', 'back', 'b', 'н'])
        if show_menu:
            all_choices.extend(['меню', 'menu', 'm', 'м'])
        if show_quit:
            all_choices.extend(['q', 'quit', 'exit', 'выход', 'в'])
        
        result = Prompt.ask(prompt_text, choices=all_choices, default=default, show_choices=False)
    else:
        result = Prompt.ask(prompt_text, default=default)
    
    # Проверяем на навигационные команды
    check_navigation(result)
    
    return result


def confirm_with_navigation(prompt_text: str, default: bool = False) -> bool:
    """
    Обёртка над Confirm.ask с поддержкой навигации.
    
    Args:
        prompt_text: Текст вопроса
        default: Значение по умолчанию
        
    Returns:
        True/False
        
    Raises:
        BackException, BackToMenuException, QuitException
    """
    print_navigation_hint(show_back=True, show_menu=True)
    
    # Формируем подсказку
    if default:
        hint = "[Y/n]"
        default_str = "y"
    else:
        hint = "[y/N]"
        default_str = "n"
    
    try:
        result = Prompt.ask(f"{prompt_text} {hint}", default=default_str)
    except KeyboardInterrupt:
        console.print()  # Новая строка после ^C
        raise QuitException()
    
    # Проверяем навигацию
    check_navigation(result)
    
    # Интерпретируем как да/нет
    positive_answers = ('y', 'yes', 'да', 'д', 'true', '1', 'т', 'ys')  # 'ys' на случай опечатки
    answer = result.strip().lower()
    
    is_positive = answer in positive_answers
    logger.debug(f"confirm_with_navigation: input='{result}', interpreted as {is_positive}")
    
    return is_positive

# ============================================================================
# КОМПОНЕНТЫ ИНТЕРФЕЙСА
# ============================================================================

def print_header():
    """Выводит заголовок приложения"""
    header = f"""
[bold {COLORS['primary']}]╔══════════════════════════════════════════════════════════════╗
║                    🤖 {APP_NAME} v{APP_VERSION}                      ║
║          Автономная генерация и анализ кода                    ║
╚══════════════════════════════════════════════════════════════╝[/]
"""
    console.print(header)


def print_status_bar():
    """Выводит строку статуса"""
    parts = []
    
    # Режим
    mode_icons = {
        "ask": "💬",
        "agent": "🤖",
        "general": "💡",
    }
    mode_names = {
        "ask": "ВОПРОС",
        "agent": "АГЕНТ",
        "general": "ОБЩИЙ ЧАТ",
    }
    mode_icon = mode_icons.get(state.mode, "❓")
    mode_name = mode_names.get(state.mode, state.mode.upper())
    
    # Добавляем пометки
    if state.is_new_project:
        mode_name += " (новый проект)"
    if state.is_legal_mode and state.mode == "general":
        mode_name += " [Legal]"
    
    parts.append(f"{mode_icon} Режим: [bold]{mode_name}[/]")
    
    # Проект
    if state.project_dir:
        project_name = Path(state.project_dir).name
        parts.append(f"📁 Проект: [bold]{project_name}[/]")
    else:
        parts.append(f"📁 Проект: [dim]Нет[/]")
    
    # Модель оркестратора
    if state.use_router:
        parts.append(f"🎯 Оркестратор: [dim]Авто[/]")
    else:
        current_model = state.get_current_orchestrator_model()
        model_name = get_model_short_name(current_model)
        if len(model_name) > 20:
            model_name = model_name[:17] + "..."
        parts.append(f"🎯 Оркестратор: [bold]{model_name}[/]")
    
    # NEW: Модель генератора (только для режимов ask и agent)
    if state.mode in ("ask", "agent"):
        gen_model = state.get_current_generator_model()
        gen_name = get_generator_model_short_name(gen_model)
        parts.append(f"⚙️ Генератор: [bold]{gen_name}[/]")
    
    # Статус проверки типов (только для Agent режима)
    if state.mode == "agent":
        if state.enable_type_checking:
            parts.append(f"🔍 Типы: [green]ON[/]")
        else:
            parts.append(f"🔍 Типы: [dim]OFF[/]")
    
        # NEW: Статус Pre-filter (для режимов ask и agent)
    if state.mode in ("ask", "agent"):
        pf_mode_label = "Продв." if state.prefilter_mode == "advanced" else "Обычн."
        if state.prefilter_model:
            pf_model_name = cfg.get_model_display_name(state.prefilter_model)
            if len(pf_model_name) > 15:
                pf_model_name = pf_model_name[:12] + "..."
            parts.append(f"🔍 PF: [bold]{pf_mode_label}[/]|{pf_model_name}")
        else:
            parts.append(f"🔍 PF: [bold]{pf_mode_label}[/]|[dim]Авто[/]")

    
    
    # Тред
    if state.current_thread:
        parts.append(f"💬 Тред: [dim]{state.current_thread.id[:8]}...[/]")
    
    # Токены
    parts.append(f"🎟️ Токены: [dim]{state.total_tokens_used:,}[/]")
    
    status = " │ ".join(parts)
    console.print(Panel(status, box=box.ROUNDED, border_style=COLORS['muted']))


def print_main_menu():
    """Выводит главное меню"""
    console.clear()
    print_header()
    
    menu = Table(show_header=False, box=None, padding=(0, 2))
    menu.add_column("Клавиша", style=f"bold {COLORS['primary']}")
    menu.add_column("Действие")
    
    menu.add_row("[1]", "💬 Режим Вопросов - Анализ кода, рекомендации")
    menu.add_row("[2]", "🤖 Режим Агента - Автономная генерация кода")
    menu.add_row("[3]", "💡 Общий Чат - Универсальный помощник")
    menu.add_row("", "")
    menu.add_row("[4]", "📜 История диалогов")
    menu.add_row("[5]", "⚙️  Настройки модели оркестратора")
    menu.add_row("[6]", f"🔧 Настройки модели генератора: [{get_generator_model_short_name(state.generator_model)}]")
    menu.add_row("[7]", "🔍 Проверка типов (mypy): " + ("[green]ВКЛ[/]" if state.enable_type_checking else "[dim]ВЫКЛ[/]"))
   
    # NEW: Настройки Pre-filter
    pf_mode_label = "Обычный" if state.prefilter_mode == "normal" else "Продвинутый"
    pf_model_name = cfg.get_model_display_name(state.prefilter_model) if state.prefilter_model else "Авто"
    menu.add_row("[8]", f"🔬 Настройки Pre-filter: [{pf_mode_label} | {pf_model_name}]")
    
    menu.add_row("[9]", "📖 О программе")
    menu.add_row("[0]", "🚪 Выход")
    
    console.print(Panel(menu, title="[bold]Главное меню[/]", border_style=COLORS['secondary']))



def print_about():
    """Выводит информацию о программе"""
    about_text = """
## 🤖 AI Код Агент

**Версия:** 1.0.0

### Описание

AI Код Агент — это интеллектуальный помощник для разработчиков, использующий 
передовые языковые модели для анализа, генерации и модификации кода.

### Как работает программа

#### 1. Индексация проекта

При выборе **существующего проекта** система автоматически создаёт:

- **Семантический индекс** — AST-анализ всех Python файлов:
  - Классы с методами и атрибутами
  - Функции с сигнатурами
  - Импорты и зависимости
  - Номера строк для навигации

- **Карту проекта** — описания всех файлов с AI-сгенерированными аннотациями:
  - Назначение каждого файла
  - Ключевые компоненты
  - Связи между модулями

Эти индексы позволяют AI понимать структуру вашего кода без необходимости 
загружать весь проект в контекст (экономия токенов и улучшение качества).

При выборе **нового проекта** индексация запускается только ПОСЛЕ того, как 
вы одобрите сгенерированные изменения.

#### 2. Умная маршрутизация (Роутер)

Система анализирует сложность вашего запроса и автоматически выбирает 
подходящую модель:

- 🟢 **Простые задачи** → GPT-5.1 Codex Max
  - Добавление метода по шаблону
  - Исправление простых багов
  - Написание тестов
  
- 🟡 **Средние задачи** → Claude Sonnet 4.5
  - Многокомпонентные изменения
  - Бизнес-логика
  - Рефакторинг
  
- 🔴 **Сложные задачи** → Claude Opus 4.5
  - Архитектурные решения
  - Безопасность
  - Конкурентность
  - Legacy-код

Вы также можете отключить роутер и выбрать модель вручную в любой момент.

#### 3. Пре-фильтр

Перед отправкой запроса к AI, система:

1. Анализирует ваш вопрос
2. Выбирает 5 наиболее релевантных фрагментов кода из индекса
3. Отправляет только их (экономия токенов и улучшение фокуса)

Для Gemini 3.0 Pro лимиты увеличены до 15 фрагментов и 150k токенов.

#### 4. Режимы работы

**💬 Режим Вопросов (Ask Mode)**
- Анализ существующего кода
- Объяснения и рекомендации
- Поиск багов и уязвимостей
- Код НЕ модифицируется

**🤖 Режим Агента (Agent Mode)**
- Автономная генерация и модификация кода
- Многоэтапная валидация изменений:
  - Синтаксис (ast.parse)
  - Импорты (stdlib, pip, project)
  - Типы (mypy)
  - Интеграция с зависимыми файлами
  - Runtime проверка
- AI-валидатор проверяет соответствие задаче
- Требует подтверждения перед применением
- Автоматический бэкап изменённых файлов

**💡 Общий Чат**
- Универсальный помощник без привязки к проекту
- Два подрежима:
  - **Обычный** — общие вопросы, программирование, анализ
  - **Legal** — юридические вопросы со специальным промптом
- Поддержка прикрепления файлов:
  - PDF (требуется pypdf)
  - DOCX (требуется python-docx)
  - TXT, MD, JSON, CSV, XML, HTML, CSS, YAML
  - Любые текстовые файлы
- Лимит: 40,000 токенов на все файлы

#### 5. Типы проектов

В режимах Ask и Agent можно работать с:

**Существующий проект:**
- Автоматическая индексация при выборе
- Инкрементальное обновление перед каждым запросом
- Полный анализ кодовой базы

**Новый проект:**
- Создание структуры с нуля
- Индексация запускается ПОСЛЕ одобрения изменений
- Идеально для генерации нового кода

#### 6. История и сжатие

- Все диалоги сохраняются автоматически в SQLite
- **Продолжение диалогов** — при входе предлагается продолжить 
  существующий диалог для проекта
- При превышении лимита токенов история сжимается:
  - Старые сообщения суммаризируются
  - Ключевой контекст сохраняется
- Можно переключаться между тредами
- Можно продолжать старые диалоги после выхода из программы

#### 7. Инструменты агента

Оркестратор может использовать инструменты:

- `read_file` — чтение файлов проекта
- `search_code` — поиск по индексу
- `web_search` — поиск в интернете (макс. 3 за сессию)
- `run_project_tests` — запуск тестов (макс. 5 за сессию)

### Навигация

| Команда | Действие |
|---------|----------|
| `0`, `назад`, `back` | Вернуться на шаг назад |
| `меню`, `menu`, `m` | Вернуться в главное меню |
| `q`, `выход`, `quit` | Выйти из программы |

### Команды в чате

| Команда | Описание |
|---------|----------|
| `/помощь`, `/help` | Справка по командам |
| `/модель`, `/model` | Сменить модель оркестратора |
| `/прикрепить`, `/attach` | Прикрепить файлы (Общий Чат) |
| `/история`, `/history` | Просмотр истории |
| `/новый`, `/new` | Новый диалог |
| `/статус`, `/status` | Текущий статус |
| `/очистить`, `/clear` | Очистить экран |
| `/меню`, `/menu` | Главное меню |
| `/выход`, `/quit` | Выход |

### Конфигурация

- Настройки: `.env` и `config/settings.py`
- Логи: `logs/agent_YYYYMMDD.log`
- AI ошибки: `logs/ai_errors_YYYYMMDD.log`
- Индексы: `.ai-agent/` в директории проекта
- Бэкапы: `.ai-agent/backups/`
- История: `data/history.db`

### Требования

- Python 3.10+
- API ключи в `.env`:
  - `OPENROUTER_API_KEY` — для Gemini и Qwen
  - `DEEPSEEK_API_KEY` — для DeepSeek
  - `ROUTERAI_API_KEY` — для Claude и GPT

---
*Разработано с ❤️ для продуктивной разработки*
"""
    console.print(Panel(
        Markdown(about_text),
        title="[bold]📖 О программе[/]",
        border_style=COLORS['info'],
        padding=(1, 2)
    ))
    console.print("\n[dim]Нажмите Enter для продолжения...[/]")
    input()


def print_thinking(content: str, title: str = "Размышления агента", translate: bool = True):
    """
    Отображает процесс мышления агента.
    
    Args:
        content: Текст размышлений
        title: Заголовок панели
        translate: Переводить ли на русский (по умолчанию да)
    """
    if not content:
        return
    
    # Перевод на русский (если включён и доступен)
    if translate and TRANSLATION_AVAILABLE and not is_mostly_russian(content):
        try:
            translated = translate_sync(content, "AI agent reasoning")
            if translated and translated != content:
                content = translated
                logger.debug("Thinking translated successfully")
            else:
                logger.debug("Translation returned same text or empty")
        except Exception as e:
            logger.warning(f"Failed to translate thinking: {e}")
            # Продолжаем с оригиналом
    
    # Усекаем если слишком длинный (3000 символов)
    max_chars = 3000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [ещё {len(content) - max_chars} символов]"    
    
    console.print(Panel(
        Text(content, style="dim italic"),
        title=f"[dim]💭 {title}[/]",
        border_style="dim",
        box=box.ROUNDED,
    ))


def print_prefilter_advice(advice: 'PreFilterAdvice', mode: str, tool_calls: int = 0) -> None:
    """
    Отображает совет Pre-filter агента с переводом на русский.
    
    ИСПРАВЛЕНО: Если структурированные поля пусты, показываем raw_response напрямую.
    
    Args:
        advice: Объект PreFilterAdvice с рекомендациями
        mode: Режим работы ("normal" или "advanced")
        tool_calls: Количество вызовов инструментов (для advanced режима)
    """
    if not advice:
        return
    
    mode_label = "🔍 Обычный" if mode == "normal" else "🔬 Продвинутый"
    
    # Формируем текст совета
    advice_parts = []
    
    if advice.clarified_query and advice.clarified_query.strip():
        advice_parts.append(f"**Уточнённый запрос:** {advice.clarified_query}")
    
    if advice.possible_cause and advice.possible_cause != "N/A" and advice.possible_cause.strip():
        advice_parts.append(f"**Возможная причина:** {advice.possible_cause}")
    
    if advice.recommended_actions:
        advice_parts.append("**Рекомендуемые действия:**")
        for action in advice.recommended_actions[:5]:
            advice_parts.append(f"  • {action}")
    
    if advice.files_to_check:
        advice_parts.append("**Файлы для проверки:**")
        for file_info in advice.files_to_check[:5]:
            advice_parts.append(f"  • `{file_info}`")
    
    if advice.additional_context and advice.additional_context != "N/A" and advice.additional_context.strip():
        advice_parts.append(f"**Дополнительный контекст:** {advice.additional_context}")
    
    # ИСПРАВЛЕНО: Если структурированные поля пусты, но raw_response есть — показываем его
    if not advice_parts and advice.raw_response:
        advice_text = advice.raw_response.strip()
        # Ограничиваем длину для отображения
        if len(advice_text) > 2000:
            advice_text = advice_text[:2000] + "\n\n[dim]... (обрезано для отображения)[/]"
    else:
        advice_text = "\n".join(advice_parts) if advice_parts else ""
    
    # Переводим на русский если нужно
    if TRANSLATION_AVAILABLE and advice_text and not is_mostly_russian(advice_text):
        try:
            advice_text = translate_sync(advice_text, "Pre-filter analysis advice")
        except Exception as e:
            logger.warning(f"Failed to translate prefilter advice: {e}")
    
    # Заголовок с информацией о режиме
    title = f"💡 Совет Pre-filter ({mode_label})"
    if mode == "advanced" and tool_calls > 0:
        title += f" | 🛠️ {tool_calls} инструментов"
    
    if advice_text:
        console.print(Panel(
            Markdown(advice_text),
            title=title,
            border_style=COLORS['info'],
            padding=(1, 2),
        ))


def _prefilter_streaming_handler(name: str, args: Dict[str, Any], result: str, success: bool):
    """Визуализация вызова инструмента Pre-filter в реальном времени"""
    icons = {
        "read_code_chunk": "📖",
        "read_file": "📄",
        "search_code": "🔍",
        "grep_search": "🔎",
        "show_file_relations": "🔗",
        "list_files": "📂",
        "web_search": "🌐"
    }
    icon = icons.get(name, "🛠️")
    
    # Сокращаем длинные аргументы для красоты
    args_str = str(args)
    if len(args_str) > 80:
        args_str = args_str[:77] + "..."
    
    status = "[green]OK[/]" if success else "[red]FAIL[/]"
    
    console.print(f"   [cyan]Stage:[/] {icon} [bold]{name}[/]([dim]{args_str}[/]) -> {status}")

async def run_prefilter_analysis(
    user_query: str,
    project_dir: str,
    project_index: Dict[str, Any],
    mode: str = "normal",
    model: Optional[str] = None,
    is_planning: bool = False,
    is_new_project: bool = False,
    on_tool_call: Optional[Callable] = None,
) -> Tuple[str, Optional[PreFilterAdvice]]:
    """
    Запускает Pre-filter анализ запроса и возвращает советы для Оркестратора.

    Эта функция объединяет:
    1. Вызов analyze_query из pre_filter.py
    2. Визуализацию результатов через print_prefilter_advice
    3. Форматирование советов в строку для передачи Оркестратору

    Args:
        user_query: Запрос пользователя
        project_dir: Путь к директории проекта
        project_index: Семантический индекс проекта
        mode: Режим работы ("normal" или "advanced")
        model: Модель для Pre-filter (None = из конфига)

    Returns:
        Tuple[str, Optional[PreFilterAdvice]]:
            - Строка с советами для Оркестратора (или пустая строка)
            - Объект PreFilterAdvice (или None при ошибке)
    """
    import time as _time
    from app.services.project_map_builder import get_project_map_for_prompt
    from app.builders.semantic_index_builder import create_chunks_list_auto
    
    pf_start = _time.time()
    
    # =====================================================================
    # ПОЛНОЕ ЛОГИРОВАНИЕ: Начало Pre-filter
    # =====================================================================
    console.print(f"\n[bold cyan]{'─' * 50}[/]")
    console.print(f"[bold cyan]🔬 PRE-FILTER: Начало анализа[/]")
    console.print(f"[bold cyan]{'─' * 50}[/]")
    console.print(f"   [dim]Режим:[/] [bold]{'Продвинутый (advanced)' if mode == 'advanced' else 'Обычный (normal)'}[/]")
    console.print(f"   [dim]Модель (параметр):[/] [bold]{model or 'None (будет определена из конфига)'}[/]")
    console.print(f"   [dim]Запрос:[/] {user_query[:100]}{'...' if len(user_query) > 100 else ''}")
    console.print(f"   [dim]Проект:[/] {project_dir}")
    console.print(f"   [dim]Индекс:[/] {'Загружен (' + str(len(project_index)) + ' ключей)' if project_index else 'Пустой/None'}")
    
    logger.info(f"[PRE-FILTER] Starting analysis: mode={mode}, model={model or 'default'}")
    
    try:
        # Определяем режим Pre-filter
        prefilter_mode = PreFilterMode.ADVANCED if mode == "advanced" else PreFilterMode.NORMAL
        
        # Определяем модель: если не указана, берём из конфига
        actual_model = model
        if actual_model is None:
            actual_model = cfg.AGENT_MODELS.get("pre_filter") or cfg.MODEL_NORMAL
            console.print(f"   [dim]Модель (из конфига AGENT_MODELS['pre_filter']):[/] [bold]{actual_model}[/]")
        
        model_display = cfg.get_model_display_name(actual_model)
        console.print(f"   [dim]Модель (итоговая):[/] [bold]{model_display}[/] ({actual_model})")
        
        logger.info(f"[PRE-FILTER] Using model: {model_display}")
        
        # Получаем project_map
        console.print(f"\n   [dim]📂 Загрузка project_map...[/]")
        pm_start = _time.time()
        project_map = get_project_map_for_prompt(project_dir) if project_dir else ""
        pm_elapsed = _time.time() - pm_start
        console.print(f"   [dim]   → project_map: {len(project_map)} символов ({pm_elapsed:.1f}с)[/]")
        
        # Загружаем compact_index из файла (как Orchestrator)
        console.print(f"   [dim]📋 Загрузка compact_index...[/]")
        compact_index = ""
        ci_start = _time.time()
        
        if project_dir:
            compact_md_path = Path(project_dir) / ".ai-agent" / "compact_index.md"
            if compact_md_path.exists():
                try:
                    compact_index = compact_md_path.read_text(encoding="utf-8")
                    ci_elapsed = _time.time() - ci_start
                    console.print(f"   [dim]   → compact_index: {len(compact_index)} символов ({ci_elapsed:.1f}с) [из файла][/]")
                    logger.debug(f"[PRE-FILTER] Loaded compact_index.md: {len(compact_index)} chars")
                except Exception as e:
                    console.print(f"   [yellow]   → ⚠️ Ошибка чтения compact_index.md: {e}[/]")
                    logger.warning(f"[PRE-FILTER] Failed to read compact_index.md: {e}")
                    # Fallback: генерируем из индекса
                    if project_index:
                        compact_index = create_chunks_list_auto(project_index)
                        console.print(f"   [dim]   → compact_index: {len(compact_index)} символов [сгенерирован][/]")
            else:
                console.print(f"   [dim]   → compact_index.md не найден, генерируем...[/]")
                if project_index:
                    compact_index = create_chunks_list_auto(project_index)
                    ci_elapsed = _time.time() - ci_start
                    console.print(f"   [dim]   → compact_index: {len(compact_index)} символов ({ci_elapsed:.1f}с) [сгенерирован][/]")
        else:
            console.print(f"   [dim]   → compact_index: пропущен (нет project_dir)[/]")
        
        # =====================================================================
        # ВЫЗОВ analyze_query
        # =====================================================================
        console.print(f"\n   [bold cyan]📡 Вызов analyze_query → LLM API...[/]")
        console.print(f"   [dim]   Модель: {actual_model}[/]")
        console.print(f"   [dim]   Режим: {prefilter_mode.value}[/]")
        console.print(f"   [dim]   system_prompt будет сформирован внутри analyze_query[/]")
        
        llm_start = _time.time()
        
        with console.status(f"[bold cyan]🔬 Pre-filter анализ ({model_display})...[/]"):
            advice = await analyze_query(
                user_query=user_query,
                project_map=project_map,
                compact_index=compact_index,
                project_dir=project_dir,
                index=project_index,
                mode=prefilter_mode,
                model=actual_model,
                is_planning=is_planning,
                is_new_project=is_new_project,
                on_tool_call=on_tool_call
            )
        
        llm_elapsed = _time.time() - llm_start
        
        # =====================================================================
        # ЛОГИРОВАНИЕ РЕЗУЛЬТАТА
        # =====================================================================
        console.print(f"\n   [bold]📊 Результат analyze_query ({llm_elapsed:.1f}с):[/]")
        
        if advice:
            console.print(f"   [dim]   raw_response: {len(advice.raw_response)} символов[/]")
            console.print(f"   [dim]   tool_calls_made: {advice.tool_calls_made}[/]")
            console.print(f"   [dim]   clarified_query: {'✅ есть' if advice.clarified_query else '❌ пусто'}[/]")
            console.print(f"   [dim]   possible_cause: {'✅ есть' if advice.possible_cause else '❌ пусто'}[/]")
            console.print(f"   [dim]   recommended_actions: {len(advice.recommended_actions)} шт.[/]")
            console.print(f"   [dim]   files_to_check: {len(advice.files_to_check)} шт.[/]")
            
            if advice.raw_response:
                # Показываем первые 200 символов raw_response для диагностики
                preview = advice.raw_response[:200].replace('\n', ' ')
                console.print(f"   [dim]   raw_response preview: {preview}{'...' if len(advice.raw_response) > 200 else ''}[/]")
            
            logger.info(f"[PRE-FILTER] Analysis complete: raw_response={len(advice.raw_response)} chars, tool_calls={advice.tool_calls_made}")
        else:
            console.print(f"   [red]   ❌ analyze_query вернул None![/]")
            logger.warning("[PRE-FILTER] Analysis returned None")
        
        if advice and advice.raw_response:
            if is_planning:
                console.print("\n[bold green]📋 Отчет Pre-filter (Комментарии + План):[/]")
                from rich.markdown import Markdown
                
                # Print the full response to show commentary, but save only the extracted plan
                full_text = getattr(advice, 'full_response', advice.raw_response)
                console.print(Markdown(full_text))
                
                if project_dir:
                    try:
                        plan_path = Path(project_dir) / ".ai-agent" / "current_plan.md"
                        plan_path.parent.mkdir(parents=True, exist_ok=True)
                        plan_path.write_text(advice.raw_response, encoding="utf-8")
                        console.print(f"[dim]План сохранён в {plan_path}[/]")
                    except Exception as e:
                        console.print(f"[yellow]Не удалось сохранить план: {e}[/]")
            else:
                print_prefilter_advice(advice, mode, advice.tool_calls_made)
            console.print(f"[green]   ✓ Pre-filter завершён успешно[/]")
        else:
            console.print("[yellow]   ⚠️ Pre-filter не вернул рекомендаций (raw_response пуст)[/]")
            if advice and hasattr(advice, 'raw_response') and advice.raw_response:
                console.print(f"[dim]   Сырой ответ ({len(advice.raw_response)} символов): {advice.raw_response[:200]}...[/]")
        
        # Форматируем советы в строку для Оркестратора
        advice_str = _format_prefilter_advice_for_orchestrator(
            advice, 
            is_planning=is_planning, 
            is_new_project=is_new_project
        ) if advice else ""
        
        # Логируем что передаём Оркестратору
        total_elapsed = _time.time() - pf_start
        
        if advice_str:
            logger.info(f"[PRE-FILTER] Advice for Orchestrator: {len(advice_str)} chars")
            console.print(f"[green]   📤 Советы переданы Оркестратору ({len(advice_str)} символов)[/]")
        else:
            logger.warning("[PRE-FILTER] No advice to pass to Orchestrator")
            console.print("[yellow]   📤 Нет советов для передачи Оркестратору[/]")
        
        console.print(f"[dim]   ⏱️ Общее время Pre-filter: {total_elapsed:.1f}с[/]")
        console.print(f"[bold cyan]{'─' * 50}[/]")
        
        return advice_str, advice
        
    except Exception as e:
        total_elapsed = _time.time() - pf_start
        error_type = type(e).__name__
        error_msg = str(e)
        
        logger.error(f"[PRE-FILTER] Analysis error: {e}", exc_info=True)
        
        # ПОЛНОЕ отображение ошибки в консоли
        console.print(f"\n[bold red]   ❌ PRE-FILTER ОШИБКА ({total_elapsed:.1f}с)[/]")
        console.print(f"   [red]Тип: {error_type}[/]")
        console.print(f"   [red]Сообщение: {error_msg}[/]")
        console.print(f"   [red]Модель: {model or 'default'}, Режим: {mode}[/]")
        
        # Показываем трейсбек ВСЕГДА (не только в DEBUG)
        import traceback as _tb
        tb_str = _tb.format_exc()
        console.print(f"\n[dim]Полный трейсбек:[/]")
        console.print(f"[red]{tb_str}[/]")
        
        console.print(f"[bold cyan]{'─' * 50}[/]")
        
        return "", None


def _format_prefilter_advice_for_orchestrator(
    advice: PreFilterAdvice, 
    is_planning: bool = False,
    is_new_project: bool = False
) -> str:
    """
    Форматирует PreFilterAdvice в строку для передачи Оркестратору.
    
    Использует соответствующие шаблоны из prompt_templates.py в зависимости от режима.
    """
    if not advice:
        return ""
    
    # Если raw_response пуст — нечего передавать
    if not advice.raw_response:
        return ""
        
    # ВЕТКА ПЛАНИРОВАНИЯ (ADVANCED MODE)
    if is_planning:
        if is_new_project:
            return _build_prefilter_advice_section_new_project_agent().format(
                prefilter_advice=advice.raw_response.strip()
            )
        else:
            return _build_prefilter_advice_section_agent().format(
                prefilter_advice=advice.raw_response.strip()
            )
    
    # ВЕТКА ОБЫЧНОГО АНАЛИЗА (NORMAL MODE)
    parts = []
    
    if advice.clarified_query and advice.clarified_query.strip():
        parts.append(f"CLARIFIED_QUERY: {advice.clarified_query}")
    
    if advice.possible_cause and advice.possible_cause != "N/A" and advice.possible_cause.strip():
        parts.append(f"POSSIBLE_CAUSE: {advice.possible_cause}")
    
    if advice.recommended_actions:
        parts.append("RECOMMENDED_ACTIONS:")
        for action in advice.recommended_actions:
            parts.append(f"- {action}")
    
    if advice.files_to_check:
        parts.append("FILES_TO_CHECK:")
        for file_info in advice.files_to_check:
            parts.append(f"- {file_info}")
    
    if advice.additional_context and advice.additional_context != "N/A" and advice.additional_context.strip():
        parts.append(f"ADDITIONAL_CONTEXT: {advice.additional_context}")
    
    if advice.tool_calls_made > 0:
        parts.append(f"TOOL_CALLS_MADE: {advice.tool_calls_made}")
    
    # Собираем контент
    if not parts:
        content = advice.raw_response.strip()
    else:
        content = "\n".join(parts)
        
    # Оборачиваем в оригинальный блок "🔮 PRE-ANALYSIS ADVISORY"
    return _build_prefilter_advice_section().format(prefilter_advice=content)
    


def print_instruction(instruction: str, title: str = "Инструкция для Code Generator"):
    """
    Отображает инструкцию, которую Оркестратор написал для Code Generator.
    """
    if not instruction:
        return
    
    # Усекаем если слишком длинная
    display_text = instruction
    max_chars = 5000
    if len(instruction) > max_chars:
        display_text = instruction[:max_chars] + f"\n\n... [ещё {len(instruction) - max_chars} символов]"
    
    console.print(Panel(
        Markdown(display_text),
        title=f"[bold cyan]📋 {title}[/]",
        border_style=COLORS['primary'],
        box=box.ROUNDED,
    ))


def print_tool_call(tool_name: str, args: Dict, output: str, success: bool):
    """
    Отображает результат вызова инструмента.
    Форматирует вывод в зависимости от типа инструмента.
    """
    status = "✅" if success else "❌"
    
    # Форматируем по типу инструмента
    if tool_name == "web_search":
        emoji = "🌐"
        query = args.get("query", "")
        description = f"Поиск в интернете: [bold cyan]{query}[/]"
        # Показываем первые результаты (до 800 символов)
        output_preview = _extract_search_results(output, max_results=3)
        
    elif tool_name == "general_web_search":
        emoji = "🔍"
        query = args.get("query", "")
        description = f"Веб-поиск: [bold cyan]{query}[/]"
        output_preview = _extract_search_results(output, max_results=3)
        
    elif tool_name == "install_dependency":
        emoji = "📦"
        package = args.get("import_name", args.get("package", ""))
        version = args.get("version", "")
        ver_str = f"=={version}" if version else ""
        description = f"Установка библиотеки: [bold green]{package}{ver_str}[/]"
        output_preview = _truncate_output(output, 300)
        
    elif tool_name == "read_file":
        emoji = "📄"
        file_path = args.get("file_path", "")
        description = f"Чтение файла: [bold yellow]{file_path}[/]"
        # Показываем размер и первые строки
        lines = output.split('\n')
        output_preview = f"[dim]({len(lines)} строк, {len(output)} символов)[/]\n"
        output_preview += '\n'.join(lines[:10])
        if len(lines) > 10:
            output_preview += f"\n[dim]... ещё {len(lines) - 10} строк[/]"
        
    elif tool_name == "read_code_chunk":
        emoji = "🧩"
        file_path = args.get("file_path", "")
        chunk_name = args.get("chunk_name", "")
        description = f"Чтение чанка: [bold magenta]{chunk_name}[/] из [yellow]{file_path}[/]"
        lines = output.split('\n')
        output_preview = f"[dim]({len(lines)} строк)[/]\n"
        output_preview += '\n'.join(lines[:15])
        if len(lines) > 15:
            output_preview += f"\n[dim]... ещё {len(lines) - 15} строк[/]"
        
    elif tool_name == "search_code":
        emoji = "🔎"
        query = args.get("query", "")
        description = f"Поиск по индексной карте: [bold cyan]{query}[/]"
        output_preview = _truncate_output(output, 600)
        
    elif tool_name == "run_project_tests":
        emoji = "🧪"
        test_file = args.get("test_file", "")
        target = args.get("target", "")
        if test_file:
            description = f"Запуск тестов: [bold yellow]{test_file}[/]"
        elif target:
            description = f"Тесты для: [bold yellow]{target}[/]"
        else:
            description = "Запуск тестов проекта"
        # Показываем результат теста полностью (важно!)
        output_preview = _truncate_output(output, 1000)
        
    elif tool_name == "search_pypi":
        emoji = "📚"
        query = args.get("query", "")
        description = f"Поиск на PyPI: [bold cyan]{query}[/]"
        output_preview = _truncate_output(output, 400)
        
    elif tool_name == "list_installed_packages":
        emoji = "📋"
        description = "Список установленных пакетов"
        output_preview = _truncate_output(output, 500)
        
    elif tool_name == "get_advice":
        emoji = "💡"
        advice_id = args.get("advice_id", "")
        description = f"Загрузка методологии: [bold cyan]{advice_id}[/]"
        output_preview = _truncate_output(output, 500)
        
    else:
        emoji = "🔧"
        args_str = ", ".join(f"{k}={repr(v)[:30]}" for k, v in args.items())
        description = f"{tool_name}({args_str})"
        output_preview = _truncate_output(output, 400)
    
    console.print(Panel(
        f"{emoji} {description}\n\n{output_preview}",
        title=f"{status} Инструмент",
        border_style=COLORS['info'] if success else COLORS['error'],
        box=box.ROUNDED,
    ))


def _truncate_output(output: str, max_chars: int) -> str:
    """Усекает вывод до указанного количества символов"""
    if len(output) <= max_chars:
        return output
    return output[:max_chars] + f"\n[dim]... [{len(output) - max_chars} символов скрыто][/]"


def _extract_search_results(output: str, max_results: int = 3) -> str:
    """Извлекает и форматирует результаты поиска"""
    # Пробуем найти заголовки результатов
    import re
    
    # Паттерн для XML результатов
    title_pattern = r'<title>(.*?)</title>'
    url_pattern = r'<url>(.*?)</url>'
    
    titles = re.findall(title_pattern, output, re.DOTALL)
    urls = re.findall(url_pattern, output, re.DOTALL)
    
    if titles:
        results = []
        for i, (title, url) in enumerate(zip(titles[:max_results], urls[:max_results])):
            results.append(f"  {i+1}. [bold]{title.strip()}[/]\n     [dim]{url.strip()}[/]")
        
        result_text = '\n'.join(results)
        if len(titles) > max_results:
            result_text += f"\n  [dim]... и ещё {len(titles) - max_results} результатов[/]"
        return result_text
    
    # Fallback — просто усекаем
    return _truncate_output(output, 600)


def print_code_block(code: str, filepath: str = "", language: str = "python"):
    """
    Отображает блок кода с подсветкой синтаксиса.
    Усекает вывод, если количество строк превышает лимит, сохраняя читаемость терминала.
    """
    MAX_DISPLAY_LINES = 100
    lines = code.splitlines()
    total_lines = len(lines)
    
    # Формируем заголовок с общим количеством строк
    if filepath:
        title = f"📄 {filepath} [dim]({total_lines} строк)[/]"
    else:
        title = f"Сгенерированный код [dim]({total_lines} строк)[/]"

    # Определяем отображаемый код
    if total_lines <= MAX_DISPLAY_LINES:
        display_code = code
        is_truncated = False
    else:
        display_code = "\n".join(lines[:MAX_DISPLAY_LINES])
        is_truncated = True

    # Рендерим Syntax и Panel
    syntax = Syntax(display_code, language, theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title=title, border_style=COLORS['success']))

    # Если код был усечён, выводим информационное сообщение
    if is_truncated:
        hidden_lines = total_lines - MAX_DISPLAY_LINES
        msg = f"[dim]... [ещё {hidden_lines} строк скрыто][/]"
        if filepath:
            msg += f" [dim]📄 Полный код: {filepath}[/]"
        console.print(msg)



def print_diff_preview(diffs: Dict[str, str]):
    """Отображает превью изменений (diff)"""
    if not diffs:
        console.print("[dim]Нет изменений для отображения[/]")
        return
    
    for filepath, diff in diffs.items():
        # Раскрашиваем diff
        colored_lines = []
        for line in diff.split('\n'):
            if line.startswith('+') and not line.startswith('+++'):
                colored_lines.append(f"[green]{line}[/]")
            elif line.startswith('-') and not line.startswith('---'):
                colored_lines.append(f"[red]{line}[/]")
            elif line.startswith('@@'):
                colored_lines.append(f"[cyan]{line}[/]")
            else:
                colored_lines.append(line)
        
        console.print(Panel(
            '\n'.join(colored_lines),
            title=f"📝 {filepath}",
            border_style=COLORS['warning'],
        ))


def print_validation_result(result: Dict[str, Any], translate: bool = True):
    """
    Отображает результаты валидации.
    
    Args:
        result: Результат валидации
        translate: Переводить ли сообщения об ошибках
    """
    success = result.get("success", False)
    
    if success:
        console.print(Panel(
            "✅ Все проверки пройдены!",
            title="Результат валидации",
            border_style=COLORS['success'],
        ))
    else:
        issues = result.get("issues", [])
        
        # Переводим issues если нужно
        if translate and TRANSLATION_AVAILABLE and issues:
            translated_issues = []
            for issue in issues[:10]:
                if isinstance(issue, str) and not is_mostly_russian(issue):
                    translated = translate_sync(issue, "validation error message")
                    translated_issues.append(translated)
                else:
                    translated_issues.append(str(issue))
            issues = translated_issues
        
        issues_text = "\n".join(f"• {issue}" for issue in issues[:10])
        if len(result.get("issues", [])) > 10:
            issues_text += f"\n... и ещё {len(result.get('issues', [])) - 10} проблем"
        
        console.print(Panel(
            f"❌ Валидация не пройдена:\n\n{issues_text}",
            title="Результат валидации",
            border_style=COLORS['error'],
        ))


def print_runtime_test_summary(summary: Dict[str, Any], title: str = "RUNTIME тестирование"):
    """
    Отображает детальный отчёт о runtime тестировании.
    
    Args:
        summary: Словарь с результатами из RuntimeTestSummary.to_dict()
        title: Заголовок отчёта
    """
    if not summary:
        console.print(f"\n[bold]▶️ {title}:[/]")
        console.print(f"   [dim]Данные недоступны[/]")
        return
    
    total_files = summary.get("total_files", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    timeouts = summary.get("timeouts", 0)
    errors = summary.get("errors", 0)
    success = summary.get("success", False)
    duration_ms = summary.get("total_duration_ms", 0)
    
    console.print(f"\n[bold]▶️ {title}:[/]")
    
    if total_files == 0:
        console.print(f"   [dim]Не выполнялось (нет файлов для проверки)[/]")
        return
    
    # Иконка общего статуса
    if success:
        status_line = f"   [green]✅ Успешно завершено[/]"
    elif failed > 0 or errors > 0:
        status_line = f"   [red]❌ Есть ошибки[/]"
    elif skipped == total_files:
        status_line = f"   [yellow]⏭ Все файлы пропущены[/]"
    else:
        status_line = f"   [yellow]⚠️ Частично успешно[/]"
    
    console.print(status_line)
    console.print(f"   [dim]Время: {duration_ms:.0f}ms[/]")
    console.print()
    
    # Таблица статистики
    stats_parts = []
    stats_parts.append(f"Всего: {total_files}")
    if passed > 0:
        stats_parts.append(f"[green]✓ Прошло: {passed}[/]")
    if failed > 0:
        stats_parts.append(f"[red]✗ Ошибок: {failed}[/]")
    if timeouts > 0:
        stats_parts.append(f"[yellow]⏱ Таймаутов: {timeouts}[/]")
    if errors > 0:
        stats_parts.append(f"[red]💥 Ошибок exec: {errors}[/]")
    if skipped > 0:
        stats_parts.append(f"[yellow]⏭ Пропущено: {skipped}[/]")
    
    console.print(f"   {' │ '.join(stats_parts)}")
    
    # Детали по файлам
    results = summary.get("results", [])
    if not results:
        return
    
    # Группируем по статусу
    passed_files = [r for r in results if r.get("status") == "passed"]
    failed_files = [r for r in results if r.get("status") == "failed"]
    timeout_files = [r for r in results if r.get("status") == "timeout"]
    skipped_files = [r for r in results if r.get("status") == "skipped"]
    error_files = [r for r in results if r.get("status") == "error"]
    
    # Показываем ошибки (подробно)
    if failed_files:
        console.print(f"\n   [bold red]Файлы с ошибками ({len(failed_files)}):[/]")
        for f in failed_files[:5]:
            file_path = f.get("file_path", "?")
            app_type = f.get("app_type", "standard")
            message = f.get("message", "")
            # Укорачиваем сообщение
            if len(message) > 100:
                message = message[:100] + "..."
            console.print(f"      [red]•[/] `{file_path}` [{app_type}]")
            console.print(f"        {message}")
        if len(failed_files) > 5:
            console.print(f"      [dim]... и ещё {len(failed_files) - 5} файлов[/]")
    
    # Показываем таймауты (если есть)
    if timeout_files:
        console.print(f"\n   [bold yellow]Таймауты ({len(timeout_files)}):[/]")
        for f in timeout_files[:3]:
            file_path = f.get("file_path", "?")
            duration = f.get("duration_ms", 0)
            console.print(f"      [yellow]⏱[/] `{file_path}` ({duration:.0f}ms)")
        if len(timeout_files) > 3:
            console.print(f"      [dim]... и ещё {len(timeout_files) - 3}[/]")
    
    # Показываем пропущенные (кратко)
    if skipped_files:
        console.print(f"\n   [bold yellow]Пропущенные файлы ({len(skipped_files)}):[/]")
        
        # Группируем по причинам
        reasons: Dict[str, List[str]] = {}
        for f in skipped_files:
            message = f.get("message", "Unknown reason")
            file_path = f.get("file_path", "?")
            # Извлекаем основную причину
            if "web app" in message.lower() or "flask" in message.lower() or "fastapi" in message.lower():
                reason = "Web приложения (Flask/FastAPI/Django)"
            elif "gui" in message.lower() or "pygame" in message.lower() or "tkinter" in message.lower():
                reason = "GUI приложения (без display)"
            elif "insufficient time" in message.lower():
                reason = "Недостаточно времени"
            elif "no test" in message.lower():
                reason = "Нет тестов для типа"
            else:
                reason = message[:50]
            
            if reason not in reasons:
                reasons[reason] = []
            reasons[reason].append(file_path)
        
        for reason, files in list(reasons.items())[:4]:
            console.print(f"      [yellow]⏭[/] {reason}: {len(files)} файл(ов)")
            for fp in files[:2]:
                console.print(f"         [dim]• {fp}[/]")
            if len(files) > 2:
                console.print(f"         [dim]... и ещё {len(files) - 2}[/]")
        
        if len(reasons) > 4:
            console.print(f"      [dim]... и ещё {len(reasons) - 4} причин[/]")
    
    # Показываем успешные (очень кратко)
    if passed_files and len(passed_files) <= 10:
        console.print(f"\n   [green]Успешно протестировано ({len(passed_files)}):[/]")
        for f in passed_files[:5]:
            file_path = f.get("file_path", "?")
            duration = f.get("duration_ms", 0)
            console.print(f"      [green]✓[/] `{file_path}` ({duration:.0f}ms)")
        if len(passed_files) > 5:
            console.print(f"      [dim]... и ещё {len(passed_files) - 5}[/]")
    elif passed_files:
        console.print(f"\n   [green]Успешно протестировано: {len(passed_files)} файлов[/]")
    
    # Информация об анализе проекта
    analysis = summary.get("analysis", {})
    if analysis:
        frameworks = analysis.get("detected_frameworks", {})
        if frameworks:
            fw_list = list(frameworks.values())[:5]
            console.print(f"\n   [dim]Обнаруженные фреймворки: {', '.join(fw_list)}[/]")


def print_error(message: str, title: str = "Ошибка"):
    """Отображает сообщение об ошибке"""
    console.print(Panel(
        f"[bold red]{message}[/]",
        title=f"❌ {title}",
        border_style=COLORS['error'],
    ))


def print_success(message: str, title: str = "Успех"):
    """Отображает сообщение об успехе"""
    console.print(Panel(
        f"[bold green]{message}[/]",
        title=f"✅ {title}",
        border_style=COLORS['success'],
    ))


def print_info(message: str, title: str = "Информация"):
    """Отображает информационное сообщение"""
    console.print(Panel(
        message,
        title=f"ℹ️ {title}",
        border_style=COLORS['info'],
    ))


def print_warning(message: str, title: str = "Предупреждение"):
    """Отображает предупреждение"""
    console.print(Panel(
        f"[yellow]{message}[/]",
        title=f"⚠️ {title}",
        border_style=COLORS['warning'],
    ))


# ============================================================================
# ФУНКЦИИ ОТОБРАЖЕНИЯ ИСТОРИИ ДИАЛОГА
# ============================================================================

def truncate_text(text: str, max_chars: int, suffix: str = "...") -> str:
    """Обрезает текст до указанной длины с добавлением суффикса"""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - len(suffix)] + suffix


def format_message_preview(content: str, max_chars: int = 3500) -> str:
    """
    Форматирует превью сообщения для отображения в списке диалогов.
    Убирает лишние переносы строк и обрезает.
    """
    if not content:
        return "[dim]Пустое сообщение[/]"
    
    # Убираем множественные переносы строк
    import re
    clean = re.sub(r'\n{3,}', '\n\n', content.strip())
    # Заменяем переносы на пробелы для однострочного превью
    single_line = re.sub(r'\s+', ' ', clean)
    
    return truncate_text(single_line, max_chars)


def extract_code_blocks_from_response(content: str) -> List[Dict[str, str]]:
    """
    Извлекает блоки кода из ответа ассистента.
    
    Returns:
        Список словарей с ключами: 'file_path', 'language', 'code'
    """
    import re
    
    blocks = []
    
    # Паттерн для блоков кода с указанием файла
    # Ищем: **Файл:** `path` или Файл: `path` перед блоком кода
    file_pattern = r'\*?\*?Файл:?\*?\*?\s*`([^`]+)`\s*\n```(\w+)?\n(.*?)```'
    
    for match in re.finditer(file_pattern, content, re.DOTALL):
        blocks.append({
            'file_path': match.group(1),
            'language': match.group(2) or 'python',
            'code': match.group(3).strip()
        })
    
    # Если не нашли с файлами, ищем просто блоки кода
    if not blocks:
        simple_pattern = r'```(\w+)?\n(.*?)```'
        for match in re.finditer(simple_pattern, content, re.DOTALL):
            blocks.append({
                'file_path': None,
                'language': match.group(1) or 'text',
                'code': match.group(2).strip()
            })
    
    return blocks


def extract_status_from_agent_response(content: str) -> Optional[str]:
    """
    Извлекает статус из ответа агента (отказ, лимит итераций и т.д.)
    
    Returns:
        Строка статуса или None
    """
    content_lower = content.lower()
    
    # Проверяем различные статусы
    if "отклонен" in content_lower or "отказ" in content_lower:
        return "❌ Изменения отклонены пользователем"
    elif "отменён" in content_lower or "отменен" in content_lower or "cancelled" in content_lower:
        return "🚫 Запрос отменён"
    elif "лимит итераций" in content_lower or "максимум итераций" in content_lower:
        return "⚠️ Достигнут лимит итераций"
    elif "ошибка" in content_lower and "критическая" in content_lower:
        return "💥 Критическая ошибка"
    elif "применены" in content_lower or "✅" in content:
        return "✅ Изменения применены"
    elif "прервана" in content_lower:
        return "⚠️ Сессия прервана"
    
    return None


async def display_thread_history(thread: Thread, mode: str, limit: int = 10):
    """
    Отображает историю сообщений при входе в диалог.
    
    Args:
        thread: Объект Thread
        mode: Режим работы ('ask', 'agent', 'general')
        limit: Максимальное количество пар сообщений для отображения
    """
    if not state.history_manager:
        return
    
    messages = await state.history_manager.get_messages(thread.id)
    
    if not messages:
        console.print("[dim]История диалога пуста[/]\n")
        return
    
    console.print(f"\n[bold]📜 История диалога[/] ({len(messages)} сообщений)\n")
    console.print("─" * 60)
    
    # Группируем сообщения по парам user-assistant
    pairs = []
    current_user_msg = None
    
    for msg in messages:
        if msg.role == "user":
            current_user_msg = msg
        elif msg.role == "assistant" and current_user_msg:
            pairs.append((current_user_msg, msg))
            current_user_msg = None
    
    # Если есть неотвеченный запрос
    if current_user_msg:
        pairs.append((current_user_msg, None))
    
    # Показываем последние N пар
    display_pairs = pairs[-limit:] if len(pairs) > limit else pairs
    
    if len(pairs) > limit:
        console.print(f"[dim]... показаны последние {limit} из {len(pairs)} диалогов ...[/]\n")
    
    for user_msg, assistant_msg in display_pairs:
        # === СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ ===
        # Форматируем время
        time_str = ""
        if user_msg.created_at:
            try:
                time_str = f" ({user_msg.created_at[11:16]})"  # HH:MM
            except:
                pass
        
        console.print(f"[bold cyan]👤 Вы{time_str}:[/]")
        
        # Обрезаем запрос до 5000 символов
        user_content = truncate_text(user_msg.content, 5000, "\n[dim]... (сообщение обрезано)[/]")
        console.print(f"   {user_content}\n")
        
        # === ОТВЕТ АССИСТЕНТА ===
        if assistant_msg:
            time_str = ""
            if assistant_msg.created_at:
                try:
                    time_str = f" ({assistant_msg.created_at[11:16]})"
                except:
                    pass
            
            console.print(f"[bold green]🤖 Ассистент{time_str}:[/]")
            
            if mode == "agent":
                # В режиме Agent показываем код и статус
                await _display_agent_response(assistant_msg.content)
            else:
                # В режимах General Chat и ASK показываем полный ответ
                # Используем Markdown для красивого форматирования
                try:
                    console.print(Panel(
                        Markdown(assistant_msg.content),
                        border_style="dim",
                        padding=(0, 1),
                    ))
                except Exception:
                    # Fallback если Markdown не парсится
                    console.print(f"   {assistant_msg.content}\n")
        else:
            console.print("[dim]   ⏳ Ожидает ответа...[/]\n")
        
        console.print("─" * 60)
    
    console.print()


async def _display_agent_response(content: str):
    """
    Отображает ответ в режиме Agent: код + статус.
    """
    # Извлекаем статус
    status = extract_status_from_agent_response(content)
    
    # Извлекаем блоки кода
    code_blocks = extract_code_blocks_from_response(content)
    
    if status:
        console.print(f"   [bold]{status}[/]")
    
    if code_blocks:
        console.print(f"   [dim]Предложено {len(code_blocks)} блок(ов) кода:[/]")
        
        for i, block in enumerate(code_blocks[:3], 1):  # Показываем макс 3 блока
            file_info = f" — `{block['file_path']}`" if block['file_path'] else ""
            console.print(f"   [cyan]{i}.{file_info}[/]")
            
            # Показываем первые 15 строк кода
            code_lines = block['code'].split('\n')
            preview_lines = code_lines[:15]
            
            try:
                syntax = Syntax(
                    '\n'.join(preview_lines),
                    block['language'],
                    theme="monokai",
                    line_numbers=True,
                    word_wrap=True,
                )
                console.print(Panel(syntax, border_style="dim", padding=(0, 0)))
            except:
                console.print(f"   ```\n{chr(10).join(preview_lines)}\n   ```")
            
            if len(code_lines) > 15:
                console.print(f"   [dim]... ещё {len(code_lines) - 15} строк[/]")
        
        if len(code_blocks) > 3:
            console.print(f"   [dim]... и ещё {len(code_blocks) - 3} блоков кода[/]")
    
    elif not status:
        # Нет ни кода, ни статуса — показываем текст (обрезанный)
        # Убираем markdown заголовки для компактности
        import re
        clean_content = re.sub(r'^#+\s+', '', content, flags=re.MULTILINE)
        clean_content = truncate_text(clean_content, 2000, "\n   [dim]... (ответ обрезан)[/]")
        console.print(f"   {clean_content}")
    
    console.print()


async def get_thread_preview(thread: Thread) -> str:
    """
    Получает превью последнего запроса для отображения в списке диалогов.
    
    Args:
        thread: Объект Thread
        
    Returns:
        Строка превью (до 100 символов для таблицы)
    """
    if not state.history_manager:
        return "[dim]—[/]"
    
    try:
        last_msg = await state.history_manager.get_last_user_message(thread.id)
        if last_msg and last_msg.content:
            # Для таблицы — короткое превью
            return format_message_preview(last_msg.content, max_chars=80)
        return "[dim]Нет сообщений[/]"
    except Exception as e:
        logger.warning(f"Failed to get thread preview: {e}")
        return "[dim]—[/]"



async def handle_validator_rejection(
    ai_result: Dict[str, Any],
    orchestrator_decision: Optional[Any],  # OrchestratorFeedbackDecision
    pipeline: 'AgentPipeline',
    history: List[Dict[str, str]],
) -> Optional[str]:
    """
    Обрабатывает отклонение кода AI Validator.
    Показывает пользователю вердикт валидатора И решение Оркестратора.
    
    Args:
        ai_result: Результат AI валидации
        orchestrator_decision: Решение Оркестратора (ACCEPT/OVERRIDE)
        pipeline: Текущий pipeline
        history: История диалога
        
    Returns:
        'continue' - продолжить с новым кодом
        'apply' - применить текущие изменения (override)
        'cancel' - отменить запрос
        None - ошибка или пользователь выбрал вернуться
        
    Raises:
        BackToMenuException, QuitException
    """
    console.print("\n[bold yellow]⚠️ AI Validator отклонил сгенерированный код[/]\n")
    
    # Показываем вердикт валидатора (С ПЕРЕВОДОМ)
    verdict = ai_result.get("verdict", "Причина не указана")
    confidence = ai_result.get("confidence", 0)
    issues = ai_result.get("critical_issues", [])
    
    # Переводим вердикт и issues
    if TRANSLATION_AVAILABLE:
        if not is_mostly_russian(verdict):
            verdict = translate_sync(verdict, "AI validator verdict")
        
        translated_issues = []
        for issue in issues:
            if isinstance(issue, str) and not is_mostly_russian(issue):
                translated_issues.append(translate_sync(issue, "validation issue"))
            else:
                translated_issues.append(str(issue))
        issues = translated_issues
    
    console.print(Panel(
        f"**Вердикт:** {verdict}\n\n"
        f"**Уверенность:** {confidence:.0%}\n\n"
        + ("**Критические проблемы:**\n" + "\n".join(f"• {i}" for i in issues) if issues else ""),
        title="🔍 Результат AI Validator",
        border_style=COLORS['warning'],
    ))
        
    # ========================================
    # NEW: Показываем решение Оркестратора
    # ========================================
    if orchestrator_decision:
        decision = orchestrator_decision.decision
        reasoning = orchestrator_decision.reasoning
        
        # Переводим reasoning Оркестратора
        if TRANSLATION_AVAILABLE and reasoning and not is_mostly_russian(reasoning):
            reasoning = translate_sync(reasoning, "orchestrator reasoning about code")
        
        if decision == "OVERRIDE":
            # Оркестратор НЕ согласен с валидатором
            console.print(Panel(
                f"**Решение:** [bold green]OVERRIDE[/] (не согласен с валидатором)\n\n"
                f"**Обоснование:**\n{reasoning}",
                title="🤖 Мнение Оркестратора",
                border_style=COLORS['success'],
            ))
                        
            console.print("\n[bold]Оркестратор считает, что код корректен.[/]")
            console.print("Выберите действие:\n")
            console.print("[1] ✅ Доверять Оркестратору — перейти к тестированию")
            console.print("[2] ⚠️  Согласиться с Валидатором — отправить критику на исправление")
            console.print("[3] ✏️  Написать свою критику")
            console.print("[4] ❌ Отменить запрос")
            console.print()
            
            try:
                choice = prompt_with_navigation(
                    "Выбор",
                    choices=["1", "2", "3", "4"],
                    default="1"  # По умолчанию доверяем Оркестратору
                )
            except BackException:
                return None
            except (BackToMenuException, QuitException):
                raise
            
            if choice == "1":
                # Доверяем Оркестратору — идём на тесты
                console.print("[dim]Переходим к тестированию...[/]")
                return "apply"
            
            elif choice == "2":
                # Пользователь согласен с Валидатором
                console.print("[dim]Отправляем критику валидатора на исправление...[/]")
                result = await pipeline.handle_user_feedback(
                    action="accept",
                    history=history,
                )
                if result and result.success:
                    return "continue"
                else:
                    print_warning("Не удалось исправить код")
                    return None
            
            elif choice == "3":
                # Своя критика
                return await _get_user_custom_critique(pipeline, history)
            
            elif choice == "4":
                await pipeline.discard_pending_changes()
                print_info("Запрос отменён")
                return "cancel"
        
        else:  # decision == "ACCEPT"
            # Оркестратор СОГЛАСЕН с валидатором — уже исправил код
            # reasoning уже переведён выше
            console.print(Panel(
                f"**Решение:** [bold cyan]ACCEPT[/] (согласен с валидатором)\n\n"
                f"**Обоснование:**\n{reasoning}\n\n"
                "[dim]Оркестратор уже сгенерировал исправленный код.[/]",
                title="🤖 Мнение Оркестратора",
                border_style=COLORS['info'],
            ))
                        
            # Код уже исправлен — продолжаем
            return "continue"
    
    # ========================================
    # Fallback: Нет решения Оркестратора — старая логика
    # ========================================
    console.print("\n[bold]Выберите действие:[/]\n")
    console.print("[1] ✅ Согласиться с валидатором — отправить критику Оркестратору для исправления")
    console.print("[2] ✏️  Написать свою критику — заменить оценку валидатора своим текстом")
    console.print("[3] ⏩ Игнорировать валидатор — перейти к тестированию (код может быть корректным)")
    console.print("[4] ❌ Отменить запрос")
    console.print()
    
    try:
        choice = prompt_with_navigation(
            "Выбор",
            choices=["1", "2", "3", "4"],
            default="1"
        )
    except BackException:
        return None
    except (BackToMenuException, QuitException):
        raise
    
    if choice == "1":
        console.print("[dim]Отправляем критику валидатора Оркестратору...[/]")
        result = await pipeline.handle_user_feedback(
            action="accept",
            history=history,
        )
        if result and result.success:
            return "continue"
        else:
            print_warning("Не удалось исправить код после критики валидатора")
            return None
    
    elif choice == "2":
        return await _get_user_custom_critique(pipeline, history)
    
    elif choice == "3":
        console.print("[dim]Игнорируем валидатор, переходим к тестам...[/]")
        result = await pipeline.handle_user_feedback(
            action="override",
            history=history,
        )
        if result and result.success:
            return "apply"
        elif result and not result.success:
            if result.errors:
                print_error("Тесты провалились:\n" + "\n".join(result.errors))
            return None
        else:
            return "apply"
    
    elif choice == "4":
        await pipeline.discard_pending_changes()
        print_info("Запрос отменён")
        return "cancel"
    
    return None


async def _get_user_custom_critique(pipeline: 'AgentPipeline', history: List[Dict[str, str]]) -> Optional[str]:
    """
    Get custom critique from user and run FULL validation cycle.
    
    Returns:
        'continue' - new code generated, show to user
        None - failed or cancelled
    """
    console.print("\n[bold]Введите вашу критику/замечания:[/]")
    console.print("[dim]Что именно не так с кодом? Что нужно исправить?[/]\n")
    
    try:
        user_critique = Prompt.ask("[bold cyan]Ваша критика[/]")
    except KeyboardInterrupt:
        return None
    
    if not user_critique.strip():
        print_warning("Критика не может быть пустой")
        return None
    
    console.print("\n[dim]⏳ Запускаем полный цикл обработки вашей критики...[/]")
    
    # Run FULL cycle, not just one iteration
    result = await pipeline.run_feedback_cycle(
        user_feedback=user_critique,
        history=history,
    )
    
    if result and result.success and result.pending_changes:
        # Show new code to user
        console.print("\n[bold green]✅ Код исправлен по вашей критике![/]\n")
        
        # Show code blocks
        console.print(f"[bold]📝 Обновлённый код ({len(result.code_blocks)} блоков):[/]\n")
        for block in result.code_blocks:
            console.print(f"[cyan]Файл:[/] `{block.file_path}` | [cyan]Режим:[/] {block.mode}")
            print_code_block(block.code, block.file_path)
        
        # Show diff
        print_diff_preview(result.diffs)
        
        return "continue"
    else:
        print_warning("Не удалось исправить код по вашей критике")
        if result and result.errors:
            for err in result.errors:
                console.print(f"   [red]• {err}[/]")
        return None



# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ВЫБОРА МОДЕЛИ
# ============================================================================

def print_model_selection_menu(show_router: bool = True, compact: bool = False):
    """
    Выводит меню выбора моделей с описаниями.
    
    Args:
        show_router: Показывать ли опцию автоматического роутера
        compact: Компактный режим (короткие описания)
    """
    if show_router:
        console.print("[bold cyan][r][/] 🔄 [bold]Автоматический роутер[/] (рекомендуется)")
        console.print("    [dim]Система сама выберет модель по сложности задачи:[/]")
        console.print("    [dim]  🟢 Простые → GPT-5.1 Codex Max[/]")
        console.print("    [dim]  🟡 Средние → Claude Sonnet 4.5[/]")
        console.print("    [dim]  🔴 Сложные → Claude Opus 4.5[/]")
        console.print()
    
    console.print("[bold]Или выберите фиксированную модель:[/]")
    console.print()
    
    for key, model_id, short_name, description in AVAILABLE_ORCHESTRATOR_MODELS:
        console.print(f"[bold cyan][{key}][/] [bold]{short_name}[/]")
        if compact:
            # Укорачиваем описание
            short_desc = description[:60] + "..." if len(description) > 60 else description
            console.print(f"    [dim]{short_desc}[/]")
        else:
            console.print(f"    [dim]{description}[/]")
        console.print()


def get_model_short_name(model_id: str) -> str:
    """
    Возвращает короткое название модели по её ID.
    
    Args:
        model_id: Идентификатор модели
        
    Returns:
        Короткое название или display_name из конфига
    """
    for key, mid, short_name, desc in AVAILABLE_ORCHESTRATOR_MODELS:
        if mid == model_id:
            return short_name
    return cfg.get_model_display_name(model_id)


def get_generator_model_short_name(model_id: str) -> str:
    """
    Возвращает короткое название модели генератора по её ID.
    
    Args:
        model_id: Идентификатор модели
        
    Returns:
        Короткое название
    """
    for key, mid, short_name, desc in AVAILABLE_GENERATOR_MODELS:
        if mid == model_id:
            return short_name
    return cfg.get_model_display_name(model_id)


def print_generator_model_selection_menu(compact: bool = False):
    """
    Выводит меню выбора моделей генератора с описаниями.
    
    Args:
        compact: Компактный режим (короткие описания)
    """
    console.print("[bold]Выберите модель генератора кода:[/]")
    console.print()
    
    for key, model_id, short_name, description in AVAILABLE_GENERATOR_MODELS:
        console.print(f"[bold cyan][{key}][/] [bold]{short_name}[/]")
        if compact:
            short_desc = description[:60] + "..." if len(description) > 60 else description
            console.print(f"    [dim]{short_desc}[/]")
        else:
            console.print(f"    [dim]{description}[/]")
        console.print()


async def select_generator_model() -> bool:
    """
    Интерактивный выбор модели генератора.
    
    Returns:
        True если выбор сделан, False если отменён
    """
    console.print("\n[bold]⚙️ Выбор модели генератора кода[/]\n")
    
    # Текущая модель
    current_model = state.get_current_generator_model()
    current_name = get_generator_model_short_name(current_model)
    console.print(f"[dim]Текущая модель: {current_name}[/]")
    console.print()
    
    # Выводим меню
    print_generator_model_selection_menu(compact=False)
    
    valid_choices = [key for key, _, _, _ in AVAILABLE_GENERATOR_MODELS]
    
    try:
        choice = prompt_with_navigation(
            "Выбор",
            choices=valid_choices,
            default="1",
            show_back=True,
            show_menu=True
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    # Найти выбранную модель
    for key, model_id, short_name, description in AVAILABLE_GENERATOR_MODELS:
        if key == choice:
            state.generator_model = model_id
            
            # Если pipeline уже создан — нужно пересоздать
            if state.pipeline is not None:
                console.print("[dim]Pipeline будет пересоздан с новой моделью генератора[/]")
                state.pipeline = None
            
            print_success(f"Выбрана модель генератора: {short_name}", "Настройки")
            return True
    
    console.print("[dim]Неверный выбор[/]")
    return False


async def select_prefilter_settings() -> bool:
    """
    Интерактивный выбор режима и модели Pre-filter.
    
    Returns:
        True если настройки изменены, False если отменено
    """
    console.print("\n[bold]🔍 Настройки Pre-filter[/]\n")
    
    # Показываем текущие настройки
    current_mode_label = "Обычный" if state.prefilter_mode == "normal" else "Продвинутый"
    current_model_name = cfg.get_model_display_name(state.prefilter_model) if state.prefilter_model else "По умолчанию"
    console.print(f"[dim]Текущие настройки: режим={current_mode_label}, модель={current_model_name}[/]\n")
    
    # Выбор режима
    console.print("[bold cyan]Выберите режим Pre-filter:[/]")
    console.print("  [green]1[/] - Обычный (normal) - быстрый анализ на основе имеющихся данных")
    console.print("  [green]2[/] - Продвинутый (advanced) - глубокий анализ с инструментами")
    console.print("  [yellow]0[/] - Отмена (вернуться в меню)")
    
    mode_choice = console.input("\n[bold]Ваш выбор (0-2):[/] ").strip()
    
    if mode_choice == "0" or mode_choice.lower() in ("q", "quit", "exit", "back"):
        console.print("[dim]Отменено[/]")
        return False
    
    if mode_choice == "1":
        state.prefilter_mode = "normal"
    elif mode_choice == "2":
        state.prefilter_mode = "advanced"
    else:
        console.print("[red]❌ Неверный выбор[/]")
        return False
    
    # Выбор модели — используем тот же список, что и для Оркестратора
    console.print("\n[bold cyan]Выберите модель Pre-filter:[/]\n")
    console.print("  [yellow]0[/] - Отмена (вернуться в меню)\n")
    
    for key, model_id, short_name, description in AVAILABLE_ORCHESTRATOR_MODELS:
        console.print(f"  [green]{key}[/] - {short_name}")
        # Укорачиваем описание для компактности
        short_desc = description[:80] + "..." if len(description) > 80 else description
        console.print(f"      [dim]{short_desc}[/]\n")
    
    model_choice = console.input(f"[bold]Ваш выбор (0-{len(AVAILABLE_ORCHESTRATOR_MODELS)}):[/] ").strip()
    
    if model_choice == "0" or model_choice.lower() in ("q", "quit", "exit", "back"):
        console.print("[dim]Отменено[/]")
        return False
    
    # Ищем выбранную модель
    selected_model = None
    for key, model_id, short_name, description in AVAILABLE_ORCHESTRATOR_MODELS:
        if key == model_choice:
            selected_model = model_id
            break
    
    if selected_model is None:
        console.print("[red]❌ Неверный выбор модели[/]")
        return False
    
    state.prefilter_model = selected_model
    
    console.print(f"\n[green]✅ Настройки Pre-filter обновлены:[/]")
    mode_label = "Обычный" if state.prefilter_mode == "normal" else "Продвинутый"
    console.print(f"   • Режим: {mode_label}")
    console.print(f"   • Модель: {cfg.get_model_display_name(state.prefilter_model)}")
    
    # Сохраняем настройки
    current_settings = get_current_user_settings()
    save_user_settings(current_settings)
    
    return True


# ============================================================================
# ВЫБОР МОДЕЛИ ОРКЕСТРАТОРА
# ============================================================================

async def select_orchestrator_model() -> bool:
    """
    Интерактивный выбор модели оркестратора или роутера.
    Теперь с подробными описаниями каждой модели.
    
    Returns:
        True если выбор сделан, False если отменён
    """
    console.print("\n[bold]🎯 Выбор модели оркестратора[/]\n")
    
    # Текущие настройки
    if state.use_router:
        console.print(f"[dim]Текущий режим: Автоматический роутер[/]")
    else:
        current_model = state.get_current_orchestrator_model()
        model_name = get_model_short_name(current_model)
        console.print(f"[dim]Текущая модель: {model_name}[/]")
    
    console.print()
    
    # Выводим меню с описаниями
    print_model_selection_menu(show_router=True, compact=False)
    
    try:
        choice = prompt_with_navigation(
            "Выбор",
            choices=["r", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
            default="r",
            show_back=True,
            show_menu=True
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    if choice.lower() == "r":
        state.use_router = True
        state.fixed_orchestrator_model = None
        print_success("Включён автоматический роутер", "Настройки модели")
        return True
    
    # Выбрана конкретная модель
    for key, model_id, short_name, description in AVAILABLE_ORCHESTRATOR_MODELS:
        if key == choice:
            state.use_router = False
            state.fixed_orchestrator_model = model_id
            print_success(f"Выбрана модель: {short_name}", "Настройки модели")
            return True
    
    console.print("[dim]Неверный выбор[/]")
    return False


async def toggle_type_checking() -> None:
    """
    Переключает проверку типов (mypy).
    
    По умолчанию выключена, так как многие проекты не используют типизацию
    и mypy генерирует много ложных срабатываний.
    """
    state.enable_type_checking = not state.enable_type_checking
    
    status = "включена" if state.enable_type_checking else "выключена"
    icon = "✅" if state.enable_type_checking else "❌"
    
    console.print(f"\n{icon} [bold]Проверка типов (mypy) {status}[/]\n")
    
    if state.enable_type_checking:
        console.print("[yellow]Предупреждение:[/]")
        console.print("  • mypy может генерировать много ошибок на проектах без типизации")
        console.print("  • Ошибки типов НЕ блокируют pipeline (передаются Оркестратору)")
        console.print("  • Рекомендуется для проектов с полной типизацией")
    else:
        console.print("[dim]Проверка типов отключена. Валидация включает:[/]")
        console.print("  • Синтаксис (ast.parse)")
        console.print("  • Импорты (stdlib, pip, project)")
        console.print("  • Интеграция (зависимости между файлами)")
    
    console.print()
    
    # Если pipeline уже создан — нужно пересоздать с новыми настройками
    if state.pipeline is not None:
        console.print("[dim]Pipeline будет пересоздан с новыми настройками при следующем запросе[/]")
        state.pipeline = None


# ============================================================================
# УПРАВЛЕНИЕ ПРОЕКТАМИ
# ============================================================================

async def select_project_type() -> Optional[str]:
    """
    Выбор типа проекта: существующий или новый.
    
    Returns:
        'existing', 'new' или None если отменено
        
    Raises:
        BackException, BackToMenuException, QuitException
    """
    console.print("\n[bold]📁 Выбор типа проекта[/]\n")
    
    console.print("[1] 📂 Существующий проект")
    console.print("    [dim]Индексация запустится сразу после выбора директории[/]")
    console.print()
    console.print("[2] 🆕 Новый проект")
    console.print("    [dim]Индексация запустится после одобрения изменений[/]")
    console.print()
    
    try:
        choice = prompt_with_navigation(
            "Выбор",
            choices=["1", "2"],
            default="1"
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    if choice == "1":
        return "existing"
    elif choice == "2":
        return "new"
    
    return None


async def select_existing_project() -> Optional[str]:
    """
    Выбор существующего проекта.
    Объединённый интерфейс: текущая директория, недавние проекты или ввод вручную.
    
    Returns:
        Путь к проекту или None
        
    Raises:
        BackException, BackToMenuException, QuitException
    """
    console.print("\n[bold]📂 Выбор существующего проекта[/]\n")
    
    # Собираем недавние проекты
    recent_projects = []
    if state.history_manager:
        threads = await state.history_manager.list_user_threads(state.user_id, limit=10)
        seen_paths = set()
        for t in threads:
            if t.project_path and t.project_path not in seen_paths:
                seen_paths.add(t.project_path)
                recent_projects.append((t.project_path, t.project_name))
    
    # Текущая директория
    current_dir = os.path.abspath(os.getcwd())
    current_dir_name = Path(current_dir).name
    
    # Выводим опции
    console.print(f"[bold cyan][1][/] 📁 Текущая директория: [bold]{current_dir_name}/[/]")
    console.print(f"    [dim]{current_dir}[/]")
    console.print()
    
    if recent_projects:
        console.print("[bold]Недавние проекты:[/]")
        for i, (path, name) in enumerate(recent_projects[:5], start=2):
            project_name = name or Path(path).name
            exists_marker = "[green]✓[/]" if os.path.isdir(path) else "[red]✗[/]"
            console.print(f"[bold cyan][{i}][/] {exists_marker} 📂 {project_name}")
            console.print(f"    [dim]{path}[/]")
        console.print()
        next_num = len(recent_projects[:5]) + 2
    else:
        next_num = 2
    
    console.print(f"[bold cyan][{next_num}][/] ✏️  Ввести путь вручную")
    console.print()
    
    # Формируем список допустимых выборов
    valid_choices = [str(i) for i in range(1, next_num + 1)]
    
    try:
        choice = prompt_with_navigation(
            "Выбор",
            choices=valid_choices,
            default="1"
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    choice_num = int(choice)
    
    # Текущая директория
    if choice_num == 1:
        return current_dir
    
    # Недавние проекты
    elif choice_num >= 2 and choice_num < next_num:
        project_idx = choice_num - 2
        if project_idx < len(recent_projects):
            selected_path = recent_projects[project_idx][0]
            if os.path.isdir(selected_path):
                return selected_path
            else:
                print_warning(f"Директория не существует: {selected_path}")
                return await select_existing_project()  # Повторить
    
    # Ввод вручную
    elif choice_num == next_num:
        console.print("\n[dim]Введите полный путь к директории проекта[/]")
        try:
            path = prompt_with_navigation("Путь")
        except (BackException, BackToMenuException, QuitException):
            raise
        
        path = os.path.expanduser(path.strip().strip('"\''))
        
        if not os.path.isdir(path):
            print_error(f"Директория не найдена: {path}")
            return await select_existing_project()  # Повторить
        
        return os.path.abspath(path)
    
    return None


async def create_new_project() -> Optional[str]:
    """
    Создание нового проекта (директории).
    
    Returns:
        Путь к новому проекту или None
        
    Raises:
        BackException, BackToMenuException, QuitException
    """
    console.print("\n[bold]🆕 Создание нового проекта[/]\n")
    
    console.print("Введите путь для нового проекта.")
    console.print("[dim]Директория будет создана, если не существует.[/]")
    console.print()
    
    try:
        path = prompt_with_navigation(
            "Путь",
            default="./new_project"
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    path = os.path.expanduser(path.strip().strip('"\''))
    
    try:
        os.makedirs(path, exist_ok=True)
        abs_path = os.path.abspath(path)
        print_success(f"Создана директория: {abs_path}")
        return abs_path
    except Exception as e:
        print_error(f"Не удалось создать директорию: {e}")
        logger.error(f"Ошибка создания директории: {e}", exc_info=True)
        return None


async def build_project_indexes(project_dir: str) -> bool:
    """
    Создаёт индексные карты и карту проекта.
    Вызывается сразу после выбора директории для существующих проектов,
    или после применения изменений для новых проектов.
    
    Args:
        project_dir: Путь к директории проекта
        
    Returns:
        True если индексация успешна, False иначе
    """
    from app.services.index_manager import FullIndexBuilder, IncrementalIndexUpdater
    
    console.print(f"\n[bold cyan]📊 Индексация проекта: {project_dir}[/]\n")
    
    ai_agent_dir = Path(project_dir) / ".ai-agent"
    index_exists = (ai_agent_dir / "semantic_index.json").exists()
    
    try:
        if index_exists:
            # Инкрементальное обновление
            console.print("[dim]Обнаружен существующий индекс. Выполняется синхронизация...[/]")
            
            updater = IncrementalIndexUpdater(project_dir)
            
            with console.status("[bold cyan]🔄 Синхронизация индексов...[/]") as status:
                def on_progress(message: str, current: int, total: int):
                    if total > 0:
                        status.update(f"[bold cyan]🔄 {message} ({current}/{total})[/]")
                    else:
                        status.update(f"[bold cyan]🔄 {message}[/]")
                
                stats = await updater.sync(on_progress=on_progress)
            
            # Выводим статистику
            console.print(f"[green]✓[/] Синхронизация завершена:")
            console.print(f"   • Новых файлов: {stats.new_files}")
            console.print(f"   • Изменённых: {stats.modified_files}")
            console.print(f"   • Удалённых: {stats.deleted_files}")
            console.print(f"   • Без изменений: {stats.unchanged_files}")
            
            if stats.ai_descriptions_generated > 0:
                console.print(f"   • AI описаний создано: {stats.ai_descriptions_generated}")
            
        else:
            # Полная индексация
            console.print("[dim]Индекс не найден. Выполняется полная индексация...[/]")
            console.print("[dim]Это может занять несколько минут для больших проектов.[/]")
            
            builder = FullIndexBuilder(project_dir)
            
            with console.status("[bold cyan]📊 Индексация проекта...[/]") as status:
                def on_progress(message: str, current: int, total: int):
                    if total > 0:
                        pct = int(current / total * 100)
                        status.update(f"[bold cyan]📊 {message} ({pct}%)[/]")
                    else:
                        status.update(f"[bold cyan]📊 {message}[/]")
                
                stats = await builder.build(on_progress=on_progress)
            
            # Выводим статистику
            console.print(f"[green]✓[/] Индексация завершена:")
            console.print(f"   • Файлов кода: {stats.code_files_indexed}")
            console.print(f"   • Классов найдено: {stats.classes_found}")
            console.print(f"   • Функций найдено: {stats.functions_found}")
            console.print(f"   • Всего токенов: {stats.code_tokens_total:,}")
            
            if stats.ai_descriptions_generated > 0:
                console.print(f"   • AI описаний создано: {stats.ai_descriptions_generated}")
            
            if stats.index_compressed:
                console.print(f"   [yellow]• Индекс был сжат (превышен лимит токенов)[/]")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка индексации проекта: {e}", exc_info=True)
        log_ai_error(e, "project_indexing", request_data={"project_dir": project_dir})
        print_error(f"Не удалось проиндексировать проект: {e}")
        return False


# [ИСПРАВЛЕНИЕ 1] Изменена функция load_project_index — теперь возвращает {} вместо None
async def load_project_index(project_dir: str) -> Dict[str, Any]:
    """
    Загружает семантический индекс проекта.
    
    ИСПРАВЛЕНО: Теперь всегда возвращает Dict (пустой {} при ошибке или отсутствии),
    а не None. Это предотвращает проблемы с пустым индексом в pipeline.
    
    Args:
        project_dir: Путь к директории проекта
        
    Returns:
        Dict с индексом или пустой {} если индекс не найден/ошибка
    """
    try:
        ai_agent_dir = Path(project_dir) / ".ai-agent"
        
        # Пробуем загрузить сжатый индекс
        compressed_path = ai_agent_dir / "semantic_index_compressed.json"
        regular_path = ai_agent_dir / "semantic_index.json"
        
        index_path = compressed_path if compressed_path.exists() else regular_path
        
        if not index_path.exists():
            logger.warning(f"Индекс не найден: {index_path}")
            # [ИСПРАВЛЕНИЕ] Возвращаем пустой dict вместо None
            return {}
        
        import json
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
        
        files_count = len(index.get("files", {})) or len(index.get("classes", []))
        
        # [ИСПРАВЛЕНИЕ] Добавлена проверка на пустой индекс
        if files_count == 0:
            logger.warning("Индекс загружен, но пуст (0 элементов)")
            console.print("[yellow]⚠️ Индекс пуст — возможно, проект не содержит Python файлов[/]")
        else:
            console.print(f"[green]✓[/] Загружен индекс ({files_count} элементов)")
        
        return index
        
    except Exception as e:
        logger.error(f"Ошибка загрузки индекса: {e}", exc_info=True)
        print_error(f"Не удалось загрузить индекс: {e}")
        # [ИСПРАВЛЕНИЕ] Возвращаем пустой dict вместо None при ошибке
        return {}


async def run_incremental_update(project_dir: str) -> bool:
    """
    Запускает инкрементальное обновление индексов.
    Вызывается перед каждым запросом в диалоге (для существующих проектов).
    
    Args:
        project_dir: Путь к проекту
        
    Returns:
        True если обновление успешно
    """
    from app.services.index_manager import IncrementalIndexUpdater
    
    try:
        updater = IncrementalIndexUpdater(project_dir)
        
        # Быстрая проверка изменений
        stats = await updater.sync(on_progress=lambda m, c, t: None)
        
        # Логируем только если были изменения
        changes = stats.new_files + stats.modified_files + stats.deleted_files
        if changes > 0:
            logger.info(
                f"Инкрементальное обновление: "
                f"+{stats.new_files} ~{stats.modified_files} -{stats.deleted_files}"
            )
            console.print(
                f"[dim]🔄 Индекс обновлён: "
                f"+{stats.new_files} ~{stats.modified_files} -{stats.deleted_files}[/]"
            )
        
        return True
        
    except Exception as e:
        logger.warning(f"Ошибка инкрементального обновления: {e}")
        # Не прерываем работу, просто логируем
        return False


# ============================================================================
# УПРАВЛЕНИЕ ДИАЛОГАМИ
# ============================================================================

async def create_new_thread() -> Optional[Thread]:
    """Создаёт новый тред диалога"""
    if not state.history_manager:
        return None
    
    title = f"Сессия {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if state.project_dir:
        title = f"{Path(state.project_dir).name} - {title}"
    elif state.mode == "general":
        if state.is_legal_mode:
            title = f"Legal Chat - {title}"
        else:
            title = f"General Chat - {title}"
    
    thread = await state.history_manager.create_thread(
        user_id=state.user_id,
        project_path=state.project_dir,
        title=title,
    )
    
    console.print(f"[green]✓[/] Создан новый диалог: {thread.title}")
    return thread


async def find_or_create_thread_for_project(project_dir: Optional[str]) -> Optional[Thread]:
    """
    Находит существующий тред для проекта или предлагает создать новый.
    Показывает превью последнего запроса для каждого диалога.
    
    Для General Chat разделяет треды по подрежиму (Legal / Обычный)
    на основе префикса в названии треда.
    
    Args:
        project_dir: Путь к проекту (None для General Chat)
        
    Returns:
        Thread для использования
        
    Raises:
        BackException, BackToMenuException, QuitException
    """
    if not state.history_manager:
        return None
    
    # Ищем существующие треды для этого проекта
    threads = await state.history_manager.list_user_threads(state.user_id, limit=20)
    
    # Фильтруем по проекту
    if project_dir:
        matching_threads = [t for t in threads if t.project_path == project_dir]
    else:
        # Для General Chat — треды без проекта, разделённые по подрежиму
        no_project_threads = [t for t in threads if not t.project_path]
        
        if state.is_legal_mode:
            # Legal режим — только треды с префиксом "Legal Chat"
            matching_threads = [
                t for t in no_project_threads 
                if (t.title or "").startswith("Legal Chat")
            ]
        else:
            # Обычный режим — треды БЕЗ префикса "Legal Chat"
            matching_threads = [
                t for t in no_project_threads 
                if not (t.title or "").startswith("Legal Chat")
            ]
    
    if not matching_threads:
        # Нет существующих — создаём новый
        return await create_new_thread()
    
    # Есть существующие — спрашиваем пользователя
    submode_label = ""
    if state.mode == "general":
        submode_label = " [Legal]" if state.is_legal_mode else " [Обычный]"
    
    console.print(f"\n[bold]💬 Найдено {len(matching_threads)} диалог(ов){submode_label}[/]\n")
    
    # Показываем последние 5 с превью
    display_threads = matching_threads[:5]
    
    table = Table(show_header=True, box=box.ROUNDED)
    table.add_column("#", style="bold cyan", width=3)
    table.add_column("Название", max_width=30)
    table.add_column("Сообщ.", width=7)
    table.add_column("Обновлён", width=12)
    table.add_column("Последний запрос", max_width=50)
    
    # Собираем превью асинхронно
    previews = []
    for t in display_threads:
        preview = await get_thread_preview(t)
        previews.append(preview)
    
    for i, (t, preview) in enumerate(zip(display_threads, previews), 1):
        # Форматируем дату
        date_str = t.updated_at[5:16].replace('T', ' ') if t.updated_at else "-"
        
        # Укорачиваем название
        title = t.title[:27] + "..." if len(t.title) > 30 else t.title
        
        table.add_row(
            str(i),
            title,
            str(t.message_count),
            date_str,
            preview
        )
    
    console.print(table)
    console.print()
    console.print("[n] Создать новый диалог")
    console.print()
    
    try:
        choice = prompt_with_navigation(
            "Выберите диалог или 'n' для нового",
            default="1"
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    if choice.lower() == 'n':
        return await create_new_thread()
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(display_threads):
            selected = display_threads[idx]
            console.print(f"\n[green]✓[/] Продолжаем диалог: [bold]{selected.title}[/]")
            console.print(f"   📊 Сообщений: {selected.message_count}, Токенов: {selected.total_tokens:,}")
            
            # Восстанавливаем подрежим из выбранного треда (для General Chat)
            if not project_dir and state.mode == "general":
                is_legal_thread = (selected.title or "").startswith("Legal Chat")
                state.is_legal_mode = is_legal_thread
                if is_legal_thread:
                    console.print(f"   ⚖️ Подрежим: [yellow]Legal[/]")
                else:
                    console.print(f"   💬 Подрежим: Обычный")
            
            # === ПОКАЗЫВАЕМ ИСТОРИЮ ПРИ ВХОДЕ ===
            await display_thread_history(selected, state.mode, limit=5)
            
            return selected
    except ValueError:
        pass
    
    # Неверный выбор — создаём новый
    return await create_new_thread()


async def select_thread() -> Optional[Thread]:
    """
    Выбор существующего треда из меню "История диалогов".
    Показывает превью последнего запроса с пагинацией по 5 диалогов на странице.
    
    Returns:
        Выбранный Thread или None
        
    Raises:
        BackException, BackToMenuException, QuitException
    """
    if not state.history_manager:
        return None
    
    current_page = 1
    per_page = 5
    
    while True:
        # Получаем диалоги с пагинацией
        threads, total_count, total_pages = await state.history_manager.list_threads_paginated(
            state.user_id, current_page, per_page
        )
        
        if total_count == 0:
            console.print("[dim]Нет сохранённых диалогов. Создаём новый...[/]")
            return await create_new_thread()
        
        # Заголовок с информацией о пагинации
        console.print(f"\n[bold]📜 История диалогов[/] ({total_count} шт.)")
        if total_pages > 1:
            console.print(f"[dim]Страница {current_page} из {total_pages}[/]\n")
        else:
            console.print()
        
        # Таблица диалогов
        table = Table(show_header=True, box=box.ROUNDED)
        table.add_column("#", style="bold cyan", width=3)
        table.add_column("Название", max_width=25)
        table.add_column("Проект", max_width=15)
        table.add_column("Сообщ.", width=7)
        table.add_column("Обновлён", width=12)
        table.add_column("Последний запрос", max_width=40)
        
        # Собираем превью для текущей страницы
        previews = []
        for t in threads:
            preview = await get_thread_preview(t)
            previews.append(preview)
        
        for i, (t, preview) in enumerate(zip(threads, previews), 1):
            # Форматируем данные
            date_str = t.updated_at[5:16].replace('T', ' ') if t.updated_at else "-"
            title = t.title[:22] + "..." if len(t.title) > 25 else t.title
            project = t.project_name[:12] + "..." if t.project_name and len(t.project_name) > 15 else (t.project_name or "-")
            
            table.add_row(
                str(i),
                title,
                project,
                str(t.message_count),
                date_str,
                preview[:37] + "..." if len(preview) > 40 else preview
            )
        
        console.print(table)
        
        # Навигация по страницам
        console.print()
        nav_options = []
        if current_page > 1:
            nav_options.append("[<] Предыдущая")
        if current_page < total_pages:
            nav_options.append("[>] Следующая")
        nav_options.append("[n] Новый диалог")
        
        console.print(" │ ".join(nav_options))
        
        try:
            choice = prompt_with_navigation("Выбор (номер, <, >, n)", default="1")
        except (BackException, BackToMenuException, QuitException):
            raise
        
        # Обработка навигации
        if choice == '<' and current_page > 1:
            current_page -= 1
            continue
        elif choice == '>' and current_page < total_pages:
            current_page += 1
            continue
        elif choice.lower() == 'n':
            return await create_new_thread()
        
        # Выбор диалога по номеру
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(threads):
                selected = threads[idx]
                
                # Определяем режим по проекту
                display_mode = "general"
                if selected.project_path:
                    display_mode = "ask"
                
                console.print(f"\n[green]✓[/] Выбран диалог: [bold]{selected.title}[/]")
                
                # Показываем историю
                await display_thread_history(selected, display_mode, limit=5)
                
                return selected
        except ValueError:
            pass
        
        console.print("[dim]Неверный выбор. Попробуйте снова.[/]")


async def load_conversation_history(current_query: str = "", active_model: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Загружает историю беседы из текущей сессии и преобразует её в формат для отображения.
    
    Args:
        current_query: Текущий запрос пользователя (для определения релевантности контекста).
        active_model: Текущая активная модель.
    
    Returns:
        Список словарей с ключами 'role' и 'content' для отображения в консоли.
    """
    if not state.history_manager or not state.current_thread:
        logger.warning("History manager or current thread not initialized")
        return []
    
    try:
        # Загружаем историю с информацией о сжатии
        history_messages, compression_stats = await state.history_manager.get_session_history(
            thread_id=state.current_thread.id,
            current_query=current_query,
            active_model=active_model
        )
        
        # Отображаем информацию о сжатии если оно произошло
        if compression_stats and compression_stats.was_compressed:
            console.print(f"[bold yellow]🗜️ История сжата с помощью AI:[/]")
            console.print(f"   [dim]До: {compression_stats.original_tokens:,} токенов ({compression_stats.messages_before} сообщений)[/]")
            console.print(f"   [dim]После: {compression_stats.compressed_tokens:,} токенов ({compression_stats.messages_after} сообщений)[/]")
            console.print(f"   [green]Сэкономлено: {compression_stats.tokens_saved:,} токенов ({(1 - compression_stats.compression_ratio) * 100:.1f}%)[/]")
            if compression_stats.model_used:
                model_name = cfg.get_model_display_name(compression_stats.model_used)
                console.print(f"   [dim]Модель сжатия: {model_name}[/]")
            console.print()
        
        # Преобразуем сообщения в формат для отображения
        history = []
        for msg in history_messages:
            # Нормализуем content: если это список, объединяем; если dict, преобразуем в строку
            content = msg.content
            if isinstance(content, list):
                content = "\n".join(str(item) for item in content)
            elif isinstance(content, dict):
                content = str(content)
            
            history.append({
                "role": msg.role,
                "content": content
            })
        
        # Логируем размер истории
        history_tokens = sum(len(m.get("content", "")) // 4 for m in history)
        if compression_stats and compression_stats.was_compressed:
            logger.info(f"Загружена история: {len(history)} сообщений, ~{history_tokens} токенов (сжато с {compression_stats.original_tokens} до {compression_stats.compressed_tokens}, model={active_model})")
        else:
            logger.info(f"Загружена история: {len(history)} сообщений, ~{history_tokens} токенов (model={active_model})")
        
        return history
        
    except Exception as e:
        logger.error(f"Error loading conversation history: {e}")
        return []



async def view_history():
    """Просмотр полной истории текущего диалога"""
    if not state.current_thread:
        print_info("Нет активного диалога")
        return
    
    if not state.history_manager:
        print_error("Менеджер истории недоступен")
        return
    
    messages = await state.history_manager.get_messages(state.current_thread.id)
    
    if not messages:
        print_info("В этом диалоге нет сообщений")
        return
    
    console.print(f"\n[bold]📜 Полная история диалога[/]")
    console.print(f"[dim]Тред: {state.current_thread.title}[/]")
    console.print(f"[dim]Всего сообщений: {len(messages)}[/]\n")
    console.print("═" * 60)
    
    for msg in messages:
        # Форматируем время
        time_str = ""
        if msg.created_at:
            try:
                time_str = f" ({msg.created_at[5:16].replace('T', ' ')})"
            except:
                pass
        
        if msg.role == "user":
            console.print(f"\n[bold cyan]👤 Вы{time_str}:[/]")
            # Обрезаем до 5000 символов
            content = truncate_text(msg.content, 5000, "\n[dim]... (сообщение обрезано)[/]")
            console.print(Panel(content, border_style="cyan", padding=(0, 1)))
            
        elif msg.role == "assistant":
            console.print(f"\n[bold green]🤖 Ассистент{time_str}:[/]")
            
            if state.mode == "agent":
                # В режиме Agent — показываем код и статус
                await _display_agent_response(msg.content)
            else:
                # Полный ответ с Markdown
                try:
                    console.print(Panel(
                        Markdown(msg.content),
                        border_style="green",
                        padding=(0, 1),
                    ))
                except Exception:
                    console.print(Panel(msg.content, border_style="green", padding=(0, 1)))
        
        elif msg.role == "tool":
            # Tool messages — компактно
            tool_name = msg.metadata.get("name", "tool") if msg.metadata else "tool"
            console.print(f"\n[dim]🔧 Инструмент: {tool_name}[/]")
            # Показываем только первые 500 символов
            content_preview = truncate_text(msg.content, 500)
            console.print(f"[dim]{content_preview}[/]")
        
        elif msg.role == "system":
            console.print(f"\n[dim]⚙️ Системное{time_str}:[/]")
            content_preview = truncate_text(msg.content, 300)
            console.print(f"[dim]{content_preview}[/]")
        
        console.print("─" * 60)
    
    console.print("\n[dim]Конец истории. Нажмите Enter для продолжения...[/]")
    input()



async def export_dialog_to_markdown():
    """Экспортирует текущий диалог в файл .md"""
    if not state.current_thread:
        print_info("Нет активного диалога для экспорта")
        return
    
    if not state.history_manager:
        print_error("Менеджер истории недоступен")
        return
    
    messages = await state.history_manager.get_messages(state.current_thread.id)
    
    if not messages:
        print_info("В этом диалоге нет сообщений для экспорта")
        return
    
    # Формируем имя файла
    safe_title = re.sub(r'[^\w\s-]', '', state.current_thread.title)
    safe_title = re.sub(r'\s+', '_', safe_title)[:50]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"dialog_{safe_title}_{timestamp}.md"
    
    # Формируем содержимое
    lines = []
    lines.append(f"# {state.current_thread.title}")
    lines.append("")
    lines.append(f"**ID диалога:** `{state.current_thread.id}`")
    lines.append(f"**Дата экспорта:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if state.project_dir:
        lines.append(f"**Проект:** `{state.project_dir}`")
    
    mode_names = {"ask": "Вопросы", "agent": "Агент", "general": "Общий Чат"}
    lines.append(f"**Режим:** {mode_names.get(state.mode, state.mode)}")
    lines.append(f"**Всего сообщений:** {len(messages)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    for msg in messages:
        # Форматируем время
        time_str = ""
        if msg.created_at:
            try:
                time_str = f" ({msg.created_at[:19].replace('T', ' ')})"
            except:
                pass
        
        if msg.role == "user":
            lines.append(f"## 👤 Пользователь{time_str}")
            lines.append("")
            lines.append(msg.content)
            lines.append("")
            
        elif msg.role == "assistant":
            lines.append(f"## 🤖 Ассистент{time_str}")
            lines.append("")
            lines.append(msg.content)
            lines.append("")
        
        elif msg.role == "system":
            lines.append(f"## ⚙️ Системное{time_str}")
            lines.append("")
            lines.append(f"```\n{msg.content}\n```")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # Сохраняем файл
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        abs_path = os.path.abspath(filename)
        print_success(f"Диалог экспортирован в файл:\n{abs_path}", "Экспорт")
        
    except Exception as e:
        logger.error(f"Ошибка экспорта диалога: {e}")
        print_error(f"Не удалось сохранить файл: {e}")
        
        
async def rename_current_thread():
    """Изменяет название текущего диалога"""
    if not state.current_thread:
        print_info("Нет активного диалога")
        return
    
    if not state.history_manager:
        print_error("Менеджер истории недоступен")
        return
    
    console.print(f"\n[bold]Текущее название:[/] {state.current_thread.title}")
    console.print("[dim]Введите новое название или нажмите Enter для отмены[/]\n")
    
    try:
        new_title = Prompt.ask("[bold cyan]Новое название[/]")
    except KeyboardInterrupt:
        console.print()
        return
    
    if not new_title.strip():
        print_info("Название не изменено")
        return
    
    try:
        # Обновляем в базе данных
        await state.history_manager.update_thread_title(
            thread_id=state.current_thread.id,
            new_title=new_title.strip()
        )
        
        # Обновляем в состоянии
        state.current_thread.title = new_title.strip()
        
        print_success(f"Название изменено на: {new_title.strip()}", "Переименование")
        
    except Exception as e:
        logger.error(f"Ошибка переименования диалога: {e}")
        print_error(f"Не удалось изменить название: {e}")        
   
   
async def generate_thread_title_from_query(query: str) -> str:
    """
    Генерирует название диалога на основе первого запроса пользователя.
    Берёт первые 50 символов запроса, убирая лишнее.
    """
    # Очищаем запрос
    title = query.strip()
    
    # Убираем переносы строк
    title = ' '.join(title.split())
    
    # Обрезаем до 50 символов
    if len(title) > 50:
        # Пытаемся обрезать по слову
        title = title[:50]
        last_space = title.rfind(' ')
        if last_space > 30:
            title = title[:last_space]
        title += "..."
    
    # Если название слишком короткое или пустое
    if len(title) < 3:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        if state.mode == "general":
            return f"Общий чат - {timestamp}"
        elif state.mode == "agent":
            return f"Агент - {timestamp}"
        else:
            return f"Вопрос - {timestamp}"
    
    return title


async def update_thread_title_if_first_message(query: str):
    """
    Обновляет название диалога, если это первое сообщение пользователя.
    """
    if not state.current_thread or not state.history_manager:
        return
    
    # Проверяем, есть ли уже сообщения пользователя в этом треде
    messages = await state.history_manager.get_messages(state.current_thread.id)
    user_messages = [m for m in messages if m.role == "user"]
    
    # Если это первое сообщение (или пока нет сообщений)
    if len(user_messages) <= 1:
        new_title = await generate_thread_title_from_query(query)
        
        try:
            await state.history_manager.update_thread_title(
                thread_id=state.current_thread.id,
                new_title=new_title
            )
            state.current_thread.title = new_title
            logger.debug(f"Thread title updated to: {new_title}")
        except Exception as e:
            logger.warning(f"Failed to update thread title: {e}")   
    
    
async def save_attached_files_to_history(files: List[Dict[str, Any]], thread_id: str) -> List[str]:
    """
    Сохраняет прикреплённые файлы в историю беседы как сообщения пользователя.
    Файлы будут в контексте и сожмутся вместе с остальной историей при необходимости.
    """
    if not files or not state.history_manager:
        return []
    
    saved_files = []
    
    for f in files:
        filename = f.get('filename', f.get('name', 'unknown'))
        content = f.get('content', '')
        file_type = f.get('file_type', f.get('type', 'text'))
        tokens = f.get('tokens', len(content) // 4)
        
        # Формируем сообщение с содержимым файла
        file_message = f"[Прикреплённый файл: {filename}]\n"
        file_message += f"Тип: {file_type} | Размер: ~{tokens} токенов\n"
        file_message += "---\n"
        file_message += content[:100000]  # Лимит на размер
        if len(content) > 100000:
            file_message += "\n\n[... файл обрезан, слишком большой ...]"
        
        # Метаданные
        metadata = {
            "type": "attached_file",
            "filename": filename,
            "file_type": file_type,
            "original_tokens": tokens,
        }
        
        try:
            # Сохраняем как USER сообщение — будет в контексте!
            await state.history_manager.add_message(
                thread_id=thread_id,
                role="user",  # НЕ system!
                content=file_message,
                tokens=tokens,
                metadata=metadata
            )
            saved_files.append(filename)
            logger.debug(f"File saved to history as user message: {filename}")
        except Exception as e:
            logger.error(f"Failed to save file {filename} to history: {e}")
    
    return saved_files    
      
        
# ============================================================================
# ПРИКРЕПЛЕНИЕ ФАЙЛОВ (GENERAL CHAT)
# ============================================================================

async def attach_files():
    """Прикрепление файлов для General Chat"""
    console.print("\n[bold]📎 Прикрепление файлов[/]\n")
    
    if state.attached_files:
        console.print(f"[dim]Уже прикреплено файлов: {len(state.attached_files)}[/]")
        for f in state.attached_files:
            console.print(f"   • {f['name']} ({f['tokens']:,} токенов)")
        console.print()
    
    console.print("Введите пути к файлам через запятую или по одному на строке.")
    console.print("Поддерживаемые форматы: PDF, DOCX, TXT, MD, JSON, CSV, PY и др.")
    console.print("Введите 'готово' или пустую строку для завершения.")
    console.print("Введите 'очистить' для удаления всех прикреплённых файлов.\n")
    
    token_counter = TokenCounter()
    total_tokens = sum(f['tokens'] for f in state.attached_files)
    max_tokens = cfg.MAX_USER_FILES_TOKENS
    
    while True:
        try:
            path_input = prompt_with_navigation(
                "Путь к файлу",
                show_back=True,
                show_menu=False
            )
        except BackException:
            break  # Выход из цикла добавления файлов
        except BackToMenuException:
            raise
        except QuitException:
            raise
        
        if not path_input or path_input.lower() == 'готово':
            break
        
        if path_input.lower() == 'очистить':
            state.attached_files = []
            total_tokens = 0
            console.print("[green]✓[/] Все файлы удалены")
            continue
        
        # Разбиваем по запятой
        paths = [p.strip().strip('"\'') for p in path_input.split(',')]
        
        for path in paths:
            if not path:
                continue
            
            file_path = Path(os.path.expanduser(path))
            
            if not file_path.exists():
                print_warning(f"Файл не найден: {path}")
                continue
            
            if not file_path.is_file():
                print_warning(f"Не является файлом: {path}")
                continue
            
            # Проверяем, не добавлен ли уже
            if any(f['path'] == str(file_path) for f in state.attached_files):
                print_warning(f"Файл уже добавлен: {file_path.name}")
                continue
            
            try:
                # Читаем содержимое
                content = await _read_file_content(file_path)
                
                if content is None:
                    print_warning(f"Не удалось прочитать файл: {file_path.name}")
                    continue
                
                file_tokens = token_counter.count(content)
                
                # Проверяем лимит
                if total_tokens + file_tokens > max_tokens:
                    print_warning(
                        f"Превышен лимит токенов! "
                        f"({total_tokens + file_tokens:,} > {max_tokens:,})"
                    )
                    continue
                
                state.attached_files.append({
                    'name': file_path.name,
                    'path': str(file_path),
                    'content': content,
                    'tokens': file_tokens,
                })
                
                total_tokens += file_tokens
                console.print(
                    f"[green]✓[/] Добавлен: {file_path.name} "
                    f"({file_tokens:,} токенов, всего: {total_tokens:,}/{max_tokens:,})"
                )
                
            except Exception as e:
                logger.error(f"Ошибка чтения файла {file_path}: {e}")
                print_error(f"Ошибка при чтении {file_path.name}: {e}")
    
    if state.attached_files:
        console.print(f"\n[green]✓[/] Прикреплено файлов: {len(state.attached_files)}")
    else:
        console.print("[dim]Файлы не прикреплены[/]")


async def _read_file_content(file_path: Path) -> Optional[str]:
    """Читает содержимое файла с поддержкой разных форматов"""
    suffix = file_path.suffix.lower()
    
    try:
        # Текстовые файлы
        text_extensions = (
            '.txt', '.md', '.py', '.js', '.ts', '.json', '.csv', '.xml',
            '.html', '.css', '.yaml', '.yml', '.ini', '.cfg', '.conf',
            '.sh', '.bash', '.sql', '.go', '.rs', '.java', '.c', '.cpp',
            '.h', '.hpp', '.rb', '.php', '.swift', '.kt', '.scala'
        )
        
        if suffix in text_extensions:
            try:
                return file_path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                return file_path.read_text(encoding='latin-1')
        
        # PDF
        elif suffix == '.pdf':
            try:
                import pypdf
                reader = pypdf.PdfReader(str(file_path))
                text_parts = []
                for page in reader.pages:
                    text_parts.append(page.extract_text() or "")
                return "\n\n".join(text_parts)
            except ImportError:
                logger.warning("pypdf не установлен, PDF файлы не поддерживаются")
                print_warning("Для PDF файлов установите: pip install pypdf")
                return None
        
        # DOCX
        elif suffix == '.docx':
            try:
                import docx
                doc = docx.Document(str(file_path))
                text_parts = [para.text for para in doc.paragraphs]
                return "\n\n".join(text_parts)
            except ImportError:
                logger.warning("python-docx не установлен, DOCX файлы не поддерживаются")
                print_warning("Для DOCX файлов установите: pip install python-docx")
                return None
        
        else:
            # Пробуем как текст
            try:
                return file_path.read_text(encoding='utf-8')
            except:
                return None
                
    except Exception as e:
        logger.error(f"Ошибка чтения файла {file_path}: {e}")
        return None


# ============================================================================
# ОБРАБОТЧИКИ РЕЖИМОВ
# ============================================================================

async def handle_ask_mode(query: str):
    """
    Обработка режима Вопросов - Анализ кода с генерацией кода для копирования.
    
    Реализовано ИДЕНТИЧНО test_agents.py:
    1. Загрузка индекса через load_semantic_index
    2. Router → Pre-filter → Orchestrator → Code Generator
    3. Отображение результатов с возможностью копирования
    4. Сохранение в историю беседы
    
    ОБНОВЛЕНО: Детальное отображение всех этапов в терминале:
    - Вызовы инструментов Оркестратора (в реальном времени)
    - Анализ Оркестратора (с переводом на русский)
    - Инструкция для Code Generator
    - Сгенерированный код с пояснениями
    
    ОБНОВЛЕНО: Отдельный парсер для ASK режима с детальным логированием
    
    ОБНОВЛЕНО: Сохранение полной инструкции Оркестратора для экспорта
    """
    import json
    from datetime import datetime
    from pathlib import Path
    
    # =========================================================================
    # ПРОВЕРКА НАЛИЧИЯ ТРЕДА (ВЫБОР БЕСЕДЫ)
    # =========================================================================
    if not state.current_thread:
        console.print("\n[yellow]⚠️ Нет активного диалога. Выбираем или создаём...[/]")
        try:
            state.current_thread = await find_or_create_thread_for_project(state.project_dir)
        except (BackException, BackToMenuException, QuitException):
            raise
        
        if not state.current_thread:
            print_error("Не удалось создать диалог")
            return
    
    # =========================================================================
    # НАСТРОЙКА ЛОГИРОВАНИЯ ДЛЯ ASK MODE
    # =========================================================================
    
    # Создаём директорию для логов ASK режима
    ask_log_dir = Path("logs/ask_mode")
    ask_log_dir.mkdir(parents=True, exist_ok=True)
    
    # Генерируем имя файла лога для этой сессии
    session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_log_file = ask_log_dir / f"ask_session_{session_timestamp}.log"
    parser_log_file = ask_log_dir / f"code_parser_{session_timestamp}.log"
    
    def write_session_log(stage: str, data: dict):
        """Записывает этап в лог сессии"""
        try:
            with open(session_log_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {stage}\n")
                f.write(f"{'='*60}\n")
                for key, value in data.items():
                    if isinstance(value, str) and len(value) > 500:
                        f.write(f"{key}: ({len(value)} chars)\n")
                        f.write(f"{value[:500]}...\n")
                    else:
                        f.write(f"{key}: {value}\n")
        except Exception as e:
            logger.warning(f"Failed to write session log: {e}")
    
    # =========================================================================
    # ТРЕЙСЕР ДЛЯ ДИАГНОСТИКИ
    # =========================================================================
    trace_data = {
        "timestamp": datetime.now().isoformat(),
        "query": query,
        "stages": [],
        "error": None,
        "final_status": None,
    }
    
    def trace_stage(stage_name: str, data: dict):
        """Добавляет этап в трейс"""
        entry = {
            "stage": stage_name,
            "timestamp": datetime.now().isoformat(),
            **data
        }
        trace_data["stages"].append(entry)
        logger.debug(f"[TRACE] {stage_name}: {data}")
        write_session_log(stage_name, data)
    
    def save_trace(error: Exception = None):
        """Сохраняет трейс в файл"""
        try:
            trace_dir = Path("logs/ask_mode_traces")
            trace_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if error:
                trace_data["error"] = {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                }
                filename = f"trace_ERROR_{timestamp}.json"
            else:
                filename = f"trace_OK_{timestamp}.json"
            
            trace_path = trace_dir / filename
            
            with open(trace_path, 'w', encoding='utf-8') as f:
                json.dump(trace_data, f, ensure_ascii=False, indent=2, default=str)
            
            logger.info(f"Trace saved to: {trace_path}")
            
            if error:
                console.print(f"[red]📋 Трейс ошибки сохранён: {trace_path}[/]")
                
        except Exception as e:
            logger.error(f"Failed to save trace: {e}")
    
    # =========================================================================
    # НАЧАЛО ОСНОВНОЙ ЛОГИКИ
    # =========================================================================
    
    # Записываем начало сессии
    write_session_log("SESSION_START", {
        "query": query,
        "project_dir": state.project_dir,
        "mode": state.mode,
        "thread_id": state.current_thread.id if state.current_thread else None,
        "session_log": str(session_log_file),
        "parser_log": str(parser_log_file),
    })
    
    try:
        from app.agents.orchestrator import orchestrate, orchestrate_agent
        from app.agents.router import route_request
        from app.builders.semantic_index_builder import create_chunks_list_auto
        from app.services.project_map_builder import get_project_map_for_prompt
        from app.services.index_manager import load_semantic_index
        from app.agents.code_generator import (
            generate_code,
            parse_ask_mode_code_blocks,
            extract_explanation_from_response,
            CodeBlock,
            CodeGeneratorResult,
        )
        
        trace_stage("IMPORTS", {"status": "success"})
        
    except ImportError as e:
        trace_stage("IMPORTS", {"status": "failed", "error": str(e)})
        save_trace(e)
        logger.error(f"Ошибка импорта модулей: {e}", exc_info=True)
        print_error(f"Не удалось загрузить необходимые модули: {e}")
        return
    
    async def save_message(role: str, content: str):
        """Вспомогательная функция для сохранения сообщений в историю"""
        if state.history_manager and state.current_thread:
            # Оценка токенов: 1 токен ≈ 4 символа (стандартная эвристика)
            tokens = len(content) // 4
            await state.history_manager.add_message(
                thread_id=state.current_thread.id,
                role=role,
                content=content,
                tokens=tokens
            )
    
    # === Вспомогательная функция: валидация инструкции (из test_agents.py) ===
    def validate_instruction(instr: str) -> tuple[bool, str]:
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
        has_file = any([
            "**File:**" in instr,
            "### FILE:" in instr,
            "FILE:" in instr,
            "app/" in instr,
            "src/" in instr,
            ".py" in instr,
        ])
        if not has_file:
            return False, "Missing file specification"
        return True, ""
    
    try:
        # =====================================================================
        # ШАГ 0: ЗАГРУЗКА ИНДЕКСА (как в test_agents.py)
        # =====================================================================
        project_index: Dict[str, Any] = {}
        
        trace_stage("INDEX_START", {
            "project_dir": state.project_dir,
            "is_new_project": state.is_new_project,
        })
        
        if state.project_dir:
            # Инкрементальное обновление (если не новый проект)
            if not state.is_new_project:
                await run_incremental_update(state.project_dir)
            
            # Загружаем индекс через load_semantic_index (как в test_agents.py)
            with console.status("[bold cyan]📂 Загрузка индекса проекта...[/]"):
                loaded_index = load_semantic_index(state.project_dir)
                
                if loaded_index is not None:
                    project_index = loaded_index
                    
                    is_compressed = project_index.get("compressed", False)
                    if is_compressed:
                        items_count = len(project_index.get("classes", [])) + len(project_index.get("functions", []))
                    else:
                        items_count = len(project_index.get("files", {}))
                    console.print(f"[dim]Загружен {'сжатый' if is_compressed else 'полный'} индекс ({items_count} элементов)[/]")
                    
                    trace_stage("INDEX_LOADED", {
                        "compressed": is_compressed,
                        "items_count": items_count,
                        "index_keys": list(project_index.keys())[:10],
                    })
                else:
                    console.print("[yellow]⚠️ Индекс проекта не найден. Анализ будет ограничен.[/]")
                    trace_stage("INDEX_LOADED", {"status": "not_found"})
        else:
            trace_stage("INDEX_LOADED", {"status": "no_project_dir"})
        
        # Сохраняем сообщение пользователя
        await save_message("user", query)
        
        await update_thread_title_if_first_message(query)
        # =====================================================================
        # ЗАГРУЗКА ИСТОРИИ (идентично Agent и General Chat)
        # =====================================================================
        history: List[Dict[str, str]] = []
        
        trace_stage("HISTORY_START", {
            "has_history_manager": bool(state.history_manager),
            "has_current_thread": bool(state.current_thread),
            "thread_id": state.current_thread.id if state.current_thread else None,
        })
        
        history_messages = []
        compression_stats = None
        
        if state.history_manager and state.current_thread:
            history_messages, compression_stats = await state.history_manager.get_session_history(
               thread_id=state.current_thread.id,
                current_query=query
            )
            
            trace_stage("HISTORY_RAW", {
                "raw_messages_count": len(history_messages),
                "compressed": bool(compression_stats),
            })
            
            for msg in history_messages:
                if msg.role in ("user", "assistant"):
                    content = msg.content
                    
                    # Нормализуем content
                    if isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict):
                                if "text" in item:
                                    text_parts.append(item["text"])
                                elif "content" in item:
                                    text_parts.append(str(item["content"]))
                            elif isinstance(item, str):
                                text_parts.append(item)
                        content = "\n".join(text_parts) if text_parts else ""
                    elif isinstance(content, dict):
                        content = content.get("text", content.get("content", str(content)))
                    
                    if not isinstance(content, str):
                        content = str(content) if content else ""
                    
                    if content.strip():
                        history.append({"role": msg.role, "content": content})
            
            # DEBUG: показываем размер контекста
            total_chars = sum(len(h["content"]) for h in history)
            console.print(f"[dim]📊 История: {len(history)} сообщений, ~{total_chars // 4:,} токенов[/]")
            
            trace_stage("HISTORY_PROCESSED", {
                "messages_count": len(history),
                "total_chars": total_chars,
                "estimated_tokens": total_chars // 4,
            })
        else:
            trace_stage("HISTORY_PROCESSED", {
                "messages_count": 0,
                "reason": "no_manager_or_thread",
            })
        
        # =====================================================================
        # ШАГ 1: РОУТЕР
        # =====================================================================
        console.print("\n" + "=" * 60)
        console.print("[bold cyan]🔍 ШАГ 1: РОУТЕР — Выбор модели[/]")
        console.print("=" * 60)
        
        trace_stage("ROUTER_START", {
            "use_router": state.use_router,
            "fixed_model": state.get_current_orchestrator_model(),
        })
        
        try:
            if state.use_router:
                with console.status("[bold cyan]🔍 Анализ сложности запроса...[/]"):
                    routing = await route_request(query, project_index)
                    model = routing.orchestrator_model
                    model_name = get_model_short_name(model)
                    
                console.print(f"[green]✓[/] Роутер выбрал: [bold]{model_name}[/]")
                console.print(f"   [dim]Сложность: {routing.complexity_level}[/]")
                if routing.reasoning:
                    reasoning_preview = routing.reasoning[:150] + "..." if len(routing.reasoning) > 150 else routing.reasoning
                    console.print(f"   [dim]Причина: {reasoning_preview}[/]")
                    
                trace_stage("ROUTER_COMPLETE", {
                    "model": model,
                    "model_name": model_name,
                    "complexity_level": routing.complexity_level,
                    "reasoning": routing.reasoning[:200] if routing.reasoning else None,
                })
            else:
                model = state.get_current_orchestrator_model()
                model_name = get_model_short_name(model)
                console.print(f"[green]✓[/] Используется фиксированная модель: [bold]{model_name}[/]")
                
                trace_stage("ROUTER_COMPLETE", {
                    "model": model,
                    "model_name": model_name,
                    "source": "fixed",
                })
        except Exception as e:
            logger.error(f"Ошибка роутера: {e}", exc_info=True)
            model = cfg.ORCHESTRATOR_SIMPLE_MODEL
            model_name = get_model_short_name(model)
            console.print(f"[yellow]⚠️ Ошибка роутера, используется модель по умолчанию: {model_name}[/]")
            
            trace_stage("ROUTER_ERROR", {
                "error": str(e),
                "fallback_model": model,
            })
        
    # =====================================================================
        # ШАГ 2: PRE-FILTER АНАЛИЗ (СОВЕТЫ ДЛЯ ОРКЕСТРАТОРА)
        # =====================================================================
        console.print("\n" + "=" * 60)
        console.print("[bold cyan]💡 ШАГ 2: PRE-FILTER АНАЛИЗ — Советы для Оркестратора[/]")
        console.print("=" * 60)
        
        prefilter_advice_str = ""
        prefilter_advice_obj = None
        
        try:
            prefilter_advice_str, prefilter_advice_obj = await run_prefilter_analysis(
                user_query=query,
                project_dir=state.project_dir or ".",
                project_index=project_index,
                mode=state.prefilter_mode,
                model=state.prefilter_model,
                is_new_project=state.is_new_project,
                on_tool_call=_prefilter_streaming_handler
            )
            
            trace_stage("PREFILTER_ANALYSIS", {
                "mode": state.prefilter_mode,
                "has_advice": bool(prefilter_advice_str),
                "tool_calls": prefilter_advice_obj.tool_calls_made if prefilter_advice_obj else 0,
            })
        except Exception as e:
            logger.warning(f"Pre-filter analysis failed: {e}")
            trace_stage("PREFILTER_ANALYSIS_ERROR", {"error": str(e)})
        
        # Компактный индекс - читаем готовый MD файл
        compact_index = ""
        if state.project_dir:
            compact_md_path = Path(state.project_dir) / ".ai-agent" / "compact_index.md"
            if compact_md_path.exists():
                try:
                    compact_index = compact_md_path.read_text(encoding="utf-8")
                    logger.info(f"Loaded compact_index.md ({len(compact_index)} chars)")
                except Exception as e:
                    logger.warning(f"Failed to load compact_index.md: {e}")
                    compact_index = create_chunks_list_auto(project_index) if project_index else ""        
        
        project_map = get_project_map_for_prompt(state.project_dir or ".")
        
        # DEBUG: размер контекста
        from app.utils.token_counter import TokenCounter
        _tc = TokenCounter()
        _compact_tokens = _tc.count(compact_index)
        _map_tokens = _tc.count(project_map)
        console.print(f"[dim]DEBUG: compact_index = {_compact_tokens:,} токенов | project_map = {_map_tokens:,} токенов[/]")
        
        trace_stage("CONTEXT_PREPARED", {
            "compact_index_tokens": _compact_tokens,
            "project_map_tokens": _map_tokens,
        })
        
        
        # =====================================================================
        # ШАГ 3: ОРКЕСТРАТОР
        # =====================================================================
        console.print("\n" + "=" * 60)
        console.print("[bold cyan]🤖 ШАГ 3: ОРКЕСТРАТОР — Анализ и планирование[/]")
        console.print("=" * 60)
        
        # Подсчёт токенов
        history_tokens = sum(len(h["content"]) for h in history) // 4
        index_tokens = len(compact_index) // 4
        map_tokens = len(project_map) // 4
        query_tokens = len(query) // 4
        
        system_prompt_base_tokens = 15000
        first_call_tokens = system_prompt_base_tokens + history_tokens + index_tokens + map_tokens + query_tokens
        
        console.print(f"[dim]Модель: {model_name}[/]")
        console.print(f"[dim]Примерный размер контекста: ~{first_call_tokens:,} токенов[/]")
        
        trace_stage("ORCHESTRATOR_START", {
            "model": model,
            "query_length": len(query),
            "history_messages": len(history),
            "first_call_tokens_estimate": first_call_tokens,
        })
        
        try:
            use_agent_orchestrator = model in [cfg.MODEL_DEEPSEEK_REASONER, cfg.MODEL_GEMINI_3_PRO]
            
            console.print("\n[dim]⏳ Оркестратор анализирует запрос...[/]")
            
            if use_agent_orchestrator:
                orchestrator_result = await orchestrate_agent(
                    user_query=query,
                    selected_chunks=[],
                    compact_index=compact_index,
                    history=history,
                    orchestrator_model=model,
                    project_dir=state.project_dir or ".",
                    index=project_index,
                    project_map=project_map,
                    is_new_project=state.is_new_project,
                    prefilter_advice=prefilter_advice_str,
                )
            else:
                orchestrator_result = await orchestrate(
                    user_query=query,
                    selected_chunks=[],
                    compact_index=compact_index,
                    history=history,
                    orchestrator_model=model,
                    project_dir=state.project_dir or ".",
                    index=project_index,
                    project_map=project_map,
                    prefilter_advice=prefilter_advice_str,
                )
            
            trace_stage("ORCHESTRATOR_COMPLETE", {
                "analysis_length": len(orchestrator_result.analysis) if orchestrator_result.analysis else 0,
                "instruction_length": len(orchestrator_result.instruction) if orchestrator_result.instruction else 0,
                "tool_calls_count": len(orchestrator_result.tool_calls),
                "target_file": orchestrator_result.target_file,
                "target_files": orchestrator_result.target_files,
            })
            
            # Сохраняем сырой ответ Оркестратора в лог
            write_session_log("ORCHESTRATOR_RAW_RESPONSE", {
                "raw_response_length": len(orchestrator_result.raw_response) if hasattr(orchestrator_result, 'raw_response') else 0,
                "raw_response": orchestrator_result.raw_response if hasattr(orchestrator_result, 'raw_response') else "[not available]",
            })
            
        except Exception as e:
            trace_stage("ORCHESTRATOR_ERROR", {
                "error_type": type(e).__name__,
                "error_message": str(e),
            })
            save_trace(e)
            logger.error(f"Ошибка оркестратора: {e}", exc_info=True)
            print_error(f"Ошибка при обработке запроса: {e}")
            return
        
        # =====================================================================
        # ОТОБРАЖЕНИЕ ВЫЗОВОВ ИНСТРУМЕНТОВ
        # =====================================================================
        if orchestrator_result.tool_calls:
            console.print(f"\n[bold yellow]🛠️ ВЫЗОВЫ ИНСТРУМЕНТОВ ({len(orchestrator_result.tool_calls)} шт.)[/]\n")
            
            for i, tc in enumerate(orchestrator_result.tool_calls, 1):
                status_icon = "[green]✅[/]" if tc.success else "[red]❌[/]"
                
                args_formatted = []
                if isinstance(tc.arguments, dict):
                    for k, v in list(tc.arguments.items())[:3]:
                        v_str = repr(v)
                        if len(v_str) > 50:
                            v_str = v_str[:47] + "..."
                        args_formatted.append(f"{k}={v_str}")
                args_str = ", ".join(args_formatted) if args_formatted else ""
                
                console.print(f"{status_icon} {i}. [bold cyan]{tc.name}[/]({args_str})")
                
                if tc.output:
                    output_preview = tc.output[:200].replace('\n', ' ')
                    if len(tc.output) > 200:
                        output_preview += "..."
                    
                    if tc.success:
                        console.print(f"   [dim]→ {output_preview}[/]")
                    else:
                        console.print(f"   [red]→ Ошибка: {output_preview}[/]")
        else:
            console.print("\n[dim]Инструменты не вызывались[/]")
        
        # =====================================================================
        # ОТОБРАЖЕНИЕ АНАЛИЗА ОРКЕСТРАТОРА
        # =====================================================================
        if orchestrator_result.analysis:
            console.print("\n" + "-" * 60)
            console.print("[bold green]📋 АНАЛИЗ ОРКЕСТРАТОРА[/]")
            console.print("-" * 60)
            
            analysis_text = orchestrator_result.analysis
            
            if TRANSLATION_AVAILABLE and not is_mostly_russian(analysis_text):
                console.print("[dim]⏳ Перевод анализа на русский...[/]")
                try:
                    analysis_text = translate_sync(analysis_text, "code analysis from AI orchestrator")
                    console.print("[dim]✓ Переведено[/]")
                except Exception as e:
                    logger.warning(f"Failed to translate analysis: {e}")
                    console.print("[dim]⚠️ Перевод не удался, показываем оригинал[/]")
            
            console.print()
            console.print(Panel(
                Markdown(analysis_text),
                title="[bold]Анализ[/]",
                border_style=COLORS['secondary'],
                padding=(1, 2),
            ))
        
        # =====================================================================
        # ОТОБРАЖЕНИЕ ИНСТРУКЦИИ ДЛЯ CODE GENERATOR
        # =====================================================================
        if orchestrator_result.instruction:
            console.print("\n" + "-" * 60)
            console.print("[bold magenta]📝 ИНСТРУКЦИЯ ДЛЯ CODE GENERATOR[/]")
            console.print("-" * 60)
            
            instruction_text = orchestrator_result.instruction
            
            if orchestrator_result.target_files:
                console.print(f"\n[cyan]Целевые файлы:[/] {', '.join(f'`{f}`' for f in orchestrator_result.target_files)}")
            elif orchestrator_result.target_file:
                console.print(f"\n[cyan]Целевой файл:[/] `{orchestrator_result.target_file}`")
            
            console.print()
            
            display_instruction = instruction_text
            if len(instruction_text) > 3000:
                display_instruction = instruction_text[:3000] + f"\n\n[dim]... ещё {len(instruction_text) - 3000} символов[/]"
            
            console.print(Panel(
                Markdown(display_instruction),
                title="[bold]Инструкция[/]",
                border_style=COLORS['accent'],
                padding=(1, 2),
            ))
        
        # =====================================================================
        # ШАГ 4: ГЕНЕРАТОР КОДА (С НОВЫМ ПАРСЕРОМ)
        # =====================================================================
        code_result = None
        instruction = orchestrator_result.instruction
        
        is_valid, validation_error = validate_instruction(instruction)
        
        trace_stage("CODE_GEN_START", {
            "instruction_length": len(instruction) if instruction else 0,
            "is_valid": is_valid,
            "validation_error": validation_error,
        })
        
        if not is_valid:
            logger.info(f"Инструкция невалидна для Code Generator: {validation_error}")
            raw = getattr(orchestrator_result, 'raw_response', None)
            if raw and "**Task:**" in raw:
                task_idx = raw.find("**Task:**")
                instruction = raw[task_idx:].strip()
                is_valid, _ = validate_instruction(instruction)
                if is_valid:
                    logger.info(f"Извлечена инструкция из raw_response ({len(instruction)} символов)")
                    trace_stage("CODE_GEN_INSTRUCTION_EXTRACTED", {
                        "new_instruction_length": len(instruction),
                    })
        
        if is_valid:
            console.print("\n" + "=" * 60)
            console.print("[bold cyan]⚙️ ШАГ 4: CODE GENERATOR — Генерация кода[/]")
            console.print("=" * 60)
            
            try:
                file_code = None
                target_file = orchestrator_result.target_file
                
                if target_file and state.project_dir:
                    target_path = Path(state.project_dir) / target_file
                    if target_path.exists():
                        try:
                            file_code = target_path.read_text(encoding='utf-8')
                            console.print(f"[dim]Загружен целевой файл: {target_file} ({len(file_code)} символов)[/]")
                        except Exception as read_err:
                            logger.warning(f"Не удалось прочитать {target_file}: {read_err}")
                            console.print(f"[yellow]⚠️ Не удалось прочитать {target_file}[/]")
                
                trace_stage("CODE_GEN_CALL", {
                    "instruction_length": len(instruction),
                    "target_file": target_file,
                    "file_code_length": len(file_code) if file_code else 0,
                })
                
                with console.status("[bold cyan]⚙️ Code Generator работает...[/]"):
                    code_result = await generate_code(
                        instruction=instruction,
                        file_code=file_code,
                        target_file=target_file,
                        model=state.generator_model,
                    )
                
                # =====================================================================
                # НОВЫЙ ПАРСЕР ДЛЯ ASK РЕЖИМА
                # =====================================================================
                
                # Если code_result имеет raw_response, перепарсим с новым парсером
                if code_result.raw_response:
                    console.print(f"\n[dim]🔍 Парсинг ответа Code Generator...[/]")
                    
                    # Записываем сырой ответ в лог
                    write_session_log("CODE_GENERATOR_RAW_RESPONSE", {
                        "raw_response_length": len(code_result.raw_response),
                        "raw_response": code_result.raw_response,
                    })
                    
                    # Используем новый парсер с логированием в файл
                    parsed_blocks, parse_summary = parse_ask_mode_code_blocks(
                        response=code_result.raw_response,
                        default_filepath=target_file,
                        log_file=str(parser_log_file),
                    )
                    
                    # Показываем краткую сводку в консоли
                    console.print(f"[dim]   {parse_summary}[/]")
                    console.print(f"[dim]   📋 Детальный лог парсера: {parser_log_file}[/]")
                    
                    # Извлекаем explanation отдельно
                    explanation = extract_explanation_from_response(code_result.raw_response)
                    
                    # Если новый парсер нашёл больше блоков — используем его результаты
                    if len(parsed_blocks) > len(code_result.code_blocks):
                        logger.info(f"New parser found more blocks: {len(parsed_blocks)} vs {len(code_result.code_blocks)}")
                        console.print(f"[green]✓[/] Новый парсер нашёл {len(parsed_blocks)} блоков (было {len(code_result.code_blocks)})")
                        code_result.code_blocks = parsed_blocks
                        code_result.explanation = explanation or code_result.explanation
                    elif len(parsed_blocks) < len(code_result.code_blocks):
                        logger.warning(f"New parser found fewer blocks: {len(parsed_blocks)} vs {len(code_result.code_blocks)}")
                        console.print(f"[yellow]⚠️[/] Новый парсер нашёл меньше блоков ({len(parsed_blocks)} vs {len(code_result.code_blocks)})")
                    else:
                        # Одинаковое количество — проверяем консистентность
                        if explanation and not code_result.explanation:
                            code_result.explanation = explanation
                
                trace_stage("CODE_GEN_COMPLETE", {
                    "success": code_result.success,
                    "code_blocks_count": len(code_result.code_blocks),
                    "error": code_result.error,
                    "model_used": code_result.model_used,
                    "blocks_details": [
                        {"filepath": b.filepath, "language": b.language, "code_len": len(b.code)}
                        for b in code_result.code_blocks
                    ],
                })
                
                if code_result.success and code_result.code_blocks:
                    console.print(f"[green]✓[/] Сгенерировано [bold]{len(code_result.code_blocks)}[/] блок(ов) кода")
                    if code_result.model_used:
                        console.print(f"[dim]Модель: {code_result.model_used}[/]")
                elif code_result.error:
                    console.print(f"[yellow]⚠️ Ошибка генерации: {code_result.error}[/]")
                
            except Exception as e:
                trace_stage("CODE_GEN_ERROR", {"error": str(e)})
                logger.error(f"Ошибка генерации кода: {e}", exc_info=True)
                print_warning(f"Не удалось сгенерировать код: {e}")
        else:
            console.print(f"\n[dim]Code Generator пропущен: {validation_error}[/]")
        
        # =====================================================================
        # ШАГ 5: ОТОБРАЖЕНИЕ РЕЗУЛЬТАТОВ
        # =====================================================================
        console.print("\n" + "=" * 60)
        console.print("[bold green]📦 РЕЗУЛЬТАТЫ[/]")
        console.print("=" * 60)
        
        if code_result and code_result.success and code_result.code_blocks:
            console.print(Panel(
                "[bold]📝 Сгенерированный код готов к копированию[/]\n\n"
                "[dim]Скопируйте нужные блоки и вставьте в соответствующие файлы.[/]\n"
                "[dim]В режиме Вопросов код НЕ применяется автоматически.[/]",
                title="💡 Код готов",
                border_style=COLORS['success'],
            ))
            
            # Отображаем каждый блок кода
            for i, block in enumerate(code_result.code_blocks, 1):
                console.print()
                
                if len(code_result.code_blocks) > 1:
                    console.print(f"[bold cyan]━━━ Блок {i} из {len(code_result.code_blocks)} ━━━[/]")
                
                # Метаданные блока
                if block.filepath:
                    console.print(f"[cyan]📁 Файл:[/] [yellow]`{block.filepath}`[/]")
                if block.context:
                    console.print(f"[cyan]📍 Контекст:[/] {block.context}")
                    
                # Показываем ACTION и TARGET (подсказка о размещении кода)   
                placement_hint = block.get_placement_hint() if hasattr(block, 'get_placement_hint') else ""
                if placement_hint:
                    console.print(f"[cyan]🔧 Действие:[/] {placement_hint}")
                elif hasattr(block, 'action') and block.action:
                    # Fallback если get_placement_hint не сработал
                    action_info = f"{block.action}"
                    if hasattr(block, 'target') and block.target:
                        action_info += f" → {block.target}"
                    console.print(f"[cyan]🔧 Действие:[/] {action_info}")
                elif hasattr(block, 'mode') and block.mode: 
                    # Legacy: mode для обратной совместимости
                    console.print(f"[cyan]🔧 Режим:[/] {block.mode}")
                
                # Сам код с подсветкой синтаксиса
                print_code_block(block.code, block.filepath or "code.py", block.language)
            
            # Пояснения к коду (отдельный блок)
            if code_result.explanation:
                console.print("\n" + "-" * 60)
                console.print("[bold blue]📖 ПОЯСНЕНИЯ К КОДУ[/]")
                console.print("-" * 60)
                
                explanation_text = code_result.explanation
                
                if TRANSLATION_AVAILABLE and not is_mostly_russian(explanation_text):
                    console.print("[dim]⏳ Перевод пояснений на русский...[/]")
                    try:
                        explanation_text = translate_sync(explanation_text, "code explanation from AI")
                        console.print("[dim]✓ Переведено[/]")
                    except Exception as e:
                        logger.warning(f"Failed to translate explanation: {e}")
                
                console.print()
                console.print(Panel(
                    Markdown(explanation_text),
                    title="[bold]Пояснения[/]",
                    border_style=COLORS['info'],
                    padding=(1, 2),
                ))
            
            console.print()
            console.print("[dim]💡 Для автоматического применения используйте [bold]Режим Агента[/] (пункт 2 в меню)[/]")
        
        elif orchestrator_result.instruction:
            console.print("\n[bold]📝 Рекомендации от Оркестратора:[/]")
            
            instruction_text = orchestrator_result.instruction
            
            if TRANSLATION_AVAILABLE and not is_mostly_russian(instruction_text):
                console.print("[dim]⏳ Перевод рекомендаций на русский...[/]")
                try:
                    instruction_text = translate_sync(instruction_text, "code recommendations")
                except Exception as e:
                    logger.warning(f"Failed to translate instruction: {e}")
            
            console.print()
            console.print(Panel(
                Markdown(instruction_text),
                title="[bold]Рекомендации[/]",
                border_style=COLORS['primary'],
                padding=(1, 2),
            ))
        
        elif orchestrator_result.analysis:
            console.print("\n[dim]Оркестратор предоставил только анализ без рекомендаций по коду.[/]")
        
        else:
            console.print("\n[yellow]⚠️ Оркестратор не вернул результатов[/]")
        
        # =====================================================================
        # ШАГ 6: СОХРАНЕНИЕ В ИСТОРИЮ (ОБНОВЛЕНО)
        # =====================================================================
        full_response = ""
        
        # Сохраняем анализ
        if orchestrator_result.analysis:
            full_response += f"## Анализ\n\n{orchestrator_result.analysis}\n\n"
        
        # === NEW: Сохраняем инструкцию Оркестратора (для экспорта) ===
        if orchestrator_result.instruction:
            full_response += f"## Инструкция для Code Generator\n\n"
            full_response += f"<details>\n<summary>📋 Полная инструкция (нажмите чтобы развернуть)</summary>\n\n"
            full_response += f"{orchestrator_result.instruction}\n\n"
            full_response += f"</details>\n\n"
        
        # Сохраняем сгенерированный код с метаданными размещения
        if code_result and code_result.success and code_result.code_blocks:
            full_response += "## Сгенерированный код\n\n"
            
            for i, block in enumerate(code_result.code_blocks, 1):
                # Заголовок блока
                if len(code_result.code_blocks) > 1:
                    full_response += f"### Блок {i}\n\n"
                
                # Файл
                if block.filepath:
                    full_response += f"**📁 Файл:** `{block.filepath}`\n\n"
                
                # === NEW: Информация о размещении кода ===
                placement_parts = []
                
                if hasattr(block, 'action') and block.action:
                    action_descriptions = {
                        "NEW_FILE": "📄 Создать новый файл",
                        "REPLACE_FILE": "📄 Заменить содержимое файла полностью",
                        "REPLACE_CLASS": "🔄 Заменить весь класс",
                        "REPLACE_METHOD": "🔄 Заменить метод",
                        "REPLACE_FUNCTION": "🔄 Заменить функцию",
                        "INSERT_AFTER": "➕ Вставить ПОСЛЕ указанного элемента",
                        "INSERT_BEFORE": "➕ Вставить ПЕРЕД указанным элементом",
                        "INSERT_AT_END": "➕ Вставить в конец класса/файла",
                        "ADD_IMPORT": "📥 Добавить в секцию импортов",
                    }
                    action_desc = action_descriptions.get(block.action.upper(), block.action)
                    placement_parts.append(f"**🔧 Действие:** {action_desc}")
                
                if hasattr(block, 'target') and block.target:
                    placement_parts.append(f"**🎯 Цель:** `{block.target}`")
                
                if hasattr(block, 'context') and block.context:
                    placement_parts.append(f"**📍 Контекст (класс):** `{block.context}`")
                
                if placement_parts:
                    full_response += "\n".join(placement_parts) + "\n\n"
                
                # Сам код
                full_response += f"```{block.language}\n{block.code}\n```\n\n"
            
            # Пояснения
            if code_result.explanation:
                full_response += f"## Пояснения\n\n{code_result.explanation}\n\n"
        
        elif orchestrator_result.instruction:
            # Если код не сгенерирован, но есть инструкция — сохраняем как рекомендации
            full_response += f"## Рекомендации\n\n{orchestrator_result.instruction}"
        
        await save_message("assistant", full_response)
        
        # Сохраняем трейс вызовов инструментов
        if orchestrator_result.tool_calls and state.history_manager and state.current_thread:
            try:
                await state.history_manager.save_orchestration_trace(
                    thread_id=state.current_thread.id,
                    tool_calls=orchestrator_result.tool_calls,
                )
            except Exception as e:
                logger.warning(f"Не удалось сохранить трейс: {e}")
        
        # Финальный трейс
        trace_data["final_status"] = "success"
        trace_stage("COMPLETE", {
            "response_length": len(full_response),
            "code_blocks_generated": len(code_result.code_blocks) if code_result else 0,
        })
        
        console.print("\n" + "=" * 60)
        console.print("[bold green]✅ Обработка завершена[/]")
        console.print("=" * 60)
        
        # Показываем путь к логам
        console.print(f"\n[dim]📋 Логи сессии сохранены:[/]")
        console.print(f"   [dim]• Сессия: {session_log_file}[/]")
        console.print(f"   [dim]• Парсер: {parser_log_file}[/]")
        
    except Exception as e:
        trace_data["final_status"] = "error"
        save_trace(e)
        logger.error(f"Unexpected error in handle_ask_mode: {e}", exc_info=True)
        print_error(f"Неожиданная ошибка: {e}")


async def handle_agent_mode(query: str):
    """Обработка режима Агента - Автономная генерация кода с валидацией"""
    try:
        from app.agents.agent_pipeline import AgentPipeline, PipelineMode, PipelineStatus
    except ImportError as e:
        logger.error(f"Не удалось импортировать AgentPipeline: {e}", exc_info=True)
        print_error(f"Пайплайн агента недоступен: {e}")
        return

    async def save_message(role: str, content: str):
        """Локальный хелпер для Agent Mode: сохранить сообщение в историю"""
        if state.history_manager and state.current_thread:
            tokens = len(content) // 4
            await state.history_manager.add_message(
                thread_id=state.current_thread.id,
                role=role,
                content=content,
                tokens=tokens
            )

    # =====================================================================
    # PRE-FILTER АНАЛИЗ ИЛИ ПЛАНИРОВАНИЕ
    # =====================================================================
    is_planning = state.prefilter_mode == 'advanced'
    
    if is_planning:
        console.print("\n[bold cyan]💡 Этап планирования архитектуры (Pre-filter)...[/]")
    else:
        console.print("\n[bold cyan]💡 Pre-filter анализ...[/]")
    
    prefilter_advice_str = ""
    prefilter_advice_obj = None
    
    try:
        if is_planning:
            plan_accepted = False
            current_query = query
            
            while not plan_accepted:
                prefilter_advice_str, prefilter_advice_obj = await run_prefilter_analysis(
                    user_query=current_query,
                    project_dir=state.project_dir or ".",
                    project_index=state.project_index or {},
                    mode=state.prefilter_mode,
                    model=state.prefilter_model,
                    is_planning=True,
                    is_new_project=state.is_new_project,
                    on_tool_call=_prefilter_streaming_handler
                )
                
                log_pipeline_stage("PLANNING", f"Planning completed", {
                    "has_advice": bool(prefilter_advice_str),
                })
                
                # Validation of plan existence
                if state.project_dir:
                    plan_path = Path(state.project_dir) / ".ai-agent" / "current_plan.md"
                    if plan_path.exists():
                        console.print("\n[green]✓ План успешно передан в Pipeline. Файл: .ai-agent/current_plan.md[/]")
                    else:
                        console.print("\n[yellow]⚠️ План .ai-agent/current_plan.md не найден. Продолжаю работу в обычном режиме.[/]")
                        # Не выходим из функции, позволяем pipeline продолжаться без плана
                        plan_accepted = True 
                        break
                
                # Ask user for feedback
                from rich.prompt import Prompt
                console.print("\n[bold yellow]План готов. Введите '!ok' на английском языке для утверждения плана и начала реализации.[/]")
                console.print("[dim]Введите '!cancel' для отмены или напишите свои правки к плану (они будут переданы планировщику для доработки).[/]")
                
                user_input = Prompt.ask("Ваше решение")
                
                if user_input.strip() == "!ok":
                    plan_accepted = True
                elif user_input.strip() == "!cancel":
                    console.print("[yellow]Отмена операции планирования.[/]")
                    return
                else:
                    # Preserve context by passing the original query, previous plan, and new feedback
                    current_query = (
                        f"{query}\n\n"
                        f"--- PREVIOUS PLAN ---\n{prefilter_advice_str}\n\n"
                        f"--- USER FEEDBACK FOR IMPROVEMENT ---\n{user_input}"
                    )
                    console.print("[cyan]Отправляю правки планировщику...[/]")
        else:
            prefilter_advice_str, prefilter_advice_obj = await run_prefilter_analysis(
                user_query=query,
                project_dir=state.project_dir or ".",
                project_index=state.project_index or {},
                mode=state.prefilter_mode,
                model=state.prefilter_model,
                is_planning=False,
                is_new_project=state.is_new_project,
                on_tool_call=_prefilter_streaming_handler
            )
            
            log_pipeline_stage("PREFILTER_ANALYSIS", f"Pre-filter completed", {
                "mode": state.prefilter_mode,
                "has_advice": bool(prefilter_advice_str),
                "tool_calls": prefilter_advice_obj.tool_calls_made if prefilter_advice_obj else 0,
            })
    except Exception as e:
        logger.warning(f"Pre-filter analysis failed: {e}")
        log_pipeline_stage("PREFILTER_ANALYSIS", f"Pre-filter failed: {e}")


    log_pipeline_stage("START", f"Processing agent request: {query[:100]}...", {
        "project_dir": state.project_dir,
        "is_new_project": state.is_new_project,
        "use_router": state.use_router,
    })

    # === ПРОВЕРКА И ЗАГРУЗКА ИНДЕКСА ===
    if not state.project_index and not state.is_new_project:
        log_pipeline_stage("INDEX_CHECK", "Project index is empty!")
        print_warning("Индекс проекта пуст или не загружен!")
        
        if state.project_dir:
            console.print("[dim]Попытка перезагрузки индекса...[/]")
            state.project_index = await load_project_index(state.project_dir)
            if not state.project_index:
                print_error("Не удалось загрузить индекс. Режим агента недоступен.")
                return
            log_pipeline_stage("INDEX_CHECK", "Index reloaded successfully")

    # Инкрементальное обновление
    if state.project_dir and not state.is_new_project:
        await run_incremental_update(state.project_dir)

    # === ИНИЦИАЛИЗАЦИЯ PIPELINE ===
    if state.pipeline is None:
        log_pipeline_stage("PIPELINE_INIT", "Creating new AgentPipeline")
        state.pipeline = AgentPipeline(
            project_dir=state.project_dir,
            history_manager=state.history_manager,
            thread_id=state.current_thread.id if state.current_thread else None,
            project_index=state.project_index,
            enable_type_checking=state.enable_type_checking,
            generator_model=state.generator_model,  # NEW: передаём модель генератора
        )
    
    # Устанавливаем модель
    if state.use_router:
        state.pipeline.set_orchestrator_model(None)
    else:
        state.pipeline.set_orchestrator_model(state.get_current_orchestrator_model())
        
    state.pipeline.set_generator_model(state.generator_model)


    await save_message("user", query)
    
    await update_thread_title_if_first_message(query)
    history = await load_conversation_history(current_query=query)

    console.print("\n[bold cyan]🤖 Режим Агента - Автономная генерация кода[/]\n")
    console.print("=" * 60)

    # === CALLBACKS ДЛЯ ОТОБРАЖЕНИЯ ПРОГРЕССА ===
    
    def on_thinking_callback(text: str):
        """Отображает размышления агента"""
        print_thinking(text, "Размышления агента")
    
    def on_tool_call_callback(name: str, args: Dict, output: str, success: bool):
        """Отображает вызовы инструментов"""
        log_pipeline_stage("TOOL_CALL", f"{name} -> {'OK' if success else 'FAIL'}")
        print_tool_call(name, args, output, success)
    
    def on_validation_callback(result: Dict):
        """Отображает краткие результаты валидации"""
        success = result.get("success", False)
        errors = result.get("error_count", 0)
        warnings = result.get("warning_count", 0)
        
        if success:
            console.print(f"   [green]✓ Валидация пройдена[/]")
        else:
            console.print(f"   [red]✗ Ошибок: {errors}[/], [yellow]предупреждений: {warnings}[/]")
    
    def on_status_callback(status: 'PipelineStatus', message: str):
        """Отображает статус pipeline"""
        status_icons = {
            "analyzing": "🎯",
            "generating": "⚙️",
            "validating": "🔍",
            "applying": "📝",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫",
            "awaiting_confirmation": "⏳",
        }
        icon = status_icons.get(status.value, "▶️")
        console.print(f"\n[bold]{icon} {message}[/]")
    
    def on_stage_callback(stage: str, message: str, details: Optional[Dict] = None):
        """
        Отображает детальные этапы pipeline.
        Ключевой callback для отображения каждой итерации цикла.
        """
        stage_icons = {
            "ITERATION": "🔄",
            "ORCHESTRATOR": "🎯",
            "ORCHESTRATOR_DECISION": "🤔",
            "INSTRUCTION": "📋",
            "CODE_GEN": "⚙️",
            "VALIDATION": "🔍",
            "AI_VALIDATION": "🤖",
            "TESTS": "🧪",
            "RUNTIME": "▶️",  # NEW: отдельная иконка для runtime
            "FEEDBACK": "🔄",
            "COMPLETE": "✅",
            "DIRECT_ANSWER": "💬",
            "DELETIONS": "🗑️",
        }
        icon = stage_icons.get(stage, "▶️")
        
        # === ITERATION — новый цикл ===
        if stage == "ITERATION":
            iteration = details.get("iteration", "?") if details else "?"
            console.print(f"\n{'='*60}")
            console.print(f"[bold cyan]{icon} ИТЕРАЦИЯ {iteration}[/]")
            console.print(f"{'='*60}")
        
        # === INSTRUCTION — показываем инструкцию ===
        elif stage == "INSTRUCTION" and details and details.get("instruction"):
            instruction = details["instruction"]
            print_instruction(instruction, "Инструкция для Code Generator")
        
        # === ORCHESTRATOR_DECISION — решение по AI Validator ===
        elif stage == "ORCHESTRATOR_DECISION" and details:
            decision = details.get("decision", "")
            reasoning = details.get("reasoning", "")
            
            if reasoning and TRANSLATION_AVAILABLE and not is_mostly_russian(reasoning):
                reasoning = translate_sync(reasoning, "orchestrator reasoning")
            
            if decision == "OVERRIDE":
                console.print(Panel(
                    f"[bold green]Решение: НЕ СОГЛАСЕН с валидатором[/]\n\n"
                    f"**Обоснование:**\n{reasoning}",
                    title=f"{icon} Решение Оркестратора",
                    border_style=COLORS['success'],
                ))
            elif decision == "ACCEPT":
                console.print(Panel(
                    f"[bold cyan]Решение: СОГЛАСЕН с валидатором[/]\n\n"
                    f"**Обоснование:**\n{reasoning}\n\n"
                    "[dim]Возвращаемся к началу цикла...[/]",
                    title=f"{icon} Решение Оркестратора",
                    border_style=COLORS['info'],
                ))
        
        # === VALIDATION — результаты технической валидации ===
        elif stage == "VALIDATION":
            if details:
                success = details.get("success")
                if success is True:
                    console.print(f"   {icon} [green]✓ {message}[/]")
                    
                    # Показываем детали RUNTIME проверок
                    runtime_checked = details.get("runtime_files_checked", 0)
                    runtime_passed = details.get("runtime_files_passed", 0)
                    runtime_failed = details.get("runtime_files_failed", 0)
                    runtime_skipped = details.get("runtime_files_skipped", 0)
                    
                    if runtime_checked > 0:
                        if runtime_failed == 0 and runtime_skipped == 0:
                            console.print(f"      [green]▶️ RUNTIME:[/] {runtime_passed}/{runtime_checked} файлов — [green]exec() успешен[/]")
                        elif runtime_failed == 0 and runtime_skipped > 0:
                            console.print(f"      [yellow]▶️ RUNTIME:[/] {runtime_passed}/{runtime_checked} файлов, [yellow]{runtime_skipped} пропущено[/]")
                        else:
                            console.print(f"      [red]▶️ RUNTIME:[/] {runtime_passed}/{runtime_checked} файлов, [red]{runtime_failed} ошибок[/]")
                    else:
                        # Runtime не запускался
                        console.print(f"      [dim]▶️ RUNTIME: не запускался (нет изменённых Python файлов)[/]")
                            
                elif success is False:
                    console.print(f"   {icon} [red]✗ {message}[/]")
                    
                    # Показываем детали ошибок
                    error_count = details.get("error_count", 0)
                    runtime_failed = details.get("runtime_files_failed", 0)
                    
                    if error_count > 0:
                        console.print(f"      [dim]Ошибок: {error_count}[/]")
                    if runtime_failed > 0:
                        console.print(f"      [red]▶️ RUNTIME ошибок: {runtime_failed}[/]")
                else:
                    console.print(f"   {icon} [dim]{message}[/]")
            else:
                console.print(f"   {icon} [dim]{message}[/]")
        
        # === AI_VALIDATION — результаты AI валидации ===
        elif stage == "AI_VALIDATION":
            if details:
                success = details.get("success")
                approved = details.get("approved")  # AI Validator использует approved
                
                # Определяем статус: либо success, либо approved
                is_ok = success is True or approved is True
                is_fail = success is False or approved is False
                
                if is_ok:
                    console.print(f"   {icon} [green]✓ {message}[/]")
                elif is_fail:
                    console.print(f"   {icon} [red]✗ {message}[/]")
                    verdict = details.get("verdict", "")
                    if verdict:
                        if TRANSLATION_AVAILABLE and not is_mostly_russian(verdict):
                            verdict = translate_sync(verdict, "validator verdict")
                        console.print(f"      [dim]Вердикт: {verdict[:100]}{'...' if len(verdict) > 100 else ''}[/]")
                else:
                    console.print(f"   {icon} [dim]{message}[/]")
            else:
                console.print(f"   {icon} [dim]{message}[/]")
        
        # === TESTS — результаты тестов ===
        elif stage == "TESTS":
            if details:
                tests_run = details.get("tests_run", 0)
                tests_passed = details.get("tests_passed", 0)
                tests_failed = details.get("tests_failed", 0)
                test_files_found = details.get("test_files_found", 0)
                success = details.get("success")
                
                # RUNTIME статистика (может прийти вместе с тестами)
                runtime_checked = details.get("runtime_files_checked", 0)
                runtime_passed = details.get("runtime_files_passed", 0)
                runtime_failed = details.get("runtime_files_failed", 0)
                runtime_skipped = details.get("runtime_files_skipped", 0)
                
                if success is True:
                    console.print(f"   {icon} [green]✓ {message}[/]")
                    
                    # === RUNTIME результаты (подробно) ===
                    if runtime_checked > 0:
                        console.print(f"\n      [bold]▶️ RUNTIME тестирование:[/]")
                        if runtime_failed == 0:
                            console.print(f"         [green]✓ Прошло: {runtime_passed}/{runtime_checked} файлов[/]")
                        else:
                            console.print(f"         [red]✗ Ошибок: {runtime_failed}[/], прошло: {runtime_passed}")
                        
                        if runtime_skipped > 0:
                            console.print(f"         [yellow]⏭ Пропущено: {runtime_skipped} файлов[/]")
                            console.print(f"         [dim](web apps, GUI без display, etc.)[/]")
                    else:
                        console.print(f"      [dim]▶️ RUNTIME: не требовался[/]")
                    
                    # === pytest тесты ===
                    console.print(f"\n      [bold]🧪 pytest тесты:[/]")
                    if tests_run > 0:
                        console.print(f"         [green]✓ Прошло: {tests_passed}/{tests_run}[/]")
                    else:
                        if test_files_found == 0:
                            console.print(f"         [dim]Тестовые файлы (test_*.py) не найдены[/]")
                            console.print(f"         [dim]Это нормально для нового проекта[/]")
                        else:
                            console.print(f"         [yellow]⚠️ Найдено {test_files_found} тестовых файлов, но запуск не удался[/]")
                            
                elif success is False:
                    console.print(f"   {icon} [red]✗ {message}[/]")
                    
                    # === RUNTIME ошибки ===
                    if runtime_checked > 0:
                        console.print(f"\n      [bold]▶️ RUNTIME тестирование:[/]")
                        if runtime_failed > 0:
                            console.print(f"         [red]✗ Ошибок: {runtime_failed}[/]")
                            # Показываем файлы с ошибками если есть
                            runtime_failures = details.get("runtime_failures", [])
                            for rf in runtime_failures[:3]:
                                file_path = rf.get("file_path", "?")
                                error_msg = rf.get("message", "")[:60]
                                console.print(f"            [red]• {file_path}:[/] {error_msg}...")
                            if len(runtime_failures) > 3:
                                console.print(f"            [dim]... и ещё {len(runtime_failures) - 3} ошибок[/]")
                        else:
                            console.print(f"         [green]✓ Прошло: {runtime_passed}/{runtime_checked}[/]")
                        
                        if runtime_skipped > 0:
                            console.print(f"         [yellow]⏭ Пропущено: {runtime_skipped}[/]")
                    
                    # === pytest failed тесты ===
                    failed = details.get("failed_tests", [])
                    if failed:
                        console.print(f"\n      [bold]🧪 pytest ошибки:[/]")
                        for t in failed[:3]:
                            console.print(f"         [red]• {t}[/]")
                        if len(failed) > 3:
                            console.print(f"         [dim]... и ещё {len(failed) - 3} ошибок[/]")
                            
                    if tests_run > 0:
                        console.print(f"      [dim]Прошло: {tests_passed}/{tests_run}[/]")
                else:
                    console.print(f"   {icon} [dim]{message}[/]")
            else:
                console.print(f"   {icon} [dim]{message}[/]")
        
        # === RUNTIME — отдельный этап для runtime тестирования ===
        elif stage == "RUNTIME":
            if details:
                runtime_checked = details.get("files_checked", 0)
                runtime_passed = details.get("files_passed", 0)
                runtime_failed = details.get("files_failed", 0)
                runtime_skipped = details.get("files_skipped", 0)
                success = details.get("success")
                
                console.print(f"\n   {icon} [bold]RUNTIME тестирование[/]")
                
                if runtime_checked == 0:
                    console.print(f"      [dim]Не запускалось (нет Python файлов для проверки)[/]")
                elif success is True:
                    console.print(f"      [green]✓ Прошло: {runtime_passed}/{runtime_checked} файлов[/]")
                    if runtime_skipped > 0:
                        console.print(f"      [yellow]⏭ Пропущено: {runtime_skipped} файлов[/]")
                        # Показываем причины пропуска
                        skipped_reasons = details.get("skipped_reasons", {})
                        if skipped_reasons:
                            for reason, count in list(skipped_reasons.items())[:3]:
                                console.print(f"         [dim]• {reason}: {count}[/]")
                elif success is False:
                    console.print(f"      [red]✗ Ошибок: {runtime_failed}/{runtime_checked}[/]")
                    if runtime_skipped > 0:
                        console.print(f"      [yellow]⏭ Пропущено: {runtime_skipped}[/]")
                    
                    # Показываем файлы с ошибками
                    failures = details.get("failures", [])
                    if failures:
                        console.print(f"      [bold red]Файлы с ошибками:[/]")
                        for f in failures[:5]:
                            file_path = f.get("file_path", "?")
                            status = f.get("status", "error")
                            msg = f.get("message", "")[:50]
                            console.print(f"         [red]• {file_path}[/] ({status}): {msg}...")
                        if len(failures) > 5:
                            console.print(f"         [dim]... и ещё {len(failures) - 5}[/]")
            else:
                console.print(f"   {icon} [dim]{message}[/]")
        
        # === CODE_GEN — генерация кода ===
        elif stage == "CODE_GEN" and details and details.get("files"):
            files = details.get("files", [])
            console.print(f"\n{icon} [bold]{message}[/]")
            for f in files[:5]:
                console.print(f"   • {f}")
            if len(files) > 5:
                console.print(f"   [dim]... и ещё {len(files) - 5} файлов[/]")
        
        # === FEEDBACK — возврат на новую итерацию ===
        elif stage == "FEEDBACK":
            console.print(f"\n{icon} [yellow]{message}[/]")
            console.print(f"   [dim]Возвращаемся к Оркестратору для исправления...[/]")
        
        # === COMPLETE — успешное завершение ===
        elif stage == "COMPLETE":
            console.print(f"\n{icon} [bold green]{message}[/]")
            if details:
                files = details.get("files", [])
                iterations = details.get("iterations", 1)
                duration = details.get("duration_ms", 0)
                
                console.print(f"   [dim]Файлов: {len(files)}, Итераций: {iterations}, Время: {duration:.0f}ms[/]")
                
                # Показываем итоговую статистику тестирования
                runtime_summary = details.get("runtime_summary", {})
                if runtime_summary:
                    checked = runtime_summary.get("checked", 0)
                    passed = runtime_summary.get("passed", 0)
                    failed = runtime_summary.get("failed", 0)
                    skipped = runtime_summary.get("skipped", 0)
                    
                    if checked > 0:
                        if failed == 0:
                            console.print(f"   [green]▶️ RUNTIME: {passed}/{checked} файлов — OK[/]")
                        else:
                            console.print(f"   [red]▶️ RUNTIME: {passed}/{checked} файлов, {failed} ошибок[/]")
                        if skipped > 0:
                            console.print(f"   [yellow]   Пропущено: {skipped} (web apps, GUI, etc.)[/]")
                    else:
                        console.print(f"   [dim]▶️ RUNTIME: не запускался[/]")
                
                tests_summary = details.get("tests_summary", {})
                if tests_summary:
                    tests_run = tests_summary.get("run", 0)
                    tests_passed = tests_summary.get("passed", 0)
                    if tests_run > 0:
                        console.print(f"   [green]🧪 pytest: {tests_passed}/{tests_run} тестов[/]")
                    else:
                        console.print(f"   [dim]🧪 pytest: тесты не найдены[/]")
        
        # === DIRECT_ANSWER — Оркестратор отвечает без генерации кода ===
        elif stage == "DIRECT_ANSWER":
            console.print(f"\n{icon} [bold cyan]{message}[/]")
            if details and details.get("answer_preview"):
                preview = details["answer_preview"]
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                console.print(f"   [dim]{preview}[/]")
        
        # === DELETIONS — показываем запланированные удаления ===
        elif stage == "DELETIONS":
            if details and details.get("deletions"):
                deletions = details["deletions"]
                console.print(f"\n{icon} [yellow]{message}[/]")
                for d in deletions[:5]:
                    console.print(f"   🗑️  `{d['target']}` в {d['file']}")
                if len(deletions) > 5:
                    console.print(f"   [dim]... и ещё {len(deletions) - 5}[/]")
            elif details and details.get("results"):
                # После применения — показываем результаты
                results = details["results"]
                success_count = sum(1 for r in results if r.get("success"))
                console.print(f"\n{icon} [green]{message}[/]")
                for r in results:
                    if r.get("success"):
                        console.print(f"   [green]✓[/] `{r['target']}` ({r['type']}) — {r.get('reason', '')[:50]}")
                    else:
                        console.print(f"   [red]✗[/] `{r['target']}`: {r.get('error', 'unknown error')}")
            else:
                console.print(f"\n{icon} [dim]{message}[/]")
        
        # === Остальные этапы ===
        else:
            console.print(f"\n{icon} [dim]{message}[/]")    
    
    
    # === CALLBACK ДЛЯ РЕШЕНИЙ ПОЛЬЗОВАТЕЛЯ ===
    async def on_user_decision_callback(decision_type: str, context: Dict) -> str:
        """
        Запрашивает решение пользователя когда Оркестратор не согласен с AI Validator.
        
        Returns:
            'cancel' - отменить запрос
            'force_fix' - заставить Оркестратора исправить код
            'proceed' - согласиться с Оркестратором и идти на тесты
        """
        if decision_type == "orchestrator_override":
            ai_result = context.get("ai_result", {})
            reasoning = context.get("orchestrator_reasoning", "")
            
            # Переводим
            verdict = ai_result.get("verdict", "")
            if TRANSLATION_AVAILABLE:
                if verdict and not is_mostly_russian(verdict):
                    verdict = translate_sync(verdict, "AI validator verdict")
                if reasoning and not is_mostly_russian(reasoning):
                    reasoning = translate_sync(reasoning, "orchestrator reasoning")
            
            console.print("\n" + "=" * 60)
            console.print("[bold yellow]⚠️ ТРЕБУЕТСЯ ВАШЕ РЕШЕНИЕ[/]")
            console.print("=" * 60)
            
            # AI Validator
            console.print(Panel(
                f"**Вердикт:** {verdict}\n"
                f"**Уверенность:** {ai_result.get('confidence', 0):.0%}\n"
                f"**Проблемы:** {', '.join(ai_result.get('critical_issues', [])[:3])}",
                title="🤖 AI Validator отклонил код",
                border_style=COLORS['warning'],
            ))
            
            # Orchestrator
            console.print(Panel(
                f"**Решение:** НЕ СОГЛАСЕН с валидатором\n\n"
                f"**Обоснование:**\n{reasoning}",
                title="🎯 Оркестратор считает код правильным",
                border_style=COLORS['success'],
            ))
            
            console.print("\n[bold]Выберите действие:[/]\n")
            console.print("[1] ✅ Доверять Оркестратору — перейти к тестам")
            console.print("[2] ⚠️  Согласиться с Валидатором — вернуться на исправление")
            console.print("[3] ❌ Отменить запрос")
            console.print()
            
            try:
                choice = prompt_with_navigation(
                    "Выбор",
                    choices=["1", "2", "3"],
                    default="1"
                )
            except BackException:
                return "cancel"
            except (BackToMenuException, QuitException):
                return "cancel"
            
            if choice == "1":
                return "proceed"
            elif choice == "2":
                return "force_fix"
            else:
                return "cancel"
        
        return "proceed"

    # === Переменная для хранения результата ===
    result = None
    
    try:
        # === ЗАПУСК PIPELINE ===
        log_pipeline_stage("PIPELINE_RUN", "Starting pipeline")
        
        result = await state.pipeline.process_request(
            user_request=query,
            history=history,
            mode=PipelineMode.AGENT,
            on_thinking=on_thinking_callback,
            on_tool_call=on_tool_call_callback,
            on_validation=on_validation_callback,
            on_status=on_status_callback,
            on_stage=on_stage_callback,
            on_user_decision=on_user_decision_callback,
            prefilter_advice=prefilter_advice_str,
        )
        
        log_pipeline_stage("PIPELINE_RESULT", f"Completed: {result.status.value}", {
            "success": result.success,
            "errors": result.errors,
            "code_blocks": len(result.code_blocks),
            "pending_changes": len(result.pending_changes),
            "iterations": result.feedback_iterations,
        })
        
    except Exception as e:
        log_pipeline_stage("PIPELINE_ERROR", f"Pipeline crashed: {e}", error=e)
        logger.error(f"Ошибка пайплайна: {e}", exc_info=True)
        print_error(f"Критическая ошибка: {e}")
        
        # === СОХРАНЯЕМ ИСТОРИЮ ДАЖЕ ПРИ КРАШЕ ===
        crash_response = f"## ❌ Критическая ошибка\n\n"
        crash_response += f"**Ошибка:** {type(e).__name__}: {str(e)}\n\n"
        crash_response += "*Произошла непредвиденная ошибка. Вы можете попробовать снова или уточнить запрос.*"
        
        await save_message("assistant", crash_response)
        
        console.print("\n[dim]Вы можете попробовать снова или уточнить запрос.[/]")
        console.print("[dim]Введите /меню для возврата в главное меню.[/]")
        return

    # === ОТОБРАЖЕНИЕ РЕЗУЛЬТАТОВ ===
    console.print("\n" + "=" * 60)
    console.print("[bold]📊 РЕЗУЛЬТАТЫ[/]")
    console.print("=" * 60)

    # Проверка на ошибки
    if result.status == PipelineStatus.FAILED:
        print_error("Пайплайн завершился с ошибкой")
        if result.errors:
            for err in result.errors:
                console.print(f"  [red]• {err}[/]")
        
        # Показываем сколько итераций было
        if result.feedback_iterations > 1:
            console.print(f"\n[dim]Было выполнено {result.feedback_iterations} итераций[/]")
        
        if result.analysis:
            console.print(Panel(
                Markdown(result.analysis),
                title="📋 Частичный анализ",
                border_style=COLORS['warning'],
            ))
        
        # === СОХРАНЯЕМ ИСТОРИЮ ДАЖЕ ПРИ ОШИБКЕ ===
        error_response = "## ❌ Ошибка выполнения\n\n"
        error_response += f"**Статус:** {result.status.value}\n"
        error_response += f"**Итераций:** {result.feedback_iterations}\n\n"
        
        if result.errors:
            error_response += "**Ошибки:**\n"
            for err in result.errors:
                error_response += f"- {err}\n"
            error_response += "\n"
        
        if result.analysis:
            error_response += f"**Частичный анализ:**\n{result.analysis}\n\n"
        
        if result.instruction:
            error_response += f"**Последняя инструкция:**\n{result.instruction[:500]}...\n\n"
        
        error_response += "*Вы можете уточнить требования или попробовать снова.*"
        
        await save_message("assistant", error_response)
        
        log_pipeline_stage("COMPLETE", "Pipeline failed - history saved", {
            "success": False,
            "errors": result.errors,
            "iterations": result.feedback_iterations,
        })
        
        # Предлагаем пользователю продолжить или выйти
        console.print("\n[dim]Вы можете уточнить требования или ввести новый запрос.[/]")
        console.print("[dim]Введите /меню для возврата в главное меню.[/]")
        return

    if result.status == PipelineStatus.CANCELLED:
        print_info("Запрос отменён")
        
        # === СОХРАНЯЕМ ИСТОРИЮ ПРИ ОТМЕНЕ ===
        cancel_response = "## ⚠️ Запрос отменён\n\n"
        if result.analysis:
            cancel_response += f"**Анализ до отмены:**\n{result.analysis}\n\n"
        cancel_response += "*Запрос был отменён пользователем.*"
        
        await save_message("assistant", cancel_response)
        
        log_pipeline_stage("COMPLETE", "Pipeline cancelled - history saved", {
            "success": False,
            "status": "cancelled",
        })
        return

    # === СТАТИСТИКА ИТЕРАЦИЙ ===
    if result.feedback_iterations > 1:
        console.print(f"\n[bold cyan]🔄 Выполнено итераций: {result.feedback_iterations}[/]")


    # === DIRECT_ANSWER — Оркестратор ответил без генерации кода ===
    is_direct_answer = (
        result.success 
        and result.analysis 
        and not result.instruction 
        and not result.code_blocks
    )
    
    if is_direct_answer:
        console.print("\n[bold cyan]💬 Оркестратор ответил напрямую (без изменений кода)[/]\n")
        
        analysis_text = result.analysis
        if TRANSLATION_AVAILABLE and not is_mostly_russian(analysis_text):
            with console.status("[dim]Перевод...[/]"):
                analysis_text = translate_sync(analysis_text, "AI response")
        
        console.print(Panel(
            Markdown(analysis_text),
            title="🤖 Ответ",
            border_style=COLORS['secondary'],
        ))
        
        # Сохраняем и завершаем
        await save_message("assistant", result.analysis)
        
        log_pipeline_stage("COMPLETE", "Direct answer (no code changes)", {
            "success": True,
            "duration_ms": result.duration_ms,
        })
        return


    # === АНАЛИЗ ===
    if result.analysis:
        analysis_text = result.analysis
        if TRANSLATION_AVAILABLE and not is_mostly_russian(analysis_text):
            with console.status("[dim]Перевод...[/]"):
                analysis_text = translate_sync(analysis_text, "code analysis")
        
        console.print(Panel(
            Markdown(analysis_text),
            title="📋 Анализ задачи",
            border_style=COLORS['secondary'],
        ))

    # === СГЕНЕРИРОВАННЫЙ КОД ===
    if result.code_blocks:
        console.print(f"\n[bold]📝 Итоговый код ({len(result.code_blocks)} блоков):[/]\n")
        for block in result.code_blocks:
            console.print(f"[cyan]Файл:[/] `{block.file_path}` | [cyan]Режим:[/] {block.mode}")
            print_code_block(block.code, block.file_path)

    # === РЕЗУЛЬТАТЫ ВАЛИДАЦИИ ===
    if result.validation_result:
        console.print(f"\n[bold]🔍 Финальная валидация:[/]")
        print_validation_result(result.validation_result)

        # === NEW: Детальный RUNTIME отчёт ===
        runtime_summary = result.validation_result.get("runtime_test_summary", {})
        if runtime_summary:
            total_files = runtime_summary.get("total_files", 0)
            passed = runtime_summary.get("passed", 0)
            failed = runtime_summary.get("failed", 0)
            skipped = runtime_summary.get("skipped", 0)
            timeouts = runtime_summary.get("timeouts", 0)
            
            console.print(f"\n[bold]▶️ RUNTIME тестирование:[/]")
            
            if total_files == 0:
                console.print(f"   [dim]Не запускалось (нет Python файлов для проверки)[/]")
            else:
                # Общий статус
                if failed == 0 and timeouts == 0:
                    console.print(f"   [green]✓ Все файлы прошли проверку[/]")
                else:
                    console.print(f"   [red]✗ Есть проблемы[/]")
                
                # Статистика
                console.print(f"   • Проверено: {total_files} файлов")
                console.print(f"   • [green]Прошло: {passed}[/]")
                if failed > 0:
                    console.print(f"   • [red]Ошибок: {failed}[/]")
                if timeouts > 0:
                    console.print(f"   • [yellow]Таймаутов: {timeouts}[/]")
                if skipped > 0:
                    console.print(f"   • [yellow]Пропущено: {skipped}[/]")
                    
                    # Показываем причины пропуска если есть
                    results = runtime_summary.get("results", [])
                    skipped_files = [r for r in results if r.get("status") == "skipped"]
                    if skipped_files:
                        console.print(f"   [dim]Пропущенные файлы:[/]")
                        for sf in skipped_files[:5]:
                            file_path = sf.get("file_path", "?")
                            reason = sf.get("message", "")[:40]
                            console.print(f"      • {file_path}: {reason}")
                        if len(skipped_files) > 5:
                            console.print(f"      [dim]... и ещё {len(skipped_files) - 5}[/]")
                
                # Показываем ошибки если есть
                if failed > 0:
                    results = runtime_summary.get("results", [])
                    failed_files = [r for r in results if r.get("status") == "failed"]
                    if failed_files:
                        console.print(f"\n   [bold red]Файлы с ошибками:[/]")
                        for ff in failed_files[:5]:
                            file_path = ff.get("file_path", "?")
                            error_msg = ff.get("message", "")[:60]
                            console.print(f"      [red]• {file_path}:[/]")
                            console.print(f"        {error_msg}...")
                        if len(failed_files) > 5:
                            console.print(f"      [dim]... и ещё {len(failed_files) - 5}[/]")
        else:
            # Runtime summary отсутствует
            runtime_checked = result.validation_result.get("runtime_files_checked", 0)
            if runtime_checked == 0:
                console.print(f"\n[bold]▶️ RUNTIME тестирование:[/]")
                console.print(f"   [dim]Не запускалось (нет изменённых Python файлов)[/]")

    # === AI VALIDATOR (финальный результат) ===
    if result.ai_validation_result:
        approved = result.ai_validation_result.get("approved", False)
        confidence = result.ai_validation_result.get("confidence", 0)
        verdict = result.ai_validation_result.get("verdict", "")
        
        if TRANSLATION_AVAILABLE and verdict and not is_mostly_russian(verdict):
            verdict = translate_sync(verdict, "AI validator verdict")
        
        color = COLORS['success'] if approved else COLORS['warning']
        status_text = "✅ ОДОБРЕНО" if approved else "⚠️ ПЕРЕОПРЕДЕЛЕНО"
        
        console.print(Panel(
            f"**Статус:** {status_text}\n"
            f"**Уверенность:** {confidence:.0%}\n"
            f"**Вердикт:** {verdict}",
            title="🤖 AI Validator",
            border_style=color,
        ))

    # === ОЖИДАЮЩИЕ ИЗМЕНЕНИЯ И ПОДТВЕРЖДЕНИЕ ===
    # Проверяем наличие изменений: либо pending_changes, либо code_blocks
    has_changes = bool(result.pending_changes) or bool(result.code_blocks)
    
    if has_changes and result.success:
        # === LOOP FOR CONFIRMATION / CRITIQUE ===
        while True:
            console.print("\n" + "=" * 60)
            console.print("[bold yellow]📝 ИЗМЕНЕНИЯ ГОТОВЫ К ПРИМЕНЕНИЮ[/]")
            console.print("=" * 60 + "\n")
            
            # Показываем diff если есть (с учетом обновлений после критики)
            if result.diffs:
                # Копируем diffs, чтобы не ломать оригинал при pop
                current_diffs = result.diffs.copy()
                deletions_info = current_diffs.pop("__deletions__", None)
                
                # Показываем diffs файлов
                if current_diffs:
                    print_diff_preview(current_diffs)
                
                # Показываем информацию об удалениях
                if deletions_info and isinstance(deletions_info, list):
                    console.print("\n[bold yellow]🗑️ Закомментированный код (soft delete):[/]\n")
                    for d in deletions_info:
                        if d.get("success"):
                            console.print(f"   [green]✓[/] `{d['target']}` ({d['type']}) в `{d['file']}`")
                            if d.get("reason"):
                                reason = d['reason']
                                if len(reason) > 60:
                                    reason = reason[:60] + "..."
                                console.print(f"      [dim]Причина: {reason}[/]")
                        else:
                            console.print(f"   [red]✗[/] `{d['target']}` в `{d['file']}`: {d.get('error', 'ошибка')}")        
            
            elif result.code_blocks:
                # Если нет diffs, показываем список файлов
                console.print("[bold]Изменения в файлах:[/]\n")
                for block in result.code_blocks:
                    console.print(f"   📄 {block.file_path} ({block.mode})")
            
            # Статистика (динамическая)
            files_count = len(result.pending_changes) if result.pending_changes else len(result.code_blocks)
            feedback_iterations = result.feedback_iterations
            
            console.print(f"\n[bold]📊 Итого:[/]")
            console.print(f"   Файлов к изменению: {files_count}")
            console.print(f"   Итераций цикла: {feedback_iterations}")
            if result.duration_ms:
                console.print(f"   Время выполнения: {result.duration_ms:.0f}ms")
            
            # === ПОДТВЕРЖДЕНИЕ ===
            console.print()
            console.print("[bold green]✅ Все проверки пройдены! Код готов к применению.[/]")
            console.print("Для принятия изменений введите Y или нажмите Enter, для отказа n")
            console.print()
            
            try:
                confirm = confirm_with_navigation(
                    "Применить изменения в реальные файлы?",
                    default=True
                )
            except (BackException, BackToMenuException, QuitException) as nav_exc:
                await state.pipeline.discard_pending_changes()
                
                # Сохраняем историю при выходе
                exit_response = "## ⚠️ Сессия прервана\n\n"
                if result.analysis:
                    exit_response += f"**Анализ:**\n{result.analysis}\n\n"
                exit_response += "\n*Пользователь вышел до подтверждения. Изменения не применены.*"
                
                await save_message("assistant", exit_response)
                print_info("Изменения отменены, история сохранена")
                raise nav_exc
            
            logger.info(f"User confirmation: {confirm}")
            
            if confirm:
                # === ПРИМЕНЕНИЕ ИЗМЕНЕНИЙ ===
                console.print("\n[dim]⏳ Применение изменений...[/]")
                
                try:
                    apply_result = await state.pipeline.apply_pending_changes()
                    
                    logger.info(f"Apply result: success={apply_result.success}, applied={apply_result.applied_files}, errors={apply_result.errors}")
                    
                    if apply_result.success:
                        print_success(f"✅ Изменения применены в {len(apply_result.applied_files)} файл(ах)!")
                        
                        if apply_result.applied_files:
                            console.print("\n[dim]Изменённые файлы:[/]")
                            for f in apply_result.applied_files:
                                console.print(f"   [green]✓[/] {f}")
                        
                        if apply_result.created_files:
                            console.print("\n[dim]Созданные файлы:[/]")
                            for f in apply_result.created_files:
                                console.print(f"   [green]+[/] {f}")
                        
                        if apply_result.backup_session_id:
                            console.print(f"\n[dim]💾 Бэкап создан: [cyan]{apply_result.backup_session_id}[/][/]")
                            console.print(f"[dim]   Для восстановления: [cyan]/restore {apply_result.backup_session_id[:20]}[/][/]")
                        
                        # Обновление индекса
                        if state.is_new_project and state.project_dir:
                            console.print("\n[dim]Индексация нового проекта...[/]")
                            if await build_project_indexes(state.project_dir):
                                state.project_index = await load_project_index(state.project_dir)
                                state.is_new_project = False
                                print_success("Проект проиндексирован")
                        elif state.project_dir:
                            console.print("[dim]Обновление индекса...[/]")
                            await run_incremental_update(state.project_dir)
                            state.project_index = await load_project_index(state.project_dir)
                            if state.pipeline:
                                state.pipeline.project_index = state.project_index
                            console.print("[green]✓[/] Индекс обновлён")
                    
                    else:
                        print_error("Не удалось применить изменения")
                        if apply_result.errors:
                            for err in apply_result.errors:
                                console.print(f"   [red]• {err}[/]")
                                
                except Exception as e:
                    logger.error(f"Error applying changes: {e}", exc_info=True)
                    print_error(f"Ошибка при применении: {e}")
                
                # Выход из цикла после применения
                break
            
            else:
                # === ПОЛЬЗОВАТЕЛЬ ОТКАЗАЛСЯ ===
                logger.info("User declined changes")
                console.print("\n[bold]Вы отказались от изменений.[/]")
                console.print("[1] ✏️  Написать критику и доработать код")
                console.print("[2] ❌ Отменить изменения и выйти")
                console.print()
                
                choice = prompt_with_navigation("Выбор", choices=["1", "2"], default="1")
                
                if choice == "1":
                    # === КРИТИКА И ДОРАБОТКА ===
                    console.print("\n[bold]Введите ваши замечания:[/]")
                    console.print("[dim]Что нужно исправить?[/]\n")
                    
                    try:
                        user_feedback = Prompt.ask("[bold cyan]Замечания[/]")
                    except KeyboardInterrupt:
                        choice = "2" # Fallback to cancel
                    
                    if choice == "1" and user_feedback.strip():
                        console.print("\n[dim]⏳ Запускаем цикл доработки...[/]")
                        
                        # Запускаем цикл обратной связи
                        new_result = await state.pipeline.run_feedback_cycle(
                            user_feedback=user_feedback,
                            history=history
                        )
                        
                        if new_result and new_result.success:
                            # Обновляем результат и идем на новую итерацию цикла
                            result = new_result
                            console.print("\n[bold green]✅ Код исправлен! Проверьте новые изменения.[/]")
                            continue
                        else:
                            print_error("Не удалось исправить код по вашей критике")
                            # Можно спросить еще раз или выйти, здесь остаемся в меню
                            continue
                    else:
                        if choice == "1": print_warning("Пустая критика")
                        # Возвращаемся к началу цикла подтверждения (показать старые изменения)
                        continue
                
                # Отмена (choice == "2")
                await state.pipeline.discard_pending_changes()
                
                # Сохраняем историю
                decline_response = "## 🚫 Отклонено пользователем\n\n*Пользователь отклонил предложенные изменения.*"
                await save_message("assistant", decline_response)
                
                print_info("Изменения отменены")
                break
    
    elif result.success:
        # Успех, но нет изменений файлов (например, только анализ)
        print_info("Задача выполнена. Изменения файлов не требуются.")
    
    else:
        # Неуспех — уже обработан выше (FAILED/CANCELLED)
        pass

    # === СОХРАНЕНИЕ В ИСТОРИЮ ===
    full_response = ""
    
    # Проверяем, был ли это DIRECT_ANSWER (analysis без instruction и без code_blocks)
    is_direct_answer_final = (
        result.analysis 
        and not result.instruction 
        and not result.code_blocks
        and result.success
    )
    
    if is_direct_answer_final:
        # DIRECT_ANSWER — уже сохранено выше, пропускаем
        pass
    else:
        # Стандартный ответ с кодом
        if result.analysis:
            full_response += f"## Анализ\n\n{result.analysis}\n\n"
        if result.instruction:
            full_response += f"## Инструкция для Code Generator\n\n{result.instruction}\n\n"
        if result.code_blocks:
            full_response += "## Сгенерированный код\n\n"
            for block in result.code_blocks:
                full_response += f"**Файл:** `{block.file_path}`\n```python\n{block.code}\n```\n\n"
        
        # Добавляем информацию об удалениях если были
        if result.diffs and "__deletions__" in result.diffs:
            deletions_info = result.diffs.get("__deletions__", [])
            if deletions_info:
                full_response += "## Закомментированный код\n\n"
                for d in deletions_info:
                    status = "✓" if d.get("success") else "✗"
                    full_response += f"- {status} `{d['target']}` ({d['type']}) в `{d['file']}`\n"
                    if d.get("reason"):
                        full_response += f"  - Причина: {d['reason']}\n"
                full_response += "\n"
        
        if full_response:
            await save_message("assistant", full_response)
    
    log_pipeline_stage("COMPLETE", "Agent mode completed", {
        "success": result.success,
        "duration_ms": result.duration_ms,
        "iterations": result.feedback_iterations,
        "is_direct_answer": is_direct_answer_final,
    })



async def handle_general_chat(query: str):
    """Обработка запроса в режиме General Chat (Общий Чат)"""
    if not state.current_thread:
        print_error("Нет активного диалога")
        return
    
    try:
        from app.agents.orchestrator import GeneralChatOrchestrator, UserFile
        
        if not hasattr(state, '_saved_file_names'):
            state._saved_file_names = set()
        
        # Сохраняем НОВЫЕ файлы в историю (только при первом прикреплении)
        if state.attached_files:
            new_files = [
                f for f in state.attached_files 
                if f.get('filename', f.get('name', '')) not in state._saved_file_names
            ]
            
            if new_files:
                saved = await save_attached_files_to_history(new_files, state.current_thread.id)
                state._saved_file_names.update(saved)
                
                if saved:
                    print_info(f"Файлы добавлены в контекст беседы: {', '.join(saved)}")
        
        # Получаем историю (с автоматическим сжатием при превышении лимита)
        history_messages, compression_stats = await state.history_manager.get_session_history(
            thread_id=state.current_thread.id,
            current_query=query
        )
        
        # Конвертируем ВСЮ историю (включая файлы) для оркестратора
        history = []
        for msg in history_messages:
            # НЕ фильтруем — всё идёт в контекст!
            history.append({"role": msg.role, "content": msg.content})
        
        # DEBUG: показываем размер контекста
        total_chars = sum(len(h["content"]) for h in history)
        console.print(f"[dim]📊 Контекст: {len(history)} сообщений, ~{total_chars // 4:,} токенов[/]")
        
        # Сохраняем текущий запрос
        await state.history_manager.add_message(
            thread_id=state.current_thread.id,
            role="user",
            content=query,
            tokens=len(query) // 4
        )
        
        await update_thread_title_if_first_message(query)
        
        # НЕ передаём файлы отдельно — они уже в истории!
        # user_files используем только если файлы ещё не в истории (первый запрос)
        user_files = []
        
        # Создаём оркестратор
        orchestrator = GeneralChatOrchestrator(
            model=state.get_current_orchestrator_model(),
            is_legal_mode=state.is_legal_mode
        )
        
        with console.status(f"[bold {COLORS['primary']}]🤖 Обрабатываю запрос...[/]"):
            result = await orchestrator.orchestrate_general(
                user_query=query,
                user_files=user_files,  # Пустой — файлы в history
                history=history
            )
        
        # Показываем инструменты
        if result.tool_calls:
            tools_used = [tc.name for tc in result.tool_calls if tc.success]
            if tools_used:
                console.print(f"[dim]🛠️ Использованы: {', '.join(set(tools_used))}[/]")
        
        # Сохраняем трейс
        if result.tool_calls and state.history_manager:
            try:
                await state.history_manager.save_orchestration_trace(
                    thread_id=state.current_thread.id,
                    tool_calls=result.tool_calls
                )
            except Exception as e:
                logger.warning(f"Failed to save orchestration trace: {e}")
        
        # Проверка на пустой ответ
        if not result.response or not result.response.strip():
            print_warning("Модель вернула пустой ответ")
            console.print("[dim]Попробуйте переформулировать запрос или начать новый диалог[/]")
            
            await state.history_manager.add_message(
                thread_id=state.current_thread.id,
                role="assistant",
                content="[Ошибка: модель не смогла сформировать ответ]",
                tokens=10
            )
            return
        
        # Выводим ответ
        console.print()
        console.print(Panel(
            Markdown(result.response),
            title="🤖 Ответ",
            border_style=COLORS['success'],
            padding=(1, 2)
        ))
        
        # Сохраняем ответ
        await state.history_manager.add_message(
            thread_id=state.current_thread.id,
            role="assistant",
            content=result.response,
            tokens=len(result.response) // 4,
            metadata={
                "tools_used": [tc.name for tc in result.tool_calls] if result.tool_calls else [],
                "is_legal_mode": state.is_legal_mode
            }
        )
        
    except Exception as e:
        logger.error(f"General chat error: {e}", exc_info=True)
        print_error(f"Произошла ошибка при обработке запроса: {e}")




# ============================================================================
# ОСНОВНОЙ ЦИКЛ ЧАТА
# ============================================================================

async def chat_loop() -> str:
    """
    Основной цикл взаимодействия в чате.
    
    Returns:
        'menu' - вернуться в главное меню
        'quit' - выйти из программы
    """
    while state.running:
        try:
            # Выводим статус
            print_status_bar()
            
            # Получаем ввод пользователя
            console.print()
            query = Prompt.ask(f"[bold {COLORS['primary']}]Вы[/]")
            
            if not query.strip():
                continue
            
            # Проверяем на выход
            if query.strip().lower() in QUIT_COMMANDS:
                state.running = False
                console.print("[dim]До свидания! 👋[/]")
                return "quit"
            
            # Проверяем на команду "назад" (возврат без действия)
            if query.strip().lower() in BACK_COMMANDS:
                # Просто продолжаем цикл — показываем статус и ждём новый ввод
                continue
            
            # Обрабатываем специальные команды
            if query.startswith("/"):
                result = await handle_chat_command(query)
                if result == "menu":
                    return "menu"
                # Если result == "help_shown" или None — просто продолжаем цикл
                # НЕ выполняем запрос как сообщение к AI
                continue
            
            # === Это обычный запрос к AI ===
            
            # Обновляем название диалога если это первое сообщение
            await update_thread_title_if_first_message(query)
            
            # Обрабатываем в зависимости от режима
            if state.mode == "ask":
                await handle_ask_mode(query)
            elif state.mode == "agent":
                await handle_agent_mode(query)
            elif state.mode == "general":
                await handle_general_chat(query)
            else:
                await handle_ask_mode(query)
            
            console.print()
            
        except BackToMenuException:
            return "menu"
        except QuitException:
            state.running = False
            console.print("[dim]До свидания! 👋[/]")
            return "quit"
        except KeyboardInterrupt:
            console.print("\n[dim]Используйте /меню или 'q' для выхода[/]")
        except Exception as e:
            logger.error(f"Ошибка в цикле чата: {e}", exc_info=True)
            print_error(str(e))
    
    return "quit"




async def handle_chat_command(command: str) -> Optional[str]:
    """
    Обработка слэш-команд в чате.
    
    Returns:
        'menu' - вернуться в меню
        'help_shown' - была показана справка (не выполнять запрос)
        None - продолжить чат
    """
    cmd = command.lower().strip()
    
    # Выход
    if cmd in ("/q", "/quit", "/exit", "/выход", "/в"):
        state.running = False
        console.print("[dim]До свидания! 👋[/]")
        raise QuitException()
    
    # В меню
    elif cmd in ("/меню", "/menu", "/m", "/м"):
        return "menu"
    
    # История
    elif cmd in ("/h", "/history", "/история", "/и"):
        await view_history()
        return "help_shown"
    
    # Экспорт диалога
    elif cmd in ("/export", "/экспорт", "/э"):
        await export_dialog_to_markdown()
        return "help_shown"
    
    # Изменить название
    elif cmd in ("/title", "/название", "/переименовать"):
        await rename_current_thread()
        return "help_shown"
    
    # Новый диалог
    elif cmd in ("/n", "/new", "/новый", "/н"):
        state.current_thread = await create_new_thread()
        state.reset_session()
        return "help_shown"
    
    # Переключение треда
    elif cmd in ("/t", "/thread", "/threads", "/тред", "/треды"):
        try:
            thread = await select_thread()
            if thread:
                state.current_thread = thread
                state.project_dir = thread.project_path
                state.reset_session()
                # Загружаем индекс если есть проект
                if state.project_dir:
                    state.project_index = await load_project_index(state.project_dir)
        except (BackException, BackToMenuException, QuitException):
            pass
        return "help_shown"
    
    # Выбор модели оркестратора
    elif cmd in ("/model", "/модель", "/мод"):
        try:
            await select_orchestrator_model()
        except (BackException, BackToMenuException, QuitException):
            pass
        return "help_shown"
    
    # NEW: Выбор модели генератора
    elif cmd in ("/generator", "/генератор", "/ген"):
        try:
            await select_generator_model()
        except (BackException, BackToMenuException, QuitException):
            pass
        return "help_shown"
    
    # Переключение проверки типов
    elif cmd in ("/types", "/типы", "/mypy"):
        await toggle_type_checking()
        return "help_shown"
    
    # Восстановление из бэкапа
    elif cmd.startswith("/restore") or cmd.startswith("/восстановить") or cmd.startswith("/бэкап"):
        parts = command.split(maxsplit=1)
        args = parts[1] if len(parts) > 1 else ""
        await handle_restore_command(args)
        return "help_shown"
    
    # История изменений файлов
    elif cmd.startswith("/changes") or cmd.startswith("/изменения"):
        parts = command.split(maxsplit=1)
        args = parts[1] if len(parts) > 1 else ""
        await handle_changes_command(args)
        return "help_shown"
    
    # Прикрепление файлов (General Chat)
    elif cmd in ("/attach", "/прикрепить", "/файл", "/файлы"):
        if state.mode != "general":
            print_warning("Прикрепление файлов доступно только в режиме Общего Чата")
        else:
            try:
                await attach_files()
            except (BackException, BackToMenuException, QuitException):
                pass
        return "help_shown"
    
    # Переключение Legal режима (General Chat)
    elif cmd in ("/legal", "/юрист"):
        if state.mode != "general":
            print_warning("Legal режим доступен только в Общем Чате")
        else:
            state.is_legal_mode = not state.is_legal_mode
            mode_str = "включён" if state.is_legal_mode else "выключен"
            print_success(f"Legal режим {mode_str}", "Настройки")
        return "help_shown"
    
    # Статус
    elif cmd in ("/s", "/status", "/статус"):
        print_status_bar()
        return "help_shown"
    
    # Очистка экрана
    elif cmd in ("/clear", "/очистить", "/cls"):
        console.clear()
        return "help_shown"
    
    # О программе
    elif cmd in ("/a", "/about", "/о", "/опрограмме"):
        print_about()
        return "help_shown"
    
    # Справка
    elif cmd in ("/help", "/?", "/помощь", "/справка"):
        print_chat_help()
        return "help_shown"
    
    else:
        console.print(f"[dim]Неизвестная команда: {command}[/]")
        console.print("[dim]Введите /помощь для списка доступных команд[/]")
        return "help_shown"
    
    return None


async def handle_restore_command(args: str):
    """Восстановление из бэкапа"""
    if not state.pipeline or not state.pipeline.backup_manager:
        print_error("Менеджер бэкапов недоступен")
        console.print("[dim]Сначала войдите в режим Агента с проектом[/]")
        return
    
    backup_manager = state.pipeline.backup_manager
    
    if not args.strip():
        # Показать список сессий С ФАЙЛАМИ
        sessions = backup_manager.list_sessions(limit=10)
        
        if not sessions:
            print_info("Нет доступных бэкапов")
            return
        
        console.print("\n[bold]💾 Доступные бэкапы:[/]\n")
        
        for i, session in enumerate(sessions, 1):
            # Форматируем дату
            created = session.created_at[:19].replace('T', ' ') if session.created_at else "?"
            
            # Форматируем ID (укорачиваем)
            session_id_short = session.session_id[:20] + "..." if len(session.session_id) > 20 else session.session_id
            
            # Описание
            desc = session.description[:50] + "..." if len(session.description) > 50 else session.description
            
            console.print(f"[bold cyan]#{i}[/] | ID: [cyan]{session_id_short}[/] | {created} | {desc}")
            
            if session.backups:
                for backup in session.backups:
                    # Показываем размер файла в человекочитаемом формате
                    size_kb = backup.file_size / 1024 if backup.file_size else 0
                    if size_kb > 1024:
                        size_str = f"{size_kb / 1024:.1f} MB"
                    else:
                        size_str = f"{size_kb:.1f} KB"
                    console.print(f"   📄 [yellow]{backup.original_path}[/] ({size_str})")
            else:
                console.print("   [dim]Нет файлов[/]")
            console.print()
        
        console.print("[dim]Для восстановления всей сессии:[/]")
        console.print("[dim]  /restore 1          (по номеру)[/]")
        console.print("[dim]  /restore 20240115   (по ID)[/]")
        console.print()
        console.print("[dim]Для восстановления одного файла из сессии:[/]")
        console.print("[dim]  /restore 1 app/services/auth.py[/]")
        return
    
    # Парсим аргументы: может быть "номер" или "номер путь_к_файлу"
    parts = args.strip().split(None, 1)
    session_selector = parts[0]
    file_filter = parts[1].strip() if len(parts) > 1 else None
    
    # Определяем сессию
    sessions = backup_manager.list_sessions(limit=50)
    
    # Проверяем, это номер или ID
    session = None
    try:
        idx = int(session_selector) - 1
        if 0 <= idx < len(sessions):
            session = sessions[idx]
            session_id = session.session_id
    except ValueError:
        # Ищем по ID (частичное совпадение)
        session_id = session_selector
        matches = [s for s in sessions if session_id in s.session_id]
        
        if len(matches) == 1:
            session = matches[0]
            session_id = session.session_id
        elif len(matches) > 1:
            print_warning(f"Найдено несколько сессий, содержащих '{session_id}':")
            for s in matches[:5]:
                console.print(f"   • {s.session_id[:30]}...")
            console.print("[dim]Уточните ID сессии[/]")
            return
        else:
            # Пробуем загрузить напрямую
            session = backup_manager.get_session(session_id)
    
    if not session:
        print_error(f"Сессия не найдена: {session_selector}")
        return
    
    # Показываем что будет восстановлено
    console.print(f"\n[bold]🔄 Восстановление из сессии:[/]")
    console.print(f"   ID: [cyan]{session.session_id}[/]")
    console.print(f"   Описание: {session.description}")
    console.print(f"   Создана: {session.created_at[:19].replace('T', ' ')}")
    console.print()
    
    if not session.backups:
        print_warning("В этой сессии нет файлов для восстановления")
        return
    
    # Если указан конкретный файл — восстанавливаем только его
    if file_filter:
        # Нормализуем путь
        file_filter = file_filter.replace('\\', '/')
        
        # Ищем файл в бэкапах
        matching_backups = [b for b in session.backups if file_filter in b.original_path]
        
        if not matching_backups:
            print_error(f"Файл не найден в сессии: {file_filter}")
            console.print("[dim]Доступные файлы:[/]")
            for b in session.backups:
                console.print(f"   • {b.original_path}")
            return
        
        if len(matching_backups) > 1:
            print_warning(f"Найдено несколько файлов, содержащих '{file_filter}':")
            for b in matching_backups:
                console.print(f"   • {b.original_path}")
            console.print("[dim]Уточните путь к файлу[/]")
            return
        
        # Один файл найден
        backup = matching_backups[0]
        
        console.print(f"[bold yellow]📄 Восстановление файла:[/]")
        console.print(f"   Путь: [cyan]{backup.original_path}[/]")
        
        # Размер файла
        size_kb = backup.file_size / 1024 if backup.file_size else 0
        if size_kb > 1024:
            size_str = f"{size_kb / 1024:.1f} MB"
        else:
            size_str = f"{size_kb:.1f} KB"
        console.print(f"   Размер: {size_str}")
        console.print()
        
        # Подтверждение
        try:
            confirm = confirm_with_navigation(
                f"Восстановить файл {backup.original_path}?",
                default=False
            )
        except (BackException, BackToMenuException, QuitException):
            raise
        
        if confirm:
            console.print("\n[dim]⏳ Восстановление...[/]")
            
            try:
                result = backup_manager.restore_file(session.session_id, backup.original_path)
                
                if result:
                    print_success(f"✅ Файл восстановлен: {backup.original_path}")
                else:
                    print_error(f"Не удалось восстановить файл: {backup.original_path}")
                    
            except Exception as e:
                logger.error(f"Error restoring file: {e}", exc_info=True)
                print_error(f"Ошибка при восстановлении: {e}")
        else:
            print_info("Восстановление отменено")
        
        return
    
    # === ВОССТАНОВЛЕНИЕ ВСЕЙ СЕССИИ ===
    
    console.print("[bold yellow]📋 Файлы для восстановления:[/]")
    for backup in session.backups:
        size_kb = backup.file_size / 1024 if backup.file_size else 0
        if size_kb > 1024:
            size_str = f"{size_kb / 1024:.1f} MB"
        else:
            size_str = f"{size_kb:.1f} KB"
        console.print(f"   📄 {backup.original_path} ({size_str})")
    
    console.print()
    
    # Подтверждение
    try:
        confirm = confirm_with_navigation(
            f"Восстановить {len(session.backups)} файл(ов)?",
            default=False
        )
    except (BackException, BackToMenuException, QuitException):
        raise
    
    if confirm:
        console.print("\n[dim]⏳ Восстановление...[/]")
        
        results = backup_manager.restore_session(session.session_id)
        
        success_count = sum(1 for v in results.values() if v)
        fail_count = len(results) - success_count
        
        if fail_count == 0:
            print_success(f"✅ Восстановлено {success_count} файл(ов)")
            
            # Показываем список
            console.print("\n[dim]Восстановленные файлы:[/]")
            for path in results.keys():
                console.print(f"   [green]✓[/] {path}")
        else:
            print_warning(f"Восстановлено {success_count}, ошибок: {fail_count}")
            for path, success in results.items():
                if success:
                    console.print(f"   [green]✓[/] {path}")
                else:
                    console.print(f"   [red]✗[/] {path}")
        
        # Обновляем индекс
        if state.project_dir:
            console.print("\n[dim]Обновление индекса...[/]")
            await run_incremental_update(state.project_dir)
            console.print("[green]✓[/] Индекс обновлён")
    else:
        print_info("Восстановление отменено")



async def handle_changes_command(args: str):
    """Просмотр истории изменений файлов"""
    if not state.history_manager:
        print_error("Менеджер истории недоступен")
        return
    
    if not state.current_thread:
        print_info("Нет активного диалога")
        console.print("[dim]Сначала войдите в режим Агента или выберите диалог[/]")
        return
    
    # Парсим аргументы
    parts = args.strip().split(maxsplit=1)
    
    # === ИСТОРИЯ КОНКРЕТНОГО ФАЙЛА ===
    if parts and parts[0].lower() in ("file", "файл"):
        if len(parts) < 2:
            console.print("[dim]Использование: /changes file <путь_к_файлу>[/]")
            console.print("[dim]Пример: /changes file app/services/auth.py[/]")
            return
        
        file_path = parts[1].strip()
        
        try:
            changes = await state.history_manager.get_file_history(file_path, limit=15)
        except Exception as e:
            logger.error(f"Failed to get file history: {e}")
            print_error(f"Ошибка получения истории: {e}")
            return
        
        if not changes:
            print_info(f"Нет записей об изменениях файла: {file_path}")
            return
        
        console.print(f"\n[bold]📜 История изменений файла:[/] [cyan]{file_path}[/]\n")
        
        table = Table(show_header=True, box=box.ROUNDED)
        table.add_column("#", style="bold", width=3)
        table.add_column("Дата", style="dim", width=19)
        table.add_column("Тип", width=8)
        table.add_column("Строк", width=12)
        table.add_column("Статус", width=18)
        table.add_column("Диалог", width=15)
        
        for i, c in enumerate(changes, 1):
            # Форматируем дату
            created = c.created_at[:19].replace('T', ' ') if c.created_at else "?"
            
            # Эмодзи типа
            type_emoji = {"create": "🆕", "modify": "📝", "delete": "🗑️"}.get(c.change_type, "?")
            
            # Статистика строк
            if c.change_type == "delete":
                lines = f"-{c.lines_removed}"
            else:
                lines = f"+{c.lines_added}/-{c.lines_removed}"
            
            # Статус
            status_parts = []
            if c.applied:
                status_parts.append("[green]✓ Applied[/]")
            if c.rolled_back:
                status_parts.append("[yellow]↩ Rollback[/]")
            if c.user_confirmed:
                status_parts.append("[cyan]👤[/]")
            if not c.validation_passed:
                status_parts.append("[red]⚠ Invalid[/]")
            status = " ".join(status_parts) if status_parts else "[dim]Pending[/]"
            
            # ID диалога (укорачиваем)
            thread_short = c.thread_id[:12] + "..." if len(c.thread_id) > 15 else c.thread_id
            
            table.add_row(
                str(i),
                created,
                f"{type_emoji} {c.change_type}",
                lines,
                status,
                thread_short
            )
        
        console.print(table)
        
        # Подсказка о восстановлении
        has_backup = any(c.backup_path for c in changes)
        if has_backup:
            console.print(f"\n[dim]💡 Для восстановления используйте: /restore[/]")
        
        return
    
    # === ИСТОРИЯ ИЗМЕНЕНИЙ ТЕКУЩЕГО ДИАЛОГА ===
    try:
        changes = await state.history_manager.get_thread_file_changes(
            state.current_thread.id,
            only_applied=False,
            limit=30
        )
    except Exception as e:
        logger.error(f"Failed to get thread changes: {e}")
        print_error(f"Ошибка получения истории: {e}")
        return
    
    if not changes:
        print_info("В этом диалоге нет записей об изменениях файлов")
        console.print("[dim]Изменения появятся после применения кода в режиме Агента[/]")
        return
    
    console.print(f"\n[bold]📜 История изменений файлов в диалоге[/]\n")
    console.print(f"[dim]Диалог: {state.current_thread.title}[/]")
    console.print(f"[dim]ID: {state.current_thread.id}[/]\n")
    
    table = Table(show_header=True, box=box.ROUNDED)
    table.add_column("#", style="bold", width=3)
    table.add_column("Дата", style="dim", width=19)
    table.add_column("Файл", max_width=40)
    table.add_column("Тип", width=8)
    table.add_column("Строк", width=10)
    table.add_column("Статус", width=12)
    
    for i, c in enumerate(changes, 1):
        # Форматируем дату
        created = c.created_at[:19].replace('T', ' ') if c.created_at else "?"
        
        # Эмодзи типа
        type_emoji = {"create": "🆕", "modify": "📝", "delete": "🗑️"}.get(c.change_type, "?")
        
        # Статистика строк
        if c.change_type == "delete":
            lines = f"-{c.lines_removed}"
        else:
            lines = f"+{c.lines_added}/-{c.lines_removed}"
        
        # Статус
        if c.rolled_back:
            status = "[yellow]↩️ Откат[/]"
        elif c.applied:
            status = "[green]✅ OK[/]"
        else:
            status = "[dim]⏳ Pending[/]"
        
        # Укорачиваем путь к файлу
        file_display = c.file_path
        if len(file_display) > 37:
            file_display = "..." + file_display[-34:]
        
        table.add_row(
            str(i),
            created,
            file_display,
            f"{type_emoji}",
            lines,
            status
        )
    
    console.print(table)
    
    # Статистика
    total_added = sum(c.lines_added for c in changes)
    total_removed = sum(c.lines_removed for c in changes)
    applied_count = sum(1 for c in changes if c.applied)
    
    console.print(f"\n[dim]Итого: {len(changes)} изменений, "
                  f"+{total_added}/-{total_removed} строк, "
                  f"{applied_count} применено[/]")
    
    # Подсказки
    console.print()
    console.print("[dim]Команды:[/]")
    console.print("[dim]  /changes file <путь>  — история конкретного файла[/]")
    console.print("[dim]  /restore              — восстановление из бэкапа[/]")


def print_chat_help():
    """Выводит справочную информацию по командам чата"""
    help_text = """
## Доступные команды в чате

| Команда | Описание |
|---------|----------|
| `/выход`, `/q` | Выход из программы |
| `/меню`, `/m` | Вернуться в главное меню |
| `/помощь`, `/?` | Показать эту справку |
| `/история`, `/h` | Просмотр истории диалога |
| `/экспорт`, `/export` | Экспортировать диалог в .md файл |
| `/новый`, `/n` | Начать новый диалог |
| `/тред`, `/t` | Переключить тред диалога |
| `/название`, `/title` | Изменить название диалога |
| `/модель` | Выбрать модель оркестратора |
| `/генератор`, `/ген` | Выбрать модель генератора кода |
| `/типы`, `/mypy` | Вкл/выкл проверку типов (mypy) |
| `/restore [id]` | Восстановить файлы из бэкапа |
| `/changes` | История изменений файлов в диалоге |
| `/changes file <path>` | История изменений конкретного файла |
| `/прикрепить` | Прикрепить файлы (Общий Чат) |
| `/legal` | Вкл/выкл Legal режим (Общий Чат) |
| `/статус`, `/s` | Показать текущий статус |
| `/очистить` | Очистить экран |
| `/о` | О программе |

## Навигация

- Введите `0` или `назад` чтобы вернуться на шаг назад
- Введите `меню` чтобы вернуться в главное меню
- Введите `q` чтобы выйти из программы
"""
    console.print(Panel(Markdown(help_text), title="Справка", border_style=COLORS['info']))


# ============================================================================
# НАСТРОЙКА СЕССИИ ДЛЯ РЕЖИМА
# ============================================================================

async def setup_mode_session(mode: str) -> bool:
    """
    Настройка сессии для выбранного режима.
    
    Args:
        mode: Режим работы ('ask', 'agent', 'general')
        
    Returns:
        True если настройка успешна и можно начинать чат
        False если настройка отменена
        
    Raises:
        BackToMenuException: если пользователь хочет вернуться в меню
        QuitException: если пользователь хочет выйти
    """
    state.mode = mode
    state.reset_project()
    
    console.clear()
    print_header()
    
    mode_titles = {
        "ask": "💬 Режим Вопросов",
        "agent": "🤖 Режим Агента",
        "general": "💡 Общий Чат"
    }
    console.print(f"\n[bold]{mode_titles.get(mode, mode)}[/]\n")
    
    # ========================================
    # General Chat — выбор подрежима и модели
    # ========================================
    if mode == "general":
        # Выбор подрежима: обычный или Legal
        console.print("[bold]Выбор подрежима:[/]")
        console.print("[1] 💬 Обычный режим — общие вопросы, программирование")
        console.print("[2] ⚖️  Legal режим — юридические вопросы")
        console.print()
        
        try:
            submode_choice = prompt_with_navigation(
                "Выбор",
                choices=["1", "2"],
                default="1"
            )
        except BackException:
            raise BackToMenuException()
        except (BackToMenuException, QuitException):
            raise
        
        state.is_legal_mode = (submode_choice == "2")
        
        if state.is_legal_mode:
            console.print("[green]✓[/] Выбран Legal режим")
            console.print("[dim]Специализированный промпт для юридических вопросов[/]")
        else:
            console.print("[green]✓[/] Выбран обычный режим")
        
        # Выбор модели С ОПИСАНИЯМИ
        console.print("\n[bold]Выбор модели:[/]\n")
        print_model_selection_menu(show_router=True, compact=False)
        
        try:
            choice = prompt_with_navigation(
                "Выбор",
                choices=["r", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
                default="r"
            )
        except BackException:
            # Возврат к выбору подрежима — рекурсия
            return await setup_mode_session(mode)
        except (BackToMenuException, QuitException):
            raise
        
        if choice.lower() == "r":
            state.use_router = True
            state.fixed_orchestrator_model = None
            console.print("[green]✓[/] Включён автоматический роутер")
        else:
            for key, model_id, short_name, description in AVAILABLE_ORCHESTRATOR_MODELS:
                if key == choice:
                    state.use_router = False
                    state.fixed_orchestrator_model = model_id
                    console.print(f"[green]✓[/] Выбрана модель: {short_name}")
                    break
        
        # Ищем существующий тред или создаём новый
        try:
            state.current_thread = await find_or_create_thread_for_project(None)
        except (BackException, BackToMenuException, QuitException):
            raise
        
        console.print("\n" + "=" * 60)
        console.print(f"[bold green]✓ Готово![/] Режим: {mode_titles[mode]}")
        if state.is_legal_mode:
            console.print("   [yellow]Подрежим: Legal[/]")
        console.print("=" * 60 + "\n")
        console.print("[dim]Введите ваш запрос или /помощь для списка команд[/]\n")
        
        return True
    
    # ========================================
    # Ask и Agent требуют выбора типа проекта
    # ========================================
    try:
        project_type = await select_project_type()
    except BackException:
        raise BackToMenuException()
    except (BackToMenuException, QuitException):
        raise
    
    if not project_type:
        return False
    
    # ========================================
    # Настройка проекта в зависимости от типа
    # ========================================
    if project_type == "existing":
        try:
            project_path = await select_existing_project()
        except BackException:
            # Возврат к выбору типа проекта
            return await setup_mode_session(mode)
        except (BackToMenuException, QuitException):
            raise
        
        if not project_path:
            return await setup_mode_session(mode)  # Повторить
        
        state.project_dir = project_path
        state.is_new_project = False
        
        # Индексация сразу после выбора директории
        console.print()
        if await build_project_indexes(project_path):
            state.project_index = await load_project_index(project_path)
        else:
            print_warning("Индексация не удалась, продолжаем без индекса")
            state.project_index = {}
        
        # Проверка что индекс не пуст для режима Agent
        if state.mode == "agent" and not state.project_index:
            print_warning("Индекс проекта пуст!")
            print_info("Убедитесь, что в проекте есть Python файлы (.py)")
            console.print("[dim]В режиме Агента пустой индекс может привести к ошибкам.[/]")
            
            try:
                if not confirm_with_navigation("Продолжить без индекса?", default=False):
                    raise BackException()
            except (BackException, BackToMenuException, QuitException):
                raise   
   
    else:  # project_type == "new"
        try:
            project_path = await create_new_project()
        except BackException:
            # Возврат к выбору типа проекта
            return await setup_mode_session(mode)
        except (BackToMenuException, QuitException):
            raise
        
        if not project_path:
            return await setup_mode_session(mode)  # Повторить
        
        state.project_dir = project_path
        state.is_new_project = True
        state.project_index = {}  # Пустой индекс
        
        console.print("[dim]Индексация будет выполнена после применения изменений[/]")
    
    # ========================================
    # Выбор модели оркестратора С ОПИСАНИЯМИ
    # ========================================
    console.print("\n[bold]Выбор модели оркестратора:[/]\n")
    print_model_selection_menu(show_router=True, compact=False)
    
    try:
        model_choice = prompt_with_navigation(
            "Выбор",
            choices=["r", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
            default="r"
        )
    except BackException:
        # Возврат к выбору проекта
        return await setup_mode_session(mode)
    except (BackToMenuException, QuitException):
        raise
    
    if model_choice.lower() == "r":
        state.use_router = True
        state.fixed_orchestrator_model = None
        console.print("[green]✓[/] Включён автоматический роутер")
    else:
        for key, model_id, short_name, description in AVAILABLE_ORCHESTRATOR_MODELS:
            if key == model_choice:
                state.use_router = False
                state.fixed_orchestrator_model = model_id
                console.print(f"[green]✓[/] Выбрана модель оркестратора: {short_name}")
                break
    
    # ========================================
    # NEW: Выбор модели генератора (для ask и agent)
    # ========================================
    console.print("\n[bold]Выбор модели генератора кода:[/]\n")
    print_generator_model_selection_menu(compact=True)
    
    gen_valid_choices = [key for key, _, _, _ in AVAILABLE_GENERATOR_MODELS]
    
    try:
        gen_choice = prompt_with_navigation(
            "Выбор генератора",
            choices=gen_valid_choices,
            default="1",
            show_back=True,
            show_menu=True
        )
    except BackException:
        # Возврат к выбору модели оркестратора — повторяем setup
        return await setup_mode_session(mode)
    except (BackToMenuException, QuitException):
        raise
    
    for key, model_id, short_name, description in AVAILABLE_GENERATOR_MODELS:
        if key == gen_choice:
            state.generator_model = model_id
            console.print(f"[green]✓[/] Выбрана модель генератора: {short_name}")
            break
    
    # ========================================
    # Ищем существующий тред или создаём новый
    # ========================================
    try:
        state.current_thread = await find_or_create_thread_for_project(state.project_dir)
    except (BackException, BackToMenuException, QuitException):
        raise
    
    # Финальное сообщение
    console.print("\n" + "=" * 60)
    console.print(f"[bold green]✓ Готово![/] Режим: {mode_titles[mode]}")
    console.print(f"   Проект: {state.project_dir}")
    if state.is_new_project:
        console.print("   [yellow]Тип: Новый проект[/]")
    if state.use_router:
        console.print("   Оркестратор: Автоматический выбор (роутер)")
    else:
        model_name = get_model_short_name(state.fixed_orchestrator_model)
        console.print(f"   Оркестратор: {model_name}")
    
    # NEW: Показываем выбранный генератор
    gen_name = get_generator_model_short_name(state.generator_model)
    console.print(f"   Генератор: {gen_name}")
    
    console.print("=" * 60 + "\n")
    console.print("[dim]Введите ваш запрос или /помощь для списка команд[/]\n")
    
    return True


# ============================================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================================

async def main_menu_loop():
    """Главный цикл меню приложения"""
    
    while state.running:
        print_main_menu()
        
        try:
            choice = Prompt.ask(
                "Выбор",
                choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
                default="1"
            )
        except KeyboardInterrupt:
            console.print("\n[dim]Используйте '0' для выхода[/]")
            continue
        
        # Выход
        if choice == "0":
            state.running = False
            console.print("[dim]До свидания! 👋[/]")
            break
        
        # Режим Вопросов
        elif choice == "1":
            try:
                if await setup_mode_session("ask"):
                    result = await chat_loop()
                    if result == "quit":
                        break
            except BackToMenuException:
                continue
            except QuitException:
                state.running = False
                break
        
        # Режим Агента
        elif choice == "2":
            try:
                if await setup_mode_session("agent"):
                    result = await chat_loop()
                    if result == "quit":
                        break
            except BackToMenuException:
                continue
            except QuitException:
                state.running = False
                break
        
        # Общий Чат
        elif choice == "3":
            try:
                if await setup_mode_session("general"):
                    result = await chat_loop()
                    if result == "quit":
                        break
            except BackToMenuException:
                continue
            except QuitException:
                state.running = False
                break
        
        # История диалогов
        elif choice == "4":
            try:
                thread = await select_thread()
                if thread:
                    state.current_thread = thread
                    state.project_dir = thread.project_path
                    
                    # Определяем режим по наличию проекта
                    if thread.project_path:
                        state.mode = "ask"  # По умолчанию ask для проектов
                        state.project_index = await load_project_index(state.project_dir)
                    else:
                        state.mode = "general"
                        state.project_index = {}
                    
                    # Показываем статус
                    print_status_bar()
                    
                    # Спрашиваем, хочет ли пользователь продолжить диалог
                    console.print("\n[bold]Продолжить этот диалог?[/]")
                    console.print("[dim]Вы сможете отправлять новые сообщения в этот диалог[/]\n")
                    
                    try:
                        if confirm_with_navigation("Продолжить диалог?", default=True):
                            # Запускаем chat_loop для продолжения
                            result = await chat_loop()
                            if result == "quit":
                                state.running = False
                                break
                    except BackException:
                        # Возврат к списку диалогов
                        continue
                        
            except BackException:
                continue
            except BackToMenuException:
                continue
            except QuitException:
                state.running = False
                break
        
        # Настройки модели оркестратора
        elif choice == "5":
            try:
                await select_orchestrator_model()
            except (BackException, BackToMenuException, QuitException):
                pass
        
        # NEW: Настройки модели генератора
        elif choice == "6":
            try:
                await select_generator_model()
            except (BackException, BackToMenuException, QuitException):
                pass
        
        # Проверка типов
        elif choice == "7":
            await toggle_type_checking()
        
                # NEW: Настройки Pre-filter
        elif choice == "8":
            try:
                await select_prefilter_settings()
            except (BackException, BackToMenuException, QuitException):
                pass

        # О программе
        elif choice == "9":
            print_about()

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================================================

async def initialize():
    """Инициализация приложения"""
    # Инициализируем менеджер истории
    try:
        state.history_manager = HistoryManager()
        logger.info("Менеджер истории инициализирован")
    except Exception as e:
        logger.error(f"Ошибка инициализации менеджера истории: {e}", exc_info=True)
        print_warning(f"История диалогов недоступна: {e}")
    
    # Загружаем и применяем пользовательские настройки (prefilter_mode, prefilter_model, generator_model и т.д.)
    try:
        saved_settings = load_user_settings()
        apply_user_settings(saved_settings)
        logger.info(f"User settings loaded and applied: prefilter_mode={state.prefilter_mode}, prefilter_model={state.prefilter_model}")
        console.print(f"[dim]⚙️ Настройки загружены: PF режим={state.prefilter_mode}, PF модель={cfg.get_model_display_name(state.prefilter_model) if state.prefilter_model else 'Авто'}[/]")
    except Exception as e:
        logger.warning(f"Failed to load user settings, using defaults: {e}")


async def shutdown():
    """Очистка при завершении работы"""
    console.print("\n[dim]Завершение работы...[/]")
    
    # Очистка ресурсов
    if state.pipeline:
        try:
            await state.pipeline.discard_pending_changes()
        except:
            pass
    
    console.print("[green]✓[/] Сессия сохранена. До свидания!")


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

async def main():
    """Главная точка входа"""
    try:
        await initialize()
        await main_menu_loop()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        print_error(f"Критическая ошибка: {e}")
    finally:
        await shutdown()


if __name__ == "__main__":
    # Обрабатываем Ctrl+C gracefully
    def signal_handler(sig, frame):
        state.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Запуск
    asyncio.run(main())