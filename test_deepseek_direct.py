import asyncio
from config.settings import cfg
from app.llm.api_client import call_llm, get_model_for_role

async def test():
    print(f"Testing model: {get_model_for_role('pre_filter')}")
    print(f"DeepSeek URL: {cfg.DEEPSEEK_BASE_URL}")
    print(f"DeepSeek key set: {bool(cfg.DEEPSEEK_API_KEY)}")
    
    try:
        response = await call_llm(
            model=cfg.MODEL_NORMAL,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=10,
        )
        print(f"Response: {response}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())