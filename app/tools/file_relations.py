# app/tools/file_relations.py
"""
Показывает связи файла в проекте: зависимости, зависимые, тесты, соседи.
Простой инструмент для понимания контекста без сложного анализа.
"""

import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


def show_file_relations_tool(
    file_path: str,
    project_dir: str,
    virtual_fs: Optional[Any] = None,
    include_tests: bool = True,
    include_siblings: bool = True,
    max_relations: int = 20,
) -> str:
    """
    Показывает связи файла в проекте.
    
    Args:
        file_path: Путь к файлу для анализа
        project_dir: Корневая директория проекта
        virtual_fs: VirtualFileSystem для учёта staged файлов
        include_tests: Включать тестовые файлы
        include_siblings: Включать файлы в той же директории
        max_relations: Максимальное количество связей каждого типа
        
    Returns:
        XML-форматированные связи файла
    """
    logger.info(f"show_file_relations: analyzing {file_path}")
    
    # Нормализуем путь
    normalized_path = file_path.replace('\\', '/')
    
    # Проверяем существование файла
    file_exists = False
    if virtual_fs is not None:
        file_exists = virtual_fs.file_exists(normalized_path)
    else:
        full_path = Path(project_dir) / normalized_path
        file_exists = full_path.exists()
    
    if not file_exists:
        return _format_error(f"Файл не найден: {normalized_path}")
    
    # Анализируем связи
    relations = _analyze_file_relations(
        file_path=normalized_path,
        project_dir=project_dir,
        virtual_fs=virtual_fs,
        include_tests=include_tests,
        include_siblings=include_siblings,
        max_relations=max_relations,
    )
    
    return _format_relations(normalized_path, relations)


def _analyze_file_relations(
    file_path: str,
    project_dir: str,
    virtual_fs: Optional[Any],
    include_tests: bool,
    include_siblings: bool,
    max_relations: int,
) -> Dict[str, List[str]]:
    """
    Анализирует связи файла.
    """
    relations = {
        "imports": [],      # Что этот файл импортирует
        "imported_by": [],  # Кто импортирует этот файл
        "tests": [],        # Тестовые файлы
        "siblings": [],     # Файлы в той же директории
    }
    
    try:
        # 1. Получаем содержимое файла (с учётом VFS)
        content = _read_file_content(file_path, project_dir, virtual_fs)
        
        if content:
            # 2. Что импортирует этот файл
            relations["imports"] = _extract_imports(content)[:max_relations]
            
            # 3. Кто импортирует этот файл (если есть VFS)
            if virtual_fs is not None:
                relations["imported_by"] = _find_importers(file_path, project_dir, virtual_fs)[:max_relations]
        
        # 4. Тестовые файлы
        if include_tests and virtual_fs is not None:
            relations["tests"] = _find_test_files(file_path, virtual_fs)[:max_relations]
        
        # 5. Файлы в той же директории
        if include_siblings:
            relations["siblings"] = _find_sibling_files(file_path, project_dir, virtual_fs)[:max_relations]
            
    except Exception as e:
        logger.error(f"Error analyzing file relations for {file_path}: {e}")
    
    return relations


def _read_file_content(
    file_path: str, 
    project_dir: str, 
    virtual_fs: Optional[Any]
) -> Optional[str]:
    """Читает содержимое файла с учётом VFS."""
    if virtual_fs is not None:
        return virtual_fs.read_file(file_path)
    else:
        full_path = Path(project_dir) / file_path
        if full_path.exists():
            try:
                return full_path.read_text(encoding='utf-8')
            except Exception:
                return None
    return None


