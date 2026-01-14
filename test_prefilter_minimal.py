import asyncio
import sys
import os

# Добавляем корень проекта в путь поиска модулей
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import cfg
from app.llm.api_client import call_llm, get_model_for_role

async def test():
    print("="*50)
    print("MINIMAL PRE-FILTER TEST")
    print("="*50)
    
    # 1. Проверяем какая модель
    model = get_model_for_role("pre_filter")
    print(f"\n1. Model for pre_filter: {model}")
    print(f"   Is it MODEL_NORMAL? {model == cfg.MODEL_NORMAL}")
    print(f"   MODEL_NORMAL = {cfg.MODEL_NORMAL}")
    
    # 2. Простой вызов
    print("\n2. Calling LLM with simple message...")
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply with JSON only."},
        {"role": "user", "content": 'Return this JSON: {"test": "ok"}'},
    ]
    
    import time
    start = time.time()
    
    try:
        response = await call_llm(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=100,
        )
        elapsed = time.time() - start
        
        print(f"\n3. Response received in {elapsed:.2f}s")
        print(f"   Type: {type(response)}")
        print(f"   Length: {len(response) if response else 0}")
        print(f"   Content: {repr(response)}")
        
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n3. EXCEPTION after {elapsed:.2f}s")
        print(f"   Type: {type(e).__name__}")
        print(f"   Message: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())