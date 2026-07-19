# config/settings.py
import os
import logging
from dotenv import load_dotenv
from pathlib import Path
from openai import OpenAI
import json
from typing import Optional, Tuple, Dict

logger = logging.getLogger(__name__)


# Находим .env и загружаем его
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

PROVIDER_KEYS_FILE = BASE_DIR / "config" / "provider_keys.json"


def _load_provider_keys() -> dict:
    """Load provider API keys from config/provider_keys.json.

    If the file doesn't exist, creates it with default values.
    Falls back to .env values for openrouter, routerai, deepseek if api_key is empty.
    """
    default_keys = {
        "openrouter": {"api_key": "", "base_url": "https://openrouter.ai/api/v1"},
        "routerai": {"api_key": "", "base_url": "https://routerai.ru/api/v1"},
        "deepseek": {"api_key": "", "base_url": "https://api.deepseek.com"},
        "glm": {"api_key": "", "base_url": "https://api.z.ai/api/paas/v4/"},
        "opencode_go": {"api_key": "", "base_url": "https://opencode.ai/zen/go/v1"},
        "qwencloud": {"api_key": "", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"},
        "reasoning_effort": "high",
        "disabled_providers": [],
        "provider_entry_order": [],
            "selected_agent_provider": None,
    }

    if PROVIDER_KEYS_FILE.exists():
        try:
            with open(PROVIDER_KEYS_FILE, "r", encoding="utf-8") as f:
                keys = json.load(f)
        except (json.JSONDecodeError, OSError):
            keys = default_keys
    else:
        keys = default_keys
        try:
            PROVIDER_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PROVIDER_KEYS_FILE, "w", encoding="utf-8") as f:
                json.dump(keys, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    # Ensure new keys exist if loading from an older file
    if "disabled_providers" not in keys:
        keys["disabled_providers"] = []
    if "provider_entry_order" not in keys:
        keys["provider_entry_order"] = []
    if "selected_agent_provider" not in keys:
        keys["selected_agent_provider"] = None

    # Fallback to .env values for providers with empty api_key
    env_fallbacks = {
        "openrouter": "OPENROUTER_API_KEY",
        "routerai": "ROUTERAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    for provider, env_var in env_fallbacks.items():
        if provider in keys and not keys[provider].get("api_key"):
            env_val = os.getenv(env_var, "")
            if env_val:
                keys[provider]["api_key"] = env_val

    return keys



PROVIDER_KEYS = _load_provider_keys()

class Config:
    # ============ СУЩЕСТВУЮЩИЙ КОД (не трогаем) ============
    # DeepSeek
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    MODEL_NORMAL = os.getenv("MODEL_NORMAL", "deepseek-chat")
    MODEL_DEEPSEEK_REASONER = os.getenv("MODEL_DEEPSEEK_REASONER", "deepseek-v4-pro")
    

    # OpenRouter
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    # Эта модель делает индексную карту
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
    
    
    # ============ ОБНОВЛЕННЫЕ МОДЕЛИ  ============
    
    # Claude Opus 4.5 - для сложных задач (security, concurrency, heisenbug)
    MODEL_OPUS_4_5 = "anthropic/claude-opus-4.5" 
    
    # Claude Opus 4.7 - для сложных задач (security, concurrency, heisenbug) и теперь с большим окном
    MODEL_OPUS_4_8 = "anthropic/claude-opus-4.8" 
    
    MODEL_OPUS_4_6 = MODEL_OPUS_4_8  # алиас для обратной совместимости
    
    # Claude Sonnet 4.5 - для средних задач (NEW!)
    MODEL_SONNET_4_5 = "anthropic/claude-sonnet-4.5"
    
    # Claude Sonnet 4.6 - для средних задач (новая модель)
    MODEL_SONNET_4_6 = "anthropic/claude-sonnet-5"
    
    # GPT-5.1 Codex Max - для простых задач
    # (переменная называется MODEL_GPT_5_2_Codex по историческим причинам)
    MODEL_GPT_5_2_Codex = "openai/gpt-5.2-codex"
    
# !!! GEMINI 3.0 PRO (РЕАЛЬНАЯ МОДЕЛЬ) !!!
    # ID модели в RouterAI/OpenRouter для версии 3.0 Pro
    MODEL_GEMINI_3_PRO = "google/gemini-3.1-pro-preview"     
    
    MODEL_MiniMax_M3 = "minimax/minimax-m3"
    
# Gemini 2.0 Flash (для роутера и сжатия истории)
    MODEL_GEMINI_2_FLASH = "google/gemini-2.0-flash-001"
    
    # Gemini 3.1 Flash-Lite (я тут менял)
    MODEL_GEMINI_FLASH_LITE = "google/gemini-3.1-flash-lite"
    
    MODEL_Gemma_4_31B = "google/gemma-4-31b-it"
    
    MODEL_GLM_5_2 = "z-ai/glm-5.2"
    
    MODEL_Kimi_K_2_7_Code = "moonshotai/kimi-k2.7-code"
    
# !!! НОВАЯ МОДЕЛЬ QWEN3 MAX THINKING !!!
    MODEL_QWEN_3_7_MAX = "qwen/qwen3.7-max"    
    
    MODEL_QWEN_3_5_Plus = "qwen/qwen3.5-plus-02-15"    
    
    MODEL_QWEN3_Coder_Next = "qwen/qwen3-coder-next"

    MODEL_Xiaomi_MiMo_V2_5_PRO = "xiaomi/mimo-v2.5-pro"

    MODEL_QWEN_3_7_Plus = "qwen/qwen3.7-plus"
    
    MODEL_Nemotron_3_ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"

    # ============ НОВЫЕ МОДЕЛИ ГЕНЕРАТОРА (OpenRouter) ============
    MODEL_GLM_5_Turbo = "z-ai/glm-5-turbo"                # GLM 4.7
    MODEL_HAIKU_4_5 = "anthropic/claude-haiku-4.5" # Claude Haiku 4.5
    MODEL_GEMINI_3_FLASH = "google/gemini-3.5-flash"
    MODEL_GPT_5_1_Codex_MINI = "openai/gpt-5.1-codex-mini"
    MODEL_QWEN_3_5 = "qwen/qwen3.5-397b-a17b"
    MODEL_Grok_4_3 = "x-ai/grok-4.3"

    # ============ ПЕРЕКЛЮЧАТЕЛЬ ГЕНЕРАТОРА ============
    # Default fallback only. Real selection is dynamic via
    # config.intermediate_agent_models.get_generator_model_for_agent() based on
    # available providers and cfg.get_selected_agent_provider().
    SELECTED_GENERATOR_MODEL = MODEL_NORMAL




    # ============ КАРТА ПРОВАЙДЕРОВ (ОБНОВЛЕНО) ============
    # Позволяет коду агента определять, какой клиент инициализировать для модели
    
    MODEL_CONFIGS = {
        # === ГРУППА ROUTER AI ===
        
        # --- GEMINI 3.0 PRO CONFIG ---(разблокировать, если на Openrouter заблочат, просто поставить ключ из переменных росс. провайдера)
        "google/gemini-3.1-pro-preview": {
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
        
        "google/gemini-2.5-flash-lite-preview-09-2025": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter (Google)",
            "extra_params": {
                # "thinking": {"enabled": False}  # опционально
            }
        },
        
        
        "google/gemini-3.5-flash": {  # Используем ID модели
            "api_key": OPENROUTER_API_KEY,          # Ключ от OpenRouter
            "base_url": OPENROUTER_BASE_URL,        # Базовый URL OpenRouter
            "provider_name": "OPENROUTER",    # Название провайдера для отображения

            # ВКЛЮЧАЕМ РЕЖИМ МЫШЛЕНИЯ!
            # Для OpenAI-совместимых API, таких как OpenRouter, используется параметр reasoning_effort [citation:10].
            "reasoning": {"effort": "high"}
        },
        
        
        "qwen/qwen3.7-max": {  # Используем ID модели
            "api_key": OPENROUTER_API_KEY,          # Ключ от OpenRouter
            "base_url": OPENROUTER_BASE_URL,        # Базовый URL OpenRouter
            "provider_name": "OPENROUTER",    # Название провайдера для отображения

            # ВКЛЮЧАЕМ РЕЖИМ МЫШЛЕНИЯ!
            # Для OpenAI-совместимых API, таких как OpenRouter, используется параметр reasoning_effort [citation:10].
            "reasoning": {"effort": "xhigh"}
        },
        
        "minimax/minimax-m3": {  # Используем ID модели
            "api_key": OPENROUTER_API_KEY,          # Ключ от OpenRouter
            "base_url": OPENROUTER_BASE_URL,        # Базовый URL OpenRouter
            "provider_name": "OPENROUTER",    # Название провайдера для отображения

            # ВКЛЮЧАЕМ РЕЖИМ МЫШЛЕНИЯ!
            # Для OpenAI-совместимых API, таких как OpenRouter, используется параметр reasoning_effort [citation:10].
            "reasoning": {"effort": "xhigh"}
        },
        
        
        # Конфигурация для Qwen3.5 Plus (НОВАЯ!)
        "qwen/qwen3.5-plus-02-15": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter (Qwen 3.5 Plus)",
            
            # ВКЛЮЧАЕМ РЕЖИМ МЫШЛЕНИЯ!
            # Используем параметр "reasoning" как указано в документации OpenRouter [citation:1]
            "reasoning": {"effort": "xhigh"}
        },
        
        "moonshotai/kimi-k2.7-code": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "xhigh"}
        },
        
        "xiaomi/mimo-v2.5-pro": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "xhigh"}
        },
        
        "z-ai/glm-5.2": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "high"}
        },
        
        "qwen/qwen3.7-plus": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "high"}
        },
        
        
