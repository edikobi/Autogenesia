# config/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path

# Находим .env и загружаем его
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Config:
    # ============ СУЩЕСТВУЮЩИЙ КОД (не трогаем) ============
    # DeepSeek
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    MODEL_NORMAL = os.getenv("MODEL_NORMAL", "deepseek-chat")
    MODEL_DEEPSEEK_REASONER = "deepseek-reasoner"

    # OpenRouter
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    MODEL_QWEN = os.getenv("MODEL_QWEN")
    
    # GigaChat
    GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY")
    GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    
    # SSL путь
    _cert_raw = os.getenv("GIGACHAT_CA_BUNDLE")
    GIGACHAT_CA_BUNDLE = str(BASE_DIR / _cert_raw) if _cert_raw else None
    
    # Оператор из РФ
    ROUTERAI_API_KEY = os.getenv("ROUTERAI_API_KEY")
    ROUTERAI_BASE_URL = os.getenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")    
    
    
    # ============ ОБНОВЛЕННЫЕ МОДЕЛИ (Через RouterAI) ============
    
    # Claude Opus 4.5 - для сложных задач (security, concurrency, heisenbug)
    MODEL_OPUS_4_5 = "anthropic/claude-opus-4.5" 
    
    # Claude Sonnet 4.5 - для средних задач (NEW!)
    MODEL_SONNET_4_5 = "anthropic/claude-sonnet-4.5"
    
    # GPT-5.1 Codex Max - для простых задач
    # (переменная называется MODEL_GPT_5_2_Codex по историческим причинам)
    MODEL_GPT_5_2_Codex = "openai/gpt-5.2-codex"
    
# !!! GEMINI 3.0 PRO (РЕАЛЬНАЯ МОДЕЛЬ) !!!
    # ID модели в RouterAI/OpenRouter для версии 3.0 Pro
    MODEL_GEMINI_3_PRO = "google/gemini-3-pro-preview"     
    
# Gemini 2.0 Flash (для роутера и сжатия истории)
    MODEL_GEMINI_2_FLASH = "google/gemini-2.0-flash-001"

    # ============ НОВЫЕ МОДЕЛИ ГЕНЕРАТОРА (OpenRouter) ============
    MODEL_GLM_4_7 = "z-ai/glm-4.7"                # GLM 4.7
    MODEL_HAIKU_4_5 = "anthropic/claude-haiku-4.5" # Claude Haiku 4.5
    MODEL_GEMINI_3_FLASH = "google/gemini-3-flash-preview"
    MODEL_GPT_5_1_Codex_MINI = "openai/gpt-5.1-codex-mini"

    # ============ ПЕРЕКЛЮЧАТЕЛЬ ГЕНЕРАТОРА ============
    # Чтобы сменить модель, просто раскомментируйте нужную строку ниже:
    
    SELECTED_GENERATOR_MODEL = MODEL_NORMAL      # <--- Включен DeepSeek (по умолчанию)
    SELECTED_GENERATOR_MODEL = MODEL_GLM_4_7     # <--- Раскомментируйте для GLM 4.7
    SELECTED_GENERATOR_MODEL = MODEL_HAIKU_4_5   # <--- Раскомментируйте для Haiku 4.5
    SELECTED_GENERATOR_MODEL = MODEL_GEMINI_3_FLASH
    SELECTED_GENERATOR_MODEL = MODEL_GPT_5_1_Codex_MINI




    # ============ КАРТА ПРОВАЙДЕРОВ (ОБНОВЛЕНО) ============
    # Позволяет коду агента определять, какой клиент инициализировать для модели
    
    MODEL_CONFIGS = {
        # === ГРУППА ROUTER AI ===
        
        # --- GEMINI 3.0 PRO CONFIG ---(разблокировать, если на Openrouter заблочат, просто поставить ключ из переменных росс. провайдера)
        "google/gemini-3-pro-preview": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER",
            # ВКЛЮЧАЕМ "ПЕРЕДАЧУ МЫСЛЕЙ" (Native Reasoning)
            "extra_params": {
                # Для RouterAI/OpenAI-compat это активирует усиленное рассуждение
                "reasoning_effort": "high",
                # Если провайдер поддерживает прямой параметр Google:
                # "thinking_mode": "enabled" 
            }
        },
        
        "anthropic/claude-opus-4.5": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER"
        },
        # Claude Sonnet 4.5 - для средних задач (multi-component, business logic)
        "anthropic/claude-sonnet-4.5": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER",
            
            # Extended thinking для Sonnet 4.5 (оптимальный бюджет для средних задач)
            "extra_params": {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 40000  # Сбалансированный бюджет: достаточно для анализа, не избыточно
                }
            }
        },
        "openai/gpt-5.2-codex": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER",
            
            # Настройка максимального рассуждения для GPT-5.1
            "extra_params": {
                "reasoning_effort": "xhigh"  # Варианты: "medium", "high", "xhigh"
            }
        },
        
        "openai/gpt-5.1-codex-mini": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            
            # Настройка максимального рассуждения для GPT-5.1
            "extra_params": {
                "reasoning_effort": "low",  # Варианты: "medium", "high", "xhigh"
                "max_tokens": 3000
            },
        },
        
        
