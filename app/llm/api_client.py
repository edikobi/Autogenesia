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

import httpx

from config.settings import cfg

# ============== LOGGING =============
logger = logging.getLogger(__name__)

# ============== CONSTANTS =============
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 8
RETRY_BASE_DELAY = 2.0  # seconds
CONCURRENT_REQUESTS = 5


# Rate limit specific settings
RATE_LIMIT_MAX_RETRIES = 5  # Больше попыток для rate limit
RATE_LIMIT_BASE_DELAY = 10.0  # Начальная задержка 10 сек
RATE_LIMIT_MAX_DELAY = 60.0  # Максимальная задержка 60 сек
# ============== DATA STRUCTURES =============
class APIProvider(Enum):
    """API providers"""
    DEEPSEEK = "deepseek"
    OPENROUTER = "openrouter"
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
    def get_connection_details(cls, model: str) -> Dict[str, Any]:
        """Получает провайдера, URL, ключ и доп. параметры из settings.py"""
        # 1. Запрашиваем конфигурацию из settings.py
        config_data = cfg.get_model_connection_config(model)

        # 2. Определяем провайдера (для статистики и логов)
        provider_name = config_data.get("provider_name", "OpenRouter")

        # 3. Пытаемся сопоставить с нашим Enum, иначе по умолчанию OpenRouter
        try:
            # Ищем совпадение значения Enum с именем провайдера (регистронезависимо)
            provider = next(p for p in APIProvider if p.value.lower() == provider_name.lower())
        except StopIteration:
            provider = APIProvider.OPENROUTER

        # 4. Возвращаем полный словарь параметров
        return {
            "provider": provider,
            "api_key": config_data["api_key"],
            "base_url": config_data["base_url"],
            # Важно: извлекаем extra_params (например, reasoning_effort, thinking)
            # Если ключа нет - вернет пустой словарь {}
            "extra_params": config_data.get("extra_params", {})
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

        Returns:
            LLMResponse with content and metadata

        Raises:
            LLMAPIError: On API errors after retries exhausted
        """
        # Determine provider and get connection details
        conn_details = ModelRouter.get_connection_details(model)
        provider = conn_details["provider"]
        api_key = conn_details["api_key"]
        extra_params = conn_details.get("extra_params", {})  # NEW: получаем extra_params

        # FIX: Проверяем наличие thinking или reasoning_effort и сбрасываем temperature
        # Это предотвращает ошибку 400 от Anthropic API
        if extra_params and ("thinking" in extra_params or "reasoning_effort" in extra_params):
            temperature = None

        # Формируем правильный URL
        base = conn_details["base_url"].rstrip("/")
        if provider == APIProvider.DEEPSEEK:
            endpoint = f"{base}/v1/chat/completions"
        else:
            # RouterAI и OpenRouter используют одинаковый путь
            endpoint = f"{base}/chat/completions"

        if not api_key:
            raise LLMAPIError(f"No API key configured for {provider.value}")

        # Build request
        request = LLMRequest(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            tool_choice=tool_choice,
        )

        # Execute with retry (NEW: передаем extra_params)
        return await self._execute_with_retry(
            request=request,
            provider=provider,
            endpoint=endpoint,
            api_key=api_key,
            extra_params=extra_params,  # NEW: передаем extra_params
        )

    async def call_with_tools(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict],
        temperature: float = 0.0,
        max_tokens: int = 4000,
        tool_choice: str = "auto",
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
        )

    async def _execute_with_retry(
        self,
        request: LLMRequest,
        provider: APIProvider,
        endpoint: str,
        api_key: str,
        extra_params: Dict = None,
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
    ) -> Dict:
        """Make HTTP request to LLM API"""
        # Build headers
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Add OpenRouter specific headers
        if provider == APIProvider.OPENROUTER:
            headers["HTTP-Referer"] = "https://ai-code-agent.local"
            headers["X-Title"] = "AI Code Agent"

        # Build request body
        body = {
            "model": request.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
        }
        
        # FIX: Добавляем temperature только если она задана (не None)
        if request.temperature is not None:
            body["temperature"] = request.temperature

        # === Обработка дополнительных параметров (extra_params) ===
        if extra_params:
            # --- Обработка параметра thinking для Claude (NEW!) ---
            # Формат Anthropic API: {"thinking": {"type": "enabled", "budget_tokens": N}}
            # При использовании thinking нельзя передавать temperature (требование API)
            if "thinking" in extra_params:
                body["thinking"] = extra_params["thinking"]
                # Удаляем temperature - несовместим с extended thinking
                if "temperature" in body:
                    del body["temperature"]
                logger.debug(
                    f"Extended thinking enabled for {request.model} "
                    f"with budget_tokens={extra_params['thinking'].get('budget_tokens', 'unlimited')}"
                )

            # --- Обработка параметра reasoning_effort для OpenAI (GPT-5.1) ---
            # При использовании reasoning_effort также нельзя передавать temperature
            if "reasoning_effort" in extra_params:
                body["reasoning_effort"] = extra_params["reasoning_effort"]
                # Удаляем temperature - несовместим с reasoning режимом
                if "temperature" in body:
                    del body["temperature"]
                logger.debug(
                    f"Reasoning effort set to '{extra_params['reasoning_effort']}' for {request.model}"
                )

        # Ensure Gemini/OpenRouter messages preserve reasoning_details for function calling
        # OpenRouter requires reasoning_details to be passed back EXACTLY as received
        # Reference: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
        for msg in body.get("messages", []):
            if msg.get("role") == "assistant":
                # Preserve reasoning_details if present (OpenRouter Gemini 3 format)
                # This is CRITICAL - missing reasoning_details causes 400 errors
                if "reasoning_details" in msg:
                    logger.debug(
                        f"Preserving reasoning_details in assistant message "
                        f"({len(msg['reasoning_details'])} items)"
                    )
                
                # Also preserve thought_signature if present (legacy/direct format)
                if msg.get("tool_calls") and "thought_signature" in msg:
                    logger.debug(
                        f"Preserving thought_signature in assistant message with tool_calls"
                    )
                
                # Preserve extra_content in tool_calls if present
                if "tool_calls" in msg:
                    for tc in msg["tool_calls"]:
                        if "extra_content" in tc:
                            logger.debug(
                                f"Preserving extra_content in tool_call {tc.get('id', 'unknown')}"
                            )

        # ==============================================================================

        # Add tools if specified
        if request.tools:
            body["tools"] = request.tools
            if request.tool_choice:
                body["tool_choice"] = request.tool_choice

        # Ensure DeepSeek messages always include reasoning_content for assistant role
        if provider == APIProvider.DEEPSEEK:
            for msg in body.get("messages", []):
                if msg.get("role") == "assistant":
                    if "reasoning_content" not in msg:
                        msg["reasoning_content"] = msg.get("content") or ""
                    if msg.get("content") is None:
                        msg["content"] = ""

        # Make request
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=body,
            )

            # Handle error responses
            if response.status_code == 429:
                raise RateLimitError(f"Rate limit exceeded: {response.text[:200]}")
            
            if response.status_code in (500, 502, 503):
                raise RetryableError(f"Server error {response.status_code}: {response.text[:200]}")
            
            if response.status_code != 200:
                # Классифицируем ошибку по содержимому
                error_text = response.text[:500]
                error_type = classify_error(error_text)
                
                if error_type == "rate_limit":
                    raise RateLimitError(f"Rate limit: {error_text}")
                elif error_type == "retryable":
                    raise RetryableError(f"Retryable error: {error_text}")
                elif error_type == "context_overflow":
                    raise ContextOverflowError(f"Context overflow: {error_text}")
                elif error_type == "message_structure":
                    raise MessageStructureError(f"Message structure error: {error_text}")
                else:
                    raise LLMAPIError(
                        f"API error {response.status_code}: {error_text}",
                        error_type="fatal"
                    )

            return response.json()


    def _parse_response(
        self,
        response: Dict,
        model: str,
        provider: APIProvider,
        latency_ms: float,
    ) -> LLMResponse:
        """Parse API response into standardized format"""
        # Extract content
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")

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
        if not thought_signature and "tool_calls" in message:
            for tc in message["tool_calls"]:
                extra_content = tc.get("extra_content", {})
                google_data = extra_content.get("google", {})
                if "thought_signature" in google_data:
                    thought_signature = google_data["thought_signature"]
                    break

        # Extract tool calls if present
        tool_calls = []
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                tool_call_data = {
                    "id": tc.get("id"),
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": tc.get("function", {}).get("name"),
                        "arguments": tc.get("function", {}).get("arguments", "{}"),
                    }
                }
                # Preserve extra_content if present (contains thought_signature for Gemini)
                if "extra_content" in tc:
                    tool_call_data["extra_content"] = tc["extra_content"]
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
        # Pricing per 1M tokens (approximate)
        pricing = {
            cfg.MODEL_OPUS_4_5: {"input": 15.0, "output": 75.0},
            cfg.MODEL_SONNET_4_5: {"input": 3.0, "output": 15.0},  # NEW: добавлен Sonnet 4.5
            cfg.MODEL_GEMINI_3_PRO: {"input": 1.25, "output": 5.0},
            cfg.MODEL_GEMINI_2_FLASH: {"input": 0.1, "output": 0.4},
            cfg.MODEL_NORMAL: {"input": 0.14, "output": 0.28},
        }
        
        # Add Qwen if configured
        if cfg.MODEL_QWEN:
            pricing[cfg.MODEL_QWEN] = {"input": 0.5, "output": 1.5}

        rates = pricing.get(model, {"input": 1.0, "output": 2.0})
        
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
    **kwargs
) -> str:
    """
    Simple function to call LLM and get response text.

    Returns:
        Response content as string
    """
    client = get_client()
    response = await client.call(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs
    )
    return response.content


async def call_llm_full(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 4000,
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
    
    Returns:
        LLMResponse object with all metadata
    """
    client = get_client()
    response = await client.call(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
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
) -> Dict[str, Any]:
    """
    Call LLM with tool support.

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
def get_model_for_role(role: str) -> str:
    """
    Get configured model for a specific agent role.

    Args:
        role: One of 'router', 'orchestrator_simple', 'orchestrator_medium',
              'orchestrator_complex', 'pre_filter', 'code_generator', 'history_compressor'

    Returns:
        Model identifier string

    Raises:
        ValueError: If role is unknown
    """
    # Special roles
    if role == "router":
        return cfg.ROUTER_MODEL
    elif role == "orchestrator_simple":
        return cfg.ORCHESTRATOR_SIMPLE_MODEL
    elif role == "orchestrator_medium":
        return cfg.ORCHESTRATOR_MEDIUM_MODEL  # NEW: добавлен medium
    elif role == "orchestrator_complex":
        return cfg.ORCHESTRATOR_COMPLEX_MODEL
    
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