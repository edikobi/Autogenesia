# app/history/context_manager.py
"""
Intra-Session Context Manager for Agent Mode.

Manages context window compression WITHIN a single agent session.
Different from compressor.py which handles between-session compression.

Key features:
1. Proactive compression for DeepSeek Reasoner and Gemini 3.0 Pro
2. Reactive compression for other models (only on context overflow error)
3. Preserves critical information (user requests, code blocks, recent messages)
4. Model-specific thresholds and targets

Usage:
    from app.history.context_manager import IntraSessionCompressor
    
    compressor = IntraSessionCompressor(model_id="deepseek-reasoner")
    messages = await compressor.check_and_compress(messages)
"""

from __future__ import annotations
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from config.settings import cfg
from app.utils.token_counter import TokenCounter

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS - Model-specific limits
# ============================================================================

# Models that require PROACTIVE compression (before LLM call)
PROACTIVE_COMPRESSION_MODELS: Dict[str, Dict[str, int]] = {
    # DeepSeek V3.2 Reasoner: compress at 100k → target 30k
    "deepseek-reasoner": {
        "threshold": 100000,
        "target": 30000,
    },
    # Gemini 3.0 Pro: compress at 600k → target 150k
    "google/gemini-3-pro-preview": {
        "threshold": 600000,
        "target": 150000,
    },
}

# Default target for REACTIVE compression (on error) - applies to ALL other models
REACTIVE_COMPRESSION_TARGET = 30000

# Messages to always preserve (from the end of conversation)
PRESERVE_LAST_N = 3


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class CompressionMode(Enum):
    """Compression mode indicator"""
    PROACTIVE = "proactive"  # Before LLM call (for specific models)
    REACTIVE = "reactive"    # After context overflow error


@dataclass
class CompressionResult:
    """Result of compression operation"""
    original_tokens: int
    compressed_tokens: int
    messages_before: int
    messages_after: int
    mode: CompressionMode
    model_id: str
    
    @property
    def tokens_saved(self) -> int:
        return self.original_tokens - self.compressed_tokens
    
    @property
    def compression_ratio(self) -> float:
        if self.original_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.original_tokens


# ============================================================================
# MAIN COMPRESSOR CLASS
# ============================================================================