def _extract_imports(content: str) -> List[str]:
    """Извлекает импорты из содержимого файла (простой regex)."""
    imports = []
    
    # import X
    import_pattern = r'^import\s+([\w.]+)'
    for match in re.finditer(import_pattern, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # from X import Y
    from_pattern = r'^from\s+([\w.]+)\s+import'
    for match in re.finditer(from_pattern, content, re.MULTILINE):
        imports.append(match.group(1))
    
    return imports


def _find_importers(
    file_path: str, 
    project_dir: str, 
    virtual_fs: Any
) -> List[str]:
    """Находит файлы, которые импортируют данный файл."""
    if not file_path.endswith('.py'):
        return []
    
    importers = []
    # Получаем все Python файлы проекта
    python_files = virtual_fs.get_all_python_files()
    
    for py_file in python_files:
        if py_file == file_path:
            continue
        
        content = virtual_fs.read_file(py_file)
        if not content:
            continue
        
        # Простая проверка: ищем имя модуля в импортах
        module_name = _path_to_module(file_path)
        if not module_name:
            continue
        
        # Проверяем, импортируется ли этот модуль
        if _file_imports_module(content, module_name):
            importers.append(py_file)
    
    return importers


def _find_test_files(file_path: str, virtual_fs: Any) -> List[str]:
    """Находит тестовые файлы для данного файла."""
    # Используем метод из VFS, если он есть
    if hasattr(virtual_fs, 'find_test_files'):
        return virtual_fs.find_test_files(file_path)
    
    # Fallback: простой поиск по паттернам
    test_files = []
    if not file_path.endswith('.py'):
        return test_files
    
    file_stem = Path(file_path).stem
    file_dir = Path(file_path).parent
    
    # Возможные паттерны тестовых файлов
    test_patterns = [
        f"test_{file_stem}.py",
        f"{file_stem}_test.py",
        str(file_dir / f"test_{file_stem}.py"),
        str(file_dir / "tests" / f"test_{file_stem}.py"),
        str(file_dir / "test" / f"test_{file_stem}.py"),
    ]
    
    for pattern in test_patterns:
        if virtual_fs.file_exists(pattern):
            test_files.append(pattern)
    
    return test_files


def _find_sibling_files(
    file_path: str, 
    project_dir: str, 
    virtual_fs: Optional[Any]
) -> List[str]:
    """Находит файлы в той же директории."""
    file_dir = Path(file_path).parent
    siblings = []
    
    # Используем VFS или прямой доступ к файловой системе
    if virtual_fs is not None:
        # Получаем все файлы в проекте и фильтруем по директории
        all_files = []
        # Простой способ: получить все staged файлы и файлы на диске
        # Для простоты используем прямой доступ к файловой системе
        try:
            dir_path = Path(project_dir) / file_dir
            if dir_path.exists():
                for item in dir_path.iterdir():
                    if item.is_file() and item.name != Path(file_path).name:
                        rel_path = str(item.relative_to(project_dir)).replace('\\', '/')
                        siblings.append(rel_path)
        except Exception as e:
            logger.debug(f"Error finding siblings: {e}")
    else:
        # Прямой доступ к файловой системе
        dir_path = Path(project_dir) / file_dir
        if dir_path.exists():
            for item in dir_path.iterdir():
                if item.is_file() and item.name != Path(file_path).name:
                    rel_path = str(item.relative_to(project_dir)).replace('\\', '/')
                    siblings.append(rel_path)
    
    return sorted(siblings)[:50]  # Ограничиваем количество


def _path_to_module(file_path: str) -> Optional[str]:
    """Конвертирует путь в имя модуля."""
    if not file_path.endswith('.py'):
        return None
    
    # Удаляем .py и заменяем / на .
    module = file_path[:-3].replace('/', '.')
    
    # Обработка __init__.py
    if module.endswith('.__init__'):
        module = module[:-9]
    
    return module


def _file_imports_module(content: str, module_name: str) -> bool:
    """Проверяет, импортирует ли файл указанный модуль."""
    # Простая regex проверка
    patterns = [
        rf'^import\s+{re.escape(module_name)}(?:\s|,|$)',
        rf'^from\s+{re.escape(module_name)}\s+import',
    ]
    
    for pattern in patterns:
        if re.search(pattern, content, re.MULTILINE):
            return True
    
    # Также проверяем частичные импорты (например, из пакета)
    parts = module_name.split('.')
    if len(parts) > 1:
        parent = re.escape(".".join(parts[:-1]))
        child = re.escape(parts[-1])
        pattern = rf'^from\s+{parent}\s+import\s+.*\b{child}\b'
        if re.search(pattern, content, re.MULTILINE):
            return True
    
    return False


def _format_relations(file_path: str, relations: Dict[str, List[str]]) -> str:
    """Форматирует связи в XML."""
    xml_parts = []
    
    xml_parts.append(f'<file_relations path="{file_path}">')
    
    # Импорты
    if relations["imports"]:
        xml_parts.append(f'  <imports count="{len(relations["imports"])}">')
        for imp in relations["imports"]:
            xml_parts.append(f'    <import>{imp}</import>')
        xml_parts.append('  </imports>')
    else:
        xml_parts.append('  <imports count="0" />')
    
    # Кто импортирует
    if relations["imported_by"]:
        xml_parts.append(f'  <imported_by count="{len(relations["imported_by"])}">')
        for importer in relations["imported_by"]:
            xml_parts.append(f'    <importer>{importer}</importer>')
        xml_parts.append('  </imported_by>')
    else:
        xml_parts.append('  <imported_by count="0" />')
    
    # Тестовые файлы
    if relations["tests"]:
        xml_parts.append(f'  <tests count="{len(relations["tests"])}">')
        for test in relations["tests"]:
            xml_parts.append(f'    <test>{test}</test>')
        xml_parts.append('  </tests>')
    else:
        xml_parts.append('  <tests count="0" />')
    
    # Файлы в директории
    if relations["siblings"]:
        xml_parts.append(f'  <siblings count="{len(relations["siblings"])}">')
        for sibling in relations["siblings"]:
            xml_parts.append(f'    <sibling>{sibling}</sibling>')
        xml_parts.append('  </siblings>')
    else:
        xml_parts.append('  <siblings count="0" />')
    
    xml_parts.append('</file_relations>')
    
    # Добавляем сводку
    summary = [
        f"<!-- FILE RELATIONS: {file_path} -->",
        f"<!-- Total imports: {len(relations['imports'])} | Imported by: {len(relations['imported_by'])} -->",
        f"<!-- Test files: {len(relations['tests'])} | Sibling files: {len(relations['siblings'])} -->",
        ""
    ]
    
    return "\n".join(summary + xml_parts)


def _format_error(message: str) -> str:
    """Форматирует сообщение об ошибке."""
    return f"""<!-- FILE RELATIONS ERROR -->
<error>
  <message>{message}</message>
  <suggestion>Проверьте путь к файлу и убедитесь, что он существует в проекте.</suggestion>
</error>"""