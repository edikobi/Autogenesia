import asyncio
import logging
import os
import sys
import json
from typing import List, Optional

# [FIX] Патчим asyncio для работы вложенных циклов событий (проблема с web_search)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

# Добавляем корень проекта в путь поиска, чтобы работали импорты
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Попытка загрузить переменные окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger.warning("python-dotenv not installed, assuming env vars are set")

# Импорты проекта
try:
    from config.settings import cfg
    from app.agents.orchestrator import GeneralChatOrchestrator, UserFile, GeneralChatResult
    from app.utils.file_parser import FileParser
    # Для типизации
    from app.agents.orchestrator import ToolCall
except ImportError as e:
    logger.error(f"Failed to import project modules: {e}")
    logger.error("Make sure you are running this script from the project root or 'scripts' folder and PYTHONPATH is correct.")
    sys.exit(1)


async def main():
    print("\n" + "="*60)
    print("🤖  GENERAL CHAT ORCHESTRATOR TEST SUITE")
    print("="*60 + "\n")

    # 1. ДИНАМИЧЕСКИЙ ВЫБОР МОДЕЛИ (ИСПРАВЛЕНО)
    print("Available Models:")
    
    # Получаем список доступных моделей из конфига
    available_models = cfg.get_available_orchestrator_models()
    # Убираем None, если вдруг есть
    available_models = [m for m in available_models if m]
    
    # Словарь для маппинга выбора пользователя
    model_map = {}
    
    for idx, model_id in enumerate(available_models, 1):
        display_name = cfg.get_model_display_name(model_id)
        print(f"{idx}. {display_name}")
        model_map[str(idx)] = model_id
        
    print(f"\nDefault: {cfg.get_model_display_name(cfg.MODEL_NORMAL)}")
    
    model_choice = input(f"\nEnter number (1-{len(available_models)}) or Press Enter for default: ").strip()
    
    selected_model = cfg.MODEL_NORMAL # Значение по умолчанию
    
    if model_choice:
        if model_choice in model_map:
            selected_model = model_map[model_choice]
        else:
            # [FIX] Запрещаем произвольный ввод, который приводил к ошибке "6 is not a valid model ID"
            print(f"❌ Invalid selection '{model_choice}'. Using default model.")

    print(f"✅ Selected Model: {cfg.get_model_display_name(selected_model)} ({selected_model})")

    # 2. Выбор режима
    mode_choice = input("\nSelect Mode [1=General, 2=Legal] (default=1): ").strip()
    is_legal = (mode_choice == "2")
    mode_name = "LEGAL ⚖️" if is_legal else "GENERAL 🌍"
    print(f"✅ Selected Mode: {mode_name}")

    # 3. Файлы (опционально)
    file_parser = FileParser()
    user_files: List[UserFile] = []
    
    files_input = input("\nEnter paths to files to attach (comma separated) or ENTER to skip: ").strip()
    if files_input:
        paths = [p.strip() for p in files_input.split(",")]
        parsed_files, warning = await file_parser.parse_files(paths)
        if warning:
            print(f"⚠️ Warning: {warning}")
        
        # Конвертация в UserFile для оркестратора
        for pf in parsed_files:
            if pf.error:
                print(f"❌ Failed to parse {pf.filename}: {pf.error}")
            else:
                user_files.append(UserFile(
                    filename=pf.filename,
                    content=pf.content,
                    tokens=pf.tokens,
                    file_type=pf.file_type
                ))
                print(f"📄 Attached: {pf.filename} ({pf.tokens} tokens)")

    # 4. Запрос
    query = input("\nEnter your query: ").strip()
    if not query:
        print("❌ Query cannot be empty.")
        return

    # 5. Инициализация и запуск
    print("\n" + "-"*30)
    print("🚀 STARTING ORCHESTRATION...")
    print("-"*30)

    orchestrator = GeneralChatOrchestrator(model=selected_model, is_legal_mode=is_legal)
    
    # Симуляция истории (пустая для начала)
    history = [] 

    try:
        result: GeneralChatResult = await orchestrator.orchestrate_general(
            user_query=query,
            user_files=user_files,
            history=history
        )
    except Exception as e:
        logger.error(f"Orchestration failed: {e}", exc_info=True)
        return

    # 6. Вывод результатов
    print("\n" + "="*60)
    print("🏁 ORCHESTRATION FINISHED")
    print("="*60)

    # --- Статистика API ---
    if result.tool_usage:
        print(f"\n📊 Tool Usage Stats:")
        print(f"  - Web Searches: {result.tool_usage.web_search_count}")
        print(f"  - Total Calls: {result.tool_usage.total_calls}")

    # --- Использованные инструменты (Log) ---
    print(f"\n🛠️  Tool Execution Log ({len(result.tool_calls)} calls):")
    for i, call in enumerate(result.tool_calls, 1):
        status = "✅" if call.success else "❌"
        print(f"\n  [{i}] {status} Tool: {call.name}")
        print(f"      Args: {json.dumps(call.arguments, ensure_ascii=False)}")
        
        # Вывод мыслей (Thinking) если есть
        if hasattr(call, 'thinking') and call.thinking:
            print(f"      🧠 Thinking: {call.thinking.strip()[:200]}..." ) # Первые 200 символов
        
        # Краткий вывод результата
        output_preview = (call.output[:100] + "...") if len(call.output) > 100 else call.output
        print(f"      Output: {output_preview}")

    # --- Ответ (Terminal) ---
    print("\n" + "-"*60)
    print("💬 FINAL RESPONSE (Terminal Preview):")
    print("-"*60)
    print(result.response)
    print("-"*60)

    # --- Сохранение полного отчета ---
    report_filename = "last_run_report.md"
    with open(report_filename, "w", encoding="utf-8") as f:
        f.write(f"# Orchestrator Report\n\n")
        f.write(f"**Model:** `{selected_model}`\n")
        f.write(f"**Mode:** `{mode_name}`\n")
        f.write(f"**Query:** {query}\n\n")
        
        f.write("## 🧠 Thought Process & Tools\n")
        for i, call in enumerate(result.tool_calls, 1):
            f.write(f"### Step {i}: {call.name}\n")
            if hasattr(call, 'thinking') and call.thinking:
                f.write(f"**Thinking:**\n> {call.thinking}\n\n")
            f.write(f"**Arguments:**\n``````\n")
            f.write(f"**Output:**\n``````\n\n")
        
        f.write("## 📝 Final Response\n")
        f.write(result.response)
    
    print(f"\n📄 Full report saved to: {os.path.abspath(report_filename)}")


if __name__ == "__main__":
    # Запуск асинхронного цикла
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
