# maintestchunk.py
import json
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.panel import Panel
from rich.prompt import Prompt

from app.services.project_scanner import ProjectScanner
from app.utils.token_counter import TokenCounter
from app.utils.file_types import FileTypeDetector

console = Console()

# Расширения бинарных файлов (не считаем токены)
BINARY_EXTENSIONS = {
    ".dat", ".exe", ".dll", ".so", ".pyc", ".pyo", ".pyd",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3"
}


def get_project_path() -> str:
    """
    Определяет путь к проекту для сканирования.
    Приоритет:
      1. Аргумент командной строки: python maintestchunk.py "C:\путь\к\проекту"
      2. Интерактивный ввод от пользователя
      3. Текущая директория (если пользователь нажал Enter)
    """
    # 1. Проверяем аргумент командной строки
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if Path(path).is_dir():
            return str(Path(path).resolve())
        else:
            console.print(f"[red]❌ Путь не найден: {path}[/red]")
            sys.exit(1)
    
    # 2. Интерактивный ввод
    console.print("[bold cyan]🔍 Выберите директорию для сканирования[/bold cyan]\n")
    console.print(f"   Текущая директория: [dim]{Path.cwd()}[/dim]")
    console.print(f"   Нажмите [green]Enter[/green] чтобы использовать её, или введите путь:\n")
    
    user_input = Prompt.ask("   Путь к проекту", default=str(Path.cwd()))
    
    path = Path(user_input).resolve()
    if path.is_dir():
        return str(path)
    else:
        console.print(f"[red]❌ Директория не существует: {path}[/red]")
        sys.exit(1)


def count_file_tokens(file_path: Path, token_counter: TokenCounter) -> int:
    """Подсчитывает токены в файле с обработкой ошибок кодировки."""
    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return 0
    
    try:
        text = file_path.read_text(encoding="utf-8")
        return token_counter.count(text)
    except (UnicodeDecodeError, PermissionError, OSError):
        # Пробуем другие кодировки
        for encoding in ["latin-1", "cp1251", "cp1252"]:
            try:
                text = file_path.read_text(encoding=encoding)
                return token_counter.count(text)
            except:
                continue
        return 0


def get_file_icon(ftype: str) -> str:
    """Возвращает иконку для типа файла."""
    icons = {
        # Код
        "code/python": "🐍",
        "code/go": "🔵",
        "code/javascript": "🟨",
        "code/typescript": "🔷",
        "code/java": "☕",
        "code/c": "🔧",
        "code/cpp": "⚙️",
        "code/csharp": "🟣",
        "code/rust": "🦀",
        "code/ruby": "💎",
        "code/php": "🐘",
        "code/swift": "🍎",
        "code/kotlin": "🟠",
        "code/scala": "🔴",
        # Данные
        "data/json": "📋",
        "data/yaml": "📑",
        "data/xml": "📰",
        "data/csv": "📊",
        "data/toml": "⚙️",
        # Текст
        "text/markdown": "📝",
        "text/plain": "📄",
        "text/rst": "📜",
        # Другое
        "sql": "🗃️",
        "config": "⚙️",
        "shell": "🐚",
        "dockerfile": "🐳",
        "other": "📎",
        "unknown": "❓",
    }
    return icons.get(ftype, "📎")


def build_folder_tree(files: list, root: str) -> Tree:
    """Строит визуальное дерево папок проекта с токенами для всех файлов."""
    tree = Tree(f"📁 [bold cyan]{Path(root).name}[/bold cyan]")
    
    # Группируем файлы по папкам
    folders = {}
    for f in files:
        parts = Path(f["path"]).parts
        if len(parts) == 1:
            folder = "."
        else:
            folder = str(Path(*parts[:-1]))
        
        if folder not in folders:
            folders[folder] = []
        folders[folder].append(f)
    
    # Строим дерево
    folder_nodes = {}
    for folder in sorted(folders.keys()):
        if folder == ".":
            node = tree
        else:
            parts = Path(folder).parts
            parent = tree
            current_path = ""
            for part in parts:
                current_path = str(Path(current_path) / part) if current_path else part
                if current_path not in folder_nodes:
                    folder_nodes[current_path] = parent.add(f"📂 [blue]{part}[/blue]")
                parent = folder_nodes[current_path]
            node = parent
        
        for f in folders[folder]:
            filename = Path(f["path"]).name
            ftype = f["type"]
            tokens = f["tokens_total"]
            
            icon = get_file_icon(ftype)
            
            # Цвет токенов в зависимости от размера
            if tokens == 0:
                token_style = "dim"
            elif tokens < 500:
                token_style = "green"
            elif tokens < 2000:
                token_style = "yellow"
            else:
                token_style = "red"
            
            node.add(f"{icon} [white]{filename}[/white] [{token_style}]({tokens:,} tok)[/{token_style}]")
    
    return tree


