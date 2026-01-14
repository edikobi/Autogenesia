# app/utils/translator.py
"""
Модуль перевода текста на русский язык через Gemini 2.0 Flash.

Используется для перевода:
- Мыслей (thinking) Оркестратора
- Объяснений кода для генератора
- Вердиктов AI Validator
- Критических замечаний

Преимущества использования LLM вместо внешних библиотек:
- Уже настроен и доступен
- Понимает технический контекст
- Стоимость ничтожна (~$0.001 за сессию)
- Сохраняет код и технические термины на английском
"""

import re
import logging
import asyncio
from typing import Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

# Флаг для отключения перевода (например, при ошибках API)
_translation_disabled = False


def is_mostly_russian(text: str, threshold: float = 0.3) -> bool:
    """
    Проверяет, что текст преимущественно на русском языке.
    
    Args:
        text: Текст для проверки
        threshold: Доля русских символов для считания текста русским
        
    Returns:
        True если текст уже на русском
    """
    if not text:
        return True
    
    # Убираем пробелы и переносы для подсчёта
    clean_text = text.replace(' ', '').replace('\n', '').replace('\t', '')
    if not clean_text:
        return True
    
    russian_chars = len(re.findall(r'[а-яА-ЯёЁ]', text))
    return russian_chars / len(clean_text) > threshold


def _contains_only_code(text: str) -> bool:
    """
    Проверяет, состоит ли текст преимущественно из кода.
    Такой текст не нужно переводить.
    """
    if not text:
        return True
    
    lines = text.strip().split('\n')
    code_indicators = 0
    
    for line in lines:
        stripped = line.strip()
        # Признаки кода
        if (stripped.startswith(('def ', 'class ', 'import ', 'from ', 'if ', 'for ', 'while ', 'return ', 'async ', '@'))
            or stripped.startswith(('#', '//', '/*', '```'))
            or stripped.endswith((':',))
            or re.match(r'^[\s\w\.\(\)\[\]\{\}=<>:,\'"+-/*%&|^~!@#$]+$', stripped)):
            code_indicators += 1
    
    # Если >70% строк выглядят как код — не переводим
    return code_indicators / len(lines) > 0.7 if lines else True


async def translate_to_russian(
    text: str,
    context: str = "AI agent reasoning",
    max_length: int = 4000
) -> str:
    """
    Переводит текст на русский язык через Gemini 2.0 Flash.
    
    Args:
        text: Текст для перевода
        context: Контекст для модели (что именно переводим)
        max_length: Максимальная длина за один запрос к API
        
    Returns:
        Переведённый текст или оригинал при ошибке/если уже на русском
    """
    global _translation_disabled
    
    # Быстрые проверки
    if not text or len(text.strip()) < 20:
        return text
    
    if _translation_disabled:
        logger.debug("Translation disabled, returning original")
        return text
    
    if is_mostly_russian(text):
        return text
    
    if _contains_only_code(text):
        return text
    
    try:
        from config.settings import cfg
        from app.llm.api_client import call_llm
        
        # Проверяем что модель доступна
        if not hasattr(cfg, 'MODEL_GEMINI_2_FLASH'):
            logger.warning("MODEL_GEMINI_2_FLASH not configured, translation disabled")
            _translation_disabled = True
            return text
        
        # Разбиваем длинный текст
        if len(text) > max_length:
            return await _translate_long_text(text, context, max_length)
        
        # Промпт для перевода
        prompt = f"""Translate this {context} to Russian.

IMPORTANT RULES:
1. Keep ALL technical terms in English: API, JSON, HTTP, SQL, async, await, etc.
2. Keep ALL code references unchanged: function names, class names, variable names, file paths
3. Keep code snippets (anything that looks like code) EXACTLY as is
4. Be natural and concise in Russian
5. Preserve formatting: line breaks, bullet points, numbered lists
6. If text contains mixed code and explanation, translate only the explanation parts

TEXT TO TRANSLATE:
{text}

RUSSIAN TRANSLATION:"""
        
        logger.debug(f"Calling Gemini Flash for translation, text length: {len(text)}")
        
        result = await call_llm(
            model=cfg.MODEL_GEMINI_2_FLASH,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,  # Низкая температура для консистентности
            max_tokens=min(len(text) * 2, 2500),  # Русский текст обычно короче
        )
        
        if result and result.strip():
            logger.debug(f"Translation successful, result length: {len(result)}")
            return result.strip()
        
        logger.warning("Translation returned empty result")
        return text
        
    except ImportError as e:
        logger.error(f"Cannot import translation dependencies: {e}")
        _translation_disabled = True
        return text
        
    except Exception as e:
        logger.error(f"Translation failed with error: {type(e).__name__}: {e}")
        # Не отключаем полностью — может быть временная ошибка
        return text

