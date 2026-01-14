import json
import logging
from typing import Dict, Any, Optional, List
from openai import OpenAI  # Используем стандартный клиент OpenAI
import httpx # Для обработки сетевых ошибок на низком уровне

# Импортируем конфиг из вашего проекта
# Предполагается, что ai_client.py лежит в app/services/
try:
    from config.settings import cfg
except ImportError:
    # Фоллбек для запуска не из корня проекта (хотя так делать не рекомендуется)
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
    from config.settings import cfg

# Настройка логгера (он будет наследовать настройки из вызывающего скрипта)
logger = logging.getLogger(__name__)

class AIService:
    """
    Универсальный сервис для работы с LLM через API, совместимое с OpenAI.
    Поддерживает DeepSeek (Official) и OpenRouter (Qwen и др.).
    """
    
    PROVIDER_DEEPSEEK = "deepseek"
    PROVIDER_OPENROUTER = "openrouter"

    def __init__(self, provider: str = PROVIDER_DEEPSEEK):
        """
        Инициализация клиента в зависимости от выбранного провайдера.
        """
        self.provider = provider
        self.client: Optional[OpenAI] = None
        self.model_name: str = ""

        try:
            if provider == self.PROVIDER_DEEPSEEK:
                if not cfg.DEEPSEEK_API_KEY:
                    raise ValueError("DEEPSEEK_API_KEY не найден в настройках")
                
                self.client = OpenAI(
                    api_key=cfg.DEEPSEEK_API_KEY,
                    base_url=cfg.DEEPSEEK_BASE_URL
                )
                self.model_name = cfg.MODEL_NORMAL # обычно deepseek-chat
                logger.info(f"✅ AIService инициализирован: провайдер DEEPSEEK, модель {self.model_name}")

            elif provider == self.PROVIDER_OPENROUTER:
                if not cfg.OPENROUTER_API_KEY:
                    raise ValueError("OPENROUTER_API_KEY не найден в настройках")
                
                self.client = OpenAI(
                    api_key=cfg.OPENROUTER_API_KEY,
                    base_url=cfg.OPENROUTER_BASE_URL
                )
                # Если модель не задана в конфиге, берем дефолтную Qwen
                self.model_name = cfg.MODEL_QWEN if cfg.MODEL_QWEN else "qwen/qwen-2.5-coder-32b-instruct"
                logger.info(f"✅ AIService инициализирован: провайдер OPENROUTER, модель {self.model_name}")
            
            else:
                raise ValueError(f"Неизвестный провайдер: {provider}")

        except Exception as e:
            logger.critical(f"❌ Критическая ошибка инициализации AIService: {e}")
            raise e

    def send_request(self, system_prompt: str, user_content: str, temperature: float = 0.7) -> Dict[str, Any]:
        """
        Отправляет запрос к модели и возвращает сырой ответ или ошибку.
        Не делает парсинг JSON/Markdown, просто возвращает текст.
        
        Args:
            system_prompt: Инструкция для модели
            user_content: Контент пользователя (обычно обернутый в XML)
            temperature: Креативность (0.0 - 1.0)
            
        Returns:
            Dict: {
                "status": "success" | "error",
                "content": str (ответ модели),
                "error": str (если есть),
                "usage": Dict (токены)
            }
        """
        if not self.client:
            return {"status": "error", "error": "Клиент не инициализирован"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        try:
            logger.debug(f"📤 Отправка запроса к {self.model_name}...")
            
            # Стандартный вызов chat completions
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                stream=False # Для начала без стриминга для простоты
            )
            
            # Извлекаем контент
            content = response.choices[0].message.content
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
            
            logger.info(f"📥 Ответ получен. Токенов: {usage['total_tokens']}")
            return {
                "status": "success",
                "content": content,
                "usage": usage
            }

        except httpx.APIStatusError as e:
            error_msg = f"Ошибка API (HTTP {e.status_code}): {e.message}"
            logger.error(f"❌ {error_msg}")
            return {"status": "error", "error": error_msg}
            
        except httpx.RequestError as e:
            error_msg = f"Ошибка сети: {e}"
            logger.error(f"❌ {error_msg}")
            return {"status": "error", "error": error_msg}
            
        except Exception as e:
            error_msg = f"Непредвиденная ошибка: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return {"status": "error", "error": error_msg}

    def close(self):
        """Закрывает соединение клиента"""
        if self.client:
            self.client.close()
            logger.info("AIService закрыт")
