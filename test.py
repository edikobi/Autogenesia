#!/usr/bin/env python3
"""
Проверка доступности AI моделей через OpenRouter.
Автономный скрипт - не требует импортов из проекта.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Загружаем .env из той же папки где лежит скрипт
load_dotenv(Path(__file__).parent / ".env")

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

# Получаем API ключ
API_KEY = os.getenv("OPENROUTER_API_KEY")# ============================================================================
# МОДЕЛИ ДЛЯ ТЕСТИРОВАНИЯ
# ============================================================================

MODELS_TO_TEST = [
    # OpenAI
    ("openai/gpt-5.1-codex-max", "GPT-5.1 codex"),
    ("openai/gpt-4o", "GPT-4o"),
    
    # Anthropic
    ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"),
    ("anthropic/claude-opus-4.5", "Claude Opus 4.5"),
    
    # Google
    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash"),
    ("google/gemini-3-pro-preview", "Gemini 3.0 Pro"),
    
    # DeepSeek (через OpenRouter)
    ("deepseek/deepseek-chat", "DeepSeek Chat (OpenRouter)"),
    
    # Meta
    ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
    
    # Qwen
    ("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B"),
    
    # Mistral
    ("mistralai/mistral-large-2411", "Mistral Large"),
]

# ============================================================================
# ТЕСТОВОЕ СООБЩЕНИЕ
# ============================================================================

TEST_MESSAGE = "Say 'Hello, I am working!' in exactly 5 words."

# ============================================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================================

async def test_model(
    client: httpx.AsyncClient,
    model_id: str,
    model_name: str,
    api_key: str,
) -> dict:
    """
    Тестирует одну модель через OpenRouter.
    
    Returns:
        dict: {
            "model_id": str,
            "model_name": str,
            "status": "success" | "error" | "blocked",
            "response": str | None,
            "error": str | None,
            "error_code": int | None,
            "latency_ms": int,
        }
    """
    result = {
        "model_id": model_id,
        "model_name": model_name,
        "status": "unknown",
        "response": None,
        "error": None,
        "error_code": None,
        "latency_ms": 0,
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/test",
        "X-Title": "Model Availability Test",
    }
    
    payload = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": TEST_MESSAGE}
        ],
        "max_tokens": 50,
        "temperature": 0,
    }
    
    start_time = datetime.now()
    
    try:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0,
        )
        
        latency = (datetime.now() - start_time).total_seconds() * 1000
        result["latency_ms"] = int(latency)
        
        if response.status_code == 200:
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            result["status"] = "success"
            result["response"] = content.strip()[:100]  # Первые 100 символов
            
        else:
            result["error_code"] = response.status_code
            
            try:
                error_data = response.json()
                error_msg = json.dumps(error_data, ensure_ascii=False, indent=2)
                
                # Проверяем на гео-блокировку
                error_str = str(error_data).lower()
                if "location" in error_str or "not supported" in error_str:
                    result["status"] = "blocked"
                    result["error"] = "🚫 GEO-BLOCKED: Location not supported"
                elif "rate limit" in error_str:
                    result["status"] = "rate_limited"
                    result["error"] = "⏳ Rate limited"
                elif "unauthorized" in error_str or "invalid" in error_str:
                    result["status"] = "auth_error"
                    result["error"] = "🔑 Authentication error"
                else:
                    result["status"] = "error"
                    result["error"] = error_msg[:200]
                    
            except json.JSONDecodeError:
                result["status"] = "error"
                result["error"] = response.text[:200]
                
    except httpx.TimeoutException:
        result["status"] = "timeout"
        result["error"] = "⏰ Request timed out (60s)"
        result["latency_ms"] = 60000
        
    except httpx.ConnectError as e:
        result["status"] = "connection_error"
        result["error"] = f"🔌 Connection error: {str(e)[:100]}"
        
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"❌ Unexpected error: {str(e)[:100]}"
    
    return result


async def run_all_tests():
    """Запускает тесты для всех моделей"""
    
    print("=" * 80)
    print("🔍 ПРОВЕРКА ДОСТУПНОСТИ AI МОДЕЛЕЙ ЧЕРЕЗ OPENROUTER")
    print("=" * 80)
    print(f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔑 API ключ: {'✅ установлен' if cfg.OPENROUTER_API_KEY else '❌ ОТСУТСТВУЕТ'}")
    print(f"📝 Тестовое сообщение: {TEST_MESSAGE}")
    print("=" * 80)
    
    if not cfg.OPENROUTER_API_KEY:
        print("\n❌ ОШИБКА: OPENROUTER_API_KEY не установлен в .env файле!")
        print("   Добавьте строку: OPENROUTER_API_KEY=your_key_here")
        return
    
    results = []
    
    async with httpx.AsyncClient() as client:
        for i, (model_id, model_name) in enumerate(MODELS_TO_TEST, 1):
            print(f"\n[{i}/{len(MODELS_TO_TEST)}] Тестирую: {model_name}")
            print(f"    Model ID: {model_id}")
            
            result = await test_model(
                client=client,
                model_id=model_id,
                model_name=model_name,
                api_key=cfg.OPENROUTER_API_KEY,
            )
            
            results.append(result)
            
            # Выводим результат
            status_icons = {
                "success": "✅",
                "blocked": "🚫",
                "error": "❌",
                "timeout": "⏰",
                "rate_limited": "⏳",
                "auth_error": "🔑",
                "connection_error": "🔌",
            }
            
            icon = status_icons.get(result["status"], "❓")
            
            if result["status"] == "success":
                print(f"    {icon} УСПЕХ ({result['latency_ms']}ms)")
                print(f"    📨 Ответ: {result['response']}")
            else:
                print(f"    {icon} {result['status'].upper()} (код: {result['error_code']})")
                print(f"    💬 {result['error']}")
            
            # Небольшая пауза между запросами
            await asyncio.sleep(1)
    
    # Итоговая таблица
    print("\n")
    print("=" * 80)
    print("📊 ИТОГОВАЯ ТАБЛИЦА РЕЗУЛЬТАТОВ")
    print("=" * 80)
    
    # Группируем по статусу
    success = [r for r in results if r["status"] == "success"]
    blocked = [r for r in results if r["status"] == "blocked"]
    errors = [r for r in results if r["status"] not in ("success", "blocked")]
    
    print(f"\n✅ РАБОТАЮТ ({len(success)}):")
    print("-" * 40)
    for r in success:
        print(f"   • {r['model_name']:30} ({r['latency_ms']}ms)")
    
    if blocked:
        print(f"\n🚫 ЗАБЛОКИРОВАНЫ ПО GEO ({len(blocked)}):")
        print("-" * 40)
        for r in blocked:
            print(f"   • {r['model_name']:30}")
    
    if errors:
        print(f"\n❌ ОШИБКИ ({len(errors)}):")
        print("-" * 40)
        for r in errors:
            print(f"   • {r['model_name']:30} - {r['status']}")
    
    # Рекомендации
    print("\n")
    print("=" * 80)
    print("💡 РЕКОМЕНДАЦИИ")
    print("=" * 80)
    
    if blocked:
        print("\n🚫 Для обхода гео-блокировок:")
        print("   1. Используйте VPN с сервером в США/Европе")
        print("   2. Или замените заблокированные модели на работающие")
    
    if success:
        print(f"\n✅ Рекомендуемые модели для использования:")
        # Сортируем по latency
        for r in sorted(success, key=lambda x: x["latency_ms"])[:5]:
            print(f"   • {r['model_id']}")
    
    print("\n" + "=" * 80)


# ============================================================================
# ЗАПУСК
# ============================================================================

if __name__ == "__main__":
    asyncio.run(run_all_tests())