async def _translate_long_text(text: str, context: str, max_length: int) -> str:
    """
    Переводит длинный текст по частям.
    Разбивает по границам абзацев/предложений.
    """
    parts = []
    current_chunk = []
    current_len = 0
    
    # Разбиваем по абзацам
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        para_len = len(para)
        
        # Если абзац сам по себе слишком длинный — разбиваем по строкам
        if para_len > max_length:
            if current_chunk:
                chunk_text = '\n\n'.join(current_chunk)
                translated = await translate_to_russian(chunk_text, context, max_length)
                parts.append(translated)
                current_chunk = []
                current_len = 0
            
            # Переводим длинный абзац по строкам
            lines = para.split('\n')
            line_chunk = []
            line_len = 0
            
            for line in lines:
                if line_len + len(line) > max_length and line_chunk:
                    chunk_text = '\n'.join(line_chunk)
                    translated = await translate_to_russian(chunk_text, context, max_length)
                    parts.append(translated)
                    line_chunk = []
                    line_len = 0
                
                line_chunk.append(line)
                line_len += len(line) + 1
            
            if line_chunk:
                chunk_text = '\n'.join(line_chunk)
                translated = await translate_to_russian(chunk_text, context, max_length)
                parts.append(translated)
            
            continue
        
        # Обычный случай — собираем абзацы
        if current_len + para_len > max_length and current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            translated = await translate_to_russian(chunk_text, context, max_length)
            parts.append(translated)
            current_chunk = []
            current_len = 0
        
        current_chunk.append(para)
        current_len += para_len + 2  # +2 для \n\n
    
    # Остаток
    if current_chunk:
        chunk_text = '\n\n'.join(current_chunk)
        translated = await translate_to_russian(chunk_text, context, max_length)
        parts.append(translated)
    
    return '\n\n'.join(parts)


# ============================================================================
# СИНХРОННЫЕ ОБЁРТКИ ДЛЯ ИСПОЛЬЗОВАНИЯ В CALLBACK-АХ
# ============================================================================

def translate_sync(text: str, context: str = "AI agent reasoning") -> str:
    """
    Синхронная обёртка для перевода.
    Используется в callback-ах где нет возможности использовать await.
    
    Args:
        text: Текст для перевода
        context: Контекст
        
    Returns:
        Переведённый текст или оригинал
    """
    global _translation_disabled
    
    if not text or len(text.strip()) < 20:
        return text
    
    if _translation_disabled:
        return text
    
    if is_mostly_russian(text):
        return text
    
    if _contains_only_code(text):
        return text
    
    try:
        # Пробуем получить текущий event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop and loop.is_running():
            # Event loop уже запущен — создаём новый loop в отдельном потоке
            import concurrent.futures
            
            def run_in_new_loop():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(
                        translate_to_russian(text, context)
                    )
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_in_new_loop)
                try:
                    result = future.result(timeout=20)  # 20 секунд таймаут
                    return result if result else text
                except concurrent.futures.TimeoutError:
                    logger.warning("Translation timed out (20s)")
                    return text
        else:
            # Нет запущенного loop — просто запускаем
            return asyncio.run(translate_to_russian(text, context))
            
    except Exception as e:
        logger.warning(f"Sync translation failed: {e}")
        return text

# ============================================================================
# КЭШИРОВАНИЕ ДЛЯ ЧАСТО ПОВТОРЯЮЩИХСЯ ФРАЗ
# ============================================================================

@lru_cache(maxsize=200)
def translate_cached(text: str, context: str = "AI reasoning") -> str:
    """
    Кэшированный синхронный перевод.
    Полезен для повторяющихся коротких фраз (статусы, заголовки).
    
    Args:
        text: Текст для перевода
        context: Контекст
        
    Returns:
        Переведённый текст
    """
    return translate_sync(text, context)


# ============================================================================
# СПЕЦИАЛИЗИРОВАННЫЕ ФУНКЦИИ ПЕРЕВОДА
# ============================================================================

async def translate_thinking(thinking: str) -> str:
    """
    Переводит мысли (thinking) агента.
    Оптимизирован для технического контекста.
    """
    return await translate_to_russian(
        thinking,
        context="AI agent's internal reasoning about code analysis and problem solving"
    )


async def translate_validator_verdict(verdict: str) -> str:
    """
    Переводит вердикт AI Validator.
    """
    return await translate_to_russian(
        verdict,
        context="code validator's assessment and verdict"
    )


async def translate_code_explanation(explanation: str) -> str:
    """
    Переводит объяснение кода для генератора.
    """
    return await translate_to_russian(
        explanation,
        context="code explanation and instructions for code generator"
    )


async def translate_analysis(analysis: str) -> str:
    """
    Переводит анализ Оркестратора.
    """
    return await translate_to_russian(
        analysis,
        context="code analysis and architectural assessment"
    )


def reset_translation_state():
    """
    Сбрасывает состояние переводчика.
    Вызывать если нужно повторно попробовать перевод после ошибок.
    """
    global _translation_disabled
    _translation_disabled = False
    translate_cached.cache_clear()
    
async def test_translation() -> bool:
    """
    Тестирует работу переводчика.
    Вызывается для проверки что API доступен.
    
    Returns:
        True если перевод работает
    """
    global _translation_disabled
    
    test_text = "This is a test of the translation system."
    
    try:
        result = await translate_to_russian(test_text, "test")
        
        # Проверяем что результат отличается от оригинала и содержит русские буквы
        if result != test_text and is_mostly_russian(result):
            logger.info("Translation test PASSED")
            return True
        else:
            logger.warning(f"Translation test FAILED: result='{result[:50]}...'")
            return False
            
    except Exception as e:
        logger.error(f"Translation test error: {e}")
        return False


def check_translation_available() -> bool:
    """
    Быстрая синхронная проверка доступности перевода.
    Не делает API вызов, только проверяет конфигурацию.
    
    Returns:
        True если переводчик должен работать
    """
    global _translation_disabled
    
    if _translation_disabled:
        return False
    
    try:
        from config.settings import cfg
        from app.llm.api_client import call_llm
        
        # Проверяем наличие модели
        if not hasattr(cfg, 'MODEL_GEMINI_2_FLASH'):
            logger.warning("MODEL_GEMINI_2_FLASH not in config")
            return False
        
        return True
        
    except ImportError as e:
        logger.warning(f"Translation imports failed: {e}")
        return False