class IntraSessionCompressor:
    """
    Manages context compression within a single agent session.
    
    For DeepSeek Reasoner and Gemini 3.0 Pro:
        - Proactively compresses when threshold is exceeded
        
    For all other models:
        - No proactive compression (they handle it themselves)
        - Reactive compression only when context overflow error occurs
    """
    
    def __init__(self, model_id: str):
        """
        Initialize compressor for a specific model.
        
        Args:
            model_id: Model identifier (e.g., "deepseek-reasoner")
        """
        self.model_id = model_id
        self.token_counter = TokenCounter()
        self._compression_count = 0
        self._total_tokens_saved = 0
        
        # Determine if this model needs proactive compression
        self._proactive_config = PROACTIVE_COMPRESSION_MODELS.get(model_id)
        
        if self._proactive_config:
            logger.info(
                f"IntraSessionCompressor: {model_id} - PROACTIVE mode "
                f"(threshold={self._proactive_config['threshold']}, "
                f"target={self._proactive_config['target']})"
            )
        else:
            logger.debug(
                f"IntraSessionCompressor: {model_id} - REACTIVE mode only "
                f"(compression on error, target={REACTIVE_COMPRESSION_TARGET})"
            )
    
    @property
    def needs_proactive_compression(self) -> bool:
        """Check if this model requires proactive compression"""
        return self._proactive_config is not None
    
    def count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        Count total tokens in messages list.
        
        Handles both string content and multimodal content (list of parts).
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.token_counter.count(content)
            elif isinstance(content, list):
                # Handle multimodal content (e.g., Claude cache format)
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += self.token_counter.count(part["text"])
                    elif isinstance(part, str):
                        total += self.token_counter.count(part)
        return total
    
    async def check_and_compress(
        self,
        messages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Optional[CompressionResult]]:
        """
        Check if proactive compression is needed and apply if so.
        
        This is the main entry point. Call BEFORE each LLM request.
        
        IMPORTANT: Only compresses for models in PROACTIVE_COMPRESSION_MODELS.
        Other models pass through unchanged.
        
        Args:
            messages: Current messages list
            
        Returns:
            Tuple of (possibly_compressed_messages, compression_result or None)
        """
        current_tokens = self.count_tokens(messages)
        
        # Log current context size for ALL models (minimal logging)
        logger.info(f"Context: {current_tokens} tokens (model: {self.model_id})")
        
        # If model doesn't need proactive compression, return unchanged
        if not self.needs_proactive_compression:
            return messages, None
        
        threshold = self._proactive_config["threshold"]
        target = self._proactive_config["target"]
        
        # Check if compression needed
        if current_tokens <= threshold:
            return messages, None
        
        # Compression needed
        logger.warning(
            f"Context threshold exceeded for {self.model_id}: "
            f"{current_tokens} > {threshold} tokens. Compressing to {target}..."
        )
        
        compressed = await self._compress_messages(
            messages=messages,
            target_tokens=target,
        )

        compressed_tokens = self.count_tokens(compressed)

        result = CompressionResult(
            original_tokens=current_tokens,
            compressed_tokens=compressed_tokens,
            messages_before=len(messages),
            messages_after=len(compressed),
            mode=CompressionMode.PROACTIVE,
            model_id=self.model_id,
        )

        
        # Update stats
        self._compression_count += 1
        self._total_tokens_saved += result.tokens_saved
        
        logger.info(
            f"Compression complete: {result.original_tokens} → {result.compressed_tokens} tokens "
            f"(saved {result.tokens_saved}, ratio {result.compression_ratio:.1%})"
        )
        
        return compressed, result
    
    async def emergency_compress(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int = REACTIVE_COMPRESSION_TARGET,
    ) -> Tuple[List[Dict[str, Any]], CompressionResult]:
        """
        Emergency compression after context overflow error.
        
        Called when API returns context overflow error.
        Applies to ANY model (reactive compression).
        
        Args:
            messages: Messages that caused the overflow
            target_tokens: Target token count (default: 30k)
            
        Returns:
            Tuple of (compressed_messages, compression_result)
        """
        current_tokens = self.count_tokens(messages)
        
        logger.warning(
            f"Emergency compression for {self.model_id}: "
            f"{current_tokens} tokens → target {target_tokens}"
        )
        
        compressed = await self._compress_messages(
            messages=messages,
            target_tokens=target_tokens,
        )

        compressed_tokens = self.count_tokens(compressed)

        result = CompressionResult(
            original_tokens=current_tokens,
            compressed_tokens=compressed_tokens,
            messages_before=len(messages),
            messages_after=len(compressed),
            mode=CompressionMode.REACTIVE,
            model_id=self.model_id,
        )

        
        # Update stats
        self._compression_count += 1
        self._total_tokens_saved += result.tokens_saved
        
        logger.info(
            f"Emergency compression complete: {result.original_tokens} → {result.compressed_tokens} tokens"
        )
        
        return compressed, result
    
    async def _compress_messages(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int,
    ) -> List[Dict[str, Any]]:        
        
        """
        Сжимает историю сообщений до целевого размера.
        
        Стратегия:
        1. Всегда сохраняем system prompt (первое сообщение)
        2. Всегда сохраняем последние N сообщений (user + assistant + tools)
        3. Средние сообщения суммаризируем через Gemini 2.0 Flash
        4. ВАЖНО: Сохраняем reasoning_content, thought_signature, reasoning_details
        для моделей типа Gemini 3.0 Pro и DeepSeek Reasoner
        
        Returns:
            (compressed_messages, tokens_removed)
        """
        if len(messages) < 4:
            return messages
        
        # Разделяем сообщения
        system_msg = messages[0] if messages[0].get("role") == "system" else None
        other_messages = messages[1:] if system_msg else messages
        
        # Определяем сколько последних сообщений сохранить (минимум 4)
        keep_recent = min(6, len(other_messages))
        
        recent_messages = other_messages[-keep_recent:]
        middle_messages = other_messages[:-keep_recent] if len(other_messages) > keep_recent else []
        
        if not middle_messages:
            return messages
        
        # Подсчитываем токены до сжатия
        original_middle_tokens = self.count_tokens(middle_messages)
        
        # Извлекаем reasoning данные из средних сообщений для сохранения контекста
        reasoning_summary = self._extract_reasoning_summary(middle_messages)
        
        # Суммаризируем средние сообщения
        summary = await self._summarize_messages(middle_messages, reasoning_summary)
        
        # Собираем результат
        compressed = []
        
        if system_msg:
            compressed.append(system_msg)
        
        # Добавляем summary как user message
        compressed.append({
            "role": "user",
            "content": f"[CONTEXT SUMMARY - Previous conversation compressed]\n\n{summary}"
        })
        
        # Добавляем recent messages (они сохраняют все поля как есть)
        compressed.extend(recent_messages)
        
        # Подсчитываем сэкономленные токены
        summary_tokens = self.token_counter.count(summary)
        tokens_saved = original_middle_tokens - summary_tokens
        
        return compressed

    def _extract_reasoning_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        Извлекает краткую сводку reasoning из сообщений.
        Нужно для сохранения контекста мышления модели.
        """
        reasoning_parts = []
        
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            
            # Собираем reasoning content
            reasoning = msg.get("reasoning_content", "")
            if reasoning:
                # Берём только первые 200 символов каждого reasoning
                short_reasoning = reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
                reasoning_parts.append(f"- {short_reasoning}")
            
            # Также учитываем tool calls если есть
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tools_used = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls]
                reasoning_parts.append(f"- Used tools: {', '.join(tools_used)}")
        
        if reasoning_parts:
            return "Key reasoning steps:\n" + "\n".join(reasoning_parts[:5])  # Макс 5 пунктов
        
        return ""

    async def _summarize_messages(
        self,
        messages: List[Dict[str, Any]],
        reasoning_summary: str = ""
    ) -> str:
        """
        Суммаризирует список сообщений через LLM.
        """
        # Форматируем сообщения для суммаризации
        formatted = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if role == "tool":
                tool_name = msg.get("name", "unknown")
                # Сокращаем длинные tool results
                if len(content) > 500:
                    content = content[:500] + "... [truncated]"
                formatted.append(f"[Tool: {tool_name}] {content}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tools = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                    formatted.append(f"[Assistant called: {', '.join(tools)}] {content[:200]}")
                else:
                    formatted.append(f"[Assistant] {content[:300]}")
            else:
                formatted.append(f"[{role.title()}] {content[:300]}")
        
        conversation_text = "\n".join(formatted)
        
        # Добавляем reasoning summary если есть
        extra_context = ""
        if reasoning_summary:
            extra_context = f"\n\n{reasoning_summary}"
        
        summary_prompt = f"""Summarize this conversation history concisely. 
    Focus on: 1) What was asked, 2) What tools were used, 3) Key findings.
    Keep it under 300 words.
    {extra_context}

    Conversation:
    {conversation_text}

    Summary:"""
        
        try:
            from app.llm.api_client import call_llm
            
            summary = await call_llm(
                model=cfg.MODEL_GEMINI_2_FLASH,
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0,
                max_tokens=500,
            )
            
            return summary.strip()
            
        except Exception as e:
            logger.warning(f"Summarization failed: {e}, using fallback")
            # Fallback: просто список действий
            actions = []
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    tools = [tc.get("function", {}).get("name") for tc in msg["tool_calls"]]
                    actions.append(f"Called: {', '.join(tools)}")
            
            if actions:
                return f"Previous actions: {'; '.join(actions[:5])}"
            return "Previous conversation context (details compressed)"    
    
    
    def _compress_tool_result(self, msg: Dict[str, Any]) -> str:
        """
        Compress a tool result message.
        
        Strategy:
        - For read_file: Keep file path + first/last few lines
        - For search_code: Keep file paths + function names
        - For web_search: Keep titles + brief snippets
        """
        content = msg.get("content", "")
        tool_name = msg.get("name", "tool")
        
        # Handle multimodal content (Claude cache format)
        if isinstance(content, list):
            # Extract text from first text block
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    content = part["text"]
                    break
            else:
                return "[PRUNED] Tool result (multimodal format)"
        
        if not isinstance(content, str):
            return "[PRUNED] Tool result"
        
        # Count tokens in original
        original_tokens = self.token_counter.count(content)
        
        # If small enough, keep as-is
        if original_tokens < 500:
            return content
        
        # === Compression based on tool type ===
        
        # read_file: Extract file path and key lines
        if tool_name == "read_file" or "<!-- File:" in content:
            return self._compress_read_file_result(content, tool_name)
        
        # read_code_chunk: Similar to read_file
        if tool_name == "read_code_chunk":
            return self._compress_read_file_result(content, tool_name)
        
        # search_code: Keep file paths and function names
        if tool_name == "search_code":
            return self._compress_search_result(content)
        
        # web_search: Keep titles and brief context
        if tool_name == "web_search":
            return self._compress_web_search_result(content)
        
        # Default: truncate with ellipsis
        return self._truncate_content(content, max_chars=1000, add_marker=True)
    
    def _compress_read_file_result(self, content: str, tool_name: str) -> str:
        """Compress read_file tool result"""
        lines = content.split('\n')
        
        # Extract metadata from XML comments
        file_info = ""
        for line in lines[:5]:
            if "<!-- File:" in line or "<!-- Type:" in line:
                file_info += line + "\n"
        
        # If we found file info, create summary
        if file_info:
            # Count lines of actual content
            content_lines = [l for l in lines if not l.startswith("<!--")]
            return (
                f"[PRUNED] {tool_name} result:\n"
                f"{file_info}"
                f"[Content: {len(content_lines)} lines, analyzed earlier]"
            )
        
        # Fallback: just show tool name and size
        return f"[PRUNED] {tool_name}: {len(lines)} lines of code analyzed"
    
    def _compress_search_result(self, content: str) -> str:
        """Compress search_code result"""
        lines = content.split('\n')
        
        # Extract file paths and function names
        files_found = []
        for line in lines:
            # Look for file paths
            if "File:" in line or ".py" in line or ".js" in line:
                # Clean up the line
                clean = line.strip()[:100]
                if clean and clean not in files_found:
                    files_found.append(clean)
                    if len(files_found) >= 10:
                        break
        
        if files_found:
            return (
                f"[PRUNED] search_code results:\n"
                + "\n".join(f"  • {f}" for f in files_found[:10])
                + (f"\n  ... and more" if len(files_found) > 10 else "")
            )
        
        return f"[PRUNED] search_code: {len(lines)} lines of results"
    
    def _compress_web_search_result(self, content: str) -> str:
        """Compress web_search result"""
        # Extract titles from search results
        titles = re.findall(r'<title>([^<]+)</title>', content)
        
        if titles:
            return (
                f"[PRUNED] web_search results ({len(titles)} pages):\n"
                + "\n".join(f"  • {t[:80]}" for t in titles[:5])
                + (f"\n  ... and {len(titles) - 5} more" if len(titles) > 5 else "")
            )
        
        return f"[PRUNED] web_search: Results analyzed"
    
    def _compress_assistant_content(self, content: str) -> str:
        """
        Compress assistant message content.
        
        Preserves code blocks, truncates reasoning.
        """
        if not content:
            return content
        
        # Check for code blocks
        triple_backticks = chr(96) * 3
        if triple_backticks in content:
            # Has code blocks - preserve them, truncate surrounding text
            return self._preserve_code_blocks(content)
        
        # No code blocks - can truncate more aggressively
        if len(content) > 2000:
            return self._truncate_content(content, max_chars=1500, add_marker=True)
        
        return content
    
    def _preserve_code_blocks(self, content: str, max_reasoning_chars: int = 500) -> str:
        """
        Preserve code blocks while truncating surrounding reasoning.
        """
        triple_backticks = chr(96) * 3
        parts = content.split(triple_backticks)
        
        result_parts = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # This is text between code blocks (reasoning)
                if len(part) > max_reasoning_chars:
                    truncated = part[:max_reasoning_chars//2] + "\n[...reasoning truncated...]\n" + part[-max_reasoning_chars//2:]
                    result_parts.append(truncated)
                else:
                    result_parts.append(part)
            else:
                # This is code block content - preserve
                result_parts.append(part)
        
        return triple_backticks.join(result_parts)
    
    def _truncate_content(
        self, 
        content: str, 
        max_chars: int = 2000,
        add_marker: bool = False,
    ) -> str:
        """Truncate content to max characters"""
        if len(content) <= max_chars:
            return content
        
        # Keep beginning and end
        half = max_chars // 2
        truncated = content[:half] + "\n\n[...truncated...]\n\n" + content[-half:]
        
        if add_marker and not truncated.startswith("[COMPRESSED]"):
            truncated = "[COMPRESSED] " + truncated
        
        return truncated
    
    def _aggressive_truncate(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int,
    ) -> List[Dict[str, Any]]:
        """
        Aggressively truncate messages to reach target.
        
        Removes oldest non-essential messages first.
        """
        current_tokens = self.count_tokens(messages)
        
        # Find removable messages (not system, not in last PRESERVE_LAST_N)
        removable_indices = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                continue
            if i >= len(messages) - PRESERVE_LAST_N:
                continue
            removable_indices.append(i)
        
        # Remove from oldest first
        result = messages.copy()
        for idx in removable_indices:
            if current_tokens <= target_tokens:
                break
            
            # Check if index is still valid after previous removals
            adjusted_idx = idx - (len(messages) - len(result))
            if 0 <= adjusted_idx < len(result) - PRESERVE_LAST_N:
                removed_msg = result.pop(adjusted_idx)
                current_tokens = self.count_tokens(result)
                logger.debug(
                    f"Removed message {adjusted_idx} ({removed_msg.get('role')}), "
                    f"now at {current_tokens} tokens"
                )
        
        return result
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get compression statistics"""
        return {
            "model_id": self.model_id,
            "mode": "proactive" if self.needs_proactive_compression else "reactive_only",
            "compression_count": self._compression_count,
            "total_tokens_saved": self._total_tokens_saved,
        }


