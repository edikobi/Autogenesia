# scripts/test_context_compression.py
"""
Тестовый скрипт для проверки сжатия контекста в Agent Mode.

Особенности:
- Делает РЕАЛЬНЫЕ API вызовы для всех моделей
- Для Gemini 3.0 Pro получает настоящие thought_signature
- Использует сниженные лимиты (threshold=1000, target=500) для быстрого тестирования
"""

import asyncio
import sys
import os
import logging
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Добавляем корень проекта в path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import cfg
from app.llm.api_client import call_llm_with_tools
from app.utils.token_counter import TokenCounter
from app.history.context_manager import (
    IntraSessionCompressor,
    get_compressor,
    is_context_overflow_error,
    CompressionResult,
    CompressionMode,
)

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================================
# TOKEN COUNTER
# ============================================================================

_token_counter = TokenCounter()

def count_tokens(text: str) -> int:
    """Подсчёт токенов в строке."""
    return _token_counter.count(text)

def count_messages_tokens(messages: List[Dict]) -> int:
    """Подсчёт токенов во всех сообщениях."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += count_tokens(part["text"])
    return total

# ============================================================================
# TEST CONFIGURATION
# ============================================================================

# Модели для тестирования
TEST_MODELS = {
    "gpt_codex": cfg.MODEL_GPT_5_1_Codex_MAX,
    "deepseek_reasoner": cfg.MODEL_DEEPSEEK_REASONER,
    "gemini_3_pro": cfg.MODEL_GEMINI_3_PRO,
}

# Тестовые лимиты (сильно снижены)
TEST_LIMITS = {
    cfg.MODEL_DEEPSEEK_REASONER: {"threshold": 1000, "target": 500},
    cfg.MODEL_GEMINI_3_PRO: {"threshold": 1000, "target": 500},
}

# Простые инструменты для теста
TEST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "Get information about a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "File name"}
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_code",
            "description": "Analyze code structure",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code to analyze"}
                },
                "required": ["code"]
            }
        }
    }
]

# ============================================================================
# TEST COMPRESSOR WITH LOW LIMITS
# ============================================================================

class TestCompressor(IntraSessionCompressor):
    """Компрессор с пониженными лимитами для тестирования."""
    
    def __init__(self, model: str, threshold: int, target: int):
        super().__init__(model)
        self.threshold_tokens = threshold
        self.target_tokens = target
        self._proactive_config = {"threshold": threshold, "target": target}
        logger.info(f"TestCompressor for {model}: threshold={threshold}, target={target}")


def get_test_compressor(model: str) -> IntraSessionCompressor:
    """Получить компрессор с тестовыми лимитами."""
    if model in TEST_LIMITS:
        limits = TEST_LIMITS[model]
        return TestCompressor(model, limits["threshold"], limits["target"])
    return get_compressor(model)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def create_tool_result(iteration: int) -> str:
    """Создаёт результат инструмента (~200-300 токенов)."""
    return f"""
# File: module_{iteration}.py
# Analysis results for iteration {iteration}

class Handler{iteration}:
    '''Handles processing for component {iteration}.'''
    
    def __init__(self, config: dict):
        self.config = config
        self.cache = {{}}
        self.initialized = False
        
    def process(self, data: list) -> dict:
        '''Process incoming data and return results.'''
        results = {{}}
        for item in data:
            key = f"item_{{item['id']}}"
            results[key] = self._transform(item)
        return results
        
    def _transform(self, item: dict) -> dict:
        '''Internal transformation logic.'''
        return {{
            "original": item,
            "processed": True,
            "timestamp": "2024-01-{iteration:02d}"
        }}

def helper_function_{iteration}(x: int, y: int) -> int:
    '''Calculate result for iteration {iteration}.'''
    return x * y + {iteration}

