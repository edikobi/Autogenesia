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
import random
import hashlib
import ssl
import certifi
from typing import Callable, List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

import httpx
from openai import AsyncOpenAI
import openai as openai_sdk

from config.settings import cfg

# ============== LOGGING =============
logger = logging.getLogger(__name__)

# ============== CONSTANTS =============
# HOTFIX (A): httpx.Timeout instead of float — float(120.0) was overriding ALL
# Extended read timeout for reasoning/thinking calls (reasoning_effort=max
# can take 300–700s on server side).
# 4 phases (connect/read/write/pool) to 120s, causing ReadTimeout on reasoning
# models that generate 300–700s. Now: read=600s covers normal generation,
# connect=10s fails fast on network issues.
# ============== CONSTANTS =============
# Явное разделение таймаутов по фазам:
# - connect=30.0 : 30 сек на TCP+TLS рукопожатие с Cloudflare (хватит для любого региона).
# - read=1200.0  : 20 минут ожидания между чанками (для моделей с reasoning_effort=max,
#                  генерирующих огромный код или долго думающих перед первым токеном).
# - write=120.0  : 2 минуты на отправку огромного промпта (1M контекст).
# - pool=30.0    : 30 сек ожидания свободного сокета из пула.
REQUEST_TIMEOUT = httpx.Timeout(
    timeout=1200.0,
    connect=30.0,
    read=1200.0,
    write=120.0,
    pool=30.0
)

# Расширенный таймаут для reasoning/thinking моделей (до 30 минут ожидания данных).
# reasoning_effort=max может уходить в глубокие размышления на 10-15 минут.
REASONING_REQUEST_TIMEOUT = httpx.Timeout(
    timeout=1800.0,
    connect=30.0,
    read=1800.0,
    write=120.0,
    pool=30.0
)


# HOTFIX (D): 8→4 + capped backoff to break the ~52min cascade.
MAX_RETRIES = 2
RETRY_BASE_DELAY = 2.0  # seconds
RETRY_MAX_DELAY = 30.0  # Cap exponential backoff (was unbounded: 2^7=128s)
# HOTFIX (C): Separate cap for timeout retries — retrying a timed-out generative
# request is wasteful (server may have completed generation = billed).
TIMEOUT_MAX_RETRIES = 2
CONCURRENT_REQUESTS = 5

RETRYABLE_5XX_STATUS_CODES = {500, 502, 503, 504, 520, 521, 522, 523, 524, 526}

# ============== SHARED HTTP CLIENT POOL (Task 3) ==============
# Module-level cache of shared httpx.AsyncClient instances per (base_url, api_key_hash).
# Each provider gets ONE persistent pool with keep-alive — eliminates per-call TLS
# handshake storms that triggered Cloudflare 5xx gate-keeping (cf-ray + cf-placement
# remote-ORD responses with text/plain body, no JSON).
#
# Why httpx.AsyncClient and not AsyncOpenAI cache:
#   - SDK's `http_client=` parameter accepts our shared pool directly (documented
#     "Custom HTTP Clients" pattern in openai-python).
#   - SDK wrapper itself is cheap (just config); the expensive resource is the
#     underlying httpx pool with its TLS sessions + keep-alive.
#   - One pool per provider = stable TLS sessions across retries + agent pipeline
#     (Orchestrator → Generator → Validator) instead of fresh handshakes per call.
_provider_clients: Dict[Tuple[str, str], httpx.AsyncClient] = {}


def _client_cache_key(base_url: str, api_key: str) -> Tuple[str, str]:
    """Build cache key with hashed api_key (no raw secret in logs/dicts).

    Returns (base_url_stripped, api_key_sha256_prefix). Same api_key+base_url
    always maps to the same key — stable across retries and agent calls.
    """
    normalized_url = (base_url or "").rstrip("/")
    if api_key:
        # Truncated sha256 — enough entropy for cache key, no PII leakage in logs.
        api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    else:
        api_key_hash = "empty"
    return (normalized_url, api_key_hash)


def _get_shared_http_client(base_url: str, api_key: str) -> httpx.AsyncClient:
    """Return a shared httpx.AsyncClient for the (base_url, api_key) pair."""
    key = _client_cache_key(base_url, api_key)
    client = _provider_clients.get(key)
    if client is None or client.is_closed:
        # FIX: Возвращаем стандартное поведение httpx (без явного verify),
        # чтобы использовать нативный SSLContext, как в старом коде.
        client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            limits=httpx.Limits(
                # FIX: Лимиты уменьшены до 5, чтобы совпадать с CONCURRENT_REQUESTS.
                # Стриминг удерживает соединения минутами. Если пул попытается открыть
                # больше 5 соединений, Cloudflare сбросит лишние (BrokenResourceError).
                max_connections=5,
                max_keepalive_connections=5,
                keepalive_expiry=40.0,
            ),
            follow_redirects=True,
        )
        _provider_clients[key] = client
        logger.debug(
            f"Created shared httpx.AsyncClient pool: "
            f"base_url={key[0]} api_key_hash={key[1]}"
        )
    return client


