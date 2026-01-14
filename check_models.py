# Временный скрипт для проверки доступных моделей
# Запусти в консоли Python или создай файл check_models.py

import requests
from config.settings import cfg

# Проверяем какие Qwen модели доступны
resp = requests.get(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}"}
)

models = resp.json().get("data", [])
qwen_models = [m["id"] for m in models if "qwen" in m["id"].lower()]

print("Доступные Qwen модели:")
for m in sorted(qwen_models):
    print(f"  {m}")