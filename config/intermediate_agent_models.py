"""
Constants for intermediate agent models per provider, plus a function to select
the appropriate model based on available providers.

These models are protected from the user's global reasoning_effort — each agent
has its own *_REASONING_EFFORT constant (default None).
"""

from typing import List, Tuple, Optional, Dict


# Pre-filter model is chosen by the user from ORCHESTRATOR_MODEL_IDS (same list).
# No separate PREFILTER_MODELS constant — it does not exist by design.

# 2. Router
ROUTER_MODELS = {
    "openrouter": "google/gemini-2.0-flash-001",
    "routerai": "google/gemini-2.0-flash-001",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "qwen3.5-plus",
}
ROUTER_REASONING_EFFORT = None

# 3. Validator (small and large)
VALIDATOR_SMALL_MODELS = {
    "openrouter": "google/gemini-3.1-flash-lite",
    "routerai": "google/gemini-3.1-flash-lite",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "qwen3-coder-next",
}
VALIDATOR_LARGE_MODELS = {
    "openrouter": "deepseek/deepseek-v4-flash",
    "routerai": "deepseek/deepseek-v4-flash",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5.2",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "qwen3.5-plus",
}
VALIDATOR_REASONING_EFFORT = None

# 4. Syntax Fixer (A and B)
SYNTAX_FIXER_MODEL_A = {
    "openrouter": "qwen/qwen3-coder-next",
    "routerai": "qwen/qwen3-coder-next",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "qwen3-coder-next",
}
SYNTAX_FIXER_MODEL_B = {
    "openrouter": "google/gemini-3.1-flash-lite",
    "routerai": "google/gemini-3.1-flash-lite",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "qwen3-coder-next",
}
SYNTAX_FIXER_REASONING_EFFORT = None

# 5. Index Builder (Qwen and DeepSeek)
INDEX_BUILDER_QWEN_MODELS = {
    "openrouter": "qwen/qwen3-30b-a3b-instruct-2507",
    "routerai": "qwen/qwen3-30b-a3b-instruct-2507",
    "qwencloud": "qwen3-coder-next",
    "opencode_go": "deepseek-v4-flash",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
}
INDEX_BUILDER_DEEPSEEK_MODELS = {
    "openrouter": "deepseek/deepseek-v4-flash",
    "routerai": "deepseek/deepseek-v4-flash",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "qwencloud": "deepseek-v4-flash",
    "opencode_go": "deepseek-v4-flash",
}
INDEX_BUILDER_REASONING_EFFORT = None

# 6. Compressor
COMPRESSOR_MODELS = {
    "openrouter": "google/gemini-2.0-flash-001",
    "routerai": "openai/gpt-5.2-codex",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "qwencloud": "qwen3.5-plus",
    "opencode_go": "deepseek-v4-flash",
}
COMPRESSOR_REASONING_EFFORT = None

# 7. Context Manager
CONTEXT_MANAGER_MODELS = {
    "openrouter": "google/gemini-2.0-flash-001",
    "routerai": "openai/gpt-5.2-codex",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "qwencloud": "deepseek-v4-flash",
    "opencode_go": "deepseek-v4-flash",
}
CONTEXT_MANAGER_REASONING_EFFORT = None

# 8. Tester Translator
TESTER_TRANSLATOR_MODELS = {
    "openrouter": "google/gemini-3.1-flash-lite",
    "routerai": "google/gemini-3.1-flash-lite",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "deepseek-v4-flash",
}
TESTER_TRANSLATOR_REASONING_EFFORT = None

# 9. Translator
TRANSLATOR_MODELS = {
    "openrouter": "google/gemini-3.1-flash-lite",
    "routerai": "google/gemini-3.1-flash-lite",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "opencode_go": "deepseek-v4-flash",
    "qwencloud": "deepseek-v4-flash",
}
TRANSLATOR_REASONING_EFFORT = None

# 10. Project Map
PROJECT_MAP_MODELS = {
    "openrouter": "deepseek-v4-flash",
    "routerai": "deepseek-v4-flash",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "qwencloud": "deepseek-v4-flash",
    "opencode_go": "deepseek-v4-flash",
}
PROJECT_MAP_REASONING_EFFORT = None

# 11. Index Updater
INDEX_UPDATER_MODELS = {
    "openrouter": "deepseek-v4-flash",
    "routerai": "deepseek-v4-flash",
    "deepseek": "deepseek-v4-flash",
    "glm": "glm-5-turbo",
    "qwencloud": "deepseek-v4-flash",
    "opencode_go": "deepseek-v4-flash",
}
INDEX_UPDATER_REASONING_EFFORT = None

# Models for which thinking MUST be disabled by default in intermediate agent roles.
# According to DeepSeek API docs: thinking is ON by default for V4 models.
# Disabling is done via extra_body={"thinking": {"type": "disabled"}} (handled by api_client).
# The sentinel value "disabled" in reasoning_effort signals this to api_client._make_request().
THINKING_DISABLED_INTERMEDIATE_MODELS = {
    # DeepSeek V4 Flash — fast/cheap, thinking overhead is counterproductive for agent tasks
    "deepseek-v4-flash",
    "deepseek/deepseek-v4-flash",
    # GLM 5 Turbo — fast model, no native reasoning benefit for agent tasks
    "glm-5-turbo",
    "z-ai/glm-5-turbo",
}

