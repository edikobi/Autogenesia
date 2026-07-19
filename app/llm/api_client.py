# app/llm/api_client.py

"""
Universal LLM API Client for AI Code Agent.

Supports:
- DeepSeek (direct API)
- OpenRouter (Claude, Gemini, Qwen)
- RouterAI (Claude, GPT, Gemini)
- Automatic retry with exponential backoff
- HTTP 429 handling with delay
- Tool/Function calling support
- Extended thinking for Claude models (NEW!)
"""

from __future__ import annotations

import json
import asyncio
import logging
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

from openai import AsyncOpenAI
import openai as openai_sdk

from config.settings import cfg

# ============== LOGGING =============
logger = logging.getLogger(__name__)

# ============== CONSTANTS =============
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 8
RETRY_BASE_DELAY = 2.0  # seconds
CONCURRENT_REQUESTS = 5

# Providers that accept `reasoning_effort` as a direct kwarg at the provider level
# (all models from these providers support it).
REASONING_EFFORT_SUPPORTED_PROVIDERS = {"deepseek", "openrouter", "routerai"}

# Providers where `reasoning_effort` support depends on the specific model.
# Only the listed model IDs (API-facing, stripped names) accept `reasoning_effort`.
REASONING_EFFORT_SUPPORTED_MODELS_PER_PROVIDER: Dict[str, set] = {
    "glm": {"glm-5.2"},
    "opencode_go": {"deepseek-v4-pro", "deepseek-v4-flash", "glm-5.2", "mimo-v2.5-pro"},
}

# DeepSeek reasoning_effort value mapping (per docs: low/medium→high, xhigh→max).
# Applied only for APIProvider.DEEPSEEK. OpenRouter/RouterAI accept xhigh as-is.
# "max" passes as-is (valid DeepSeek value). "none" is handled separately (skip).
DEEPSEEK_REASONING_EFFORT_MAP = {"minimal": "high", "low": "high", "medium": "high", "xhigh": "max"}

# Models that natively accept reasoning_effort="max" as a direct API value.
# For other models, user-selected "max" is downgraded to "xhigh" automatically.
MAX_CAPABLE_MODELS = {"deepseek-v4-pro", "glm-5.2"}

# Models that natively support reasoning_effort only up to "high" (not xhigh/max).
# User-selected "xhigh" or "max" is downgraded to "high" for these models.
REASONING_EFFORT_MAX_HIGH_MODELS = {"mimo-v2.5-pro"}


def _supports_reasoning_effort(provider: "APIProvider", api_model: str) -> bool:
    """Check whether a (provider, model) pair supports `reasoning_effort` as a direct kwarg.

    Args:
        provider: The resolved APIProvider enum value.
        api_model: The API-facing (stripped) model name.

    Returns:
        True if `reasoning_effort` can be passed as a direct parameter.
    """
    pv = provider.value.lower()
    if pv in REASONING_EFFORT_SUPPORTED_PROVIDERS:
        return True
    models = REASONING_EFFORT_SUPPORTED_MODELS_PER_PROVIDER.get(pv)
    return bool(models) and api_model in models


def _resolve_reasoning_effort_value(
    re_val: str,
    provider: "APIProvider",
    api_model: str,
) -> str:
    """Resolve the final reasoning_effort value to send to the API.

    Applies three transformations:
    1. max→xhigh downgrade for non-max-capable models (only deepseek-v4-pro and
       glm-5.2 natively accept "max"; other models get "xhigh" instead).
    1.5. xhigh/max→high downgrade for models capped at high (e.g. Xiaomi MiMo-V2.5-Pro
       supports reasoning_effort only from low to high).
    2. DeepSeek-specific mapping: minimal/low/medium→high, xhigh→max (for
       max-capable) or →high (for non-max-capable). "high" and "max" pass as-is.

    Args:
        re_val: The user-selected reasoning_effort (e.g. "high", "max", "xhigh").
        provider: The resolved APIProvider enum value.
        api_model: The API-facing (stripped) model name.

    Returns:
        The final reasoning_effort string to pass to the API.
    """
    if not re_val or re_val == "disabled" or re_val == "none":
        return re_val

    # Step 1: max→xhigh downgrade for non-max-capable models
    if re_val == "max" and api_model not in MAX_CAPABLE_MODELS:
        re_val = "xhigh"

    # Step 1.5: xhigh/max→high downgrade for models capped at high
    # (e.g. Xiaomi MiMo-V2.5-Pro supports reasoning_effort only from low to high)
    if api_model in REASONING_EFFORT_MAX_HIGH_MODELS and re_val in ("xhigh", "max"):
        re_val = "high"

    # Step 2: DeepSeek-specific mapping (DeepSeek API accepts only high/max)
    if provider == APIProvider.DEEPSEEK:
        if re_val in ("minimal", "low", "medium"):
            re_val = "high"
        elif re_val == "xhigh":
            re_val = "max" if api_model in MAX_CAPABLE_MODELS else "high"
        # "high" and "max" pass as-is
    return re_val