# === ГРУППА ГЕНЕРАТОРОВ (OPENROUTER) ===
        # Qwen3.5 397B A17B
        "qwen/qwen3.5-397b-a17b": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
           "reasoning": {"effort": "high"}
        },
        
        "nvidia/nemotron-3-ultra-550b-a55b": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "high"}
        },
        
        
        "anthropic/claude-opus-4.5": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER"
        },
        
        "anthropic/claude-opus-4.8": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER",
            
            # Extended thinking для Opus 4.6 (оптимальный бюджет для средних задач)
            "extra_body": {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 45000  # Сбалансированный бюджет: достаточно для анализа, не избыточно
                }
            }
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
        
        # Claude Sonnet 4.6 - для средних задач (multi-component, business logic)
        "anthropic/claude-sonnet-5": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OPENROUTER",
            
            # Extended thinking для Sonnet 4.6 (оптимальный бюджет для средних задач)
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
            "reasoning": {"effort": "xhigh"}
        },
        
        "openai/gpt-5.1-codex-mini": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            
            # Настройка максимального рассуждения для GPT-5.1
            "extra_body": {
                "max_tokens": 3000
            },
        },
        
        
# === ГРУППА ГЕНЕРАТОРОВ (OPENROUTER) ===
        # GLM 4.7 (Thinking В)
        "z-ai/glm-5-turbo": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "high"}
        },
        
        # Claude Haiku 4.5 (Thinking)
        "anthropic/claude-haiku-4.5": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter (Anthropic)",
            "extra_params": {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 7500  # Сбалансированный бюджет: достаточно для анализа, не избыточно
                }
            }
        },

        "x-ai/grok-4.3": {
            "api_key": OPENROUTER_API_KEY,
            "base_url": OPENROUTER_BASE_URL,
            "provider_name": "OpenRouter",
            "reasoning": {"effort": "xhigh"}
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
        # === DEEPSEEK V4 (current generation) ===
        "deepseek-v4-flash": {
            "api_key": DEEPSEEK_API_KEY,
            "base_url": DEEPSEEK_BASE_URL,
            "provider_name": "DeepSeek",
            "extra_params": {
            }
        },
        "deepseek-v4-pro": {
            "api_key": DEEPSEEK_API_KEY,
            "base_url": DEEPSEEK_BASE_URL,
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
    #    Роутер выбирает между 3 уровнями: simple, medium, complex (dynamic per provider)
    # 2. Если ROUTER_ENABLED = False → проверяем ORCHESTRATOR_FIXED_MODEL:
    #    - если None → используем dynamic orchestrator model по умолчанию
    #    - если задана модель → используем указанную модель

    # Включен ли автоматический роутер
    ROUTER_ENABLED = True

    # Модель для самого роутера (классификатора) — DEPRECATED static fallback.
    # Динамический выбор через get_router_model() (get_intermediate_model("router", ...)).
    # Если OpenRouter disabled, get_router_model() выбирает через preferred-провайдера.
    ROUTER_MODEL = MODEL_GEMINI_2_FLASH  # Gemini 2.0 Flash через OpenRouter (fallback only)

    # DEPRECATED static fallbacks for orchestrator tiers. Real selection is dynamic
    # via get_orchestrator_model_config() which calls get_orchestrator_models_3level().
    # Kept as safe fallbacks (NOT OpenRouter) so import-time references still work.
    ORCHESTRATOR_SIMPLE_MODEL = MODEL_NORMAL   # dynamic: get_orchestrator_models_3level()
    ORCHESTRATOR_MEDIUM_MODEL = MODEL_NORMAL   # dynamic: get_orchestrator_models_3level()
    ORCHESTRATOR_COMPLEX_MODEL = MODEL_NORMAL  # dynamic: get_orchestrator_models_3level()

    # Фиксированная модель (если ROUTER_ENABLED = False)
    # None = использовать dynamic orchestrator model по умолчанию
    # Или указать конкретную модель из доступных (включая MODEL_DEEPSEEK_REASONER)
    ORCHESTRATOR_FIXED_MODEL = None  # Пример: MODEL_OPUS_4_5, MODEL_SONNET_4_5, MODEL_DEEPSEEK_REASONER
    
    # ============ МОДЕЛИ ДЛЯ ДРУГИХ РОЛЕЙ ============
    AGENT_MODELS = {
        # Эти роли всегда используют указанные модели, независимо от роутера
        "pre_filter": MODEL_NORMAL,               # DeepSeek с прямого API
        "code_generator": SELECTED_GENERATOR_MODEL,           # DeepSeek с прямого API
        "history_compressor": MODEL_GEMINI_2_FLASH,   # Gemini 2.0 Flash через OpenRouter
    }
    
        # ============ НАСТРОЙКИ PRE-FILTER ============
    # Режим Pre-filter: "normal" или "advanced"
    # normal — анализ на основе имеющихся данных (быстрее, дешевле)
    # advanced — с доступом к инструментам (глубже, но дороже)
    PREFILTER_DEFAULT_MODE = "normal"
    
    # Модель Pre-filter по умолчанию (можно переопределить в user_settings.json)
    PREFILTER_DEFAULT_MODEL = MODEL_NORMAL

    
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
        "ai_validator_token_threshold": 300000,
        "ai_validator_model_small": MODEL_GEMINI_FLASH_LITE,
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
    def get_router_model(cls) -> str:
        """Dynamically select the router classifier model.

        Uses get_intermediate_model("router", ...) so the router classifier
        works even when OpenRouter is disabled. Falls back to cls.ROUTER_MODEL
        (static OpenRouter model) on any error.
        """
        try:
            from config.intermediate_agent_models import get_intermediate_model
            model_id, _re, _provider = get_intermediate_model(
                "router",
                cls.get_available_providers(),
                preferred_provider=cls.get_selected_agent_provider(),
            )
            return model_id
        except Exception:
            return cls.ROUTER_MODEL

    @classmethod
    def _get_orchestrator_models_3level(cls) -> Tuple[Dict[str, str], Optional[str]]:
        """Dynamically compute simple/medium/complex orchestrator models.

        Returns:
            Tuple of ({"simple": ..., "medium": ..., "complex": ...}, provider_name).
            On any error, falls back to ({all three = cls.ORCHESTRATOR_SIMPLE_MODEL}, None).
        """
        try:
            from config.intermediate_agent_models import get_orchestrator_models_3level
            models, provider_name = get_orchestrator_models_3level(
                cls.get_available_providers(),
                preferred_provider=cls.get_selected_agent_provider(),
            )
            return models, provider_name
        except Exception:
            return ({"simple": cls.ORCHESTRATOR_SIMPLE_MODEL,
                     "medium": cls.ORCHESTRATOR_MEDIUM_MODEL,
                     "complex": cls.ORCHESTRATOR_COMPLEX_MODEL}, None)

    @classmethod
    def get_orchestrator_model_config(cls):
        """
        Возвращает конфигурацию выбора модели оркестратора
        согласно схеме из плана проекта (теперь с 3 уровнями, dynamic per provider)
        
        Returns:
            dict: {
                "mode": "router" | "fixed",
                "router_model": "model_name",  # для классификации (dynamic)
                "orchestrator_models": {"simple", "medium", "complex"},  # для выбора (dynamic)
                "orchestrator_provider": "provider_name" or None,  # provider for the 3 models
                "fixed_model": "model_name" or None
            }
        """
        if cls.ROUTER_ENABLED:
            models, provider_name = cls._get_orchestrator_models_3level()
            return {
                "mode": "router",
                "router_model": cls.get_router_model(),
                "orchestrator_models": models,
                "orchestrator_provider": provider_name,
                "fixed_model": None
            }
        else:
            # Если роутер отключен, используем фиксированную модель
            # Если фиксированная модель не указана, используем dynamic simple model
            if cls.ORCHESTRATOR_FIXED_MODEL:
                fixed_model = cls.ORCHESTRATOR_FIXED_MODEL
                # Provider unknown for user-specified fixed model; let call_llm resolve
                provider_name = None
            else:
                models, provider_name = cls._get_orchestrator_models_3level()
                fixed_model = models["simple"]
            return {
                "mode": "fixed",
                "router_model": None,
                "orchestrator_models": None,
                "orchestrator_provider": provider_name,
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
            cls.MODEL_OPUS_4_8,
            cls.MODEL_SONNET_4_5,  # NEW!
            cls.MODEL_SONNET_4_6,
            cls.MODEL_GPT_5_2_Codex,
            cls.MODEL_GEMINI_2_FLASH,
            cls.MODEL_GEMINI_3_PRO,
            cls.MODEL_DEEPSEEK_REASONER,
            cls.MODEL_QWEN_3_7_MAX,
            cls.MODEL_QWEN_3_5_Plus,
            cls.MODEL_QWEN_3_7_Plus,
            cls.MODEL_Xiaomi_MiMo_V2_5_PRO,
            cls.MODEL_Kimi_K_2_7_Code,
            cls.MODEL_GLM_5_2,
            cls.MODEL_MiniMax_M3,
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
    def get_model_connection_config(cls, model_name, preferred_provider=None):
        """
        Возвращает параметры подключения (api_key, base_url) для конкретной модели.

        Prioritizes PROVIDER_KEYS (live keys from JSON) over MODEL_CONFIGS.
        Injects 'stripped_model' key into returned dict for API-facing model name.

        Contract:
            - For user-facing roles (Orchestrator / Pre-filter / Tester / Code Generator),
              the caller MUST pass the per-role provider chosen together with the model
              (state.orchestrator_provider / state.prefilter_provider /
              state.generator_provider / tester_provider). Passing None here silently
              routes multi-provider models to cfg.get_selected_agent_provider(), which
              is the background-agents default and may be the WRONG provider for the
              user's selection.
            - For background AI agents (router, validator, compressor, ...), passing
              None is correct and falls back to cfg.get_selected_agent_provider().

        Args:
            model_name: Model identifier (e.g. "deepseek-v4-flash", "anthropic/claude-opus-4.5")
            preferred_provider: Optional preferred provider name. If None, uses
                cls.get_selected_agent_provider(). The preferred provider is tried
                first; remaining available providers (with API key, not disabled)
                are tried afterwards in JSON order.
        """
        # Resolve effective preferred provider
        if preferred_provider is None:
            preferred_provider = cls.get_selected_agent_provider()
            # Diagnostic warning: default fallback for a known multi-provider model
            # means a user-facing role forgot to pass its per-role provider. This
            # would route the call to the background-agents provider, which is
            # almost certainly not what the user picked in the UI.
            _MULTI_PROVIDER_MODEL_IDS = {
                "deepseek-v4-pro", "deepseek-v4-flash",
                "glm-5.2", "glm-5-turbo",
                "qwen3.7-max", "minimax-m3",
                "kimi-k2.7-code", "kimi-k2.6",
                "deepseek-chat", "deepseek-reasoner",
            }
            if cls.normalize_model_id(model_name) in _MULTI_PROVIDER_MODEL_IDS:
                logger.warning(
                    f"get_model_connection_config: preferred_provider=None for "
                    f"multi-provider model '{model_name}' — falling back to "
                    f"selected_agent_provider='{preferred_provider}'. User-facing "
                    f"roles must pass an explicit per-role provider."
                )

        # 1. First: Search PROVIDER_KEYS via PROVIDER_MODELS (ordered by preference)
        try:
            from config.provider_models import PROVIDER_MODELS

            meta_keys = {"reasoning_effort", "disabled_providers", "provider_entry_order", "selected_agent_provider"}
            disabled = PROVIDER_KEYS.get("disabled_providers", [])

            # Build ordered provider list: preferred first, then other available
            ordered_providers = []
            if preferred_provider and preferred_provider not in meta_keys and preferred_provider not in disabled:
                pk = PROVIDER_KEYS.get(preferred_provider)
                if isinstance(pk, dict) and pk.get("api_key"):
                    ordered_providers.append(preferred_provider)
            for provider, keys_config in PROVIDER_KEYS.items():
                if provider in meta_keys or provider in disabled:
                    continue
                if not isinstance(keys_config, dict) or not keys_config.get("api_key"):
                    continue
                if provider not in ordered_providers:
                    ordered_providers.append(provider)

            for provider in ordered_providers:
                keys_config = PROVIDER_KEYS[provider]
                provider_models = PROVIDER_MODELS.get(provider, [])
                stripped_model = cls._compute_stripped_model(model_name, provider)

                for model_id, _display_name, _description in provider_models:
                    if model_name == model_id or stripped_model == model_id:
                        # Found match — build config from live PROVIDER_KEYS
                        extra_params = {}
                        model_config = cls.MODEL_CONFIGS.get(model_name, {})
                        if "extra_params" in model_config:
                            extra_params = dict(model_config["extra_params"])
                        elif "reasoning" in model_config:
                            extra_params = {"reasoning_effort": model_config["reasoning"].get("effort")}

                        return {
                            "api_key": keys_config["api_key"],
                            "base_url": keys_config["base_url"],
                            "provider_name": provider,
                            "extra_params": extra_params,
                            "stripped_model": stripped_model,
                        }
        except ImportError:
            pass
        except Exception:
            pass

        # 2. Second: Fall back to MODEL_CONFIGS
        config = cls.MODEL_CONFIGS.get(model_name)
        if config:
            config = dict(config)  # shallow copy
            provider_name = config.get("provider_name", "OpenRouter")

            # Inject live key from PROVIDER_KEYS by matching provider_name
            pn_lower = provider_name.lower()
            provider_key_map = {
                "openrouter": "openrouter",
                "routerai": "routerai",
                "deepseek": "deepseek",
                "glm": "glm",
                "opencode_go": "opencode_go",
                "qwencloud": "qwencloud",
            }
            for key_substr, provider_key in provider_key_map.items():
                if key_substr in pn_lower:
                    live_config = PROVIDER_KEYS.get(provider_key, {})
                    if isinstance(live_config, dict) and live_config.get("api_key"):
                        config["api_key"] = live_config["api_key"]
                        config["base_url"] = live_config["base_url"]
                    break

            # Convert reasoning to extra_params if extra_params not present
            if "extra_params" not in config and "reasoning" in config:
                config["extra_params"] = {"reasoning_effort": config["reasoning"].get("effort")}

            config["stripped_model"] = cls._compute_stripped_model(model_name, provider_name)
            return config

        # 3. Third: Fallback to default
        fallback = dict(cls.MODEL_CONFIGS["default"])
        # Inject live OpenRouter key if available
        live_or = PROVIDER_KEYS.get("openrouter", {})
        if isinstance(live_or, dict) and live_or.get("api_key"):
            fallback["api_key"] = live_or["api_key"]
            fallback["base_url"] = live_or["base_url"]
        fallback["stripped_model"] = cls._compute_stripped_model(model_name, fallback.get("provider_name", "OpenRouter"))
        return fallback

    @classmethod
    def _compute_stripped_model(cls, model_name: str, provider_name: str) -> str:
        """Compute the API-facing model name by stripping vendor prefix for direct providers.

        OpenRouter and RouterAI are intentionally exempt from prefix stripping
        because they require the full 'vendor/model' format per their API rules.
        """
        pn_lower = provider_name.lower()
        # OpenRouter/RouterAI require prefixed model ids — keep them unchanged
        if "openrouter" in pn_lower or "routerai" in pn_lower:
            return model_name
        # Direct providers: strip leading "vendor/" prefix
        if "/" in model_name:
            return model_name.split("/", 1)[1]
        return model_name

    @classmethod
    def get_available_providers(cls) -> list:
        """Return list of provider names that have an API key set and are not disabled."""
        disabled = PROVIDER_KEYS.get("disabled_providers", [])
        meta_keys = {"reasoning_effort", "disabled_providers", "provider_entry_order", "selected_agent_provider"}
        providers = []
        for key, config in PROVIDER_KEYS.items():
            if key in meta_keys:
                continue
            if key in disabled:
                continue
            if isinstance(config, dict) and config.get("api_key"):
                providers.append(key)
        return providers

    @classmethod
    def get_user_reasoning_effort(cls) -> str:
        """Return the user's global reasoning_effort setting."""
        return PROVIDER_KEYS.get("reasoning_effort", "high")

    @classmethod
    def save_provider_keys(cls, keys: dict):
        """Save provider keys to file and update module-level variable."""
        global PROVIDER_KEYS
        with open(PROVIDER_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, indent=2, ensure_ascii=False)
        PROVIDER_KEYS = keys

    @classmethod
    def get_provider_keys(cls) -> dict:
        """Return the module-level PROVIDER_KEYS dict."""
        return PROVIDER_KEYS

    @classmethod
    def disable_provider(cls, provider: str):
        """Disable a provider so it won't be used."""
        keys = cls.get_provider_keys()
        disabled = keys.get("disabled_providers", [])
        if provider not in disabled:
            disabled.append(provider)
            keys["disabled_providers"] = disabled
            cls.save_provider_keys(keys)

    @classmethod
    def enable_provider(cls, provider: str):
        """Enable a previously disabled provider."""
        keys = cls.get_provider_keys()
        disabled = keys.get("disabled_providers", [])
        if provider in disabled:
            disabled.remove(provider)
            keys["disabled_providers"] = disabled
            cls.save_provider_keys(keys)

    @classmethod
    def is_provider_disabled(cls, provider: str) -> bool:
        """Check if a provider is disabled by the user."""
        keys = cls.get_provider_keys()
        disabled = keys.get("disabled_providers", [])
        return provider in disabled

    @classmethod
    def get_all_configured_providers(cls) -> list:
        """Return ALL providers with configured API keys, including disabled ones."""
        providers = []
        for key, config in PROVIDER_KEYS.items():
            if key in ("reasoning_effort", "disabled_providers", "provider_entry_order"):
                continue
            if isinstance(config, dict) and config.get("api_key"):
                providers.append(key)
        return providers

    @classmethod
    def record_provider_entry(cls, provider: str):
        """Record that a provider had its key manually entered."""
        keys = cls.get_provider_keys()
        order = keys.get("provider_entry_order", [])
        if provider not in order:
            order.append(provider)
            keys["provider_entry_order"] = order
            cls.save_provider_keys(keys)

    @classmethod
    def get_manually_entered_providers(cls) -> list:
        """Return the list of providers in manual entry order."""
        keys = cls.get_provider_keys()
        return keys.get("provider_entry_order", [])

    @staticmethod
    def normalize_model_id(model_id: str) -> str:
        """Strip vendor prefix from a model id.

        Examples:
            'z-ai/glm-5.2'     -> 'glm-5.2'
            'anthropic/claude-opus-4.5' -> 'claude-opus-4.5'
            'deepseek-v4-pro'  -> 'deepseek-v4-pro'  (no prefix, unchanged)
            ''                 -> ''

        Direct providers (deepseek, glm, opencode_go, qwencloud) use bare model
        names while OpenRouter/RouterAI use 'vendor/model' prefixes. This helper
        normalizes both forms so model comparisons are prefix-agnostic.
        """
        if not model_id:
            return ""
        if "/" in model_id:
            return model_id.split("/", 1)[1]
        return model_id

    @classmethod
    def is_model(cls, model_id: str, *constants: str) -> bool:
        """Check if model_id matches any of the given Config.MODEL_* constants,
        ignoring vendor prefix differences.

        Example:
            cfg.is_model('glm-5.2', cfg.MODEL_GLM_5_2)  # True even though
            cfg.MODEL_GLM_5_2 == 'z-ai/glm-5.2'
        """
        if not model_id:
            return False
        norm = cls.normalize_model_id(model_id)
        return any(cls.normalize_model_id(c) == norm for c in constants if c)

    @classmethod
    def get_selected_agent_provider(cls) -> Optional[str]:
        """Return the explicitly user-selected provider for background AI agents, or None."""
        return PROVIDER_KEYS.get("selected_agent_provider")

    @classmethod
    def set_selected_agent_provider(cls, provider: Optional[str]):
        """Set or clear the selected agent provider in provider_keys.json."""
        keys = cls.get_provider_keys()
        if provider is None:
            keys.pop("selected_agent_provider", None)
        else:
            keys["selected_agent_provider"] = provider
        cls.save_provider_keys(keys)

    @classmethod
    def resolve_provider_for_model(cls, model_id: str, preferred: Optional[str] = None) -> Optional[str]:
        """Resolve which provider owns a model_id, for migration of legacy settings.

        Tries to find the provider whose PROVIDER_MODELS list contains model_id.
        Used by apply_user_settings to recover a missing per-role provider
        (old settings saved before per-role providers were introduced).

        Priority:
            1. ``preferred`` — if it has the model and is available (not disabled,
               has API key).
            2. ``cfg.get_selected_agent_provider()`` — the background-agents
               provider, if it has the model.
            3. The first *available* provider that has the model.
            4. The first *configured* (possibly disabled) provider that has the
               model — last resort so the user's disabled providers can still
               be resolved for display.
            5. ``None`` — model not found in any provider.

        Comparison is prefix-agnostic: ``"z-ai/glm-5.2"`` matches a provider
        listing ``"glm-5.2"`` (and vice versa) via normalize_model_id.

        Never raises. Returns None if model_id is empty or not found.
        """
        if not model_id:
            return None
        try:
            from config.provider_models import PROVIDER_MODELS
        except ImportError:
            return None

        meta_keys = {"reasoning_effort", "disabled_providers",
                     "provider_entry_order", "selected_agent_provider"}
        disabled = PROVIDER_KEYS.get("disabled_providers", [])

        def _provider_has_model(provider: str) -> bool:
            for m_id, _n, _d in PROVIDER_MODELS.get(provider, []):
                if cls.normalize_model_id(m_id) == cls.normalize_model_id(model_id):
                    return True
            return False

        def _is_available(provider: str) -> bool:
            if provider in meta_keys or provider in disabled:
                return False
            pk = PROVIDER_KEYS.get(provider)
            return bool(isinstance(pk, dict) and pk.get("api_key"))

        # 1. Explicitly preferred
        if preferred and _is_available(preferred) and _provider_has_model(preferred):
            return preferred
        # 2. Background-agents provider
        selected = cls.get_selected_agent_provider()
        if selected and _is_available(selected) and _provider_has_model(selected):
            return selected
        # 3. Any available provider that has the model
        for provider, pk in PROVIDER_KEYS.items():
            if provider in meta_keys or provider in disabled:
                continue
            if not isinstance(pk, dict) or not pk.get("api_key"):
                continue
            if _provider_has_model(provider):
                return provider
        # 4. Any configured provider (including disabled) that has the model
        for provider, pk in PROVIDER_KEYS.items():
            if provider in meta_keys:
                continue
            if not isinstance(pk, dict) or not pk.get("api_key"):
                continue
            if _provider_has_model(provider):
                return provider
        # 5. Not found
        return None

    @classmethod
    def get_model_provider_display(cls, model_id: str, provider_hint: Optional[str] = None) -> str:
        """Return the provider name (uppercased) for a given model_id, for UI display.

        Args:
            model_id: Model identifier.
            provider_hint: Optional preferred provider — for multi-provider models
                (same id in several providers, e.g. "deepseek-v4-pro" in both
                opencode_go and deepseek), displays this provider instead of the
                default-resolved one. None falls back to selected_agent_provider.
                Pass the per-role provider (state.orchestrator_provider, etc.) for
                accurate display next to a user-selected model.
        """
        try:
            config = cls.get_model_connection_config(model_id, preferred_provider=provider_hint)
            return config.get("provider_name", "").upper() or "?"
        except Exception:
            return "?"


    @classmethod
    def get_default_provider_for_agent(cls) -> str:
        """Get the default provider for AI agents based on deterministic priority."""
        available = cls.get_available_providers()
        if not available:
            raise ValueError("Нет доступных провайдеров")
        # First priority: explicitly selected agent provider
        selected = cls.get_selected_agent_provider()
        if selected and selected in available:
            return selected
        # Second priority: manually entered providers in entry order
        manual = cls.get_manually_entered_providers()
        for provider in manual:
            if provider in available:
                return provider
        # Fallback: first available (deterministic, NOT random)
        return available[0]

    @classmethod
    def get_test_model_for_provider(cls, provider: str) -> str:
        """Return a cheap/fast model ID for the given provider for key validation."""
        CHEAP_MODELS = {
            "openrouter": "google/gemini-2.0-flash-001",
            "routerai": "google/gemini-2.0-flash-001",
            "deepseek": "deepseek-v4-flash",
            "glm": "glm-5-turbo",
            "opencode_go": "deepseek-v4-flash",
            "qwencloud": "qwen3-coder-next",
        }
        if provider in CHEAP_MODELS:
            return CHEAP_MODELS[provider]
        from config.provider_models import PROVIDER_MODELS
        models = PROVIDER_MODELS.get(provider, [])
        if models:
            return models[0][0]
        return "deepseek-chat"



    @classmethod
    def get_model_display_name(cls, model_id, provider_hint: Optional[str] = None):
        # Словарь красивых имен
        model_names = {
            cls.MODEL_OPUS_4_5: "Claude Opus 4.5",
            cls.MODEL_OPUS_4_8: "Claude Opus 4.8",
            cls.MODEL_SONNET_4_5: "Claude Sonnet 4.5",  # NEW!
            cls.MODEL_SONNET_4_6: "Claude Sonnet 5",
            cls.MODEL_DEEPSEEK_REASONER: "Deepseek V4 Pro",
            cls.MODEL_GPT_5_2_Codex: "GPT-5.2 Codex",
            cls.MODEL_GEMINI_3_PRO: "✨ Gemini 3.1 Pro (Thinking)",
            cls.MODEL_QWEN_3_7_MAX: "🚀 Qwen3 Max Thinking (Deep Reasoning)",
            cls.MODEL_QWEN_3_5_Plus: "🌟 Qwen3.5 Plus",
            cls.MODEL_GEMINI_2_FLASH: "Gemini 2.0 Flash",
            cls.MODEL_NORMAL: "DeepSeek Chat (прямой API)",
            cls.MODEL_Kimi_K_2_7_Code: "Kimi K2.7 Code",
            cls.MODEL_QWEN_3_7_Plus: "Qwen3.7 Plus",
            cls.MODEL_Xiaomi_MiMo_V2_5_PRO: "Xiaomi: MiMo-V2-Pro",
            cls. MODEL_GLM_5_2: "GLM 5.1",
            cls.MODEL_MiniMax_M3: "MiniMAX M2.3",
            # Модели генератора
            cls.MODEL_GLM_5_Turbo: "GLM 5 Turbo (OpenRouter)",
            cls.MODEL_HAIKU_4_5: "Claude Haiku 4.5 (OpenRouter)",
            cls.MODEL_GEMINI_3_FLASH : "Gemini 3.5 flash",
            cls.MODEL_GPT_5_1_Codex_MINI : "GPT-5.1-Codex-Mini"
        }
        
        # Если модель есть в словаре - возвращаем красивое имя
        if model_id in model_names:
            return model_names[model_id]
            
        # Универсальный фолбек: если модели нет в списке, 
        # пробуем определить провайдера и вывести хоть что-то
        if "/" in model_id:
            try:
                conf = cls.get_model_connection_config(model_id, preferred_provider=provider_hint)
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
# Формат: (key, model_id, short_name, description), а вообще это мусор, все так как список генераторов есть в точке входа
# [REMOVED] Static AVAILABLE_GENERATOR_MODELS list — caused NameError (cfg not yet defined at module level).
# Now built dynamically in main.py via _build_available_models("generator").
# AVAILABLE_GENERATOR_MODELS is now built dynamically in main.py via _build_available_models("generator")

# AVAILABLE_PREFILTER_MODELS removed — pre-filter uses AVAILABLE_ORCHESTRATOR_MODELS from main.py.
# The pre-filter model is user-selected from the orchestrator model list (ORCHESTRATOR_MODEL_IDS).
    

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
        print(f"   • Модель роутера: {cfg.get_model_display_name(cfg.get_router_model())}")
        orch_models = router_config.get("orchestrator_models", {})
        print(f"   • 🟢 Простые задачи: {cfg.get_model_display_name(orch_models.get('simple', cfg.ORCHESTRATOR_SIMPLE_MODEL))}")
        print(f"   • 🟡 Средние задачи: {cfg.get_model_display_name(orch_models.get('medium', cfg.ORCHESTRATOR_MEDIUM_MODEL))}")
        print(f"   • 🔴 Сложные задачи: {cfg.get_model_display_name(orch_models.get('complex', cfg.ORCHESTRATOR_COMPLEX_MODEL))}")
    else:
        print(f"   • Автоматический роутер: ❌ ВЫКЛЮЧЕН")
        if cfg.ORCHESTRATOR_FIXED_MODEL:
            print(f"   • Фиксированная модель: {cfg.get_model_display_name(cfg.ORCHESTRATOR_FIXED_MODEL)}")
        else:
            fixed = router_config.get("fixed_model", cfg.ORCHESTRATOR_SIMPLE_MODEL)
            print(f"   • Фиксированная модель: {cfg.get_model_display_name(fixed)} (dynamic default)")
    
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
    
