import sys
import logging
from pathlib import Path
from datetime import datetime

# ==========================================
# 1. Настройка путей импорта
# ==========================================
# Добавляем корневую директорию проекта в sys.path, чтобы видеть модуль app
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# ==========================================
# 2. Импорты модулей проекта
# ==========================================
try:
    from app.services.ai_client import AIService
    from app.utils.xml_wrapper import XMLWrapper
    # file_types.py нам нужен только как текст для отправки, импортировать его как модуль не обязательно,
    # но проверим путь к нему
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("Убедитесь, что структура проекта верна и вы запускаете скрипт из правильного окружения.")
    sys.exit(1)

# ==========================================
# 3. Настройка логирования (в файл и консоль)
# ==========================================
log_dir = BASE_DIR / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"test_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TestScript")

def main():
    logger.info("🚀 Запуск тестового скрипта...")
    
    # -------------------------------------------------
    # Шаг 1: Подготовка данных (читаем file_types.py)
    # -------------------------------------------------
    target_file_path = BASE_DIR / "app" / "utils" / "file_types.py"
    if not target_file_path.exists():
        logger.error(f"❌ Файл не найден: {target_file_path}")
        return

    try:
        file_content = target_file_path.read_text(encoding="utf-8")
        logger.info(f"📄 Файл {target_file_path.name} прочитан ({len(file_content)} символов)")
    except Exception as e:
        logger.error(f"❌ Ошибка чтения файла: {e}")
        return

    # -------------------------------------------------
    # Шаг 2: Оборачивание в XMLWrapper
    # -------------------------------------------------
    wrapper = XMLWrapper()
    # Эмулируем структуру файлов, которую ожидает wrapper
    # wrapper.wrap ожидает словарь {path: content} или список
    # Посмотрим в xml_wrapper.py (из вашего аттача): 
    # def create_context_xml(self, files_data: List[Dict[str, str]], ...):
    
    files_data = [
        {
            "path": "app/utils/file_types.py",
            "content": file_content
        }
    ]
    
    logger.info("📦 Упаковка данных в XML...")
    xml_context = wrapper.create_context_xml(
        files_data=files_data,
        instruction="Проанализируй этот файл. Повтори (напиши заново) код класса FileTypeDetector и кратко объясни, как он работает.",
        project_context="Это часть AI Assistant Pro."
    )
    
    # -------------------------------------------------
    # Шаг 3: Инициализация AI Клиента
    # -------------------------------------------------
    # Выбор провайдера: 'deepseek' или 'openrouter'
    PROVIDER = "deepseek"  # Поменяйте на "openrouter" для теста Qwen
    
    logger.info(f"🔌 Подключение к API ({PROVIDER})...")
    try:
        ai_service = AIService(provider=PROVIDER)
    except Exception as e:
        logger.critical("Не удалось создать AIService. Проверьте .env файл.")
        return

    # -------------------------------------------------
    # Шаг 4: Отправка запроса
    # -------------------------------------------------
    system_prompt = "Ты опытный Python разработчик. Твоя задача - анализировать код и объяснять его."
    
    logger.info("📤 Отправка запроса модели...")
    response_data = ai_service.send_request(
        system_prompt=system_prompt,
        user_content=xml_context
    )

    # -------------------------------------------------
    # Шаг 5: Обработка ответа
    # -------------------------------------------------
    if response_data["status"] == "success":
        content = response_data["content"]
        usage = response_data["usage"]
        
        logger.info("✅ Ответ успешно получен!")
        logger.info(f"📊 Использовано токенов: {usage.get('total_tokens')}")
        
        # -------------------------------------------------
        # Шаг 6: Сохранение результата в .md
        # -------------------------------------------------
        output_file = BASE_DIR / f"response_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        
        md_content = f"""# Отчет о тестировании AI модели

**Дата:** {datetime.now()}
**Провайдер:** {PROVIDER}
**Модель:** {ai_service.model_name}
**Файл источник:** {target_file_path.name}

## 1. Статистика
- Prompt Tokens: {usage.get('prompt_tokens')}
- Completion Tokens: {usage.get('completion_tokens')}
- Total Tokens: {usage.get('total_tokens')}

## 2. Ответ модели
{content}
"""
        try:
            output_file.write_text(md_content, encoding="utf-8")
            logger.info(f"💾 Полный отчет сохранен в: {output_file}")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения MD файла: {e}")

    else:
        logger.error(f"❌ Ошибка API: {response_data.get('error')}")

    # Закрываем клиент
    ai_service.close()
    logger.info("🏁 Тест завершен.")

if __name__ == "__main__":
    main()