# === ГРУППА ГЕНЕРАТОРОВ (OPENROUTER) ===
        # GLM 4.7 (Thinking ВЫКЛЮЧЕН)
        "z-ai/glm-4.7": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter (Zhipu)",
            "extra_params": {
                "reasoning": {
                    "effort": "none"
                }
            }
        },
        
        # Claude Haiku 4.5 (Thinking ВЫКЛЮЧЕН)
        "anthropic/claude-haiku-4.5": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter (Anthropic)",
            "extra_params": {} # Явно пустой, без reasoning
        },


        "google/gemini-3-flash-preview": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter (Google)",
            "extra_params": {
            }
        },


        
        # === ГРУППА DEEPSEEK ===
        "deepseek-chat": {
            "api_key": DEEPSEEK_API_KEY,  # ← переменная класса!,
            "base_url": DEEPSEEK_BASE_URL,
            "provider_name": "DeepSeek"
        },
        # DeepSeek V3.2 Reasoning (R1)
        "deepseek-reasoner": {
            "api_key": DEEPSEEK_API_KEY,
            "base_url":DEEPSEEK_BASE_URL,
            "provider_name": "DeepSeek",
            "extra_params": {
            }
        },
        
        # === ГРУППА ПО УМОЛЧАНИЮ (OPENROUTER) ===
        "default": {
            "api_key": os.getenv("OPENROUTER_API_KEY"),
            "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "provider_name": "OpenRouter"
        }
    }
    
    
    @classmethod
    def get_provider_config(cls, model_name):
        """
        Возвращает правильный (api_key, base_url) для конкретной модели.
        Используйте этот метод при создании клиента OpenAI!
        """
        # 1. Принудительные правила для RouterAI
        if model_name in [ cls.MODEL_GPT_5_2_Codex]:
            return {
                "api_key": cls.ROUTERAI_API_KEY,
                "base_url": cls.ROUTERAI_BASE_URL,
                "name": "RouterAI"
            }
            
        # 2. Правила для DeepSeek
        if model_name in [cls.MODEL_NORMAL, cls.MODEL_DEEPSEEK_REASONER]:
            return {
                "api_key": cls.DEEPSEEK_API_KEY,
                "base_url": cls.DEEPSEEK_BASE_URL,
                "name": "DeepSeek"
            }
            
        # 3. Все остальное по умолчанию идет в OpenRouter
        return {
            "api_key": cls.OPENROUTER_API_KEY,
            "base_url": cls.OPENROUTER_BASE_URL,
            "name": "OpenRouter"
        }
    
    
    # ============ НАСТРОЙКИ РОУТЕРА И ОРКЕСТРАТОРА (ОБНОВЛЕНО) ============
    # Схема работы:
    # 1. Если ROUTER_ENABLED = True → используем автоматический выбор модели через роутер
    #    Роутер выбирает между 3 уровнями: simple (GPT-5.1), medium (Sonnet 4.5), complex (Opus 4.5)
    # 2. Если ROUTER_ENABLED = False → проверяем ORCHESTRATOR_FIXED_MODEL:
    #    - если None → используем ORCHESTRATOR_SIMPLE_MODEL по умолчанию
    #    - если задана модель → используем указанную модель
    
    # Включен ли автоматический роутер
    ROUTER_ENABLED = True
    
    # Модель для самого роутера (классификатора)
    ROUTER_MODEL = MODEL_GEMINI_2_FLASH  # Gemini 2.0 Flash через OpenRouter
    
    # Модели для автоматического выбора (если ROUTER_ENABLED = True)
    # ТРЁХУРОВНЕВАЯ СИСТЕМА:
    ORCHESTRATOR_SIMPLE_MODEL = MODEL_GPT_5_2_Codex   # 🟢 Простые задачи → GPT-5.1 Codex Max
    ORCHESTRATOR_MEDIUM_MODEL = MODEL_SONNET_4_5     # 🟡 Средние задачи → Claude Sonnet 4.5 (NEW!)
    ORCHESTRATOR_COMPLEX_MODEL = MODEL_OPUS_4_5      # 🔴 Сложные задачи → Claude Opus 4.5
    
    # Фиксированная модель (если ROUTER_ENABLED = False)
    # None = использовать ORCHESTRATOR_SIMPLE_MODEL по умолчанию
    # Или указать конкретную модель из доступных (включая MODEL_DEEPSEEK_REASONER)
    ORCHESTRATOR_FIXED_MODEL = None  # Пример: MODEL_OPUS_4_5, MODEL_SONNET_4_5, MODEL_DEEPSEEK_REASONER
    
    # ============ МОДЕЛИ ДЛЯ ДРУГИХ РОЛЕЙ ============
    AGENT_MODELS = {
        # Эти роли всегда используют указанные модели, независимо от роутера
        "pre_filter": MODEL_NORMAL,               # DeepSeek с прямого API
        "code_generator": SELECTED_GENERATOR_MODEL,           # DeepSeek с прямого API
        "history_compressor": MODEL_GEMINI_2_FLASH,   # Gemini 2.0 Flash через OpenRouter
    }
    
    # ============ НАСТРОЙКИ AI АГЕНТА ============
    PRE_FILTER_MAX_CHUNKS = 5
    PRE_FILTER_MAX_TOKENS = 75000  # Лимит 75k токенов
    
    HISTORY_COMPRESSION_ENABLED = True
    HISTORY_THRESHOLD_TOKENS = 8000
    HISTORY_MAX_MESSAGES = 20
    
    # ============ ПУТИ ДЛЯ AI АГЕНТА ============
    INDEX_FILE = ".ai-agent/index.json"
    
    # ============ НАСТРОЙКИ PROJECT MAP ============
    PROJECT_MAP_FILE = ".ai-agent/project_map.json"
    PROJECT_MAP_MAX_FILE_TOKENS = 30000  # Лимит токенов для AI-анализа файла
    PROJECT_MAP_DESCRIBE_MODEL = MODEL_NORMAL  # DeepSeek для описаний    
    
    # Модель по умолчанию для режима General Chat (используем мощную GPT-5.1)
    GENERAL_CHAT_MODEL = os.getenv("GENERAL_CHAT_MODEL", MODEL_GPT_5_2_Codex)    
    
    # Лимит токенов на все файлы пользователя в режиме General Chat (PDF, DOCX, TXT и т.д.)
    MAX_USER_FILES_TOKENS = 55000
    
    
    # ============ AGENT MODE SETTINGS ============
    
    AGENT_MODE_CONFIG = {
        # --- Iteration Limits ---
        "max_iterations": None,  # Без лимита для Agent Mode
        "max_validator_retries": 3,
        "max_orchestrator_revisions": 3,
        
        # --- Timeouts ---
        "validation_timeout_sec": 120,
        "ai_validator_timeout_sec": 60,
        
    # --- Test Execution Limits (NEW) ---
        "max_test_runs_per_session": 5,
        "test_timeout_sec": 30,
        "test_output_limit": 2000,
        
        # --- AI Validator Model Selection ---
        "ai_validator_token_threshold": 10000,
        "ai_validator_model_small": "qwen/qwen-2.5-coder-32b-instruct",
        "ai_validator_model_large": "deepseek-chat",
        
        # --- Validation Levels ---
        # ВСЕ уровни включены по умолчанию
        # Соответствует ValidatorConfig в change_validator.py
        "validation_levels": [
            "syntax",       # Проверка синтаксиса (ast.parse)
            "imports",      # Проверка импортов (stdlib, pip, project)
            "types",        # Проверка типов (mypy)
            "integration",  # Проверка совместимости с зависимыми файлами
            "runtime",      # Import check в subprocess
        ],
        
        # Уровни, которые пользователь может отключить
        # Пример: ["types", "runtime"] — отключит mypy и runtime check
        "disabled_validation_levels": [],
        
        # --- User Confirmation ---
        "require_user_confirmation": True,
        "show_diff_preview": True,
        "show_affected_files": True,
        
        # --- Backup ---
        "backup_enabled": True,
        "backup_retention_days": 7,
        "backup_dir": ".ai-agent/backups",
    }
    
    
    
    
    
    # ============ МЕТОДЫ ДЛЯ УДОБНОГО ВЫБОРА МОДЕЛИ (ОБНОВЛЕНО) ============
    @classmethod
    def get_orchestrator_model_config(cls):
        """
        Возвращает конфигурацию выбора модели оркестратора
        согласно схеме из плана проекта (теперь с 3 уровнями!)
        
        Returns:
            dict: {
                "mode": "router" | "fixed",
                "router_model": "model_name",  # для классификации
                "orchestrator_models": {"simple", "medium", "complex"},  # для выбора
                "fixed_model": "model_name" or None
            }
        """
        if cls.ROUTER_ENABLED:
            return {
                "mode": "router",
                "router_model": cls.ROUTER_MODEL,
                "orchestrator_models": {
                    "simple": cls.ORCHESTRATOR_SIMPLE_MODEL,
                    "medium": cls.ORCHESTRATOR_MEDIUM_MODEL,  # NEW!
                    "complex": cls.ORCHESTRATOR_COMPLEX_MODEL
                },
                "fixed_model": None
            }
        else:
            # Если роутер отключен, используем фиксированную модель
            # Если фиксированная модель не указана, используем простую по умолчанию
            fixed_model = cls.ORCHESTRATOR_FIXED_MODEL or cls.ORCHESTRATOR_SIMPLE_MODEL
            return {
                "mode": "fixed",
                "router_model": None,
                "orchestrator_models": None,
                "fixed_model": fixed_model
            }
            
            
    
    @classmethod
    def get_available_orchestrator_models(cls):
        """
        Возвращает список всех доступных моделей для оркестратора
        
        Returns:
            list: Список имен моделей
        """
        return [
            cls.MODEL_OPUS_4_5,
            cls.MODEL_SONNET_4_5,  # NEW!
            cls.MODEL_GPT_5_2_Codex,
            cls.MODEL_GEMINI_2_FLASH,
            cls.MODEL_GEMINI_3_PRO,
            cls.MODEL_DEEPSEEK_REASONER,
            cls.MODEL_QWEN if cls.MODEL_QWEN else None,
        ]
    
    @classmethod
    def validate_orchestrator_model(cls, model_name):
        """
        Проверяет, является ли модель допустимой для оркестратора
        
        Args:
            model_name: Имя модели для проверки
            
        Returns:
            bool: True если модель допустима
        """
        available_models = cls.get_available_orchestrator_models()
        return model_name in available_models
    
    @classmethod
    def get_model_connection_config(cls, model_name):
        """
        Возвращает параметры подключения (api_key, base_url) для конкретной модели.
        """
        return cls.MODEL_CONFIGS.get(model_name, cls.MODEL_CONFIGS["default"])

    @classmethod
    def get_model_display_name(cls, model_id):
        # Словарь красивых имен
        model_names = {
            cls.MODEL_OPUS_4_5: "Claude Opus 4.5",
            cls.MODEL_SONNET_4_5: "Claude Sonnet 4.5",  # NEW!
            cls.MODEL_DEEPSEEK_REASONER: "DeepSeek V3.2 Reasoning",
            cls.MODEL_GPT_5_2_Codex: "GPT-5.2 Codex",
            cls.MODEL_GEMINI_3_PRO: "✨ Gemini 3.0 Pro (Thinking)",
            cls.MODEL_GEMINI_2_FLASH: "Gemini 2.0 Flash",
            cls.MODEL_NORMAL: "DeepSeek Chat (прямой API)",
            # Модели генератора
            cls.MODEL_GLM_4_7: "GLM 4.7 (OpenRouter)",
            cls.MODEL_HAIKU_4_5: "Claude Haiku 4.5 (OpenRouter)",
            cls.MODEL_GEMINI_3_FLASH : "Gemini 3.0 flash",
            cls.MODEL_GPT_5_1_Codex_MINI : "GPT-5.1-Codex-Mini"
        }
        
        # Если модель есть в словаре - возвращаем красивое имя
        if model_id in model_names:
            return model_names[model_id]
            
        # Универсальный фолбек: если модели нет в списке, 
        # пробуем определить провайдера и вывести хоть что-то
        if "/" in model_id:
            try:
                conf = cls.get_model_connection_config(model_id)
                provider = conf.get("provider_name", "OpenRouter")
                short_name = model_id.split("/")[-1]
                return f"{short_name} ({provider})"
            except:
                pass
                
        return model_id


    
    @classmethod
    def get_active_validation_levels(cls) -> list:
        """
        Возвращает активные уровни валидации.
        Учитывает disabled_validation_levels.
        
        Returns:
            List[str]: Активные уровни ["syntax", "imports", ...]
        """
        all_levels = cls.AGENT_MODE_CONFIG["validation_levels"]
        disabled = cls.AGENT_MODE_CONFIG.get("disabled_validation_levels", [])
        return [level for level in all_levels if level not in disabled]
    
    @classmethod
    def get_ai_validator_model(cls, token_count: int) -> str:
        """
        Выбирает модель для AI Validator в зависимости от размера контекста.
        
        Args:
            token_count: Количество токенов в контексте
            
        Returns:
            Идентификатор модели
        """
        threshold = cls.AGENT_MODE_CONFIG["ai_validator_token_threshold"]
        if token_count < threshold:
            return cls.AGENT_MODE_CONFIG["ai_validator_model_small"]
        return cls.AGENT_MODE_CONFIG["ai_validator_model_large"]


         
         
         
            # Доступные модели генератора для выбора