# ============================================================================
# CONTEXT OVERFLOW ERROR DETECTION
# ============================================================================

def is_context_overflow_error(error: Exception) -> bool:
    """
    Check if an exception is a context overflow error.
    
    Detects various error messages from different providers:
    - OpenAI/OpenRouter: "context_length_exceeded", "maximum context length"
    - Anthropic: "prompt is too long", "max tokens"
    - DeepSeek: "context length", "too many tokens"
    - Google: "exceeds the maximum", "context window"
    
    Args:
        error: The exception to check
        
    Returns:
        True if this is a context overflow error
    """
    error_str = str(error).lower()
    
    overflow_indicators = [
        "context_length_exceeded",
        "context length",
        "maximum context length",
        "context window",
        "too many tokens",
        "token limit",
        "exceeds the maximum",
        "prompt is too long",
        "max_tokens",
        "input too long",
        "request too large",
        "content too large",
    ]
    
    return any(indicator in error_str for indicator in overflow_indicators)


# ============================================================================
# MODULE-LEVEL CACHE FOR COMPRESSORS
# ============================================================================

_compressor_cache: Dict[str, IntraSessionCompressor] = {}


def get_compressor(model_id: str) -> IntraSessionCompressor:
    """
    Get or create an IntraSessionCompressor for a model.
    
    Caches compressors to preserve stats across calls within a session.
    """
    if model_id not in _compressor_cache:
        _compressor_cache[model_id] = IntraSessionCompressor(model_id)
    return _compressor_cache[model_id]


def reset_compressor(model_id: str) -> None:
    """Reset compressor for a model (new session)"""
    if model_id in _compressor_cache:
        del _compressor_cache[model_id]


def reset_all_compressors() -> None:
    """Reset all cached compressors"""
    _compressor_cache.clear()


# ============================================================================
# CONVENIENCE FUNCTION FOR ORCHESTRATOR
# ============================================================================

async def maybe_compress_context(
    messages: List[Dict[str, Any]],
    model_id: str,
) -> List[Dict[str, Any]]:
    """
    Convenience function: compress messages if needed for model.
    
    Use this in orchestrator before each LLM call.
    
    Args:
        messages: Messages to potentially compress
        model_id: Target model ID
        
    Returns:
        Possibly compressed messages (original if no compression needed)
    """
    compressor = get_compressor(model_id)
    compressed, _ = await compressor.check_and_compress(messages)
    return compressed