# Rate limit specific settings
RATE_LIMIT_MAX_RETRIES = 5  # Больше попыток для rate limit
RATE_LIMIT_BASE_DELAY = 10.0  # Начальная задержка 10 сек
RATE_LIMIT_MAX_DELAY = 60.0  # Максимальная задержка 60 сек
# ============== DATA STRUCTURES =============
class APIProvider(Enum):
    """API providers"""
    DEEPSEEK = "deepseek"
    OPENROUTER = "openrouter"
    GLM = "glm"
    OPENCODE_GO = "opencode_go"
    QWENCLOUD = "qwencloud"
    ROUTERAI = "routerai"


@dataclass
class LLMResponse:
    """Standardized LLM response"""
    content: str
    model: str
    provider: APIProvider
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    tool_calls: List[Dict[str, str]] = field(default_factory=list)
    raw_response: Optional[Dict] = None
    # [NEW] Добавляем поле для мыслей DeepSeek
    reasoning_content: Optional[str] = None
    # [NEW] Добавляем поле для Thought Signatures Gemini 3.0 Pro
    thought_signature: Optional[str] = None
    # [NEW] Добавляем поле для reasoning_details (OpenRouter Gemini 3 compatibility)
    # Это массив с зашифрованными данными рассуждений, который ДОЛЖЕН быть передан обратно
    reasoning_details: Optional[List[Dict[str, Any]]] = None

    # =========================================================================
    # NEW: finish_reason для диагностики обрезки ответов
    # Возможные значения: "stop", "length", "content_filter", "tool_calls", "end_turn"
    # "length" означает, что ответ был обрезан из-за достижения max_tokens
    # =========================================================================
    finish_reason: Optional[str] = None



@dataclass
class LLMRequest:
    """Standardized LLM request"""
    messages: List[Dict[str, Any]]
    model: str
    # FIX: temperature сделан Optional, чтобы поддерживать None для thinking-моделей
    temperature: Optional[float] = 0.0
    max_tokens: int = 4000
    top_p: float = 0.9
    tools: Optional[List[Dict]] = None
    tool_choice: Optional[str] = None



# ============== ERROR CLASSIFICATION =============

# Ошибки, которые можно повторить (временные)
RETRYABLE_ERROR_PATTERNS = [
    # Network errors
    "connection reset",
    "econnreset",
    "timeout",
    "timed out",
    "upstream connect error",
    "network error",
    
    # Provider overload
    "overloaded",
    "capacity",
    "temporarily unavailable",
    "service unavailable",
    "try again later",
    "too many requests",  # иногда не 429
    
    # Transient server issues
    "internal server error",
    "bad gateway",
    "gateway timeout",
]

# Ошибки переполнения контекста (требуют сжатия, не retry)
CONTEXT_OVERFLOW_PATTERNS = [
    "context_length_exceeded",
    "maximum context length",
    "token limit",
    "request too large",
    "content too long",
    "max_tokens",
    "context window",
    "too many tokens",
]

# Ошибки структуры сообщений (требуют исправления, не retry)
MESSAGE_STRUCTURE_PATTERNS = [
    "thought_signature",
    "missing a `thought_signature`",
    "parts field",
    "must include at least one parts",
    "invalid message",
    "malformed",
]


def classify_error(error_message: str) -> str:
    """
    Классифицирует ошибку API.
    
    Returns:
        "retryable" - можно повторить запрос
        "rate_limit" - rate limit, нужна большая пауза
        "context_overflow" - нужно сжатие контекста
        "message_structure" - нужно исправить сообщения
        "fatal" - нельзя исправить, нужно падать
    """
    error_lower = error_message.lower()
    
    # Rate limit (специальная обработка)
    if "rate limit" in error_lower or "429" in error_lower:
        return "rate_limit"
    
    # Context overflow
    for pattern in CONTEXT_OVERFLOW_PATTERNS:
        if pattern in error_lower:
            return "context_overflow"
    
    # Message structure errors
    for pattern in MESSAGE_STRUCTURE_PATTERNS:
        if pattern in error_lower:
            return "message_structure"
    
    # Retryable errors
    for pattern in RETRYABLE_ERROR_PATTERNS:
        if pattern in error_lower:
            return "retryable"
    
    # По умолчанию - fatal
    return "fatal"