# Формат: (key, model_id, short_name, description)
AVAILABLE_GENERATOR_MODELS = [
    (
        "1",
        "deepseek-chat",
        "DeepSeek Chat",
        "Базовая модель. Быстрая, дешёвая, хорошо справляется с простыми задачами генерации."
    ),
    (
        "2",
        "z-ai/glm-4.7",
        "GLM 4.7",
        "Китайская модель от Zhipu AI. Хороша для структурированного кода, поддерживает thinking mode."
    ),
    (
        "3",
        "anthropic/claude-haiku-4.5",
        "Claude Haiku 4.5",
        "Лёгкая модель от Anthropic. Быстрая, качественная, отлично следует инструкциям."
    ),
    (
        "4",
        "google/gemini-3-flash-preview",
        "Gemini 3.0 Flash",
        "Быстрая модель Google через OpenRouter. Хорошо подходит для генерации кода"
    ),

    (
        "5",
        "openai/gpt-5.1-codex-mini",
        "GPT-5.1-Codex-Mini",
        "Младшая модель CODEX от OpenAI, мнимально думающая и быстрая, потом это надо иметь в виду"
    ),
]


# Создаем объект конфигурации
cfg = Config()

# ============ ФУНКЦИИ ДЛЯ РАБОТЫ С КОНФИГОМ ============
def print_config_summary():
    """Выводит сводку конфигурации"""
    print("=" * 60)
    print("⚙️  КОНФИГУРАЦИЯ AI АГЕНТА")
    print("=" * 60)
    
    # Настройки роутера
    router_config = cfg.get_orchestrator_model_config()
    
    print(f"\n🎯 РЕЖИМ ВЫБОРА МОДЕЛИ ОРКЕСТРАТОРА:")
    if router_config["mode"] == "router":
        print(f"   • Автоматический роутер: ✅ ВКЛЮЧЕН")
        print(f"   • Модель роутера: {cfg.get_model_display_name(cfg.ROUTER_MODEL)}")
        print(f"   • 🟢 Простые задачи: {cfg.get_model_display_name(cfg.ORCHESTRATOR_SIMPLE_MODEL)}")
        print(f"   • 🟡 Средние задачи: {cfg.get_model_display_name(cfg.ORCHESTRATOR_MEDIUM_MODEL)}")  # NEW!
        print(f"   • 🔴 Сложные задачи: {cfg.get_model_display_name(cfg.ORCHESTRATOR_COMPLEX_MODEL)}")
    else:
        print(f"   • Автоматический роутер: ❌ ВЫКЛЮЧЕН")
        if cfg.ORCHESTRATOR_FIXED_MODEL:
            print(f"   • Фиксированная модель: {cfg.get_model_display_name(cfg.ORCHESTRATOR_FIXED_MODEL)}")
        else:
            print(f"   • Фиксированная модель: {cfg.get_model_display_name(cfg.ORCHESTRATOR_SIMPLE_MODEL)} (по умолчанию)")
    
    print(f"\n🤖 МОДЕЛИ ДЛЯ ДРУГИХ РОЛЕЙ:")
    for role, model in cfg.AGENT_MODELS.items():
        display_name = cfg.get_model_display_name(model)
        source = "прямой API DeepSeek" if model == cfg.MODEL_NORMAL else "OpenRouter"
        print(f"   • {role:20} → {display_name:30} ({source})")
    
    print(f"\n⚙️  НАСТРОЙКИ:")
    print(f"   • Pre-filter макс. чанков: {cfg.PRE_FILTER_MAX_CHUNKS}")
    print(f"   • Pre-filter макс. токенов: {cfg.PRE_FILTER_MAX_TOKENS}")
    print(f"   • Сжатие истории: {'✅ Вкл' if cfg.HISTORY_COMPRESSION_ENABLED else '❌ Выкл'}")
    
    print(f"\n🔑 ПРОВЕРКА КЛЮЧЕЙ:")
    print(f"   • OpenRouter API ключ: {'✅ установлен' if cfg.OPENROUTER_API_KEY else '❌ отсутствует'}")
    print(f"   • DeepSeek API ключ: {'✅ установлен' if cfg.DEEPSEEK_API_KEY else '❌ отсутствует'}")
    print(f"   • RouterAI API ключ: {'✅ установлен' if cfg.ROUTERAI_API_KEY else '❌ отсутствует'}")
    
    print("\n" + "=" * 60)

# Автоматически выводим сводку при импорте (только если не в основном модуле)
if __name__ != "__main__":
    print_config_summary()
    