# 12. Master registry
# "pre_filter" removed — pre-filter uses ORCHESTRATOR_MODEL_IDS (same list).
INTERMEDIATE_AGENT_REGISTRY: Dict[str, Tuple[dict, Optional[str]]] = {
    "router":             (ROUTER_MODELS,             ROUTER_REASONING_EFFORT),
    "validator_small":    (VALIDATOR_SMALL_MODELS,    VALIDATOR_REASONING_EFFORT),
    "validator_large":    (VALIDATOR_LARGE_MODELS,    VALIDATOR_REASONING_EFFORT),
    "syntax_fixer_a":     (SYNTAX_FIXER_MODEL_A,      SYNTAX_FIXER_REASONING_EFFORT),
    "syntax_fixer_b":     (SYNTAX_FIXER_MODEL_B,      SYNTAX_FIXER_REASONING_EFFORT),
    "index_builder_qwen": (INDEX_BUILDER_QWEN_MODELS, INDEX_BUILDER_REASONING_EFFORT),
    "index_builder_deepseek": (INDEX_BUILDER_DEEPSEEK_MODELS, INDEX_BUILDER_REASONING_EFFORT),
    "compressor":         (COMPRESSOR_MODELS,         COMPRESSOR_REASONING_EFFORT),
    "context_manager":    (CONTEXT_MANAGER_MODELS,    CONTEXT_MANAGER_REASONING_EFFORT),
    "tester_translator":  (TESTER_TRANSLATOR_MODELS,  TESTER_TRANSLATOR_REASONING_EFFORT),
    "translator":         (TRANSLATOR_MODELS,         TRANSLATOR_REASONING_EFFORT),
    "project_map":        (PROJECT_MAP_MODELS,        PROJECT_MAP_REASONING_EFFORT),
    "index_updater":      (INDEX_UPDATER_MODELS,      INDEX_UPDATER_REASONING_EFFORT),
}