# ============== MODEL ROUTING =============
class ModelRouter:
    """
    Routes model names to appropriate API provider and endpoint.
    Uses centralized configuration from settings.py
    """

    @classmethod
    def get_connection_details(cls, model: str, preferred_provider: Optional[str] = None) -> Dict[str, Any]:
        """Resolve connection details for a model, including stripped API-facing model name.

        Args:
            model: Model identifier (e.g. "deepseek-v4-flash", "anthropic/claude-opus-4.5").
            preferred_provider: Optional preferred provider name. If None, falls back
                to cfg.get_selected_agent_provider() inside get_model_connection_config.
        """
        config_data = cfg.get_model_connection_config(model, preferred_provider=preferred_provider)

        # Fallback when api_key is None or empty
        if not config_data.get("api_key"):
            from config.settings import PROVIDER_KEYS
            available = cfg.get_available_providers()
            if available:
                first_provider = available[0]
                pk = PROVIDER_KEYS.get(first_provider, {})
                if isinstance(pk, dict) and pk.get("api_key"):
                    config_data["api_key"] = pk["api_key"]
                    config_data["base_url"] = pk.get("base_url", config_data.get("base_url", ""))
                    config_data["provider_name"] = first_provider
            if not config_data.get("api_key"):
                raise LLMAPIError(f"No API key configured for any provider (model={model})")

        provider_name = config_data.get("provider_name", "OpenRouter")

        try:
            provider = next(p for p in APIProvider if p.value.lower() == provider_name.lower())
        except StopIteration:
            provider = APIProvider.OPENROUTER

        return {
            "provider": provider,
            "api_key": config_data["api_key"],
            "base_url": config_data["base_url"],
            "extra_params": config_data.get("extra_params", {}),
            "stripped_model": config_data.get("stripped_model", model),
        }


