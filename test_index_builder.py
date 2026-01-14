# scripts/test_detailed_index.py
#!/usr/bin/env python3
"""
ПРОСТОЙ ТЕСТОВЫЙ СКРИПТ ДЛЯ ПРОВЕРКИ ПОДРОБНОЙ ИНДЕКСНОЙ КАРТЫ
Запуск: python scripts/test_detailed_index.py
"""

import sys
import json
import time
import os
from pathlib import Path

# Корень проекта
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

print("Введите путь к папке для индексации:")
print("Пример: .  (текущая папка)")
path = input("Путь: ").strip()

target_dir = Path(path).resolve()
print(f"Индексируем: {target_dir}")

# Теперь импортируем наши модули
try:
    # Проверяем существование модулей
    services_path = project_root / "app" / "services"
    if not services_path.exists():
        print(f"❌ Ошибка: папка app/services не найдена: {services_path}")
        sys.exit(1)
    
    # Пытаемся импортировать необходимые модули
    from app.services.detailed_index_builder import DetailedIndexBuilder
    from app.utils.token_counter import TokenCounter
    
    # Проверяем доступность API ключей
    from config.settings import cfg
    if not cfg.OPENROUTER_API_KEY:
        print("⚠️  Внимание: OPENROUTER_API_KEY не установлен в config/settings.py")
        print("   Индексация может не работать")
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("\nВозможные причины:")
    print("1. Запускайте из корня проекта: python scripts/test_detailed_index.py")
    print("2. Проверьте структуру проекта (должна быть папка app/services/)")
    print("3. Убедитесь, что все модули установлены")
    print(f"   Текущая рабочая директория: {os.getcwd()}")
    print(f"   Путь к проекту: {project_root}")
    sys.exit(1)


def select_project_directory():
    """Позволяет пользователю выбрать директорию проекта."""
    print("\n" + "="*60)
    print("📁 ВЫБОР ДИРЕКТОРИИ ДЛЯ ИНДЕКСАЦИИ")
    print("="*60)
    print()
    
    # Предлагаем несколько вариантов
    current_dir = Path.cwd()
    options = [
        ("Текущая директория", current_dir),
        ("Пример тестового проекта", current_dir / "test_project"),
        ("Корень нашего проекта", project_root),
        ("Указать другой путь", None)
    ]
    
    for i, (name, path) in enumerate(options, 1):
        if path and path.exists():
            print(f"{i}. {name}: {path}")
        else:
            print(f"{i}. {name}")
    
    print()
    
    while True:
        try:
            choice = input(f"Выберите вариант (1-{len(options)}): ").strip()
            if not choice:
                continue
                
            index = int(choice) - 1
            if 0 <= index < len(options):
                name, path = options[index]
                
                if path:  # Для предопределенных путей
                    if not path.exists() and name == "Пример тестового проекта":
                        # Создаем тестовый проект
                        create_test_project(path)
                    
                    if path.exists():
                        return path
                    else:
                        print(f"❌ Директория не существует: {path}")
                        continue
                else:  # Пользователь вводит свой путь
                    while True:
                        custom_path = input("Введите полный путь к проекту: ").strip()
                        if not custom_path:
                            print("❌ Путь не может быть пустым")
                            continue
                        
                        path = Path(custom_path).expanduser().resolve()
                        if not path.exists():
                            print(f"❌ Директория не существует: {path}")
                            print("Попробуйте снова или нажмите Ctrl+C для выхода")
                            continue
                        
                        if not path.is_dir():
                            print(f"❌ Это не директория: {path}")
                            print("Попробуйте снова или нажмите Ctrl+C для выхода")
                            continue
                        
                        return path
            else:
                print(f"❌ Неверный выбор. Введите число от 1 до {len(options)}")
                
        except ValueError:
            print("❌ Введите число")
        except KeyboardInterrupt:
            print("\n\n❌ Прервано пользователем")
            sys.exit(0)


