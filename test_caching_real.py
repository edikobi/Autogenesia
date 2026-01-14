import asyncio
import json
import os
import sys
import logging
from unittest.mock import patch

# Добавляем путь к проекту
sys.path.append(os.getcwd())

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("CacheTest")

# Импорты
from config.settings import cfg
from app.llm.api_client import call_llm_with_tools as original_call_llm
from app.agents.orchestrator import orchestrate

TEST_FILE = "demo_cache.py"

# Список моделей для проверки
MODELS_TO_TEST = [
    cfg.MODEL_SONNET_4_5,
    cfg.MODEL_OPUS_4_5
]

# === ПОДГОТОВКА ===
def create_test_file():
    content = """
class DataProcessor:
    def process(self):
        # Dummy content for caching test
        # We repeat this to make the file heavy enough
        return True
""" * 50 
    with open(TEST_FILE, "w", encoding="utf-8") as f:
        f.write(content)

def cleanup():
    if os.path.exists(TEST_FILE):
        os.remove(TEST_FILE)

# === ПЕРЕХВАТЧИК ===
async def intercepted_call(*args, **kwargs):
    model = kwargs.get('model')
    messages = kwargs.get('messages', [])
    
    # Ищем tool messages (ответы инструментов)
    tool_msgs = [m for m in messages if m.get('role') == 'tool']
    
    if tool_msgs:
        last_msg = tool_msgs[-1]
        content = last_msg.get('content')
        
        has_cache = False
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('cache_control', {}).get('type') == 'ephemeral':
                    has_cache = True
                    break
        
        if has_cache:
            logger.info(f"✅ SUCCESS for {model}: 'cache_control' FOUND!")
        else:
            logger.info(f"❌ FAILURE for {model}: 'cache_control' NOT found.")
    
    # === РЕАЛЬНЫЙ ВЫЗОВ ===
    # Мы все равно вызываем API, чтобы цикл Оркестратора не сломался
    return await original_call_llm(*args, **kwargs)

# === ЗАПУСК ===
async def main():
    create_test_file()
    
    # Патчим один раз для всех тестов
    with patch('app.agents.orchestrator.call_llm_with_tools', side_effect=intercepted_call):
        
        for model in MODELS_TO_TEST:
            logger.info(f"\nTesting Model: {model} ...")
            try:
                await orchestrate(
                    user_query=f"Read {TEST_FILE}",
                    selected_chunks=[], 
                    compact_index="",
                    history=[],
                    orchestrator_model=model, # Подставляем текущую модель
                    project_dir=os.getcwd(),
                    index={},
                    project_map=""
                )
            except Exception as e:
                logger.error(f"Error testing {model}: {e}")

    cleanup()

if __name__ == "__main__":
    asyncio.run(main())