# ============== MAIN CLIENT =============
class LLMClient:
    """
    Universal LLM client with support for multiple providers.

    Features:
    - Automatic provider detection based on model name
    - Retry logic with exponential backoff
    - HTTP 429 (rate limit) handling
    - Tool/function calling support
    - Extended thinking support for Claude models (NEW!)
    - Request/response logging
    """

    def __init__(self, max_concurrent: int = CONCURRENT_REQUESTS):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._request_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    async def call(
            self,
            model: str,
            messages: List[Dict[str, Any]],
            temperature: Optional[float] = 0.0,
            max_tokens: int = 4000,
            top_p: float = 0.9,
            tools: Optional[List[Dict]] = None,
            tool_choice: Optional[str] = None,
            preferred_provider: Optional[str] = None,
            extra_params_override: Optional[Dict] = None,
            is_intermediate: bool = False,
        ) -> LLMResponse:
            """
            Universal LLM call with automatic provider routing.

            Args:
                model: Model identifier (e.g., "deepseek-chat", "anthropic/claude-opus-4.5")
                messages: List of message dicts with 'role' and 'content'
                temperature: Sampling temperature (0.0 = deterministic)
                max_tokens: Maximum tokens in response
                top_p: Nucleus sampling parameter
                tools: List of tool definitions (OpenAI format)
                tool_choice: How to select tools ("auto", "none", or tool name)
                preferred_provider: Optional preferred provider name. Forwarded to
                    ModelRouter.get_connection_details to route the call through the
                    user-selected provider (overrides JSON-order provider selection).
                is_intermediate: If True, this call belongs to an intermediate AI agent
                    (router, validator, compressor, etc.) and the user's global
                    reasoning_effort is NOT applied. If False (default), the call is a
                    user-facing role (Orchestrator / Pre-filter / Tester / Code Generator)
                    and the global reasoning_effort IS applied (model is irrelevant).

            Returns:
                LLMResponse with content and metadata

            Raises:
                LLMAPIError: On API errors after retries exhausted
            """
            # Determine provider and get connection details
            conn_details = ModelRouter.get_connection_details(model, preferred_provider=preferred_provider)
            provider = conn_details["provider"]
            api_key = conn_details["api_key"]
            extra_params = conn_details.get("extra_params", {})
            if extra_params_override is not None:
                extra_params = {**extra_params, **extra_params_override}
            stripped_model = conn_details.get("stripped_model", model)
            base_url = conn_details.get("base_url", "")

            # FIX: Reset temperature for thinking/reasoning models
            if extra_params and ("thinking" in extra_params or "reasoning_effort" in extra_params):
                temperature = None

            # Construct endpoint (kept for backward compatibility / logging)
            base = base_url.rstrip("/")
            if provider == APIProvider.DEEPSEEK:
                endpoint = f"{base}/v1/chat/completions"
            else:
                endpoint = f"{base}/chat/completions"

            if not api_key:
                raise LLMAPIError(f"No API key configured for {provider.value}")

            # Build request (original model for logging/display)
            request = LLMRequest(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                tools=tools,
                tool_choice=tool_choice,
            )

            # Execute with retry (pass stripped_model and base_url for OpenAI SDK)
            return await self._execute_with_retry(
                request=request,
                provider=provider,
                endpoint=endpoint,
                api_key=api_key,
                extra_params=extra_params,
                stripped_model=stripped_model,
                base_url=base_url,
                is_intermediate=is_intermediate,
            )

    async def call_with_tools(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict],
        temperature: float = 0.0,
        max_tokens: int = 4000,
        tool_choice: str = "auto",
        preferred_provider: Optional[str] = None,
        is_intermediate: bool = False,
    ) -> LLMResponse:
        """
        LLM call with tool/function calling support.
        """
        return await self.call(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            preferred_provider=preferred_provider,
            is_intermediate=is_intermediate,
        )

    async def _execute_with_retry(
        self,
        request: LLMRequest,
        provider: APIProvider,
        endpoint: str,
        api_key: str,
        extra_params: Dict = None,
            stripped_model: str = None,
            base_url: str = None,
            is_intermediate: bool = False,
    ) -> LLMResponse:
        """Execute request with retry logic and comprehensive error handling"""
        async with self._semaphore:
            last_error = None
            rate_limit_retries = 0
            
            for attempt in range(MAX_RETRIES):
                try:
                    start_time = time.time()
                    response = await self._make_request(
                                            request=request,
                                            provider=provider,
                                            endpoint=endpoint,
                                            api_key=api_key,
                                            extra_params=extra_params,
                                            stripped_model=stripped_model,
                                            base_url=base_url,
                                            is_intermediate=is_intermediate,
                                        )
                    latency_ms = (time.time() - start_time) * 1000

                    # Parse response
                    result = self._parse_response(
                        response=response,
                        model=request.model,
                        provider=provider,
                        latency_ms=latency_ms,
                    )

                    # Update stats
                    self._request_count += 1
                    self._total_tokens += result.total_tokens
                    self._total_cost += result.cost_usd
                    logger.info(
                        f"LLM call success: model={request.model}, "
                        f"tokens={result.total_tokens}, latency={latency_ms:.0f}ms"
                    )

                    return result

                except RateLimitError as e:
                    rate_limit_retries += 1
                    
                    # Специальная логика для rate limit с большим количеством попыток
                    if rate_limit_retries <= RATE_LIMIT_MAX_RETRIES:
                        # Экспоненциальная задержка с максимумом
                        delay = min(
                            RATE_LIMIT_BASE_DELAY * (2 ** (rate_limit_retries - 1)),
                            RATE_LIMIT_MAX_DELAY
                        )
                        
                        # Для Gemini добавляем дополнительное время
                        if "gemini" in request.model.lower():
                            delay = min(delay * 1.5, RATE_LIMIT_MAX_DELAY)
                        
                        logger.warning(
                            f"Rate limit hit (rate_limit_retry {rate_limit_retries}/{RATE_LIMIT_MAX_RETRIES}), "
                            f"waiting {delay:.0f}s before retry"
                        )
                        await asyncio.sleep(delay)
                        last_error = e
                        
                        # Не считаем rate limit как обычную попытку
                        # (позволяем продолжить основной цикл)
                        continue
                    else:
                        # Исчерпали rate limit retries
                        raise LLMAPIError(
                            f"Rate limit retries exhausted ({RATE_LIMIT_MAX_RETRIES}). "
                            f"Last error: {e}",
                            error_type="rate_limit"
                        )

                except RetryableError as e:
                    # Server errors (500, 502, 503) - retry with backoff
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Retryable error (attempt {attempt + 1}/{MAX_RETRIES}): {e}, "
                        f"waiting {delay}s"
                    )
                    await asyncio.sleep(delay)
                    last_error = e

                except ContextOverflowError as e:
                    # НЕ retry - пробрасываем наверх для обработки через compression
                    logger.warning(f"Context overflow detected: {e}")
                    raise

                except MessageStructureError as e:
                    # НЕ retry - пробрасываем наверх для исправления сообщений
                    logger.error(f"Message structure error (not retryable): {e}")
                    raise

                except LLMAPIError as e:
                    # Non-retryable error
                    logger.error(f"LLM API error (non-retryable): {e}")
                    raise

            # All retries exhausted
            raise LLMAPIError(
                f"All {MAX_RETRIES} retries exhausted. Last error: {last_error}"
            )

    async def _make_request(
                self,
                request: LLMRequest,
                provider: APIProvider,
                endpoint: str,
                api_key: str,
                extra_params: Dict = None,
                stripped_model: str = None,
                base_url: str = None,
                is_intermediate: bool = False,
            ) -> Dict:
                """Make HTTP request to LLM API using OpenAI SDK."""
                # Use stripped_model for API call, original model for logging
                api_model = stripped_model if stripped_model else request.model

                # Determine base_url for OpenAI SDK client
                if not base_url:
                    base_url = endpoint.rsplit("/chat/completions", 1)[0] if "/chat/completions" in endpoint else endpoint
                    base_url = base_url.rsplit("/v1/chat/completions", 1)[0] if "/v1/chat/completions" in base_url else base_url

                # Create OpenAI SDK client
                client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=REQUEST_TIMEOUT,
                )

                # Build kwargs for chat.completions.create()
                kwargs: Dict[str, Any] = {
                    "model": api_model,
                    "messages": request.messages,
                    "max_tokens": request.max_tokens,
                    "top_p": request.top_p,
                }

                if request.temperature is not None:
                    kwargs["temperature"] = request.temperature

                # === Handle extra_params (thinking, reasoning_effort) ===
                # Convert reasoning to extra_params if needed
                if extra_params:
                    extra_params = dict(extra_params)  # shallow copy
                else:
                    extra_params = {}
                if "reasoning_effort" not in extra_params:
                    model_cfg = cfg.MODEL_CONFIGS.get(request.model, {})
                    if "reasoning" in model_cfg:
                        extra_params["reasoning_effort"] = model_cfg["reasoning"].get("effort")

                # Resolve whether this (provider, model) supports reasoning_effort as direct kwarg
                supports_re = _supports_reasoning_effort(provider, api_model)

                if extra_params:
                    if "thinking" in extra_params:
                        existing = kwargs.get("extra_body", {})
                        kwargs["extra_body"] = {**existing, "thinking": extra_params["thinking"]}
                        if "temperature" in kwargs:
                            del kwargs["temperature"]
                        if "top_p" in kwargs:
                            del kwargs["top_p"]
                        logger.debug(
                            f"Extended thinking enabled for {request.model} "
                            f"with budget_tokens={extra_params['thinking'].get('budget_tokens', 'unlimited')}"
                        )

                    if "reasoning_effort" in extra_params:
                        if extra_params["reasoning_effort"] == "disabled":
                            # Thinking explicitly disabled — inject extra_body for API
                            existing_body = kwargs.get("extra_body", {})
                            kwargs["extra_body"] = {**existing_body, "thinking": {"type": "disabled"}}
                            logger.debug(f"Thinking explicitly disabled for {request.model}")
                        elif supports_re:
                            # Model supports reasoning_effort as a direct kwarg
                            re_val = _resolve_reasoning_effort_value(
                                extra_params["reasoning_effort"], provider, api_model
                            )
                            kwargs["reasoning_effort"] = re_val
                            if "temperature" in kwargs:
                                del kwargs["temperature"]
                            if "top_p" in kwargs:
                                del kwargs["top_p"]
                            logger.debug(
                                f"Reasoning effort set to '{re_val}' for {request.model} (provider={provider.value})"
                            )
                        else:
                            # Model does NOT support reasoning_effort — do not pass it.
                            # Do NOT remove temperature/top_p (model uses them normally).
                            logger.debug(
                                f"reasoning_effort='{extra_params['reasoning_effort']}' NOT supported "
                                f"by {request.model} (provider={provider.value}); skipped"
                            )

    # === Handle user's global reasoning_effort ===
                # Role-based: is_intermediate flag is passed by the caller.
                # - Orchestrator / Pre-filter / Tester / Code Generator (is_intermediate=False)
                #   → global reasoning_effort IS applied (model is irrelevant).
                # - Other AI agents (is_intermediate=True) → global reasoning_effort is NOT
                #   applied; agent's own "local" settings remain in effect.
                try:
                    user_reasoning_effort = cfg.get_user_reasoning_effort()
                    if user_reasoning_effort and user_reasoning_effort != "none" and "reasoning_effort" not in (extra_params or {}):
                        if not is_intermediate and supports_re:
                            # Apply user's global reasoning_effort to user-facing roles only.
                            re_val = _resolve_reasoning_effort_value(
                                user_reasoning_effort, provider, api_model
                            )
                            kwargs["reasoning_effort"] = re_val
                            if "temperature" in kwargs:
                                del kwargs["temperature"]
                            if "top_p" in kwargs:
                                del kwargs["top_p"]
                            logger.debug(f"User reasoning_effort '{re_val}' applied to {request.model} (provider={provider.value})")
                        elif not is_intermediate and not supports_re:
                            # Model does not support reasoning_effort; for glm-5-turbo, ensure thinking
                            # stays enabled (default) so it can still reason when user wants max reasoning.
                            logger.debug(
                                f"User reasoning_effort '{user_reasoning_effort}' NOT supported by "
                                f"{request.model} (provider={provider.value}); skipped"
                            )
                except Exception:
                    pass

                # === GLM glm-5-turbo thinking:disabled (for speed) when no reasoning_effort is active ===
                # Per GLM docs: glm-5-turbo supports thinking disabled via extra_body.
                # Apply only when no reasoning_effort was set (neither per-model nor global),
                # to keep the fast path fast. If user wants max reasoning, thinking stays ON.
                try:
                    re_active = "reasoning_effort" in kwargs
                    is_glm_turbo = api_model == "glm-5-turbo"
                    if is_glm_turbo and not re_active:
                        existing_body = kwargs.get("extra_body", {})
                        if "thinking" not in existing_body:
                            kwargs["extra_body"] = {**existing_body, "thinking": {"type": "disabled"}}
                            logger.debug(f"GLM glm-5-turbo: thinking disabled (no reasoning_effort active) for {request.model}")
                except Exception:
                    pass

                # === Add tools if specified ===
                if request.tools:
                    kwargs["tools"] = request.tools
                    if request.tool_choice:
                        kwargs["tool_choice"] = request.tool_choice

                # === OpenRouter-specific headers ===
                if provider == APIProvider.OPENROUTER:
                    kwargs["extra_headers"] = {
                        "HTTP-Referer": "https://ai-code-agent.local",
                        "X-Title": "AI Code Agent",
                    }

                # === Preserve reasoning_details and thought_signature in assistant messages ===
                for msg in kwargs.get("messages", []):
                    if msg.get("role") == "assistant":
                        if "reasoning_details" in msg:
                            logger.debug(
                                f"Preserving reasoning_details in assistant message "
                                f"({len(msg['reasoning_details'])} items)"
                            )
                        if msg.get("tool_calls") and "thought_signature" in msg:
                            logger.debug(
                                f"Preserving thought_signature in assistant message with tool_calls"
                            )
                        if "tool_calls" in msg:
                            for tc in msg["tool_calls"]:
                                if "extra_content" in tc:
                                    logger.debug(
                                        f"Preserving extra_content in tool_call {tc.get('id', 'unknown')}"
                                    )

                # === DeepSeek-specific message handling ===
                if provider == APIProvider.DEEPSEEK:
                    for msg in kwargs.get("messages", []):
                        if msg.get("role") == "assistant":
                            if "reasoning_content" not in msg:
                                msg["reasoning_content"] = msg.get("content") or ""
                            if msg.get("content") is None:
                                msg["content"] = ""

                # === Make API call using OpenAI SDK ===
                try:
                    response = await client.chat.completions.create(**kwargs)
                    return response.model_dump()

                except openai_sdk.RateLimitError as e:
                    raise RateLimitError(str(e))

                except openai_sdk.APIConnectionError as e:
                    raise RetryableError(str(e))

                except openai_sdk.APIStatusError as e:
                    if e.status_code in (500, 502, 503):
                        raise RetryableError(str(e))
                    error_text = str(e)
                    error_type = classify_error(error_text)
                    if error_type == "rate_limit":
                        raise RateLimitError(error_text)
                    elif error_type == "context_overflow":
                        raise ContextOverflowError(error_text)
                    elif error_type == "message_structure":
                        raise MessageStructureError(error_text)
                    elif error_type == "retryable":
                        raise RetryableError(error_text)
                    else:
                        raise LLMAPIError(error_text, error_type="fatal")


    def _parse_response(
        self,
        response: Dict,
        model: str,
        provider: APIProvider,
        latency_ms: float,
    ) -> LLMResponse:
        """Parse API response into standardized format"""
        # Extract content
        choices = response.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message", {}) or {}
        content = message.get("content", "")
        
        if content is None:
            content = ""

        # =====================================================================
        # NEW: Извлекаем finish_reason для диагностики обрезки
        # =====================================================================
        finish_reason = choice.get("finish_reason")
        
        # Логируем предупреждение если ответ обрезан по длине
        if finish_reason == "length":
            content_preview = content[-100:] if len(content) > 100 else content
            logger.warning(
                f"⚠️ Response TRUNCATED (finish_reason=length) for model={model}. "
                f"output_tokens may have hit max_tokens limit. "
                f"Last 100 chars: ...{repr(content_preview)}"
            )
        elif finish_reason and finish_reason not in ("stop", "end_turn", "tool_calls"):
            logger.info(f"LLM finish_reason={finish_reason} for model={model}")
        
        # DEBUG: Логируем finish_reason для всех запросов при отладке
        logger.debug(f"LLM response: model={model}, finish_reason={finish_reason}, content_length={len(content)}")

        # [EXISTING] Извлекаем reasoning_content (специфично для DeepSeek R1)
        reasoning_content = message.get("reasoning_content")

        # [EXISTING] Извлекаем reasoning_details (OpenRouter Gemini 3 compatibility)
        reasoning_details = None
        
        # Check message level first
        if "reasoning_details" in message:
            reasoning_details = message["reasoning_details"]
        # Check delta level (streaming format)
        elif "delta" in choice and "reasoning_details" in choice["delta"]:
            reasoning_details = choice["delta"]["reasoning_details"]
        
        # [EXISTING] Извлекаем thought_signature (специфично для Gemini 3.0 Pro)
        thought_signature = None
        
        # First, try to extract from reasoning_details if present
        if reasoning_details and isinstance(reasoning_details, list):
            for detail in reasoning_details:
                if isinstance(detail, dict):
                    if detail.get("type") == "reasoning.encrypted" and "data" in detail:
                        thought_signature = detail.get("data")
                        break
        
        # Fallback: Gemini 3 returns thought_signature in parts array
        if not thought_signature:
            parts = message.get("parts", [])
            if parts:
                for part in parts:
                    if isinstance(part, dict) and "thought_signature" in part:
                        thought_signature = part["thought_signature"]
                        break
        
        # Fallback: check direct message field (OpenAI compatibility format)
        if not thought_signature:
            thought_signature = message.get("thought_signature")
        
        # Check inside tool_calls for extra_content (OpenRouter format)
        if not thought_signature and message.get("tool_calls"):
            for tc in message["tool_calls"]:
                extra_content = tc.get("extra_content", {}) or {}
                google_data = extra_content.get("google", {}) or {}
                if "thought_signature" in google_data:
                    thought_signature = google_data["thought_signature"]
                    break

        # Extract tool calls if present
        tool_calls = []
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                tc_dict = tc if isinstance(tc, dict) else {}
                tc_func = tc_dict.get("function", {}) or {}
                tool_call_data = {
                    "id": tc_dict.get("id"),
                    "type": tc_dict.get("type", "function"),
                    "function": {
                        "name": tc_func.get("name"),
                        "arguments": tc_func.get("arguments", "{}"),
                    }
                }
                # Preserve extra_content if present (contains thought_signature for Gemini)
                if "extra_content" in tc_dict:
                    tool_call_data["extra_content"] = tc_dict["extra_content"]
                tool_calls.append(tool_call_data)

        # Extract usage
        usage = response.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

        # Calculate cost
        cost_usd = self._estimate_cost(model, input_tokens, output_tokens)

        return LLMResponse(
            content=content,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
            raw_response=response,
            reasoning_content=reasoning_content,
            thought_signature=thought_signature,
            reasoning_details=reasoning_details,
            finish_reason=finish_reason,  # NEW
        )
        
        
    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD based on model pricing"""
        # Pricing per 1M tokens (approximate). Keys are normalized (no vendor prefix)
        # so that e.g. both "z-ai/glm-5.2" and "glm-5.2" match the same entry.
        pricing = {
            cfg.normalize_model_id(cfg.MODEL_OPUS_4_5): {"input": 15.0, "output": 75.0},
            cfg.normalize_model_id(cfg.MODEL_SONNET_4_5): {"input": 3.0, "output": 15.0},
            cfg.normalize_model_id(cfg.MODEL_GEMINI_3_PRO): {"input": 1.25, "output": 5.0},
            cfg.normalize_model_id(cfg.MODEL_GEMINI_2_FLASH): {"input": 0.1, "output": 0.4},
            cfg.normalize_model_id(cfg.MODEL_NORMAL): {"input": 0.14, "output": 0.28},
        }
        
        # Add Qwen if configured
        if cfg.MODEL_QWEN:
            pricing[cfg.normalize_model_id(cfg.MODEL_QWEN)] = {"input": 0.5, "output": 1.5}

        rates = pricing.get(cfg.normalize_model_id(model), {"input": 1.0, "output": 2.0})
        
        input_cost = (input_tokens / 1_000_000) * rates["input"]
        output_cost = (output_tokens / 1_000_000) * rates["output"]
        
        return input_cost + output_cost

    @property
    def stats(self) -> Dict[str, Any]:
        """Get usage statistics"""
        return {
            "total_requests": self._request_count,
            "total_tokens": self._total_tokens,
            "total_cost_usd": round(self._total_cost, 4),
        }



# ============== CONVENIENCE FUNCTIONS =============
# Global client instance
_default_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    """Get or create default LLM client"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