async def close_all_clients() -> None:
    """Close all cached shared HTTP clients. Safe to call multiple times.

    Intended for shutdown paths (e.g. asyncio shutdown hook in main.py).
    Not calling this is non-fatal — clients live until process exit, OS
    reclaims sockets. No agent-side dependency on this being called.
    """
    closed = 0
    for key, client in list(_provider_clients.items()):
        try:
            if not client.is_closed:
                await client.aclose()
            closed += 1
        except Exception as e:
            logger.warning(f"Error closing shared httpx client for {key[0]}: {e}")
    _provider_clients.clear()
    if closed:
        logger.info(f"Closed {closed} shared HTTP client pool(s) on shutdown")


def _extract_5xx_diagnostics(error: "openai_sdk.APIStatusError") -> Dict[str, Any]:
    """Extract Cloudflare/server diagnostic fields from APIStatusError response.

    Pulls: status_code, cf-ray, cf-placement, server, retry-after (parsed to
    float seconds when numeric), and first 200 bytes of response body.

    All extraction is defensive — missing/None fields are silently skipped.
    Body bytes are decoded as utf-8 with errors='replace' to never raise on
    binary/garbage payloads.
    """
    diag: Dict[str, Any] = {"status_code": getattr(error, "status_code", None)}
    try:
        response = getattr(error, "response", None)
        if response is None:
            return diag

        # httpx.Headers is case-insensitive — both "cf-ray" and "CF-RAY" resolve.
        headers = getattr(response, "headers", None) or {}
        diag["cf-ray"] = headers.get("cf-ray")
        diag["cf-placement"] = headers.get("cf-placement")
        diag["server"] = headers.get("server")

        retry_after_raw = headers.get("retry-after")
        diag["retry-after"] = retry_after_raw
        if retry_after_raw:
            try:
                # Numeric seconds (most common form). HTTP-date format is not
                # supported here — caller falls back to exponential backoff.
                diag["retry-after-seconds"] = float(retry_after_raw)
            except (ValueError, TypeError):
                diag["retry-after-seconds"] = None

        # First 200 bytes of body for incident triage. Cloudflare's 75-byte
        # text/plain error bodies (no JSON) land here for accurate diagnosis.
        try:
            body_bytes = response.content if hasattr(response, "content") else b""
            if body_bytes:
                diag["body_preview"] = body_bytes[:200].decode("utf-8", errors="replace")
            else:
                diag["body_preview"] = ""
        except Exception:
            diag["body_preview"] = "<unreadable>"
    except Exception as ex:
        logger.debug(f"Failed to extract 5xx diagnostics: {ex}")
    return diag



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
    max_tokens: Optional[int] = 4000
    top_p: float = 0.9
    tools: Optional[List[Dict]] = None
    tool_choice: Optional[str] = None
    reasoning_effort: Optional[str] = None



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
                max_tokens: Optional[int] = 4000,
                top_p: float = 0.9,
                tools: Optional[List[Dict]] = None,
                tool_choice: Optional[str] = None,
                preferred_provider: Optional[str] = None,
                extra_params_override: Optional[Dict] = None,
                is_intermediate: bool = False,
                on_delta: Optional[Callable[[str], None]] = None,
                on_reasoning_delta: Optional[Callable[[str], None]] = None,
                reasoning_effort: Optional[str] = None,
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
                    on_delta: Optional streaming callback invoked for each content delta.
                    on_reasoning_delta: Optional streaming callback invoked for reasoning deltas.

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
                    reasoning_effort=reasoning_effort,
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
                    on_delta=on_delta,
                    on_reasoning_delta=on_reasoning_delta,
                )

    async def call_with_tools(
                self,
                model: str,
                messages: List[Dict[str, Any]],
                tools: List[Dict],
                temperature: float = 0.0,
                max_tokens: Optional[int] = 4000,
                tool_choice: str = "auto",
                preferred_provider: Optional[str] = None,
                is_intermediate: bool = False,
                on_delta: Optional[Callable[[str], None]] = None,
                on_reasoning_delta: Optional[Callable[[str], None]] = None,
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
                    on_delta=on_delta,
                    on_reasoning_delta=on_reasoning_delta,
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
            on_delta: Optional[Callable[[str], None]] = None,
            on_reasoning_delta: Optional[Callable[[str], None]] = None,
        ) -> LLMResponse:
            """Execute request with retry logic and comprehensive error handling.

            When a streaming attempt fails mid-stream and is retried, the stream
            restarts from the beginning. The deduplicating wrappers below suppress
            the duplicate content/reasoning prefix that was already delivered to
            the real callbacks, so UI callbacks never receive the same text twice.
            """
            async with self._semaphore:
                last_error = None
                # Task 3: track whether the last error was a 5xx server overload.
                # If so, raise ServerOverloadError instead of generic LLMAPIError
                # after retries exhausted — agents catch it explicitly and continue
                # with the same messages list (no mutation, no compression).
                last_error_was_5xx = False
                rate_limit_retries = 0
                timeout_retries = 0  # HOTFIX (C): separate counter for timeout retries

                # Mid-stream retry deduplication state. These counters persist
                # across all retry attempts so each new attempt knows how many
                # characters were already delivered and can skip that prefix.
                dedup_state = {
                    "delivered_content": 0,
                    "delivered_reasoning": 0,
                    "skip_content": 0,
                    "skip_reasoning": 0,
                }

                def _dedup_content_delta(piece: str) -> None:
                    """Forward content delta, discarding the already-delivered retry prefix."""
                    if not piece:
                        return
                    skip = dedup_state["skip_content"]
                    if skip > 0:
                        if len(piece) <= skip:
                            dedup_state["skip_content"] = skip - len(piece)
                            return
                        piece = piece[skip:]
                        dedup_state["skip_content"] = 0
                    dedup_state["delivered_content"] += len(piece)
                    if on_delta is not None:
                        on_delta(piece)

                def _dedup_reasoning_delta(piece: str) -> None:
                    """Forward reasoning delta with retry-prefix suppression.

                    This wrapper is a no-op when on_reasoning_delta is None.
                    """
                    if on_reasoning_delta is None or not piece:
                        return
                    skip = dedup_state["skip_reasoning"]
                    if skip > 0:
                        if len(piece) <= skip:
                            dedup_state["skip_reasoning"] = skip - len(piece)
                            return
                        piece = piece[skip:]
                        dedup_state["skip_reasoning"] = 0
                    dedup_state["delivered_reasoning"] += len(piece)
                    on_reasoning_delta(piece)

                for attempt in range(MAX_RETRIES):
                    # Reset the skip prefix to the current cumulative delivered
                    # character count immediately before each streaming attempt.
                    # On the first attempt the counts are zero, so every delta is
                    # forwarded in full (original behavior is preserved exactly).
                    dedup_state["skip_content"] = dedup_state["delivered_content"]
                    dedup_state["skip_reasoning"] = dedup_state["delivered_reasoning"]

                    try:
                        start_time = time.time()
                        api_model_for_stream = stripped_model if stripped_model else request.model
                        if on_delta is not None and should_stream(provider, api_model_for_stream, request.tools is not None):
                            response = await self._make_stream_request(
                                request=request,
                                provider=provider,
                                endpoint=endpoint,
                                api_key=api_key,
                                extra_params=extra_params,
                                stripped_model=stripped_model,
                                base_url=base_url,
                                is_intermediate=is_intermediate,
                                on_delta=_dedup_content_delta,
                                on_reasoning_delta=_dedup_reasoning_delta,
                            )
                        else:
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

                    # Отступы надо лечить, дядя Вова нам поможет
                    except LLMTimeoutError as e:
                        # HOTFIX (C): Separate branch for timeouts.
                        # Retry capped at TIMEOUT_MAX_RETRIES — each timed-out request
                        # may have been billed (server completes generation regardless).
                        timeout_retries += 1

                        # === FIX: Drop stale connection pool on timeout ===
                        # ConnectTimeout means the socket in the pool is guaranteed
                        # dead (server never completed TLS handshake).  ReadTimeout
                        # may also leave the socket in an inconsistent state.
                        # Removing the client from the pool forces the next retry
                        # to create a fresh TCP+TLS connection (like curl does).
                        client_key = _client_cache_key(base_url, api_key)
                        stale_client = _provider_clients.pop(client_key, None)
                        if stale_client is not None:
                            try:
                                await stale_client.aclose()
                            except Exception:
                                pass
                            logger.warning(
                                f"Dropped stale httpx pool for {client_key[0]} "
                                f"(timeout recovery, timeout_retry {timeout_retries})"
                            )
                        # ================================================

                        if timeout_retries <= TIMEOUT_MAX_RETRIES:
                            logger.error(
                                f"⏳ Таймаут {timeout_retries}/{TIMEOUT_MAX_RETRIES} "
                                f"(attempt {attempt + 1}/{MAX_RETRIES}): "
                                f"модель продолжала генерацию на сервере. {e}"
                            )
                            await asyncio.sleep(min(RETRY_BASE_DELAY, 5.0))
                            last_error = e
                            continue
                        else:
                            logger.error(
                                f"⏳ Таймаут: исчерпано {TIMEOUT_MAX_RETRIES} попыток. {e}"
                            )
                            raise LLMAPIError(
                                f"Timeout after {TIMEOUT_MAX_RETRIES} retries. "
                                f"Model was still generating on server. Last error: {e}",
                                error_type="timeout"
                            )
                            
                    except RetryableError as e:
                        # Task 3: Honour server's Retry-After if provided (Cloudflare
                        # and origin sometimes return this header). Otherwise use
                        # exponential backoff with jitter (0.5x..1.0x multiplier)
                        # to avoid thundering herd of concurrent retries from the
                        # agent pipeline (Orchestrator + Generator + Validator).
                        status_code = getattr(e, "status_code", None)
                        retry_after = getattr(e, "retry_after", None)

                        if status_code in RETRYABLE_5XX_STATUS_CODES:
                            last_error_was_5xx = True
                        else:
                            # Network errors have status_code=None — keep generic path.
                            last_error_was_5xx = False

                        if retry_after is not None and retry_after > 0:
                            # Server explicitly told us to wait — honour it, but cap
                            # to RETRY_MAX_DELAY so a malicious/huge Retry-After can't
                            # stall the pipeline indefinitely.
                            delay = min(float(retry_after), RETRY_MAX_DELAY)
                        else:
                            # HOTFIX (D): exponential backoff capped at RETRY_MAX_DELAY.
                            base = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                            # Jitter: 0.5x..1.0x — randomizes retry timing across
                            # concurrent agents so they don't all retry at once.
                            delay = base * (0.5 + random.random() * 0.5)

                        # HOTFIX (F): error-level log for visibility in ai_errors_*.log
                        logger.error(
                            f"Retryable error (attempt {attempt + 1}/{MAX_RETRIES}): "
                            f"status={status_code} delay={delay:.1f}s "
                            f"retry_after_hint={retry_after} error={e}"
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

                # All retries exhausted.
                # Task 3: distinguish 5xx server overload path from generic exhaustion
                # so agents can catch ServerOverloadError explicitly and continue
                # with the same messages (no mutation, no compression).
                if last_error_was_5xx:
                    status_code = getattr(last_error, "status_code", None)
                    raise ServerOverloadError(
                        f"Server overload after {MAX_RETRIES} retries "
                        f"(last status={status_code}). Last error: {last_error}",
                        status_code=status_code,
                    )
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

            # Create OpenAI SDK client using SHARED httpx.AsyncClient pool.
            # Task 3: one pool per (base_url, api_key) — TLS sessions reused
            # across retries and across agent pipeline, eliminating the
            # per-call handshake burst that triggered Cloudflare 5xx gating.
            # HOTFIX (B) preserved: max_retries=0 disables SDK's hidden retry
            # loop (SDK default max_retries=2 was silently retrying timeouts,
            # compounding with our outer loop → 3×120s per external attempt).
            shared_http = _get_shared_http_client(base_url, api_key)
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=shared_http,
                timeout=REQUEST_TIMEOUT,
                max_retries=0,
            )

            # All kwargs construction now lives in a dedicated helper.
            kwargs = self._build_chat_kwargs(
                request=request,
                provider=provider,
                api_model=api_model,
                extra_params=extra_params,
                is_intermediate=is_intermediate,
            )

            # HOTFIX (A): Use extended timeout for reasoning/thinking calls.
            # We extract nested dicts safely: dict.get(key, {}) does NOT protect
            # against explicit None values, so we use explicit isinstance checks.
            _extra_body = kwargs.get("extra_body")
            _thinking_cfg = _extra_body.get("thinking") if isinstance(_extra_body, dict) else None
            _has_thinking = isinstance(_thinking_cfg, dict) and _thinking_cfg.get("type") == "enabled"
            _uses_reasoning = "reasoning_effort" in kwargs or _has_thinking

            if _uses_reasoning:
                client = client.with_options(timeout=REASONING_REQUEST_TIMEOUT)

            # === Make API call using OpenAI SDK ===
            try:
                response = await client.chat.completions.create(**kwargs)
            except (openai_sdk.APIError, httpx.HTTPError) as e:
                raise self._map_sdk_exception(e)
            return response.model_dump()

    async def _make_stream_request(
            self,
            request: LLMRequest,
            provider: APIProvider,
            endpoint: str,
            api_key: str,
            extra_params: Dict = None,
            stripped_model: str = None,
            base_url: str = None,
            is_intermediate: bool = False,
            on_delta: Optional[Callable[[str], None]] = None,
            on_reasoning_delta: Optional[Callable[[str], None]] = None,
        ) -> Dict:
            """Make an SSE streaming request and normalise it into the non-streaming schema."""
            api_model = stripped_model if stripped_model else request.model

            if not base_url:
                base_url = endpoint.rsplit("/chat/completions", 1)[0] if "/chat/completions" in endpoint else endpoint
                base_url = base_url.rsplit("/v1/chat/completions", 1)[0] if "/v1/chat/completions" in base_url else base_url

            # Transparent fallback for models/tool combinations that must preserve reasoning signatures.
            if not should_stream(provider, api_model, request.tools is not None):
                logger.warning(
                    f"Streaming disabled for {api_model} (provider={provider.value}) with tools present; "
                    f"falling back to non-streaming request"
                )
                return await self._make_request(
                    request=request,
                    provider=provider,
                    endpoint=endpoint,
                    api_key=api_key,
                    extra_params=extra_params,
                    stripped_model=stripped_model,
                    base_url=base_url,
                    is_intermediate=is_intermediate,
                )

            shared_http = _get_shared_http_client(base_url, api_key)
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=shared_http,
                timeout=REQUEST_TIMEOUT,
                max_retries=0,
            )

            kwargs = self._build_chat_kwargs(
                request=request,
                provider=provider,
                api_model=api_model,
                extra_params=extra_params,
                is_intermediate=is_intermediate,
            )
            kwargs["stream"] = True
            # надо бы удалить kwargs["stream_options"] = {"include_usage": True}

            _extra_body = kwargs.get("extra_body")
            _thinking_cfg = _extra_body.get("thinking") if isinstance(_extra_body, dict) else None
            _has_thinking = isinstance(_thinking_cfg, dict) and _thinking_cfg.get("type") == "enabled"
            _uses_reasoning = "reasoning_effort" in kwargs or _has_thinking
            if _uses_reasoning:
                client = client.with_options(timeout=REASONING_REQUEST_TIMEOUT)

            # Отступы(
           
            # ==================================================================
            # FIXED: Replaced `while True` + quiet retry with a linear
            # two-attempt flow.  On stream_options 400 the stale pool is
            # dropped FIRST so the retry uses a fresh TCP+TLS connection
            # (prevents ConnectTimeout on a half-closed socket).
            # ==================================================================

            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_call_slots: Dict[int, Dict[str, Any]] = {}
            finish_reason: Optional[str] = None
            usage: Any = None
            stream_started = False

            async def _consume_stream(stream_obj) -> None:
                """Consume one SSE stream, populating outer-scope accumulators."""
                nonlocal finish_reason, usage, stream_started
                stream_started = True
                async for chunk in stream_obj:
                    if not chunk.choices:
                        if getattr(chunk, "usage", None):
                            usage = chunk.usage
                        continue
                    choice = chunk.choices[0]
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue

                    content_piece = getattr(delta, "content", None) or ""
                    if content_piece:
                        content_parts.append(content_piece)
                        if on_delta is not None:
                            try:
                                on_delta(content_piece)
                            except Exception as cb_err:
                                logger.warning(f"on_delta callback failed: {cb_err}")

                    reasoning_piece = getattr(delta, "reasoning_content", None) or ""
                    if reasoning_piece:
                        reasoning_parts.append(reasoning_piece)
                        if on_reasoning_delta is not None:
                            try:
                                on_reasoning_delta(reasoning_piece)
                            except Exception as cb_err:
                                logger.warning(f"on_reasoning_delta callback failed: {cb_err}")

                    for tc in getattr(delta, "tool_calls", []) or []:
                        index = getattr(tc, "index", 0)
                        slot = tool_call_slots.setdefault(
                            index,
                            {"id": None, "type": "function", "name": None, "arguments": []},
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        if getattr(tc, "type", None):
                            slot["type"] = tc.type
                        func = getattr(tc, "function", None)
                        if func is not None:
                            if getattr(func, "name", None):
                                slot["name"] = func.name
                            if getattr(func, "arguments", None):
                                slot["arguments"].append(func.arguments)

                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage

            # ---------- Attempt 1: normal streaming request ----------
            try:
                async with await client.chat.completions.create(**kwargs) as stream:
                    await _consume_stream(stream)

            except openai_sdk.APIStatusError as e:
                if e.status_code == 400 and "stream_options" in str(e).lower():
                    # Provider rejected stream_options.
                    # 1) Drop the stale pool (the 400 may have left the socket
                    #    half-closed → reusing it causes ConnectTimeout).
                    # 2) Remove stream_options from kwargs.
                    # 3) Create a FRESH client on a new connection.
                    # 4) Retry ONCE (no loop).
                    logger.warning(
                        f"stream_options rejected by provider for {api_model}, "
                        f"resetting pool and retrying without stream_options: {e}"
                    )
                    client_key = _client_cache_key(base_url, api_key)
                    stale_client = _provider_clients.pop(client_key, None)
                    if stale_client is not None:
                        try:
                            await stale_client.aclose()
                        except Exception:
                            pass

                    kwargs.pop("stream_options", None)

                    # Fresh client on a new TLS connection
                    shared_http = _get_shared_http_client(base_url, api_key)
                    client = AsyncOpenAI(
                        api_key=api_key,
                        base_url=base_url,
                        http_client=shared_http,
                        timeout=REQUEST_TIMEOUT,
                        max_retries=0,
                    )
                    if _uses_reasoning:
                        client = client.with_options(timeout=REASONING_REQUEST_TIMEOUT)

                    # Reset accumulators for the clean retry
                    content_parts.clear()
                    reasoning_parts.clear()
                    tool_call_slots.clear()
                    finish_reason = None
                    usage = None
                    stream_started = False

                    # ---------- Attempt 2: retry without stream_options ----------
                    try:
                        async with await client.chat.completions.create(**kwargs) as stream:
                            await _consume_stream(stream)
                    except openai_sdk.APIStatusError as e2:
                        if e2.status_code == 400 and not stream_started:
                            logger.warning(
                                f"Streaming create failed with 400 for {api_model} "
                                f"on retry, falling back to non-streaming: {e2}"
                            )
                            return await self._make_request(
                                request=request,
                                provider=provider,
                                endpoint=endpoint,
                                api_key=api_key,
                                extra_params=extra_params,
                                stripped_model=stripped_model,
                                base_url=base_url,
                                is_intermediate=is_intermediate,
                            )
                        raise self._map_sdk_exception(e2)
                    except openai_sdk.APIError as e2:
                        raise self._map_sdk_exception(e2)

                elif e.status_code == 400 and not stream_started:
                    logger.warning(
                        f"Streaming create failed with 400 for {api_model}, "
                        f"falling back to non-streaming request: {e}"
                    )
                    return await self._make_request(
                        request=request,
                        provider=provider,
                        endpoint=endpoint,
                        api_key=api_key,
                        extra_params=extra_params,
                        stripped_model=stripped_model,
                        base_url=base_url,
                        is_intermediate=is_intermediate,
                    )
                else:
                    raise self._map_sdk_exception(e)

            except (openai_sdk.APIError, httpx.HTTPError) as e:
                raise self._map_sdk_exception(e)

            content = "".join(content_parts)
            
            assembled_tool_calls = []
            for index in sorted(tool_call_slots):
                slot = tool_call_slots[index]
                if not slot.get("id"):
                    continue
                assembled_tool_calls.append({
                    "id": slot["id"],
                    "type": slot.get("type", "function"),
                    "function": {
                        "name": slot.get("name") or "",
                        "arguments": "".join(slot["arguments"]),
                    },
                })

            if usage is None:
                usage_dump = None
            elif isinstance(usage, dict):
                usage_dump = usage
            else:
                try:
                    usage_dump = usage.model_dump()
                except Exception:
                    usage_dump = None

            message: Dict[str, Any] = {
                "role": "assistant",
                "content": content or "",
            }
            if assembled_tool_calls:
                message["tool_calls"] = assembled_tool_calls
            reasoning_content = "".join(reasoning_parts)
            if reasoning_content:
                message["reasoning_content"] = reasoning_content

            return {
                "choices": [{"message": message, "finish_reason": finish_reason}],
                "usage": usage_dump,
                "model": api_model,
            }

    def _parse_response(
        self,
        response: Dict,
        model: str,
        provider: APIProvider,
        latency_ms: float,
    ) -> LLMResponse:
        """Parse API response into standardized format"""
        # FIX: защита от None / не-dict
        if not isinstance(response, dict):
            response = {}

        # Extract content
        choices = response.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content") or ""
                
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
        else:
            delta = choice.get("delta")
            if isinstance(delta, dict) and "reasoning_details" in delta:
                reasoning_details = delta["reasoning_details"]    
        
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
        # FIX: .get("usage", {}) вернёт None если ключ есть, но значение None
        usage = response.get("usage") or {}
        input_tokens = usage.get("prompt_tokens") or 0
        output_tokens = usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens") or (input_tokens + output_tokens)

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

    def _build_chat_kwargs(
            self,
            request: LLMRequest,
            provider: APIProvider,
            api_model: str,
            extra_params: Optional[Dict],
            is_intermediate: bool,
        ) -> Dict[str, Any]:
            """Build kwargs for chat.completions.create().

            This preserves the exact behaviour of the previous inline block:
            model/messages/top_p, temperature, max_tokens, extra_params,
            reasoning_effort, GLM thinking, tools/tool_choice, OpenRouter
            headers, message preservation and DeepSeek normalisation.
            """
            # max_tokens НЕ включается сюда — добавляется условно ниже,
            # чтобы None/<=0 действительно означало «параметр не отправлять».
            kwargs: Dict[str, Any] = {
                "model": api_model,
                "messages": request.messages,
                "top_p": request.top_p,
            }

            if request.temperature is not None:
                kwargs["temperature"] = request.temperature

            # === max_tokens: значение пользователя приоритетно ===
            if request.max_tokens is not None and request.max_tokens > 0:
                effective_max_tokens = request.max_tokens
                try:
                    from config.provider_models import get_model_output_limit
                    limit = get_model_output_limit(request.model)
                    if limit and effective_max_tokens > limit:
                        logger.info(
                            f"max_tokens for {request.model}: role requested "
                            f"{effective_max_tokens}, applying user setting "
                            f"from MODEL_OUTPUT_LIMITS: {limit}"
                        )
                        effective_max_tokens = limit
                except Exception as e:
                    logger.debug(f"MODEL_OUTPUT_LIMITS lookup failed (non-fatal): {e}")
                kwargs["max_tokens"] = effective_max_tokens

            # === Handle extra_params (thinking, reasoning_effort) ===
            if extra_params:
                extra_params = dict(extra_params)  # shallow copy
            else:
                extra_params = {}

            if "reasoning_effort" not in extra_params:
                model_cfg = cfg.MODEL_CONFIGS.get(request.model, {})
                _reasoning_cfg = model_cfg.get("reasoning")
                if isinstance(_reasoning_cfg, dict):
                    extra_params["reasoning_effort"] = _reasoning_cfg.get("effort")

            # Resolve whether this (provider, model) supports reasoning_effort as direct kwarg
            supports_re = _supports_reasoning_effort(provider, api_model)

            if extra_params:
                if "thinking" in extra_params:
                    existing = kwargs.get("extra_body") or {}
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
                        existing_body = kwargs.get("extra_body") or {}
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
            try:
                user_reasoning_effort = cfg.get_user_reasoning_effort()
                if user_reasoning_effort and user_reasoning_effort != "none" and "reasoning_effort" not in extra_params:
                    if not is_intermediate and supports_re:
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
                        logger.debug(
                            f"User reasoning_effort '{user_reasoning_effort}' NOT supported by "
                            f"{request.model} (provider={provider.value}); skipped"
                        )
            except Exception:
                pass

            # === GLM glm-5-turbo thinking:disabled (for speed) when no reasoning_effort is active ===
            try:
                re_active = "reasoning_effort" in kwargs
                is_glm_turbo = api_model == "glm-5-turbo"
                if is_glm_turbo and not re_active:
                    existing_body = kwargs.get("extra_body") or {}
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

            return kwargs

    def _map_sdk_exception(self, e: Exception) -> Exception:
            """Map OpenAI SDK exceptions to the canonical retry/error hierarchy."""
            # HOTFIX (C): APITimeoutError is a SUBCLASS of APIConnectionError!
            # Must be caught FIRST to route timeouts to LLMTimeoutError
            # (separate retry cap = TIMEOUT_MAX_RETRIES) instead of generic
            # RetryableError (which uses MAX_RETRIES = 4).
            if isinstance(e, openai_sdk.RateLimitError):
                return RateLimitError(str(e))

            if isinstance(e, openai_sdk.APITimeoutError):
                return LLMTimeoutError(str(e))

            if isinstance(e, openai_sdk.APIConnectionError):
                return RetryableError(str(e))

            if isinstance(e, openai_sdk.APIStatusError):
                status_code = e.status_code

                # Task 3: status-code classification FIRST (before text
                # heuristics) — Cloudflare 520-526 and 504 were previously
                # misclassified as fatal because their text bodies
                # ("Origin Error", "Web server is down") didn't match
                # RETRYABLE_ERROR_PATTERNS.
                if status_code == 429:
                    return RateLimitError(str(e))

                if status_code in RETRYABLE_5XX_STATUS_CODES:
                    diag = _extract_5xx_diagnostics(e)
                    # Diagnostic logging for incident triage: cf-ray identifies
                    # the exact CF edge node, cf-placement shows the PoP,
                    # retry-after hints the backoff, body_preview shows the
                    # actual upstream error (often text/plain, no JSON).
                    logger.error(
                        f"5xx retryable from provider: "
                        f"status={status_code} "
                        f"cf-ray={diag.get('cf-ray')} "
                        f"cf-placement={diag.get('cf-placement')} "
                        f"server={diag.get('server')} "
                        f"retry-after={diag.get('retry-after')} "
                        f"body_preview={diag.get('body_preview', '')!r}"
                    )
                    return RetryableError(
                        str(e),
                        status_code=status_code,
                        retry_after=diag.get("retry-after-seconds"),
                    )

                # Non-5xx: fall back to text-based classification (unchanged)
                error_text = str(e)
                error_type = classify_error(error_text)
                if error_type == "rate_limit":
                    return RateLimitError(error_text)
                elif error_type == "context_overflow":
                    return ContextOverflowError(error_text)
                elif error_type == "message_structure":
                    return MessageStructureError(error_text)
                elif error_type == "retryable":
                    return RetryableError(error_text)
                else:
                    return LLMAPIError(error_text, error_type="fatal")

            # === FIX: Catch raw httpx network/transport errors that OpenAI SDK 
            # fails to wrap during SSE streaming (e.g., Cloudflare drops idle conns) ===
            if isinstance(e, httpx.TransportError):
                if isinstance(e, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
                    return LLMTimeoutError(str(e))
                return RetryableError(str(e))

            if isinstance(e, httpx.HTTPError):
                return RetryableError(str(e))

            return LLMAPIError(str(e), error_type="fatal")


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
    max_tokens: Optional[int] = 4000,
    preferred_provider: Optional[str] = None,
    is_intermediate: bool = False,
    on_delta: Optional[Callable[[str], None]] = None,
    on_reasoning_delta: Optional[Callable[[str], None]] = None,
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
        on_delta: Optional streaming callback invoked for each content delta.
        on_reasoning_delta: Optional streaming callback invoked for reasoning deltas.

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
        on_delta=on_delta,
        on_reasoning_delta=on_reasoning_delta,
        **kwargs
    )
    return response.content



async def call_llm_full(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: Optional[int] = 4000,
    preferred_provider: Optional[str] = None,
    is_intermediate: bool = False,
    on_delta: Optional[Callable[[str], None]] = None,
    on_reasoning_delta: Optional[Callable[[str], None]] = None,
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
        on_delta: Optional streaming callback invoked for each content delta.
        on_reasoning_delta: Optional streaming callback invoked for reasoning deltas.

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
        on_delta=on_delta,
        on_reasoning_delta=on_reasoning_delta,
        **kwargs
    )
    return response



async def call_llm_with_tools(
    model: str,
    messages: List[Dict[str, str]],
    tools: List[Dict],
    temperature: float = 0.0,
    max_tokens: Optional[int] = 4000,
    tool_choice: str = "auto",
    preferred_provider: Optional[str] = None,
    is_intermediate: bool = False,
    on_delta: Optional[Callable[[str], None]] = None,
    on_reasoning_delta: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Call LLM with tool support.

    Args:
        preferred_provider: Optional preferred provider name forwarded to the client.
        is_intermediate: See call_llm() docstring.
        on_delta: Optional streaming callback invoked for each content delta.
        on_reasoning_delta: Optional streaming callback invoked for reasoning deltas.

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
            on_delta=on_delta,
            on_reasoning_delta=on_reasoning_delta,
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
    """Errors that can be retried (5xx, network issues).

    Carries optional status_code and retry_after hint so _execute_with_retry
    can honour server's Retry-After header (Cloudflare/Origin) with jitter.
    Both default to None for backward compatibility with existing callers
    that raise RetryableError(str(e)) without these fields (network errors
    have no status code).
    """
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message, error_type="retryable")
        self.status_code = status_code
        self.retry_after = retry_after


class ServerOverloadError(LLMAPIError):
    """5xx errors (incl. Cloudflare 520-526) after retries exhausted.

    Subclass of LLMAPIError → existing `except Exception` handlers in agents
    keep working unchanged. User-facing agents (orchestrator/code_generator/
    tester/validator) catch this explicitly to continue with the SAME messages
    list (no mutation, no compression — the compression contract stays intact:
    only context_overflow triggers compression).

    Raised ONLY from the 5xx retry-exhaustion path in _execute_with_retry;
    timeout/rate_limit paths keep their own error types (LLMAPIError with
    error_type="timeout"/"rate_limit").
    """
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message, error_type="server_overload")
        self.status_code = status_code