def create_test_project(test_dir):
    """Создает тестовый проект для проверки."""
    print(f"\n📝 Создаю тестовый проект в: {test_dir}")
    
    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        
        # Создаем несколько тестовых Python файлов
        files = {
            "main.py": '''"""
Основной модуль тестового проекта.
"""

import os
import json
from datetime import datetime


APP_VERSION = "1.0.0"
DEBUG = True


class User:
    """Класс пользователя системы."""
    
    def __init__(self, username: str, email: str):
        self.username = username
        self.email = email
        self.created_at = datetime.now()
        self.is_active = True
    
    def login(self, password: str) -> bool:
        """Аутентификация пользователя по паролю."""
        # Простая проверка (в реальности должна быть сложнее)
        if password == "test123":
            self.is_active = True
            return True
        return False
    
    def logout(self) -> None:
        """Завершение сессии пользователя."""
        self.is_active = False
        print(f"Пользователь {self.username} вышел из системы")
    
    def get_info(self) -> dict:
        """Получение информации о пользователе."""
        return {
            "username": self.username,
            "email": self.email,
            "active": self.is_active
        }


class Admin(User):
    """Класс администратора, наследуется от User."""
    
    def __init__(self, username: str, email: str, role: str = "admin"):
        super().__init__(username, email)
        self.role = role
        self.permissions = ["read", "write", "delete"]
    
    def ban_user(self, user: User) -> bool:
        """Блокировка пользователя."""
        user.is_active = False
        return True


def create_user(username: str, email: str) -> User:
    """Создание нового пользователя."""
    return User(username, email)


def save_users(users: list, filename: str = "users.json") -> bool:
    """Сохранение списка пользователей в файл."""
    try:
        data = [user.get_info() for user in users]
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Ошибка сохранения: {e}")
        return False


if __name__ == "__main__":
    # Пример использования
    user1 = create_user("alice", "alice@example.com")
    admin1 = Admin("bob", "bob@example.com")
    
    print(f"Создан пользователь: {user1.username}")
    print(f"Создан администратор: {admin1.username}")
''',

            "utils/__init__.py": "# Пакет utils",

            "utils/helpers.py": '''"""
Вспомогательные функции.
"""

import hashlib
import random
import string


def generate_token(length: int = 32) -> str:
    """Генерация случайного токена."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def hash_password(password: str) -> str:
    """Хеширование пароля (упрощенное)."""
    return hashlib.sha256(password.encode()).hexdigest()


class Validator:
    """Класс для валидации данных."""
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Проверка email (простая)."""
        return '@' in email and '.' in email
    
    @staticmethod  
    def validate_password(password: str) -> bool:
        """Проверка пароля."""
        return len(password) >= 8


# Декоратор для логирования
def log_call(func):
    """Декоратор для логирования вызовов функций."""
    def wrapper(*args, **kwargs):
        print(f"Вызов функции {func.__name__}")
        return func(*args, **kwargs)
    return wrapper
''',

            "tests/__init__.py": "# Пакет тестов",

            "tests/test_auth.py": '''"""
Тесты для аутентификации.
"""

import unittest
from main import User, Admin


class TestUser(unittest.TestCase):
    """Тесты класса User."""
    
    def test_user_creation(self):
        user = User("test", "test@example.com")
        self.assertEqual(user.username, "test")
        self.assertEqual(user.email, "test@example.com")
        self.assertTrue(user.is_active)
    
    def test_login_success(self):
        user = User("test", "test@example.com")
        self.assertTrue(user.login("test123"))
    
    def test_login_failure(self):
        user = User("test", "test@example.com")
        self.assertFalse(user.login("wrong"))


class TestAdmin(unittest.TestCase):
    """Тесты класса Admin."""
    
    def test_admin_creation(self):
        admin = Admin("admin", "admin@example.com")
        self.assertEqual(admin.role, "admin")
        self.assertEqual(admin.permissions, ["read", "write", "delete"])
    
    def test_ban_user(self):
        admin = Admin("admin", "admin@example.com")
        user = User("user", "user@example.com")
        self.assertTrue(admin.ban_user(user))
        self.assertFalse(user.is_active)


if __name__ == "__main__":
    unittest.main()
'''
        }
        
        for file_path, content in files.items():
            full_path = test_dir / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            print(f"  ✅ Создан: {file_path}")
        
        print(f"\n✅ Тестовый проект создан в: {test_dir}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка создания тестового проекта: {e}")
        return False