async def call_llm(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 4000,
    preferred_provider: Optional[str] = None,
    is_intermediate: bool = False,
    **kwargs
) -> str:
    """
    Simple function to call LLM and get response text.

    Args:
        preferred_provider: Optional preferred provider name forwarded to the client.
        is_intermediate: If True, this is an intermediate AI agent (router, validator,
            compressor, etc.) and the user's global reasoning_effort is NOT applied.
            If False (default), this is a user-facing role (Orchestrator / Pre-filter /
            Tester / Code Generator) and the global reasoning_effort IS applied.

    Returns:
        Response content as string
    """
    client = get_client()
    response = await client.call(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        preferred_provider=preferred_provider,
        is_intermediate=is_intermediate,
        **kwargs
    )
    return response.content


async def call_llm_full(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 4000,
    preferred_provider: Optional[str] = None,
    is_intermediate: bool = False,
    **kwargs
) -> LLMResponse:
    """
    Call LLM and get FULL response object with metadata.

    Unlike call_llm() which returns only content string,
    this returns the complete LLMResponse including:
    - finish_reason (critical for detecting truncation)
    - token counts
    - latency
    - raw_response for debugging

    Use this when you need to check if response was truncated.

    Args:
        preferred_provider: Optional preferred provider name forwarded to the client.
        is_intermediate: See call_llm() docstring.

    Returns:
        LLMResponse object with all metadata
    """
    client = get_client()
    response = await client.call(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        preferred_provider=preferred_provider,
        is_intermediate=is_intermediate,
        **kwargs
    )
    return response