def get_intermediate_model(agent_name: str, available_providers: list, preferred_provider: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """Select the appropriate model for an intermediate agent based on available providers.

    Args:
        agent_name: Name of the intermediate agent (must be in INTERMEDIATE_AGENT_REGISTRY).
        available_providers: List of provider names that have API keys configured.
        preferred_provider: Optional explicitly preferred provider for agent models.

    Returns:
        Tuple of (model_id, reasoning_effort, provider_name) where:
        - reasoning_effort is the agent-specific one (typically None). May return the
          sentinel string ``"disabled"`` when the resolved model_id is in
          THINKING_DISABLED_INTERMEDIATE_MODELS and the registry entry has no
          explicit reasoning_effort configured. This signals
          api_client._make_request() to inject extra_body={"thinking": {"type": "disabled"}}.
        - provider_name is the name of the provider from which the model was selected
          (used to route the API call through the correct provider).

    Raises:
        ValueError: If agent_name is not in the registry.
    """
    if agent_name not in INTERMEDIATE_AGENT_REGISTRY:
        raise ValueError(f"Unknown intermediate agent: {agent_name}. "
                         f"Valid agents: {list(INTERMEDIATE_AGENT_REGISTRY.keys())}")

    models_dict, reasoning_effort = INTERMEDIATE_AGENT_REGISTRY[agent_name]

    model_id = None
    resolved_provider: Optional[str] = None

    # Check preferred provider first
    if preferred_provider is not None and preferred_provider in available_providers and preferred_provider in models_dict:
        model_id = models_dict[preferred_provider]
        resolved_provider = preferred_provider
    else:
        # Provider priority order: DeepSeek first (cheapest), OpenRouter as fallback
        priority = ["deepseek", "glm", "qwencloud", "opencode_go", "openrouter", "routerai"]

        # Iterate through available providers in priority order
        for provider in priority:
            if provider in available_providers and provider in models_dict:
                model_id = models_dict[provider]
                resolved_provider = provider
                break

    # Fallback to openrouter
    if model_id is None and "openrouter" in models_dict:
        model_id = models_dict["openrouter"]
        resolved_provider = "openrouter"

    # Fallback to first value in dict
    if model_id is None and models_dict:
        first_provider = next(iter(models_dict))
        model_id = models_dict[first_provider]
        resolved_provider = first_provider

    if model_id is None:
        raise ValueError(f"No model available for agent: {agent_name}")

    # Check if thinking should be disabled for this model in intermediate roles.
    # Only apply "disabled" sentinel if the registry entry has no explicit reasoning_effort
    # (i.e., reasoning_effort is None). If a non-None value is configured, preserve it.
    if model_id in THINKING_DISABLED_INTERMEDIATE_MODELS and reasoning_effort is None:
        return model_id, "disabled", resolved_provider

    return model_id, reasoning_effort, resolved_provider



def get_orchestrator_model_for_agent(available_providers: list, preferred_provider: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """Select an orchestrator-tier model for agents that need orchestrator-level intelligence.

    Uses get_role_models(provider, "orchestrator") from config.provider_models to select
    from orchestrator model lists. Iterates through providers in priority order, with
    preferred_provider tried first.

    Args:
        available_providers: List of provider names that have API keys configured.
        preferred_provider: Optional explicitly preferred provider. Tried first.

    Returns:
        Tuple of (model_id, None, provider_name) — reasoning_effort is None because
        global user reasoning_effort is applied in api_client._make_request (these
        models are NOT in INTERMEDIATE_AGENT_REGISTRY). provider_name is the provider
        from which the model was selected.

    Raises:
        ValueError: If no orchestrator model available from any configured provider.
    """
    from config.provider_models import get_role_models

    # Build ordered provider list: preferred first, then priority, then remaining
    priority = ["deepseek", "glm", "qwencloud", "opencode_go", "openrouter", "routerai"]
    ordered = []
    if preferred_provider and preferred_provider in available_providers:
        ordered.append(preferred_provider)
    for provider in priority:
        if provider in available_providers and provider not in ordered:
            ordered.append(provider)
    for provider in available_providers:
        if provider not in ordered:
            ordered.append(provider)

    # Try ordered providers
    for provider in ordered:
        models = get_role_models(provider, "orchestrator")
        if models:
            return (models[0][0], None, provider)

    raise ValueError("No orchestrator model available from any configured provider")


def get_generator_model_for_agent(available_providers: list, preferred_provider: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """Select a generator-tier model for the code_generator agent.

    Uses get_role_models(provider, "generator") from config.provider_models to select
    from generator model lists. Same priority order and iteration logic as
    get_orchestrator_model_for_agent, with preferred_provider tried first.

    Args:
        available_providers: List of provider names that have API keys configured.
        preferred_provider: Optional explicitly preferred provider. Tried first.

    Returns:
        Tuple of (model_id, None, provider_name) — reasoning_effort is None because
        global user reasoning_effort is applied in api_client._make_request.
        provider_name is the provider from which the model was selected.

    Raises:
        ValueError: If no generator model available from any configured provider.
    """
    from config.provider_models import get_role_models

    # Build ordered provider list: preferred first, then priority, then remaining
    priority = ["deepseek", "glm", "qwencloud", "opencode_go", "openrouter", "routerai"]
    ordered = []
    if preferred_provider and preferred_provider in available_providers:
        ordered.append(preferred_provider)
    for provider in priority:
        if provider in available_providers and provider not in ordered:
            ordered.append(provider)
    for provider in available_providers:
        if provider not in ordered:
            ordered.append(provider)

    # Try ordered providers
    for provider in ordered:
        models = get_role_models(provider, "generator")
        if models:
            return (models[0][0], None, provider)

    raise ValueError("No generator model available from any configured provider")


def get_orchestrator_models_3level(available_providers: list, preferred_provider: Optional[str] = None) -> Tuple[Dict[str, str], Optional[str]]:
    """Select three orchestrator-tier models (simple/medium/complex) for the 3-level router.

    Picks models from the orchestrator list of the preferred provider (or first
    available provider with orchestrator models). The list is split into three tiers:
        - 3+ models: first=simple, middle=medium, last=complex
        - 2 models:  first=simple, last=complex; medium=complex
        - 1 model:   all three levels = that single model

    Args:
        available_providers: List of provider names that have API keys configured.
        preferred_provider: Optional explicitly preferred provider. Tried first.

    Returns:
        Tuple of (models_dict, provider_name) where models_dict has keys
        "simple", "medium", "complex" mapping to model_id strings, and
        provider_name is the provider from which models were selected.

    Raises:
        ValueError: If no orchestrator model available from any configured provider.
    """
    from config.provider_models import get_role_models

    # Build ordered provider list: preferred first, then priority, then remaining
    priority = ["deepseek", "glm", "qwencloud", "opencode_go", "openrouter", "routerai"]
    ordered = []
    if preferred_provider and preferred_provider in available_providers:
        ordered.append(preferred_provider)
    for provider in priority:
        if provider in available_providers and provider not in ordered:
            ordered.append(provider)
    for provider in available_providers:
        if provider not in ordered:
            ordered.append(provider)

    selected_models = None
    selected_provider: Optional[str] = None
    for provider in ordered:
        models = get_role_models(provider, "orchestrator")
        if models:
            selected_models = models
            selected_provider = provider
            break

    if not selected_models:
        raise ValueError("No orchestrator model available from any configured provider")

    model_ids = [m[0] for m in selected_models]

    if len(model_ids) >= 3:
        simple = model_ids[0]
        medium = model_ids[len(model_ids) // 2]
        complex_m = model_ids[-1]
    elif len(model_ids) == 2:
        simple = model_ids[0]
        medium = model_ids[-1]
        complex_m = model_ids[-1]
    else:
        simple = medium = complex_m = model_ids[0]

    return ({"simple": simple, "medium": medium, "complex": complex_m}, selected_provider)