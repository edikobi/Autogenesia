"""
Centralized registry of models per provider.
Easy to add/remove models by editing a single list.
"""


OPENROUTER_MODELS = [
    # Orchestrator models
    ("openai/gpt-5.2-codex", "GPT-5.2 Codex", "Новая модель от OpenAI, отзывы очень хорошие."),
    ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5", "Рабочая лошадка. Хорошо работает с инструментами, неплохо анализирует."),
    ("anthropic/claude-sonnet-5", "Claude Sonnet 5", "Новая рабочая лошадка, говорят, лучше прошлой."),
    ("anthropic/claude-opus-4.5", "Claude Opus 4.5", "Гигант мысли! Только для ОЧЕНЬ серьёзных задач. Очень дорогой! Контекстное окно 200к токенов."),
    ("anthropic/claude-opus-4.8", "Claude Opus 4.8", "Гигант мысли! Контекстное окно 1 млн. токенов. Очень дорогой!"),
    ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Огромное окно 1 млн токенов. Относительно дешёвая."),
    ("deepseek-v4-pro", "DeepSeek V4 PRO", "Неплохо думает, ОЧЕНЬ дешёвый! Маленькое контекстное окно."),
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "Так себе думает, ОЧЕНЬ дешёвый! Маленькое контекстное окно."),
    ("moonshotai/kimi-k2.7-code", "Kimi K2.7 Code", "Очень неплохой китайский ИИ, но маленький контекст (200к)."),
    ("qwen/qwen3.7-plus", "Qwen3.7 Plus", "ИИ от китайцев, дешевый с неплохим знанием кода."),
    ("xiaomi/mimo-v2.5-pro", "Xiaomi MiMo-V2.5-Pro", "Нахваливают эту модель, должна быть топ за свои деньги."),
    ("z-ai/glm-5.2", "GLM 5.2", "Говорят, что передовая модель."),
    ("qwen/qwen3.7-max", "Qwen 3.7 Max", "Флагманская модель в серии Qwen3.7 от Alibaba."),
    ("minimax/minimax-m3", "MiniMAX M3", "Бьют рейтинги в тестах, как все китайцы."),
    ("x-ai/grok-4.3", "Grok 4.3", "Контекстное окно 1 млн., вроде особо не пиздит."),
    # Generator models
    ("z-ai/glm-5-turbo", "GLM 5 Turbo", "Китайская модель от Zhipu AI. Поддерживает thinking mode."),
    ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5", "Лёгкая модель от Anthropic. Самая лучшая и исполнительная."),
    ("google/gemini-3-flash-preview", "Gemini 3 Flash", "Быстрая модель Google через OpenRouter. Хорошо подходит для генерации кода."),
    ("openai/gpt-5.1-codex-mini", "GPT-5.1-Codex-Mini", "Младшая модель CODEX от OpenAI, минимально думает."),
    ("qwen/qwen3.5-397b-a17b", "Qwen3.5 397B A17B", "Китайская ИИ с большим контекстным окном."),
    ("nvidia/nemotron-3-ultra-550b-a55b", "Nemotron 3 ULTRA", "Одна из лучших открытых моделей от NVIDIA, 262К контекста."),
    # Intermediate agent models (via OpenRouter)
    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash", "Быстрая модель для роутера и сжатия истории."),
    ("google/gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", "Лёгкая модель для валидации и перевода."),
    ("deepseek-chat", "DeepSeek Chat", "Базовая модель DeepSeek через OpenRouter."),
    ("qwen/qwen3-coder-next", "Qwen3 Coder Next", "Модель для исправления синтаксиса и индексации."),
    ("qwen/qwen3-30b-a3b-instruct-2507", "Qwen3 30B Instruct", "Модель для построения индекса."),
    # Additional models from MODEL_CONFIGS
    ("google/gemini-3.5-flash", "Gemini 3.5 Flash", "Модель Google с reasoning."),
    ("qwen/qwen3.5-plus-02-15", "Qwen3.5 Plus", "Модель Qwen с reasoning."),
    ("google/gemini-2.5-flash-lite-preview-09-2025", "Gemini 2.5 Flash Lite", "Предварительная версия Gemini 2.5 Flash Lite."),
]