async def call_llm_with_tools(
    model: str,
    messages: List[Dict[str, str]],
    tools: List[Dict],
    temperature: float = 0.0,
    max_tokens: int = 4000,
    tool_choice: str = "auto",
    preferred_provider: Optional[str] = None,
    is_intermediate: bool = False,
) -> Dict[str, Any]:
    """
    Call LLM with tool support.

    Args:
        preferred_provider: Optional preferred provider name forwarded to the client.
        is_intermediate: See call_llm() docstring.

    Returns:
        Dict with 'content', 'tool_calls', 'reasoning_content',
        'thought_signature', 'reasoning_details', and 'raw_response' keys
    """
    client = get_client()
    response = await client.call_with_tools(
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        tool_choice=tool_choice,
        preferred_provider=preferred_provider,
        is_intermediate=is_intermediate,
    )
    return {
        "content": response.content,
        "tool_calls": response.tool_calls,
        "reasoning_content": response.reasoning_content,
        "thought_signature": response.thought_signature,
        "reasoning_details": response.reasoning_details,
        "raw_response": response.raw_response,
    }


# ============== MODEL HELPERS =============
def get_model_for_role(role: str, preferred_provider: Optional[str] = None) -> str:
    """
    Get configured model for a specific agent role.

    Uses provider-aware selection for pre_filter and code_generator roles,
    falling back to orchestrator/generator model lists based on available providers.

    DEPRECATED for 'pre_filter' and 'code_generator' roles: prefer passing the
    per-role provider explicitly via the calling agent (analyze_query /
    generate_code_agent_mode) — this helper returns only the model_id and
    discards the resolved provider, forcing callers to guess the provider
    (which is unsafe for multi-provider models like "deepseek-v4-pro" that
    exist in several providers). Kept for backward compatibility with any
    external callers.

    Args:
        role: One of 'router', 'orchestrator_simple', 'orchestrator_medium',
              'orchestrator_complex', 'pre_filter', 'code_generator', 'history_compressor'
        preferred_provider: Optional preferred provider. If None, uses
            cfg.get_selected_agent_provider() (the background-agents default).
            Pass the per-role provider for user-facing roles.

    Returns:
        Model identifier string

    Raises:
        ValueError: If role is unknown
    """
    # Special roles
    if role == "router":
        return cfg.get_router_model()
    elif role == "orchestrator_simple":
        return cfg.get_orchestrator_model_config()["orchestrator_models"]["simple"]
    elif role == "orchestrator_medium":
        return cfg.get_orchestrator_model_config()["orchestrator_models"]["medium"]
    elif role == "orchestrator_complex":
        return cfg.get_orchestrator_model_config()["orchestrator_models"]["complex"]
    elif role == "pre_filter":
        from config.intermediate_agent_models import get_orchestrator_model_for_agent
        model_id, _re, _provider = get_orchestrator_model_for_agent(
            cfg.get_available_providers(),
            preferred_provider=preferred_provider or cfg.get_selected_agent_provider(),
        )
        return model_id
    elif role == "code_generator":
        from config.intermediate_agent_models import get_generator_model_for_agent
        model_id, _re, _provider = get_generator_model_for_agent(
            cfg.get_available_providers(),
            preferred_provider=preferred_provider or cfg.get_selected_agent_provider(),
        )
        return model_id

    # Roles from AGENT_MODELS dict
    elif role in cfg.AGENT_MODELS:
        return cfg.AGENT_MODELS[role]
    else:
        raise ValueError(f"Unknown role: {role}. Valid roles: router, orchestrator_simple, "
                         f"orchestrator_medium, orchestrator_complex, {', '.join(cfg.AGENT_MODELS.keys())}")