def check_project_structure(project_dir):
    """Проверяет структуру проекта перед индексацией."""
    print(f"\n🔍 Проверяю структуру проекта...")
    
    # Проверяем наличие Python файлов
    python_files = list(project_dir.rglob("*.py"))
    if not python_files:
        print("❌ Ошибка: В проекте нет Python файлов (.py)")
        return False
    
    print(f"✅ Найдено {len(python_files)} Python файлов")
    
    # Показываем структуру
    print("\n📁 Структура проекта:")
    for file in python_files[:10]:  # Показываем первые 10 файлов
        rel_path = file.relative_to(project_dir)
        try:
            size = file.stat().st_size
            print(f"  {rel_path} ({size:,} байт)")
        except:
            print(f"  {rel_path}")
    
    if len(python_files) > 10:
        print(f"  ... и ещё {len(python_files) - 10} файлов")
    
    # Оцениваем размер в токенах
    print("\n🧮 Оцениваю размер проекта...")
    token_counter = TokenCounter()
    
    sample_size = min(5, len(python_files))
    total_tokens = 0
    
    for i in range(sample_size):
        try:
            content = python_files[i].read_text(encoding='utf-8', errors='ignore')
            tokens = token_counter.count(content)
            total_tokens += tokens
        except Exception as e:
            print(f"  ⚠️ Не удалось прочитать файл: {e}")
    
    if sample_size > 0:
        avg_tokens = total_tokens / sample_size
        estimated_total = avg_tokens * len(python_files)
        
        print(f"  Примерный размер: {estimated_total:,.0f} токенов")
        
        if estimated_total > 500000:
            print("  ⚠️  Предупреждение: Очень большой проект (>500K токенов)")
            print("     Индексация может занять много времени и денег")
        elif estimated_total > 100000:
            print("  ⚠️  Предупреждение: Большой проект (>100K токенов)")
            print("     Индексация может занять значительное время")
    else:
        print("  ⚠️ Не удалось оценить размер")
        estimated_total = 0
    
    return {
        "python_files": len(python_files),
        "estimated_tokens": estimated_total
    }


def confirm_indexing(project_dir, stats):
    """Запрашивает подтверждение начала индексации."""
    print("\n" + "="*60)
    print("🚀 ПОДТВЕРЖДЕНИЕ ИНДЕКСАЦИИ")
    print("="*60)
    print()
    print(f"📁 Проект: {project_dir}")
    print(f"📄 Python файлов: {stats['python_files']}")
    print(f"🧮 Примерный размер: {stats['estimated_tokens']:,.0f} токенов")
    print()
    print("⚠️  ВНИМАНИЕ:")
    print("   • Индексация использует API Qwen/DeepSeek")
    print("   • Может стоить денег (в зависимости от размера)")
    print("   • Может занять время")
    print()
    print("="*60)
    
    while True:
        response = input("\nНачать индексацию? (y/n): ").strip().lower()
        if response in ['y', 'yes', 'да']:
            return True
        elif response in ['n', 'no', 'нет']:
            return False
        else:
            print("❌ Введите 'y' для подтверждения или 'n' для отмены")


