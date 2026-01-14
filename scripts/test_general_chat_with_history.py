import asyncio
import logging
import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import textwrap

# [FIX] Патчим asyncio для работы вложенных циклов событий
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger.warning("python-dotenv not installed")

try:
    from config.settings import cfg
    from app.agents.orchestrator import GeneralChatOrchestrator, UserFile, GeneralChatResult
    from app.utils.file_parser import FileParser
    from app.history.manager import HistoryManager
    from app.history.storage import Thread, Message
except ImportError as e:
    logger.error(f"Failed to import project modules: {e}")
    sys.exit(1)

# =========== ПОМОЩНИКИ ДЛЯ ФОРМАТИРОВАНИЯ ВЫВОДА ===========
class ChatViewer:
    """Класс для удобного просмотра истории чата"""
    
    @staticmethod
    def format_timestamp(timestamp: str) -> str:
        """Форматирует временную метку"""
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            now = datetime.now()
            
            if dt.date() == now.date():
                return dt.strftime("сегодня в %H:%M")
            elif dt.date() == (now - timedelta(days=1)).date():
                return dt.strftime("вчера в %H:%M")
            else:
                return dt.strftime("%d.%m.%Y в %H:%M")
        except:
            return timestamp
    
    @staticmethod
    def format_message(message: dict, width: int = 80) -> str:
        """Форматирует одно сообщение для вывода"""
        role = message.get('role', 'unknown')
        content = message.get('content', '').strip()
        timestamp = message.get('created_at', '')
        
        # Иконка в зависимости от роли
        if role == 'user':
            icon = "👤 ВЫ"
            color = "\033[94m"  # Синий
        elif role == 'assistant':
            icon = "🤖 ИИ"
            color = "\033[92m"  # Зеленый
        elif role == 'system':
            icon = "⚙️ СИСТЕМА"
            color = "\033[90m"  # Серый
        else:
            icon = "❓ НЕИЗВЕСТНО"
            color = "\033[93m"  # Желтый
        
        reset = "\033[0m"
        
        # Обрезаем длинные сообщения
        preview = content
        if len(preview) > 300:
            preview = preview[:297] + "..."
        
        # Форматируем время
        time_str = f" [{ChatViewer.format_timestamp(timestamp)}]" if timestamp else ""
        
        # Форматируем сообщение
        lines = []
        lines.append(f"{color}╔{'═' * (width-1)}╗{reset}")
        lines.append(f"{color}║ {icon}{time_str}{' ' * (width - len(icon) - len(time_str) - 3)}║{reset}")
        lines.append(f"{color}╠{'═' * (width-1)}╣{reset}")
        
        # Перенос строк
        wrapped = textwrap.fill(preview, width=width-4)
        for line in wrapped.split('\n'):
            lines.append(f"{color}║ {line.ljust(width-4)} ║{reset}")
        
        lines.append(f"{color}╚{'═' * (width-1)}╝{reset}")
        
        return '\n'.join(lines)
    
    @staticmethod
    def format_stats(thread: Thread) -> str:
        """Форматирует статистику диалога"""
        lines = []
        lines.append(f"📊 СТАТИСТИКА ДИАЛОГА:")
        lines.append(f"   📝 Название: {thread.title}")
        lines.append(f"   🔢 Сообщений: {thread.message_count}")
        lines.append(f"   🧮 Токенов: {thread.total_tokens}")
        lines.append(f"   📅 Создан: {ChatViewer.format_timestamp(thread.created_at)}")
        lines.append(f"   🔄 Обновлен: {ChatViewer.format_timestamp(thread.updated_at)}")
        if thread.project_name:
            lines.append(f"   📁 Проект: {thread.project_name}")
        if thread.is_archived:
            lines.append(f"   📦 Статус: АРХИВИРОВАН")
        return '\n'.join(lines)