def run_full_test(project_path: str):
    console.print(Panel.fit(
        "[bold green]🚀 AI Assistant Pro: Полный тест системы чанкирования[/bold green]",
        border_style="green"
    ))
    
    # Инициализируем счётчик токенов для не-Python файлов
    token_counter = TokenCounter()
    file_type_detector = FileTypeDetector()
    
    # === ШАГ 1: Сканирование проекта ===
    console.print("\n[bold yellow]═══ ШАГ 1: Сканирование проекта ═══[/bold yellow]\n")
    
    scanner = ProjectScanner(root_path=project_path)
    project_map = scanner.scan()
    
    console.print(f"✅ Корень проекта: [cyan]{project_map['root']}[/cyan]")
    console.print(f"✅ Найдено файлов: [cyan]{len(project_map['files'])}[/cyan]")
    console.print(f"✅ Карта сохранена: [cyan]{project_path}\\project_map.json[/cyan]\n")
    
    # === ШАГ 2: Визуализация структуры ===
    console.print("[bold yellow]═══ ШАГ 2: Структура проекта (Дорожная карта) ═══[/bold yellow]\n")
    
    tree = build_folder_tree(project_map["files"], project_map["root"])
    console.print(tree)
    
    # === ШАГ 3: Таблица файлов ПО ТИПАМ ===
    console.print("\n[bold yellow]═══ ШАГ 3: Детали файлов (все типы) ═══[/bold yellow]\n")
    
    # Группируем по типам для статистики
    files_by_type: dict[str, list] = {}
    for f in project_map["files"]:
        ftype = f["type"]
        if ftype not in files_by_type:
            files_by_type[ftype] = []
        files_by_type[ftype].append(f)
    
    # Таблица статистики по типам
    type_stats_table = Table(title="📊 Статистика по типам файлов")
    type_stats_table.add_column("Тип", style="cyan")
    type_stats_table.add_column("Иконка", justify="center")
    type_stats_table.add_column("Файлов", justify="right", style="white")
    type_stats_table.add_column("Токенов", justify="right", style="green")
    type_stats_table.add_column("% токенов", justify="right", style="yellow")
    
    total_tokens = sum(f["tokens_total"] for f in project_map["files"])
    
    for ftype in sorted(files_by_type.keys()):
        files_of_type = files_by_type[ftype]
        type_tokens = sum(f["tokens_total"] for f in files_of_type)
        percent = (type_tokens / total_tokens * 100) if total_tokens > 0 else 0
        
        type_stats_table.add_row(
            ftype,
            get_file_icon(ftype),
            str(len(files_of_type)),
            f"{type_tokens:,}",
            f"{percent:.1f}%"
        )
    
    console.print(type_stats_table)
    
    # Полная таблица файлов
    console.print()
    table = Table(title="📋 Карта проекта (все файлы)")
    table.add_column("", justify="center", width=3)  # Иконка
    table.add_column("Путь", style="cyan", no_wrap=False)
    table.add_column("Тип", style="magenta")
    table.add_column("Токены", justify="right", style="green")
    table.add_column("Hash (MD5)", style="dim")
    
    # Сортируем: сначала по типу, потом по пути
    sorted_files = sorted(project_map["files"], key=lambda x: (x["type"], x["path"]))
    
    for f in sorted_files:
        icon = get_file_icon(f["type"])
        tokens = f["tokens_total"]
        
        # Подсветка больших файлов
        if tokens > 5000:
            token_str = f"[bold red]{tokens:,}[/bold red]"
        elif tokens > 2000:
            token_str = f"[yellow]{tokens:,}[/yellow]"
        else:
            token_str = f"{tokens:,}"
        
        table.add_row(
            icon,
            f["path"],
            f["type"],
            token_str,
            f["hash"][:12] + "..."
        )
    
    console.print(table)
    console.print(f"\n[bold]📊 Всего токенов в проекте: [green]{total_tokens:,}[/green][/bold]")
    
    # === ШАГ 4: Иерархическое чанкирование Python-файлов ===
    console.print("\n[bold yellow]═══ ШАГ 4: Иерархическое чанкирование (Python) ═══[/bold yellow]\n")
    
    python_files = [f for f in project_map["files"] if f["type"] == "code/python"]
    non_python_files = [f for f in project_map["files"] if f["type"] != "code/python"]
    
    python_tokens = sum(f["tokens_total"] for f in python_files)
    non_python_tokens = sum(f["tokens_total"] for f in non_python_files)
    
    console.print(f"🐍 Python-файлов: [cyan]{len(python_files)}[/cyan] ([green]{python_tokens:,}[/green] токенов)")
    console.print(f"📁 Других файлов: [cyan]{len(non_python_files)}[/cyan] ([green]{non_python_tokens:,}[/green] токенов)\n")
    
    all_chunks = {}
    
    for pf in python_files:
        chunks = scanner.get_python_chunks(pf["path"])
        all_chunks[pf["path"]] = []
        
        file_chunk = next((c for c in chunks if c.kind == "file"), None)
        console.print(f"[bold cyan]📄 {pf['path']}[/bold cyan] — {file_chunk.tokens if file_chunk else '?'} токенов")
        
        chunk_table = Table(show_header=True, header_style="bold")
        chunk_table.add_column("Тип", style="yellow")
        chunk_table.add_column("Имя", style="white")
        chunk_table.add_column("Родитель", style="dim")
        chunk_table.add_column("Строки", justify="center")
        chunk_table.add_column("Токены", justify="right", style="green")
        
        for ch in chunks:
            if ch.kind == "file":
                continue
            
            kind_icon = {"class": "🏛️", "method": "  🔧", "function": "⚡"}.get(ch.kind, "?")
            
            chunk_table.add_row(
                f"{kind_icon} {ch.kind}",
                ch.name,
                ch.parent or "-",
                f"{ch.start_line}–{ch.end_line}",
                str(ch.tokens)
            )
            
            all_chunks[pf["path"]].append({
                "kind": ch.kind,
                "name": ch.name,
                "parent": ch.parent,
                "start_line": ch.start_line,
                "end_line": ch.end_line,
                "tokens": ch.tokens
            })
        
        if len(chunks) > 1:
            console.print(chunk_table)
        else:
            console.print("   [dim](нет классов/функций)[/dim]")
        console.print()
    
    # === ШАГ 4.5: Информация о не-Python файлах ===
    if non_python_files:
        console.print("[bold yellow]═══ ШАГ 4.5: Токены не-Python файлов ═══[/bold yellow]\n")
        
        # Топ-10 самых больших не-Python файлов
        top_non_python = sorted(non_python_files, key=lambda x: x["tokens_total"], reverse=True)[:10]
        
        if top_non_python:
            top_table = Table(title="🔝 Топ-10 не-Python файлов по токенам")
            top_table.add_column("", justify="center", width=3)
            top_table.add_column("Файл", style="cyan")
            top_table.add_column("Тип", style="magenta")
            top_table.add_column("Токены", justify="right", style="green")
            
            for f in top_non_python:
                if f["tokens_total"] > 0:
                    top_table.add_row(
                        get_file_icon(f["type"]),
                        f["path"],
                        f["type"],
                        f"{f['tokens_total']:,}"
                    )
            
            console.print(top_table)
            console.print()
    
    # === ШАГ 5: Сохранение результатов ===
    console.print("[bold yellow]═══ ШАГ 5: Сохранение результатов ═══[/bold yellow]\n")
    
    chunks_index_path = Path(project_path) / "chunks_index.json"
    with open(chunks_index_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    
    console.print(f"✅ Индекс чанков сохранён: [cyan]{chunks_index_path}[/cyan]")
    
    # Сохраняем расширенную статистику
    stats = {
        "total_files": len(project_map["files"]),
        "python_files": len(python_files),
        "non_python_files": len(non_python_files),
        "total_tokens": total_tokens,
        "python_tokens": python_tokens,
        "non_python_tokens": non_python_tokens,
        "by_type": {
            ftype: {
                "count": len(files),
                "tokens": sum(f["tokens_total"] for f in files)
            }
            for ftype, files in files_by_type.items()
        }
    }
    
    stats_path = Path(project_path) / "token_stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    console.print(f"✅ Статистика токенов сохранена: [cyan]{stats_path}[/cyan]")
    
    # === ИТОГ ===
    console.print("\n" + "═" * 60)
    console.print(Panel.fit(
        f"""[bold green]🎉 Тест завершён успешно![/bold green]

📁 Всего файлов: [cyan]{len(project_map['files'])}[/cyan]
   ├── 🐍 Python: [cyan]{len(python_files)}[/cyan] ([green]{python_tokens:,}[/green] токенов)
   └── 📂 Других: [cyan]{len(non_python_files)}[/cyan] ([green]{non_python_tokens:,}[/green] токенов)

📊 Всего токенов: [bold green]{total_tokens:,}[/bold green]
   ├── Python: [green]{python_tokens:,}[/green] ({python_tokens/total_tokens*100:.1f}% if total_tokens else 0)
   └── Другие: [green]{non_python_tokens:,}[/green] ({non_python_tokens/total_tokens*100:.1f}% if total_tokens else 0)

Созданные файлы (в папке проекта):
  • [white]project_map.json[/white] — карта проекта
  • [white]chunks_index.json[/white] — иерархия чанков (Python)
  • [white]token_stats.json[/white] — статистика токенов

[dim]Следующий шаг: IndexUpdater для Qwen[/dim]""",
        title="📋 Результат",
        border_style="green"
    ))


if __name__ == "__main__":
    project_path = get_project_path()
    run_full_test(project_path)