def build_index(project_dir):
    """Строит индексную карту для проекта."""
    print("\n" + "="*60)
    print("🏗️  НАЧАЛО ИНДЕКСАЦИИ")
    print("="*60)
    
    start_time = time.time()
    
    try:
        # Создаем строитель индекса
        print("\n1. Инициализация строителя индекса...")
        builder = DetailedIndexBuilder(str(project_dir))
        
        # Строим индекс
        print("2. Сканирование проекта...")
        print("3. Анализ методов и функций...")
        print("4. Анализ классов...")
        print("5. Формирование индексной карты...")
        print()
        
        index = builder.build_detailed_index()
        
        # Сохраняем результат
        output_file = project_dir / "detailed_index.json"
        print(f"\n💾 Сохраняю результат в: {output_file}")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        
        # Статистика
        end_time = time.time()
        duration = end_time - start_time
        
        return {
            "success": True,
            "index": index,
            "output_file": output_file,
            "duration": duration,
            "stats": index.get("statistics", {})
        }
        
    except Exception as e:
        print(f"\n❌ Ошибка при индексации: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


def show_results(results, project_dir):
    """Показывает результаты индексации."""
    if not results["success"]:
        print(f"\n❌ ИНДЕКСАЦИЯ ЗАВЕРШИЛАСЬ С ОШИБКОЙ")
        print(f"   Ошибка: {results.get('error', 'Неизвестная ошибка')}")
        return
    
    print("\n" + "="*60)
    print("✅ ИНДЕКСНАЯ КАРТА УСПЕШНО СОЗДАНА!")
    print("="*60)
    
    index = results["index"]
    duration = results["duration"]
    stats = results["stats"]
    output_file = results["output_file"]
    
    # Основная статистика
    print(f"\n📊 РЕЗУЛЬТАТЫ:")
    print(f"  📁 Проиндексировано файлов: {index.get('total_files', 0)}")
    
    # Считаем классы и методы
    total_classes = 0
    total_methods = 0
    
    for file_path, file_data in index.get("files", {}).items():
        if isinstance(file_data, dict):
            total_classes += len(file_data.get("classes", []))
            total_methods += len(file_data.get("methods", []))
    
    print(f"  🏛️  Найдено классов: {total_classes}")
    print(f"  ⚙️  Найдено методов/функций: {total_methods}")
    print(f"  🧮 Использовано токенов: {stats.get('total_tokens_analyzed', 0):,}")
    print(f"  📡 Выполнено API запросов: {stats.get('total_requests', 0)}")
    print(f"  ⏱️  Время выполнения: {duration:.2f} секунд ({duration/60:.1f} минут)")
    print(f"  💾 Файл с индексной картой: {output_file}")
    
    # Размер файла
    try:
        file_size = output_file.stat().st_size
        print(f"  📦 Размер файла: {file_size / 1024:.1f} KB")
    except:
        pass
    
    # Показываем пример данных
    print(f"\n🔍 ПРИМЕРЫ ДАННЫХ:")
    
    files = index.get("files", {})
    if files:
        # Ищем первый файл с данными
        for file_path, file_data in files.items():
            if isinstance(file_data, dict):
                print(f"\n📄 Файл: {file_path}")
                
                # Импорты
                imports = file_data.get("imports", [])
                if imports:
                    print(f"  📦 Импорты: {len(imports)}")
                    for imp in imports[:3]:
                        print(f"    • {imp}")
                    if len(imports) > 3:
                        print(f"    • ... и ещё {len(imports) - 3}")
                
                # Классы
                classes = file_data.get("classes", [])
                if classes:
                    print(f"\n  🏛️  Классы: {len(classes)}")
                    for cls in classes[:2]:
                        print(f"    • {cls.get('name', 'Без имени')}")
                        summary = cls.get('summary', '')
                        if summary:
                            print(f"      {summary[:80]}...")
                
                # Методы
                methods = file_data.get("methods", [])
                if methods:
                    print(f"\n  ⚙️  Методы/функции: {len(methods)}")
                    for method in methods[:2]:
                        name = method.get('name', 'Без имени')
                        parent = method.get('parent', '')
                        if parent:
                            name = f"{parent}.{name}"
                        
                        summary = method.get('summary', '')
                        if summary:
                            summary_preview = summary[:60] + "..." if len(summary) > 60 else summary
                            print(f"    • {name}: {summary_preview}")
                
                break  # Показываем только первый файл
    
    print(f"\n" + "="*60)
    print("🎉 ИНДЕКСАЦИЯ ЗАВЕРШЕНА УСПЕШНО!")
    print("="*60)


def check_index_file(index_file):
    """Проверяет созданную индексную карту."""
    print(f"\n🔍 ПРОВЕРКА ИНДЕКСНОЙ КАРТЫ")
    print("-"*40)
    
    if not index_file.exists():
        print(f"❌ Файл не найден: {index_file}")
        return
    
    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            index_data = json.load(f)
        
        # Базовая проверка
        print(f"✅ Файл загружен успешно")
        print(f"   Размер: {index_file.stat().st_size / 1024:.1f} KB")
        
        files_count = len(index_data.get("files", {}))
        print(f"   Файлов в индексе: {files_count}")
        
        # Проверяем структуру
        required_keys = ["project_root", "index_version", "files"]
        missing_keys = [key for key in required_keys if key not in index_data]
        
        if missing_keys:
            print(f"⚠️  Отсутствуют ключи: {missing_keys}")
        else:
            print(f"✅ Структура корректна")
        
        # Можно предложить дальнейшие действия
        print(f"\n💡 ДАЛЬНЕЙШИЕ ДЕЙСТВИЯ:")
        print(f"   1. Откройте файл для просмотра: {index_file}")
        print(f"   2. Используйте скрипт для поиска: python scripts/check_index.py {index_file}")
        
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка чтения JSON: {e}")
    except Exception as e:
        print(f"❌ Ошибка при проверке: {e}")


def main():
    """Главная функция скрипта."""
    print("\n" + "="*60)
    print("🔧 ТЕСТИРОВАНИЕ ПОДРОБНОЙ ИНДЕКСНОЙ КАРТЫ")
    print("="*60)
    print()
    print("Этот скрипт создаст подробную индексную карту для выбранного проекта.")
    print("Индексная карта будет содержать информацию о всех классах, методах,")
    print("импортах и зависимостях в проекте.")
    print()
    
    # 1. Выбираем директорию проекта
    project_dir = select_project_directory()
    
    # 2. Проверяем структуру
    stats = check_project_structure(project_dir)
    if not stats:
        print("\n❌ Проект не подходит для индексации")
        return
    
    # 3. Подтверждаем индексацию
    if not confirm_indexing(project_dir, stats):
        print("\n❌ Индексация отменена пользователем")
        return
    
    # 4. Строим индекс
    results = build_index(project_dir)
    
    # 5. Показываем результаты
    show_results(results, project_dir)
    
    # 6. Проверяем созданный файл
    if results.get("success"):
        index_file = results.get("output_file")
        check_index_file(index_file)
    
    print("\n" + "="*60)
    print("👋 СКРИПТ ЗАВЕРШЕН")
    print("="*60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Работа прервана пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)