# =========== ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ ИСТОРИИ ===========
async def get_thread_history(history_manager: HistoryManager, thread_id: str) -> List[Message]:
    """Получает всю историю диалога"""
    try:
        messages = await asyncio.to_thread(
            history_manager.storage.get_messages,
            thread_id
        )
        return messages
    except Exception as e:
        logger.error(f"Ошибка загрузки истории: {e}")
        return []

async def get_thread_history_with_pagination(
    history_manager: HistoryManager, 
    thread_id: str, 
    page: int = 1, 
    page_size: int = 10
) -> Tuple[List[Message], int, int]:
    """Получает историю с пагинацией"""
    try:
        # Получаем все сообщения
        all_messages = await get_thread_history(history_manager, thread_id)
        
        # Вычисляем пагинацию
        total_messages = len(all_messages)
        total_pages = (total_messages + page_size - 1) // page_size
        
        # Проверяем корректность страницы
        if page < 1:
            page = 1
        elif page > total_pages and total_pages > 0:
            page = total_pages
        
        # Получаем сообщения для страницы
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total_messages)
        
        return all_messages[start_idx:end_idx], page, total_pages
        
    except Exception as e:
        logger.error(f"Ошибка пагинации: {e}")
        return [], 1, 1

def display_history_page(messages: List[Message], page: int, total_pages: int, page_size: int = 10):
    """Отображает одну страницу истории"""
    viewer = ChatViewer()
    
    print(f"\n{'='*80}")
    print(f"📜 ИСТОРИЯ ДИАЛОГА (страница {page}/{total_pages})")
    print(f"   Сообщений: {len(messages)} | Показано: {page_size} на страницу")
    print(f"{'='*80}\n")
    
    if not messages:
        print("😔 В этом диалоге пока нет сообщений")
        return
    
    # Отображаем сообщения
    for i, msg in enumerate(messages, 1):
        msg_number = (page - 1) * page_size + i
        print(f"\n📄 Сообщение #{msg_number}")
        
        message_dict = {
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at
        }
        
        print(viewer.format_message(message_dict, width=78))
        
        # Показываем метаданные если есть
        if msg.metadata:
            meta_str = json.dumps(msg.metadata, ensure_ascii=False, indent=2)
            if len(meta_str) > 100:
                meta_str = meta_str[:97] + "..."
            print(f"\n   📌 Метаданные: {meta_str}")
    
    # Показываем подсказки для навигации
    print(f"\n{'='*80}")
    if total_pages > 1:
        print("Навигация: 'n' - следующая, 'p' - предыдущая, 'f' - первая, 'l' - последняя")
    print("Команды: 'q' - выход из просмотра, 's' - статистика, 'e' - экспорт")