class LLMTimeoutError(LLMAPIError):
    """API timeout — model was still generating on server side.

    APITimeoutError is a SUBCLASS of APIConnectionError in OpenAI SDK,
    so it MUST be caught BEFORE APIConnectionError in _make_request.
    Retried up to TIMEOUT_MAX_RETRIES times in _execute_with_retry,
    then raised as fatal LLMAPIError(error_type="timeout").
    """
    def __init__(self, message: str):
        super().__init__(message, error_type="timeout")

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


# [MOVED] — the active copy of should_stream now lives directly above class LLMClient:.
# def should_stream(provider, api_model, tools_present: bool) -> bool:
#     """Determine whether an SSE streaming call is safe for the given model.
#
#     Gemini reasoning-signature models need the full non-streaming response so
#     their ``thought_signature`` / ``reasoning_details`` round-trip is available
#     when tools are used. Any None/empty inputs are treated as streamable.
#     """
#     if not tools_present:
#         return True
#     try:
#         if api_model and cfg.is_model(api_model, cfg.MODEL_GEMINI_3_PRO):
#             return False
#     except Exception:
#         # Defensive: if model matching is unavailable, prefer the streaming path.
#         pass
#     return True



def should_stream(provider, api_model, tools_present: bool) -> bool:
    """Determine whether an SSE streaming call is safe for the given model.

    Gemini reasoning-signature models need the full non-streaming response so
    their ``thought_signature`` / ``reasoning_details`` round-trip is available
    when tools are used. Any None/empty inputs are treated as streamable.
    """
    if not tools_present:
        return True
    try:
        if api_model and cfg.is_model(api_model, cfg.MODEL_GEMINI_3_PRO):
            return False
    except Exception:
        # Defensive: if model matching is unavailable, prefer the streaming path.
        pass
    return True