ROUTERAI_MODELS = [
    ("openai/gpt-5.2-codex", "GPT-5.2 Codex", "GPT model via RouterAI"),
]

DEEPSEEK_MODELS = [
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "Быстрая и дешёвая. Заменяет deepseek-chat."),
    ("deepseek-v4-pro", "DeepSeek V4 PRO", "Топовая с reasoning. Заменяет deepseek-reasoner."),
    ("deepseek-chat", "DeepSeek Chat (legacy)", "Устаревший алиас → deepseek-v4-flash (до 2026-07-24)."),
    ("deepseek-reasoner", "DeepSeek Reasoner (legacy)", "Устаревший алиас → deepseek-v4-pro (до 2026-07-24)."),
]

GLM_MODELS = [
    ("glm-5.2", "GLM 5.2", "Флагманская модель GLM от Zhipu AI. Передовая модель."),
    ("glm-5-turbo", "GLM 5 Turbo", "Быстрая модель GLM. Хороша для структурированного кода."),
]

OPENCODE_GO_MODELS = [
    ("deepseek-v4-pro", "DeepSeek V4 PRO", "Неплохо думает, ОЧЕНЬ дешёвый! Маленькое контекстное окно."),
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "Так себе думает, ОЧЕНЬ дешёвый! Маленькое контекстное окно."),
    ("kimi-k2.7-code", "Kimi K2.7 Code", "Очень неплохой китайский ИИ, но маленький контекст (200к)."),
    ("kimi-k2.6", "Kimi K2.6", "Предыдущая версия Kimi, стабильная для генерации кода."),
    ("qwen3.7-plus", "Qwen3.7 Plus", "ИИ от китайцев, дешевый с неплохим знанием кода."),
    ("qwen3.7-max", "Qwen3.7 Max", "Флагманская модель в серии Qwen3.7 от Alibaba."),
    ("qwen3.6-plus", "Qwen3.6 Plus", "Предыдущая версия Qwen3.6 Plus, дешевле."),
    ("mimo-v2.5", "Xiaomi MiMo-V2.5", "Базовая модель Xiaomi MiMo."),
    ("mimo-v2.5-pro", "Xiaomi MiMo-V2.5-Pro", "Нахваливают эту модель, должна быть топ за свои деньги."),
    ("glm-5.2", "GLM 5.2", "Говорят, что передовая модель."),
    ("glm-5-turbo", "GLM 5 Turbo", "Быстрая модель GLM. Хороша для структурированного кода."),
    ("minimax-m3", "MiniMAX M3", "Бьют рейтинги в тестах, как все китайцы."),
]

QWENCLOUD_MODELS = [
    ("qwen3.7-max", "Qwen3.7 Max", "Флагманская модель Qwen3.7 от Alibaba через QwenCloud."),
    ("qwen3.5-plus", "Qwen3.5 Plus", "Быстрая и дешёвая модель Qwen3.5 Plus."),
    ("qwen3-coder-next", "Qwen3 Coder Next", "Специализированная модель для кода от Qwen."),
    ("qwen/qwen3.7-plus", "Qwen3.7 Plus", "ИИ от китайцев, дешевый с неплохим знанием кода."),
    ("deepseek-v4-pro", "DeepSeek V4 PRO", "Неплохо думает, ОЧЕНЬ дешёвый! Маленькое контекстное окно."),
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "Так себе думает, ОЧЕНЬ дешёвый! Маленькое контекстное окно."),
    ("moonshotai/kimi-k2.7-code", "Kimi K2.7 Code", "Очень неплохой китайский ИИ, но маленький контекст (200к)."),
]