# Constants
MAX_ITEMS_{iteration} = 1000
DEFAULT_TIMEOUT_{iteration} = 30
"""


def log_messages_summary(messages: List[Dict], label: str = "") -> int:
    """Логирует краткую статистику по сообщениям."""
    total_tokens = count_messages_tokens(messages)
    tool_count = len([m for m in messages if m.get("role") == "tool"])
    assistant_count = len([m for m in messages if m.get("role") == "assistant"])
    
    # Проверяем наличие thought_signature
    thought_sig_count = len([
        m for m in messages 
        if m.get("role") == "assistant" and m.get("thought_signature")
    ])
    
    logger.info(f"{label} {len(messages)} msgs, {tool_count} tool, {assistant_count} assistant, ~{total_tokens} tokens")
    if thought_sig_count > 0:
        logger.info(f"{label} thought_signature present in {thought_sig_count} messages")
    
    return total_tokens


def extract_assistant_fields(response: Dict) -> Dict[str, Any]:
    """Извлекает все важные поля из ответа assistant."""
    # Гарантируем что content не пустой (Gemini требует parts)
    content = response.get("content") or ""
    
    # Если content пустой и нет tool_calls — добавляем placeholder
    if not content and not response.get("tool_calls"):
        content = "I'll continue analyzing."
    
    fields = {
        "role": "assistant",
        "content": content,
    }
    
    # Tool calls
    if response.get("tool_calls"):
        fields["tool_calls"] = response["tool_calls"]
    
    # Reasoning fields (важно для Gemini и DeepSeek!)
    for field in ["thought_signature", "reasoning_content", "reasoning_details"]:
        if response.get(field):
            fields[field] = response[field]
    
    return fields

# ============================================================================
# CORE TEST: BUILD CONTEXT WITH REAL API CALLS
# ============================================================================

async def build_real_context(
    model: str,
    num_iterations: int = 5,
    target_tokens: int = 2000,
) -> List[Dict[str, Any]]:
    """
    Строит контекст через РЕАЛЬНЫЕ API вызовы.
    
    Это гарантирует что thought_signature и другие поля — настоящие.
    
    Args:
        model: ID модели
        num_iterations: Минимальное количество итераций
        target_tokens: Целевой размер контекста в токенах
    
    Returns:
        Список сообщений с реальными данными от API
    """
    messages = [
        {
            "role": "system",
            "content": "You are a code analysis assistant. When asked to analyze files, use the provided tools. Be concise in responses."
        },
        {
            "role": "user",
            "content": "Analyze the Python modules in my project. Start by getting info about the first few files."
        }
    ]
    
    iteration = 0
    
    while iteration < num_iterations or count_messages_tokens(messages) < target_tokens:
        iteration += 1
        current_tokens = count_messages_tokens(messages)
        
        logger.info(f"  Iteration {iteration}: {current_tokens} tokens, {len(messages)} messages")
        
        # Защита от бесконечного цикла
        if iteration > 15:
            logger.warning("  Max iterations reached, stopping")
            break
        
        try:
            # Делаем реальный API вызов
            response = await call_llm_with_tools(
                model=model,
                messages=messages,
                tools=TEST_TOOLS,
                temperature=0,
                max_tokens=300,
            )
            
            # Извлекаем все поля (включая thought_signature!)
            assistant_msg = extract_assistant_fields(response)
            messages.append(assistant_msg)
            
            # Логируем что получили
            has_thought_sig = "thought_signature" in assistant_msg
            has_tool_calls = "tool_calls" in assistant_msg
            logger.info(f"    Got response: thought_sig={has_thought_sig}, tool_calls={has_tool_calls}")
            
            # Если есть tool calls — добавляем результаты
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    tool_result = {
                        "role": "tool",
                        "tool_call_id": tc.get("id", f"call_{iteration}"),
                        "name": tc.get("function", {}).get("name", "unknown"),
                        "content": create_tool_result(iteration)
                    }
                    messages.append(tool_result)
            else:
                # Модель не вызвала инструмент
                # Проверяем что последний assistant message имеет content
                if messages[-1].get("role") == "assistant" and not messages[-1].get("content"):
                    messages[-1]["content"] = "I understand. Let me continue."
                
                # Добавляем user message чтобы продолжить
                messages.append({
                    "role": "user",
                    "content": f"Now analyze module_{iteration}.py using the get_file_info tool."
                })            
            
            # Небольшая пауза между вызовами
            is_gemini = "gemini" in model.lower()
            delay = 3.0 if is_gemini else 0.5
            await asyncio.sleep(delay)
            
        except Exception as e:
            logger.error(f"    API error at iteration {iteration}: {e}")
            # Проверяем на context overflow
            if is_context_overflow_error(e):
                logger.warning("    Context overflow detected, stopping build")
                break
            raise
    
    logger.info(f"  Built context: {len(messages)} messages, ~{count_messages_tokens(messages)} tokens")
    return messages


# ============================================================================
# TEST CASES
# ============================================================================

async def test_no_proactive_compression(model: str, model_name: str) -> bool:
    """
    Тест модели БЕЗ проактивного сжатия (GPT-5.1).
    Проверяем что сжатие НЕ происходит даже при большом контексте.
    """
    logger.info("=" * 70)
    logger.info(f"TEST: {model_name} - NO proactive compression")
    logger.info("=" * 70)
    
    # Строим контекст через реальные вызовы
    logger.info("Building context with real API calls...")
    messages = await build_real_context(model, num_iterations=3, target_tokens=1500)
    
    original_count = len(messages)
    original_tokens = log_messages_summary(messages, "BEFORE:")
    
    # Получаем компрессор (обычный, не тестовый!)
    compressor = get_compressor(model)
    
    # Проверяем что эта модель НЕ в списке проактивного сжатия
    logger.info(f"Needs proactive compression: {compressor.needs_proactive_compression}")
    
    # Пробуем сжать
    compressed_messages, result = await compressor.check_and_compress(messages)
    
    if result:
        logger.warning(f"⚠️ Unexpected compression: {result.original_tokens} → {result.compressed_tokens}")
        return False
    
    logger.info("✅ No compression happened (as expected)")
    
    # Проверяем что сообщения не изменились
    if len(compressed_messages) != original_count:
        logger.error(f"❌ Message count changed: {original_count} → {len(compressed_messages)}")
        return False
    
    # Финальный API вызов для проверки
    logger.info("Making final API call...")
    try:
        response = await call_llm_with_tools(
            model=model,
            messages=compressed_messages,
            tools=TEST_TOOLS,
            temperature=0,
            max_tokens=200,
        )
        logger.info(f"✅ Final API call successful!")
        logger.info(f"   Response: {response.get('content', '')[:100]}...")
        return True
        
    except Exception as e:
        logger.error(f"❌ Final API call failed: {e}")
        return False


async def test_proactive_compression(model: str, model_name: str) -> bool:
    """
    Тест модели С проактивным сжатием (DeepSeek Reasoner, Gemini 3.0 Pro).
    
    1. Строим контекст через реальные API вызовы (получаем настоящие thought_signature)
    2. Применяем сжатие
    3. Проверяем что API работает после сжатия
    """
    logger.info("=" * 70)
    logger.info(f"TEST: {model_name} - Proactive compression")
    logger.info("=" * 70)
    
    # Строим контекст через реальные вызовы
    logger.info("Building context with real API calls...")
    messages = await build_real_context(model, num_iterations=5, target_tokens=2000)
    
    original_count = len(messages)
    original_tokens = log_messages_summary(messages, "BEFORE:")
    
    # Проверяем что достаточно токенов для теста
    if original_tokens < 1000:
        logger.warning(f"⚠️ Context too small ({original_tokens} < 1000), compression won't trigger")
        logger.info("   Adding more messages...")
        
        # Добавляем ещё сообщений
        messages = await build_real_context(model, num_iterations=8, target_tokens=2500)
        original_tokens = log_messages_summary(messages, "EXTENDED:")
    
    # Получаем тестовый компрессор с низкими лимитами
    compressor = get_test_compressor(model)
    logger.info(f"Test limits: threshold={compressor.threshold_tokens}, target={compressor.target_tokens}")
    
    # Применяем сжатие
    compressed_messages, result = await compressor.check_and_compress(messages)
    
    if not result:
        logger.error(f"❌ Compression did not happen! Tokens: {original_tokens}")
        return False
    
    logger.info(f"✅ Compression happened!")
    logger.info(f"   Original: {result.original_tokens} tokens, {result.messages_before} messages")
    logger.info(f"   Compressed: {result.compressed_tokens} tokens, {result.messages_after} messages")
    logger.info(f"   Saved: {result.tokens_saved} tokens ({100 - result.compression_ratio*100:.1f}%)")
    
    # Показываем структуру сжатого контекста
    logger.info("Compressed context structure:")
    for i, msg in enumerate(compressed_messages[:6]):
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:50].replace("\n", " ")
        has_ts = "✓TS" if msg.get("thought_signature") else ""
        has_tc = "✓TC" if msg.get("tool_calls") else ""
        logger.info(f"   [{i}] {role}: {content}... {has_ts} {has_tc}")
    
    if len(compressed_messages) > 6:
        logger.info(f"   ... and {len(compressed_messages) - 6} more messages")
    
    # КРИТИЧЕСКИЙ ТЕСТ: API вызов после сжатия
    logger.info("")
    logger.info("🔥 CRITICAL TEST: API call after compression...")
    
    try:
        response = await call_llm_with_tools(
            model=model,
            messages=compressed_messages,
            tools=TEST_TOOLS,
            temperature=0,
            max_tokens=300,
        )
        
        logger.info(f"✅ API call SUCCESSFUL after compression!")
        logger.info(f"   Response: {response.get('content', '')[:100]}...")
        
        # Проверяем что можем продолжить диалог
        if response.get("tool_calls"):
            logger.info("   Model made tool calls, testing continuation...")
            
            # Добавляем ответ
            assistant_msg = extract_assistant_fields(response)
            compressed_messages.append(assistant_msg)
            
            # Добавляем tool results
            for tc in response["tool_calls"]:
                compressed_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", "test"),
                    "name": tc.get("function", {}).get("name", "unknown"),
                    "content": "File analysis complete. Found 5 functions and 2 classes."
                })
            
            # Ещё один вызов
            response2 = await call_llm_with_tools(
                model=model,
                messages=compressed_messages,
                tools=TEST_TOOLS,
                temperature=0,
                max_tokens=200,
            )
            
            logger.info(f"✅ Continuation also successful!")
            logger.info(f"   Response: {response2.get('content', '')[:80]}...")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ API call FAILED after compression!")
        logger.error(f"   Error: {e}")
        
        if is_context_overflow_error(e):
            logger.error("   This is a context overflow error")
        
        # Проверяем на thought_signature ошибку
        if "thought_signature" in str(e).lower():
            logger.error("   ⚠️ thought_signature issue detected!")
            logger.error("   Checking compressed messages for thought_signature...")
            
            for i, msg in enumerate(compressed_messages):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    has_ts = "YES" if msg.get("thought_signature") else "NO"
                    logger.error(f"      [{i}] assistant with tool_calls: thought_signature={has_ts}")
        
        return False


async def test_emergency_compression(model: str, model_name: str) -> bool:
    """
    Тест аварийного сжатия.
    """
    logger.info("=" * 70)
    logger.info(f"TEST: {model_name} - Emergency compression")
    logger.info("=" * 70)
    
    # Строим большой контекст
    logger.info("Building large context...")
    messages = await build_real_context(model, num_iterations=6, target_tokens=2500)
    
    original_tokens = log_messages_summary(messages, "BEFORE EMERGENCY:")
    
    # Принудительное аварийное сжатие
    compressor = get_test_compressor(model)
    compressed_messages, result = await compressor.emergency_compress(messages)
    
    logger.info(f"Emergency compression result:")
    logger.info(f"   Original: {result.original_tokens} tokens")
    logger.info(f"   Compressed: {result.compressed_tokens} tokens")
    logger.info(f"   Saved: {result.tokens_saved} tokens")
    
    log_messages_summary(compressed_messages, "AFTER EMERGENCY:")
    
    # Проверяем что API работает
    logger.info("Testing API after emergency compression...")
    try:
        response = await call_llm_with_tools(
            model=model,
            messages=compressed_messages,
            tools=TEST_TOOLS,
            temperature=0,
            max_tokens=200,
        )
        logger.info(f"✅ API call successful after emergency compression!")
        return True
        
    except Exception as e:
        logger.error(f"❌ API call failed: {e}")
        return False

# ============================================================================
# MAIN TEST RUNNER
# ============================================================================

async def run_all_tests():
    """Запуск всех тестов."""
    
    print("\n" + "=" * 70)
    print("🧪 CONTEXT COMPRESSION TEST SUITE (Real API Calls)")
    print(f"   Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")
    
    results = {}
    
    # =========================================================================
    # TEST 1: GPT-5.1 Codex Max - НЕ должна сжиматься
    # =========================================================================
    print("\n" + "🔵" * 35)
    print("PHASE 1: GPT-5.1 Codex Max (NO compression)")
    print("🔵" * 35 + "\n")
    
    try:
        results["gpt_codex"] = await test_no_proactive_compression(
            TEST_MODELS["gpt_codex"],
            "GPT-5.1 Codex Max"
        )
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results["gpt_codex"] = False
    
    await asyncio.sleep(2)
    
    # =========================================================================
    # TEST 2: DeepSeek Reasoner - ДОЛЖНА сжиматься
    # =========================================================================
    print("\n" + "🟡" * 35)
    print("PHASE 2: DeepSeek V3.2 Reasoner (WITH compression)")
    print("🟡" * 35 + "\n")
    
    try:
        results["deepseek_reasoner"] = await test_proactive_compression(
            TEST_MODELS["deepseek_reasoner"],
            "DeepSeek V3.2 Reasoner"
        )
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results["deepseek_reasoner"] = False
    
    await asyncio.sleep(2)
    
    # =========================================================================
    # TEST 3: Gemini 3.0 Pro - ДОЛЖНА сжиматься (с thought_signature!)
    # =========================================================================
    print("\n" + "🟢" * 35)
    print("PHASE 3: Gemini 3.0 Pro (WITH compression + thought_signature)")
    print("🟢" * 35 + "\n")
    
    try:
        results["gemini_3_pro"] = await test_proactive_compression(
            TEST_MODELS["gemini_3_pro"],
            "Gemini 3.0 Pro"
        )
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results["gemini_3_pro"] = False
    
    await asyncio.sleep(2)
    
    # =========================================================================
    # TEST 4: Emergency Compression
    # =========================================================================
    print("\n" + "🔴" * 35)
    print("PHASE 4: Emergency Compression (DeepSeek)")
    print("🔴" * 35 + "\n")
    
    try:
        results["emergency"] = await test_emergency_compression(
            TEST_MODELS["deepseek_reasoner"],
            "DeepSeek V3.2 Reasoner"
        )
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results["emergency"] = False
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 70)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 70)
    
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"   {test_name:25} : {status}")
    
    total_passed = sum(1 for v in results.values() if v)
    total_tests = len(results)
    
    print("-" * 70)
    print(f"   TOTAL: {total_passed}/{total_tests} tests passed")
    
    if total_passed == total_tests:
        print("\n🎉 ALL TESTS PASSED! Compression system is reliable.")
    else:
        print("\n⚠️  Some tests failed. Check logs above for details.")
    
    print("=" * 70 + "\n")
    
    return all(results.values())

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🚀 Context Compression Test Suite")
    print("   Using REAL API calls for all models")
    print("=" * 70)
    
    # Проверяем API ключи
    print("\n🔑 Checking API keys...")
    
    keys_status = {
        "ROUTERAI_API_KEY": bool(cfg.ROUTERAI_API_KEY),
        "DEEPSEEK_API_KEY": bool(cfg.DEEPSEEK_API_KEY),
        "OPENROUTER_API_KEY": bool(cfg.OPENROUTER_API_KEY),
    }
    
    all_keys_ok = True
    for key, present in keys_status.items():
        status = "✅" if present else "❌"
        print(f"   {status} {key}")
        if not present:
            all_keys_ok = False
    
    if not all_keys_ok:
        print("\n⚠️  Some API keys are missing!")
        print("   Set them in .env file.")
        sys.exit(1)
    
    # Показываем модели
    print("\n📋 Test models:")
    for key, model in TEST_MODELS.items():
        display_name = cfg.get_model_display_name(model)
        print(f"   {key}: {display_name}")
    
    # Показываем лимиты
    print("\n⚙️  Test compression limits:")
    for model, limits in TEST_LIMITS.items():
        display_name = cfg.get_model_display_name(model)
        print(f"   {display_name}:")
        print(f"      threshold: {limits['threshold']} tokens")
        print(f"      target: {limits['target']} tokens")
    
    # Предупреждение о стоимости
    print("\n💰 NOTE: This test makes ~20-30 real API calls.")
    print("   Estimated cost: ~$0.10-0.50 depending on models.")
    
    print("\n" + "-" * 70)
    input("Press Enter to start tests...")
    
    success = asyncio.run(run_all_tests())
    
    sys.exit(0 if success else 1)