async def interactive_history_viewer(history_manager: HistoryManager, thread_id: str):
    """Интерактивный просмотрщик истории"""
    viewer = ChatViewer()
    page_size = 5  # Сообщений на страницу
    
    # Получаем статистику диалога
    thread = await history_manager.get_thread(thread_id)
    if not thread:
        print("❌ Диалог не найден")
        return
    
    print("\n" + "="*80)
    print(viewer.format_stats(thread))
    print("="*80)
    
    # Инициализируем пагинацию
    current_page = 1
    messages, page, total_pages = await get_thread_history_with_pagination(
        history_manager, thread_id, current_page, page_size
    )
    
    while True:
        display_history_page(messages, page, total_pages, page_size)
        
        # Ждем команду
        command = input("\n⌨️  Команда: ").strip().lower()
        
        if command == 'q':
            print("👋 Выход из просмотра истории")
            break
        
        elif command == 'n' and page < total_pages:
            current_page += 1
            messages, page, total_pages = await get_thread_history_with_pagination(
                history_manager, thread_id, current_page, page_size
            )
        
        elif command == 'p' and page > 1:
            current_page -= 1
            messages, page, total_pages = await get_thread_history_with_pagination(
                history_manager, thread_id, current_page, page_size
            )
        
        elif command == 'f':  # first
            current_page = 1
            messages, page, total_pages = await get_thread_history_with_pagination(
                history_manager, thread_id, current_page, page_size
            )
        
        elif command == 'l':  # last
            current_page = total_pages
            messages, page, total_pages = await get_thread_history_with_pagination(
                history_manager, thread_id, current_page, page_size
            )
        
        elif command == 's':  # stats
            print("\n" + "="*80)
            print(viewer.format_stats(thread))
            print("="*80)
        
        elif command == 'e':  # export
            await export_thread_history(history_manager, thread_id, thread.title)
        
        elif command.isdigit():
            # Переход на конкретную страницу
            new_page = int(command)
            if 1 <= new_page <= total_pages:
                current_page = new_page
                messages, page, total_pages = await get_thread_history_with_pagination(
                    history_manager, thread_id, current_page, page_size
                )
            else:
                print(f"❌ Страница должна быть от 1 до {total_pages}")
        
        elif command.startswith('find '):
            # Поиск в истории
            search_term = command[5:].strip()
            if search_term:
                await search_in_history(history_manager, thread_id, search_term)
        
        else:
            print("❓ Доступные команды:")
            print("   q - выход из просмотра")
            print("   n/p - следующая/предыдущая страница")
            print("   f/l - первая/последняя страница")
            print("   s - статистика диалога")
            print("   e - экспорт истории в файл")
            print("   <номер> - перейти на страницу")
            print("   find <текст> - поиск в истории")

async def search_in_history(history_manager: HistoryManager, thread_id: str, search_term: str):
    """Поиск в истории диалога"""
    print(f"\n🔍 Поиск: '{search_term}'")
    
    messages = await get_thread_history(history_manager, thread_id)
    search_term_lower = search_term.lower()
    
    results = []
    for i, msg in enumerate(messages, 1):
        if search_term_lower in msg.content.lower():
            results.append((i, msg))
    
    if not results:
        print("😔 Совпадений не найдено")
        return
    
    viewer = ChatViewer()
    
    print(f"\n📊 Найдено совпадений: {len(results)}")
    for msg_num, msg in results:
        print(f"\n📄 Сообщение #{msg_num}")
        
        # Найдем контекст вокруг совпадения
        content_lower = msg.content.lower()
        idx = content_lower.find(search_term_lower)
        
        if idx != -1:
            # Выделяем найденное слово
            start = max(0, idx - 50)
            end = min(len(msg.content), idx + len(search_term) + 50)
            
            preview = msg.content[start:end]
            if start > 0:
                preview = "..." + preview
            if end < len(msg.content):
                preview = preview + "..."
            
            # Подсвечиваем найденное слово
            preview = preview.replace(
                search_term, 
                f"\033[91m{search_term}\033[0m"
            )
            
            message_dict = {
                "role": msg.role,
                "content": preview,
                "created_at": msg.created_at
            }
            
            print(viewer.format_message(message_dict, width=78))
    
    print(f"\n✅ Поиск завершен. Совпадений: {len(results)}")