def get_orchestrator_model(is_complex: bool = False) -> str:
    """
    Get appropriate orchestrator model based on task complexity.
    Uses cfg.get_orchestrator_model_config() from settings.py

    Args:
        is_complex: Whether the task is complex (needs stronger model)

    Returns:
        Model identifier
    """
    config = cfg.get_orchestrator_model_config()
    
    if config["mode"] == "fixed":
        return config["fixed_model"]

    # Router mode
    if is_complex:
        return config["orchestrator_models"]["complex"]
    
    return config["orchestrator_models"]["simple"]


def is_router_enabled() -> bool:
    """Check if automatic router is enabled"""
    return cfg.ROUTER_ENABLED

# ... весь остальной код ...

# ============== EXCEPTIONS =============

class LLMAPIError(Exception):
    """Base exception for LLM API errors"""
    def __init__(self, message: str, error_type: str = "fatal"):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class RateLimitError(LLMAPIError):
    """HTTP 429 rate limit error"""
    def __init__(self, message: str):
        super().__init__(message, error_type="rate_limit")


class RetryableError(LLMAPIError):
    """Errors that can be retried (5xx, network issues)"""
    def __init__(self, message: str):
        super().__init__(message, error_type="retryable")


class ContextOverflowError(LLMAPIError):
    """Context/token limit exceeded - needs compression"""
    def __init__(self, message: str):
        super().__init__(message, error_type="context_overflow")


class MessageStructureError(LLMAPIError):
    """Invalid message structure (thought_signature, empty parts)"""
    def __init__(self, message: str):
        super().__init__(message, error_type="message_structure")


# ============== HELPER FUNCTIONS =============

def is_context_overflow_error(error: Exception) -> bool:
    """Check if error is context overflow (for external use)"""
    if isinstance(error, ContextOverflowError):
        return True
    
    error_str = str(error).lower()
    for pattern in CONTEXT_OVERFLOW_PATTERNS:
        if pattern in error_str:
            return True
    return False


def is_message_structure_error(error: Exception) -> bool:
    """Check if error is message structure issue (for external use)"""
    if isinstance(error, MessageStructureError):
        return True
    
    error_str = str(error).lower()
    for pattern in MESSAGE_STRUCTURE_PATTERNS:
        if pattern in error_str:
            return True
    return False