PROVIDER_MODELS = {
    "openrouter": OPENROUTER_MODELS,
    "routerai": ROUTERAI_MODELS,
    "deepseek": DEEPSEEK_MODELS,
    "glm": GLM_MODELS,
    "opencode_go": OPENCODE_GO_MODELS,
    "qwencloud": QWENCLOUD_MODELS,
}

# ============================================================================
# ROLE CLASSIFICATION SETS
# ============================================================================

ORCHESTRATOR_MODEL_IDS = {
    "openai/gpt-5.2-codex",
    "anthropic/claude-sonnet-4.5", "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.5", "anthropic/claude-opus-4.8",
    "google/gemini-3.1-pro-preview",
    "deepseek-v4-pro", "deepseek-reasoner",
    "moonshotai/kimi-k2.7-code", "kimi-k2.7-code",
    "moonshotai/kimi-k2.6", "kimi-k2.6",
    "qwen/qwen3.7-plus", "qwen3.7-plus", "qwen/qwen3.7-max", "qwen3.7-max", "qwen3.6-plus",
    "xiaomi/mimo-v2.5-pro", "mimo-v2.5-pro", "mimo-v2.5",
    "z-ai/glm-5.2", "glm-5.2",
    "minimax/minimax-m3", "minimax-m3",
    "x-ai/grok-4.3",
    "google/gemini-2.0-flash-001",
    "qwen/qwen3.5-plus-02-15",
    "google/gemini-3.5-flash",
    "google/gemini-2.5-flash-lite-preview-09-2025",
    "google/gemini-3.1-flash-lite",
    "deepseek-chat",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "deepseek/deepseek-v4-flash", "deepseek-v4-flash",
}

GENERATOR_MODEL_IDS = {
    # --- OpenRouter / RouterAI variants (full vendor/model prefix) ---
    "z-ai/glm-5-turbo",
    "anthropic/claude-haiku-4.5",
    "google/gemini-3-flash-preview", "google/gemini-3.5-flash",
    "openai/gpt-5.1-codex-mini",
    "qwen/qwen3.5-397b-a17b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "qwen/qwen3-coder-next",
    "qwen/qwen3-30b-a3b-instruct-2507",
    "x-ai/grok-4.3",
    "qwen/qwen3.7-max",
    "qwen/qwen3.7-plus",
    "minimax/minimax-m3",
    "z-ai/glm-5.2",
    "moonshotai/kimi-k2.7-code",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "xiaomi/mimo-v2.5-pro",
    # --- Direct-provider variants (no prefix, for deepseek / glm / opencode_go / qwencloud) ---
    "glm-5-turbo",
    "qwen3-coder-next",
    "qwen3.7-max",
    "qwen3.5-plus",
    "qwen3.6-plus",
    "minimax-m3",
    "glm-5.2",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "deepseek-chat",
    "deepseek-reasoner",
}


def get_role_models(provider: str, role: str) -> list:
    """Return models for a given provider filtered by role classification.

    Args:
        provider: Provider name (e.g., 'openrouter', 'deepseek')
        role: Either 'orchestrator' or 'generator'

    Returns:
        List of (model_id, display_name, description) tuples matching the role.
        Unclassified models default to the orchestrator role as a safety net.
    """
    ids = ORCHESTRATOR_MODEL_IDS if role == "orchestrator" else GENERATOR_MODEL_IDS
    provider_models = PROVIDER_MODELS.get(provider, [])
    result = [(m, n, d) for (m, n, d) in provider_models if m in ids]

    # Safety net for orchestrator role: unclassified ids default to orchestrator
    if role == "orchestrator":
        classified = GENERATOR_MODEL_IDS | ORCHESTRATOR_MODEL_IDS
        for (m, n, d) in provider_models:
            if m not in classified and (m, n, d) not in result:
                result.append((m, n, d))

    return result