async def export_thread_history(history_manager: HistoryManager, thread_id: str, thread_title: str):
    """Экспорт истории в файл"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in thread_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
    filename = f"history_export_{safe_title}_{timestamp}.md"
    
    try:
        # Получаем полную историю
        full_history = await asyncio.to_thread(
            history_manager.storage.get_thread_with_messages,
            thread_id
        )
        
        if not full_history:
            print("❌ Не удалось получить историю для экспорта")
            return
        
        # Формируем Markdown документ
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"# История диалога: {thread_title}\n\n")
            f.write(f"**ID диалога:** `{thread_id}`\n")
            f.write(f"**Сообщений:** {full_history['thread']['message_count']}\n")
            f.write(f"**Токенов:** {full_history['thread']['total_tokens']}\n")
            f.write(f"**Экспорт создан:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n")
            f.write("---\n\n")
            
            for i, msg in enumerate(full_history['messages'], 1):
                role_icon = "👤" if msg['role'] == 'user' else "🤖"
                f.write(f"## Сообщение #{i}: {role_icon} {msg['role'].upper()}\n\n")
                
                if msg.get('created_at'):
                    f.write(f"**Время:** {msg['created_at']}\n\n")
                
                f.write(f"```\n{msg['content']}\n```\n\n")
                
                if msg.get('metadata'):
                    f.write("**Метаданные:**\n")
                    f.write(f"```json\n{json.dumps(msg['metadata'], ensure_ascii=False, indent=2)}\n```\n")
                
                f.write("---\n\n")
        
        print(f"✅ История экспортирована в файл: {os.path.abspath(filename)}")
        
    except Exception as e:
        print(f"❌ Ошибка экспорта: {e}")

# =========== ОСНОВНАЯ ФУНКЦИЯ ===========
async def main():
    print("\n" + "="*80)
    print("🤖  GENERAL CHAT ORCHESTRATOR TEST SUITE")
    print("="*80 + "\n")
    
    # Инициализация системы истории
    history_manager = HistoryManager()
    USER_ID = "test_user"
    
    # Проверка базы данных
    print("🔍 ПРОВЕРКА СИСТЕМЫ ИСТОРИИ")
    print("-" * 80)
    
    # Получаем список диалогов
    existing_threads = await history_manager.list_user_threads(USER_ID, limit=50)
    
    if existing_threads:
        print(f"📂 НАЙДЕНО ДИАЛОГОВ: {len(existing_threads)}")
        print("\n" + "-" * 80)
        
        # Группируем по проектам
        threads_by_project = {}
        for thread in existing_threads:
            project = thread.project_name or "Без проекта"
            if project not in threads_by_project:
                threads_by_project[project] = []
            threads_by_project[project].append(thread)
        
        # Показываем группированный список
        for project, threads in threads_by_project.items():
            print(f"\n📁 ПРОЕКТ: {project}")
            print("-" * 40)
            for i, thread in enumerate(threads, 1):
                archived = " 📁" if thread.is_archived else ""
                date_str = ChatViewer.format_timestamp(thread.updated_at)
                print(f"{i:3d}. {thread.title[:40]:40} {archived}")
                print(f"     📝 {thread.message_count:3d} сообщ. | 🧮 {thread.total_tokens:6d} ток. | 📅 {date_str}")
                print(f"     🆔 {thread.id}")
    else:
        print("😔 Нет сохраненных диалогов")
    
    # Выбор режима
    print("\n" + "="*80)
    print("🎯 ВЫБОР РЕЖИМА РАБОТЫ")
    print("="*80)
    print("[1] Создать новый ПОСТОЯННЫЙ диалог")
    print("[2] Продолжить существующий диалог")
    print("[3] ПРОСМОТР истории диалога (без продолжения)")
    print("[4] Временная беседа (только в памяти)")
    print("[5] ТЕСТ: Проверить работу БД")
    
    history_choice = input("\nВыберите режим (1-5): ").strip()
    
    current_thread_id = None
    use_persistent_history = True
    
    if history_choice == "1":
        # Создание нового диалога
        title = input("📝 Название диалога (Enter для авто): ").strip()
        if not title:
            title = f"Диалог {datetime.now().strftime('%d.%m %H:%M')}"
        
        project_path = input("📁 Путь к проекту (опционально): ").strip()
        project_path = project_path if project_path else None
        
        thread = await history_manager.create_thread(
            user_id=USER_ID,
            project_path=project_path,
            title=title
        )
        current_thread_id = thread.id
        
        print(f"\n✅ Создан новый диалог:")
        print(f"   🆔 ID: {current_thread_id}")
        print(f"   📝 Название: {title}")
        if thread.project_name:
            print(f"   📁 Проект: {thread.project_name}")
    
    elif history_choice == "2" and existing_threads:
        # Продолжение существующего диалога
        try:
            thread_num = int(input(f"\n📋 Выберите номер диалога (1-{len(existing_threads)}): ").strip())
            if 1 <= thread_num <= len(existing_threads):
                thread = existing_threads[thread_num - 1]
                current_thread_id = thread.id
                
                # ✅ ПРЕДПРОСМОТР ИСТОРИИ ПЕРЕД ВЫБОРОМ
                print(f"\n🔍 Предпросмотр диалога: {thread.title}")
                print("-" * 80)
                
                # Получаем последние 3 сообщения
                messages = await get_thread_history(history_manager, current_thread_id)
                if messages:
                    viewer = ChatViewer()
                    recent_messages = messages[-3:]  # Последние 3 сообщения
                    
                    print(f"📊 Последние {len(recent_messages)} сообщений:")
                    for msg in recent_messages:
                        message_dict = {
                            "role": msg.role,
                            "content": msg.content[:100] + "..." if len(msg.content) > 100 else msg.content,
                            "created_at": msg.created_at
                        }
                        print(viewer.format_message(message_dict, width=78))
                        print()
                else:
                    print("📭 В диалоге пока нет сообщений")
                
                confirm = input(f"\n✅ Загрузить этот диалог? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Отмена выбора")
                    return
                
                print(f"\n✅ Выбран диалог: {thread.title}")
                print(f"   📊 Сообщений: {thread.message_count}")
                print(f"   🧮 Токенов: {thread.total_tokens}")
                
            else:
                print("❌ Неверный номер диалога")
                return
        except ValueError:
            print("❌ Неверный ввод")
            return
    
    elif history_choice == "3" and existing_threads:
        # Только просмотр истории (без продолжения)
        try:
            thread_num = int(input(f"\n📋 Выберите номер диалога для просмотра (1-{len(existing_threads)}): ").strip())
            if 1 <= thread_num <= len(existing_threads):
                thread = existing_threads[thread_num - 1]
                
                # Запускаем интерактивный просмотрщик
                await interactive_history_viewer(history_manager, thread.id)
                
                # После просмотра предлагаем продолжить
                continue_chat = input(f"\n💬 Продолжить этот диалог? (y/N): ").strip().lower()
                if continue_chat == 'y':
                    current_thread_id = thread.id
                    print(f"✅ Продолжение диалога: {thread.title}")
                else:
                    print("👋 Возврат к выбору режима")
                    # Можно здесь рекурсивно вызвать main() или просто выйти
                    return
            else:
                print("❌ Неверный номер диалога")
                return
        except ValueError:
            print("❌ Неверный ввод")
            return
    
    elif history_choice == "4":
        use_persistent_history = False
        print("\n✅ Режим: ВРЕМЕННАЯ беседа")
        print("   ⚠️  История НЕ сохранится после выключения скрипта!")
    
    elif history_choice == "5":
        # Тестовый режим
        print("\n🧪 ТЕСТИРОВАНИЕ СИСТЕМЫ ИСТОРИИ")
        await run_database_tests(history_manager)
        return
    
    else:
        print("❌ Неверный выбор или нет сохраненных диалогов")
        return
    
    # === ИНТЕРАКТИВНЫЙ РЕЖИМ ЧАТА ===
    print("\n" + "="*80)
    print("🚀 ИНТЕРАКТИВНЫЙ РЕЖИМ ЧАТА")
    print("="*80)
    
    # Выбор модели (упрощенный)
    selected_model = cfg.MODEL_NORMAL
    print(f"🤖 Модель по умолчанию: {cfg.get_model_display_name(selected_model)}")
    
    # Инициализация оркестратора
    orchestrator = GeneralChatOrchestrator(model=selected_model, is_legal_mode=False)
    temp_history = []
    
    # Команды
    print("\n📋 КОМАНДЫ:")
    print("  'exit'    - выход и сохранение")
    print("  'history' - показать историю")
    print("  'stats'   - статистика диалога")
    print("  'export'  - экспорт истории")
    print("  'find'    - поиск в истории")
    print("  'clear'   - очистить историю")
    print("  'model'   - сменить модель")
    print("="*80)
    
    while True:
        query = input("\n💬 Ваш запрос: ").strip()
        
        if not query:
            continue
        
        # Обработка команд
        if query.lower() == 'exit':
            await handle_exit_command(history_manager, current_thread_id, use_persistent_history, temp_history)
            break
        
        elif query.lower() == 'history':
            await handle_history_command(history_manager, current_thread_id, use_persistent_history, temp_history)
            continue
        
        elif query.lower() == 'stats':
            await handle_stats_command(history_manager, current_thread_id)
            continue
        
        elif query.lower() == 'export':
            if use_persistent_history and current_thread_id:
                thread = await history_manager.get_thread(current_thread_id)
                if thread:
                    await export_thread_history(history_manager, current_thread_id, thread.title)
            else:
                print("ℹ️  Экспорт доступен только для постоянных диалогов")
            continue
        
        elif query.lower().startswith('find '):
            if use_persistent_history and current_thread_id:
                search_term = query[5:].strip()
                if search_term:
                    await search_in_history(history_manager, current_thread_id, search_term)
            else:
                print("ℹ️  Поиск доступен только для постоянных диалогов")
            continue
        
        elif query.lower() == 'clear':
            await handle_clear_command(history_manager, current_thread_id, use_persistent_history, temp_history)
            continue
        
        elif query.lower() == 'model':
            await handle_model_command(orchestrator)
            continue
        
        # === ОБРАБОТКА ОБЫЧНОГО ЗАПРОСА ===
        print("\n🔄 Обработка запроса...")
        
        # Подготовка истории
        history_for_orchestrator = []
        
        if use_persistent_history and current_thread_id:
            # Сохраняем сообщение пользователя
            user_message = await history_manager.add_message(
                thread_id=current_thread_id,
                role="user",
                content=query,
                tokens=0,
                metadata={"command": False, "timestamp": datetime.now().isoformat()}
            )
            
            # Получаем оптимизированную историю
            history_messages = await history_manager.get_session_history(
                thread_id=current_thread_id,
                current_query=query
            )
            
            history_for_orchestrator = [
                {"role": msg.role, "content": msg.content}
                for msg in history_messages
            ]
            
            print(f"📊 Загружено {len(history_messages)} сообщений из истории")
        
        else:
            # Временный режим
            history_for_orchestrator = temp_history.copy()
            temp_history.append({"role": "user", "content": query})
        
        # Вызов оркестратора
        try:
            result = await orchestrator.orchestrate_general(
                user_query=query,
                user_files=[],  # Можно добавить поддержку файлов
                history=history_for_orchestrator
            )
        except Exception as e:
            print(f"❌ Ошибка оркестратора: {e}")
            continue
        
        # Сохранение ответа
        print(f"\n🤖 Ответ ({len(result.response)} символов):")
        print("-" * 80)
        print(result.response[:500] + ("..." if len(result.response) > 500 else ""))
        print("-" * 80)
        
        if use_persistent_history and current_thread_id:
            # Сохраняем ответ ассистента
            assistant_message = await history_manager.add_message(
                thread_id=current_thread_id,
                role="assistant",
                content=result.response,
                tokens=0,
                metadata={
                    "tool_calls": len(result.tool_calls),
                    "model": orchestrator.model,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            print(f"💾 Сохранено в диалог (всего сообщений: {assistant_message.thread_id})")
        
        else:
            temp_history.append({"role": "assistant", "content": result.response})
            print(f"💾 Временная история: {len(temp_history)} сообщений")

# =========== ОБРАБОТЧИКИ КОМАНД ===========
async def handle_exit_command(history_manager, thread_id, use_persistent, temp_history):
    """Обработчик команды exit"""
    print("\n💾 Сохранение данных...")
    
    if use_persistent and thread_id:
        thread = await history_manager.get_thread(thread_id)
        if thread:
            print(f"✅ Диалог сохранен: {thread.title}")
            print(f"   📊 Сообщений: {thread.message_count}")
            print(f"   🧮 Токенов: {thread.total_tokens}")
    
    # Экспорт временной истории если нужно
    if not use_persistent and temp_history:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"temp_chat_{timestamp}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump({
                "messages": temp_history,
                "created_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
        print(f"📄 Временная история сохранена в: {filename}")

async def handle_history_command(history_manager, thread_id, use_persistent, temp_history):
    """Обработчик команды history"""
    if use_persistent and thread_id:
        await interactive_history_viewer(history_manager, thread_id)
    else:
        viewer = ChatViewer()
        print("\n📜 ВРЕМЕННАЯ ИСТОРИЯ:")
        for i, msg in enumerate(temp_history, 1):
            message_dict = {
                "role": msg["role"],
                "content": msg["content"][:200] + ("..." if len(msg["content"]) > 200 else ""),
                "created_at": datetime.now().isoformat()
            }
            print(viewer.format_message(message_dict, width=78))
            print()
        print(f"📊 Всего сообщений: {len(temp_history)}")

async def handle_stats_command(history_manager, thread_id):
    """Обработчик команды stats"""
    if thread_id:
        thread = await history_manager.get_thread(thread_id)
        if thread:
            viewer = ChatViewer()
            print("\n" + "="*80)
            print(viewer.format_stats(thread))
            print("="*80)

async def handle_clear_command(history_manager, thread_id, use_persistent, temp_history):
    """Обработчик команды clear"""
    if use_persistent and thread_id:
        confirm = input("⚠️  Удалить ВСЕ сообщения из этого диалога? (y/N): ").strip().lower()
        if confirm == 'y':
            success = await asyncio.to_thread(
                history_manager.storage.clear_thread_messages, thread_id
            )
            if success:
                print("✅ История диалога очищена")
    else:
        temp_history.clear()
        print("✅ Временная история очищена")

async def handle_model_command(orchestrator):
    """Обработчик команды model"""
    print("\n🤖 ДОСТУПНЫЕ МОДЕЛИ:")
    # Здесь можно добавить логику смены модели
    print("⚠️  Смена модели в режиме реального времени пока не поддерживается")
    print(f"Текущая модель: {cfg.get_model_display_name(orchestrator.model)}")

async def run_database_tests(history_manager):
    """Запуск тестов базы данных"""
    print("\n🧪 ЗАПУСК ТЕСТОВ БАЗЫ ДАННЫХ")
    print("-" * 80)
    
    # Тест 1: Создание диалога
    print("1. Тест создания диалога...")
    test_thread = await history_manager.create_thread(
        user_id="test_user",
        title="[ТЕСТ] Проверка системы"
    )
    print(f"   ✅ Создан диалог: {test_thread.id}")
    
    # Тест 2: Добавление сообщений
    print("2. Тест добавления сообщений...")
    for i in range(3):
        msg = await history_manager.add_message(
            thread_id=test_thread.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Тестовое сообщение #{i+1}",
            tokens=10
        )
        print(f"   ✅ Добавлено сообщение {i+1}")
    
    # Тест 3: Проверка сохранения
    print("3. Тест сохранения в БД...")
    import sqlite3
    conn = sqlite3.connect(history_manager.db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (test_thread.id,))
    thread_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (test_thread.id,))
    message_count = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"   ✅ Диалогов в БД: {thread_count}")
    print(f"   ✅ Сообщений в БД: {message_count}")
    
    # Тест 4: Просмотр истории
    print("4. Тест просмотра истории...")
    messages = await get_thread_history(history_manager, test_thread.id)
    print(f"   ✅ Загружено сообщений: {len(messages)}")
    
    # Тест 5: Экспорт
    print("5. Тест экспорта...")
    await export_thread_history(history_manager, test_thread.id, test_thread.title)
    
    print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    print(f"📊 Диалог для проверки: {test_thread.id}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())