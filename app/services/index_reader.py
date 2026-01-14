# app/services/index_reader.py
"""
Index Reader - API для удобного доступа к семантическому индексу.
Позволяет быстро находить информацию о файлах, классах и функциях.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Union, Set
from enum import Enum


INDEX_FILENAME = "semantic_index.json"


class DetailLevel(Enum):
    """Уровень детализации при выводе информации"""
    MINIMAL = "minimal"      # Только имя и краткое описание
    STANDARD = "standard"    # + методы, ссылки, строки
    FULL = "full"            # + импорты, глобалы, токены, всё


@dataclass
class SearchResult:
    """Результат поиска"""
    type: str           # "file" | "class" | "function" | "method"
    name: str
    file_path: str
    description: str
    tokens: int
    relevance: float    # Оценка релевантности (0-1)
    data: Dict          # Полные данные для детального просмотра
    
    def __repr__(self):
        return f"<{self.type}: {self.name} in {self.file_path}>"


@dataclass
class FileInfo:
    """Структурированная информация о файле"""
    name: str
    path: str
    description: str
    tokens: int
    lines: int
    imports: List[str]
    globals: List[str]
    classes: List[Dict]
    functions: List[Dict]
    
    def summary(self) -> str:
        """Краткая сводка о файле"""
        return (
            f"{self.name} ({self.tokens} tokens, {self.lines} lines)\n"
            f"  {self.description}\n"
            f"  Classes: {', '.join(c['name'] for c in self.classes) or 'none'}\n"
            f"  Functions: {', '.join(f['name'] for f in self.functions) or 'none'}"
        )


class IndexReader:
    """
    Читает семантический индекс и предоставляет удобные методы поиска.
    
    Примеры использования:
        reader = IndexReader("/path/to/project")
        
        # Получить информацию о файле
        info = reader.get_file("auth.py")
        
        # Найти класс по имени
        info = reader.get_class("AuthService")
        
        # Поиск по паттерну
        results = reader.search("auth")
        
        # Форматировать для отправки в ИИ
        context = reader.format_for_ai("auth.py", detail=DetailLevel.STANDARD)
        
        # Получить контекст для конкретного класса
        context = reader.get_class_context("AuthService")
    """
    
    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.index_path = self.project_path / INDEX_FILENAME
        self._index: Optional[Dict] = None
        self._file_name_map: Dict[str, str] = {}  # filename -> full path
        self._class_map: Dict[str, List[str]] = {}  # class_name -> [file_paths]
        self._function_map: Dict[str, List[str]] = {}  # func_name -> [file_paths]
        self._load_index()
    
    def _load_index(self):
        """Загружает индекс в память и строит вспомогательные карты"""
        if not self.index_path.exists():
            raise FileNotFoundError(
                f"Индекс не найден: {self.index_path}\n"
                f"Сначала выполните индексацию проекта."
            )
        
        with self.index_path.open("r", encoding="utf-8") as f:
            self._index = json.load(f)
        
        self._build_lookup_maps()
    
    def _build_lookup_maps(self):
        """Строит карты для быстрого поиска"""
        self._file_name_map.clear()
        self._class_map.clear()
        self._function_map.clear()
        
        for path, file_data in self._index.get("files", {}).items():
            # Карта имён файлов
            file_name = file_data.get("name", "")
            self._file_name_map[file_name] = path
            self._file_name_map[file_name.replace(".py", "")] = path
            
            # Карта классов
            for cls in file_data.get("classes", []):
                class_name = cls.get("name")
                if class_name:
                    if class_name not in self._class_map:
                        self._class_map[class_name] = []
                    self._class_map[class_name].append(path)
            
            # Карта функций
            for func in file_data.get("functions", []):
                func_name = func.get("name")
                if func_name:
                    if func_name not in self._function_map:
                        self._function_map[func_name] = []
                    self._function_map[func_name].append(path)
    
    def reload(self):
        """Перезагрузить индекс (если он обновился на диске)"""
        self._load_index()
    
    @property
    def stats(self) -> Dict:
        """Статистика индекса"""
        return {
            "version": self._index.get("version"),
            "updated_at": self._index.get("updated_at"),
            "total_files": self._index.get("total_files", 0),
            "total_tokens": self._index.get("total_tokens", 0),
            "total_classes": sum(
                len(f.get("classes", [])) 
                for f in self._index.get("files", {}).values()
            ),
            "total_functions": sum(
                len(f.get("functions", []))
                for f in self._index.get("files", {}).values()
            ),
        }
    
    # ==================== ПОЛУЧЕНИЕ ИНФОРМАЦИИ ====================
    
    def get_file(self, filename: str) -> Optional[Dict]:
        """
        Получить информацию о файле по имени или пути.
        
        Args:
            filename: Имя файла ("auth.py") или путь ("app/services/auth.py")
        
        Returns:
            Словарь с полной информацией о файле или None
        
        Examples:
            >>> reader.get_file("auth.py")
            >>> reader.get_file("app/services/auth.py")
            >>> reader.get_file("auth")  # без расширения тоже работает
        """
        files = self._index.get("files", {})
        
        # 1. Точное совпадение по полному пути
        if filename in files:
            return {"path": filename, **files[filename]}
        
        # 2. Поиск через карту имён
        if filename in self._file_name_map:
            path = self._file_name_map[filename]
            return {"path": path, **files[path]}
        
        # 3. Поиск по частичному совпадению пути
        for path, data in files.items():
            if path.endswith(filename) or path.endswith(f"/{filename}"):
                return {"path": path, **data}
        
        # 4. Нечёткий поиск (имя файла содержит запрос)
        filename_lower = filename.lower().replace(".py", "")
        for path, data in files.items():
            if filename_lower in data.get("name", "").lower():
                return {"path": path, **data}
        
        return None
    
    def get_file_structured(self, filename: str) -> Optional[FileInfo]:
        """
        Получить структурированную информацию о файле.
        
        Returns:
            FileInfo объект или None
        """
        data = self.get_file(filename)
        if not data:
            return None
        
        imports_data = data.get("imports") or {}
        globals_data = data.get("globals") or {}
        
        return FileInfo(
            name=data.get("name", ""),
            path=data.get("path", ""),
            description=data.get("description", ""),
            tokens=data.get("tokens_total", 0),
            lines=data.get("lines_total", 0),
            imports=imports_data.get("modules", []),
            globals=globals_data.get("names", []),
            classes=data.get("classes", []),
            functions=data.get("functions", [])
        )
    
    def get_class(self, class_name: str, file_hint: str = None) -> Optional[Dict]:
        """
        Найти класс по имени.
        
        Args:
            class_name: Имя класса ("AuthService")
            file_hint: Подсказка по файлу для уточнения ("auth" или "auth.py")
        
        Returns:
            Словарь с информацией о классе, включая file_path
        
        Examples:
            >>> reader.get_class("AuthService")
            >>> reader.get_class("User", file_hint="models")
        """
        if class_name not in self._class_map:
            return None
        
        file_paths = self._class_map[class_name]
        files = self._index.get("files", {})
        
        # Фильтруем по подсказке
        if file_hint:
            file_hint_lower = file_hint.lower()
            filtered = [p for p in file_paths if file_hint_lower in p.lower()]
            if filtered:
                file_paths = filtered
        
        # Собираем результаты
        results = []
        for path in file_paths:
            file_data = files.get(path, {})
            for cls in file_data.get("classes", []):
                if cls.get("name") == class_name:
                    results.append({
                        "file_path": path,
                        "file_name": file_data.get("name"),
                        "file_description": file_data.get("description"),
                        **cls
                    })
        
        if not results:
            return None
        
        if len(results) == 1:
            return results[0]
        
        # Несколько классов с таким именем
        return {
            "multiple_matches": True,
            "count": len(results),
            "matches": results
        }
    
    def get_function(self, func_name: str, file_hint: str = None) -> Optional[Dict]:
        """
        Найти функцию верхнего уровня по имени.
        
        Args:
            func_name: Имя функции ("validate_password")
            file_hint: Подсказка по файлу
        
        Returns:
            Словарь с информацией о функции, включая file_path
        """
        if func_name not in self._function_map:
            return None
        
        file_paths = self._function_map[func_name]
        files = self._index.get("files", {})
        
        if file_hint:
            file_hint_lower = file_hint.lower()
            filtered = [p for p in file_paths if file_hint_lower in p.lower()]
            if filtered:
                file_paths = filtered
        
        results = []
        for path in file_paths:
            file_data = files.get(path, {})
            for func in file_data.get("functions", []):
                if func.get("name") == func_name:
                    results.append({
                        "file_path": path,
                        "file_name": file_data.get("name"),
                        "file_description": file_data.get("description"),
                        **func
                    })
        
        if not results:
            return None
        
        if len(results) == 1:
            return results[0]
        
        return {
            "multiple_matches": True,
            "count": len(results),
            "matches": results
        }
    
    def get_method(self, method_name: str, class_name: str = None) -> List[Dict]:
        """
        Найти метод по имени, опционально в конкретном классе.
        
        Args:
            method_name: Имя метода ("login", "__init__")
            class_name: Имя класса для фильтрации (опционально)
        
        Returns:
            Список найденных методов с контекстом класса и файла
        
        Examples:
            >>> reader.get_method("login")
            >>> reader.get_method("__init__", class_name="AuthService")
        """
        files = self._index.get("files", {})
        results = []
        
        for path, file_data in files.items():
            for cls in file_data.get("classes", []):
                if class_name and cls.get("name") != class_name:
                    continue
                
                if method_name in cls.get("methods", []):
                    results.append({
                        "method_name": method_name,
                        "class_name": cls.get("name"),
                        "class_description": cls.get("description"),
                        "class_lines": cls.get("lines"),
                        "file_path": path,
                        "file_name": file_data.get("name")
                    })
        
        return results
    
    def list_files(self) -> List[Dict]:
        """
        Получить список всех файлов с краткой информацией.
        
        Returns:
            Список словарей с name, path, description, tokens
        """
        files = self._index.get("files", {})
        return [
            {
                "name": data.get("name"),
                "path": path,
                "description": data.get("description", "")[:100],
                "tokens": data.get("tokens_total", 0),
                "classes_count": len(data.get("classes", [])),
                "functions_count": len(data.get("functions", []))
            }
            for path, data in sorted(files.items())
        ]
    
    def list_classes(self, file_filter: str = None) -> List[Dict]:
        """
        Получить список всех классов.
        
        Args:
            file_filter: Фильтр по имени/пути файла (опционально)
        
        Returns:
            Список классов с информацией о файле
        """
        files = self._index.get("files", {})
        results = []
        
        for path, file_data in files.items():
            if file_filter and file_filter.lower() not in path.lower():
                continue
            
            for cls in file_data.get("classes", []):
                results.append({
                    "name": cls.get("name"),
                    "file_path": path,
                    "file_name": file_data.get("name"),
                    "description": cls.get("description"),
                    "tokens": cls.get("tokens", 0),
                    "methods": cls.get("methods", []),
                    "methods_count": len(cls.get("methods", []))
                })
        
        return sorted(results, key=lambda x: x["name"])
    
    def list_functions(self, file_filter: str = None) -> List[Dict]:
        """
        Получить список всех функций верхнего уровня.
        
        Args:
            file_filter: Фильтр по имени/пути файла
        
        Returns:
            Список функций
        """
        files = self._index.get("files", {})
        results = []
        
        for path, file_data in files.items():
            if file_filter and file_filter.lower() not in path.lower():
                continue
            
            for func in file_data.get("functions", []):
                results.append({
                    "name": func.get("name"),
                    "file_path": path,
                    "file_name": file_data.get("name"),
                    "description": func.get("description"),
                    "tokens": func.get("tokens", 0)
                })
        
        return sorted(results, key=lambda x: x["name"])
    
    # ==================== ПОИСК ====================
    
    def search(
        self, 
        query: str, 
        search_in: List[str] = None,
        limit: int = 20
    ) -> List[SearchResult]:
        """
        Поиск по индексу с нечётким совпадением.
        
        Args:
            query: Поисковый запрос ("auth", "user login", "validate")
            search_in: Где искать ["files", "classes", "functions", "descriptions"]
                       По умолчанию — везде
            limit: Максимальное количество результатов
        
        Returns:
            Список результатов поиска, отсортированный по релевантности
        
        Examples:
            >>> reader.search("auth")
            >>> reader.search("user", search_in=["classes"])
            >>> reader.search("валидация", search_in=["descriptions"])
        """
        if search_in is None:
            search_in = ["files", "classes", "functions", "descriptions"]
        
        query_lower = query.lower()
        query_words = set(query_lower.split())
        results: List[SearchResult] = []
        files = self._index.get("files", {})
        
        for path, file_data in files.items():
            file_name = file_data.get("name", "")
            file_desc = file_data.get("description", "")
            
            # Поиск в именах файлов
            if "files" in search_in:
                relevance = self._calculate_relevance(
                    query_lower, query_words, 
                    [file_name, path]
                )
                if relevance > 0:
                    results.append(SearchResult(
                        type="file",
                        name=file_name,
                        file_path=path,
                        description=file_desc[:150],
                        tokens=file_data.get("tokens_total", 0),
                        relevance=relevance,
                        data=file_data
                    ))
            
            # Поиск в описаниях файлов
            if "descriptions" in search_in and "files" not in search_in:
                relevance = self._calculate_relevance(
                    query_lower, query_words,
                    [file_desc]
                )
                if relevance > 0:
                    results.append(SearchResult(
                        type="file",
                        name=file_name,
                        file_path=path,
                        description=file_desc[:150],
                        tokens=file_data.get("tokens_total", 0),
                        relevance=relevance * 0.8,  # Чуть ниже приоритет
                        data=file_data
                    ))
            
            # Поиск в классах
            if "classes" in search_in:
                for cls in file_data.get("classes", []):
                    cls_name = cls.get("name", "")
                    cls_desc = cls.get("description", "")
                    
                    search_fields = [cls_name]
                    if "descriptions" in search_in:
                        search_fields.append(cls_desc)
                    
                    relevance = self._calculate_relevance(
                        query_lower, query_words, search_fields
                    )
                    if relevance > 0:
                        results.append(SearchResult(
                            type="class",
                            name=cls_name,
                            file_path=path,
                            description=cls_desc[:150],
                            tokens=cls.get("tokens", 0),
                            relevance=relevance,
                            data={**cls, "file_path": path, "file_name": file_name}
                        ))
            
            # Поиск в функциях
            if "functions" in search_in:
                for func in file_data.get("functions", []):
                    func_name = func.get("name", "")
                    func_desc = func.get("description", "")
                    
                    search_fields = [func_name]
                    if "descriptions" in search_in:
                        search_fields.append(func_desc)
                    
                    relevance = self._calculate_relevance(
                        query_lower, query_words, search_fields
                    )
                    if relevance > 0:
                        results.append(SearchResult(
                            type="function",
                            name=func_name,
                            file_path=path,
                            description=func_desc[:150],
                            tokens=func.get("tokens", 0),
                            relevance=relevance,
                            data={**func, "file_path": path, "file_name": file_name}
                        ))
        
        # Сортируем по релевантности и ограничиваем
        results.sort(key=lambda x: (-x.relevance, x.name))
        return results[:limit]
    
    def _calculate_relevance(
        self, 
        query: str, 
        query_words: Set[str], 
        fields: List[str]
    ) -> float:
        """
        Вычисляет релевантность совпадения.
        
        Returns:
            Число от 0 до 1, где 1 - точное совпадение
        """
        max_relevance = 0.0
        
        for field in fields:
            if not field:
                continue
            
            field_lower = field.lower()
            
            # Точное совпадение имени
            if field_lower == query:
                return 1.0
            
            # Запрос содержится в поле целиком
            if query in field_lower:
                # Чем короче поле относительно запроса, тем выше релевантность
                relevance = len(query) / len(field_lower)
                max_relevance = max(max_relevance, min(0.9, relevance + 0.3))
            
            # Поле начинается с запроса
            if field_lower.startswith(query):
                max_relevance = max(max_relevance, 0.85)
            
            # Проверяем совпадение слов
            field_words = set(re.split(r'[_\s\-\.]+', field_lower))
            matching_words = query_words & field_words
            if matching_words:
                word_relevance = len(matching_words) / len(query_words) * 0.7
                max_relevance = max(max_relevance, word_relevance)
        
        return max_relevance
    
    def find_references_to(self, name: str) -> List[Dict]:
        """
        Найти все места, которые ссылаются на указанный класс/функцию.
        
        Args:
            name: Имя класса или функции ("AuthService", "validate_password")
        
        Returns:
            Список элементов, которые ссылаются на name
        """
        files = self._index.get("files", {})
        results = []
        
        for path, file_data in files.items():
            # Проверяем классы
            for cls in file_data.get("classes", []):
                refs = cls.get("references", [])
                if any(name in ref for ref in refs):
                    results.append({
                        "type": "class",
                        "name": cls.get("name"),
                        "file_path": path,
                        "references": [r for r in refs if name in r]
                    })
            
            # Проверяем функции
            for func in file_data.get("functions", []):
                refs = func.get("references", [])
                if any(name in ref for ref in refs):
                    results.append({
                        "type": "function",
                        "name": func.get("name"),
                        "file_path": path,
                        "references": [r for r in refs if name in r]
                    })
        
        return results
    
    def find_dependencies(self, filename: str) -> Dict:
        """
        Найти зависимости файла: что он импортирует и что на него ссылается.
        
        Args:
            filename: Имя или путь файла
        
        Returns:
            Словарь с imports (что файл импортирует) и used_by (кто использует)
        """
        file_data = self.get_file(filename)
        if not file_data:
            return {"error": f"File not found: {filename}"}
        
        file_path = file_data.get("path", "")
        file_name = file_data.get("name", "")
        
        # Что файл импортирует
        imports = []
        imports_data = file_data.get("imports") or {}
        for module in imports_data.get("modules", []):
            # Пробуем найти этот модуль в проекте
            module_file = self.get_file(module.split(".")[0])
            if module_file:
                imports.append({
                    "module": module,
                    "is_local": True,
                    "file_path": module_file.get("path")
                })
            else:
                imports.append({
                    "module": module,
                    "is_local": False
                })
        
        # Кто использует этот файл
        used_by = []
        base_name = file_name.replace(".py", "")
        
        for other_path, other_data in self._index.get("files", {}).items():
            if other_path == file_path:
                continue
            
            other_imports = (other_data.get("imports") or {}).get("modules", [])
            for imp in other_imports:
                if base_name in imp:
                    used_by.append({
                        "file_path": other_path,
                        "file_name": other_data.get("name"),
                        "import": imp
                    })
                    break
        
        return {
            "file": file_path,
            "imports": imports,
            "used_by": used_by
        }
    
    # ==================== ФОРМАТИРОВАНИЕ ДЛЯ AI ====================
    
    def format_for_ai(
        self, 
        target: str, 
        detail: DetailLevel = DetailLevel.STANDARD,
        include_related: bool = False
    ) -> str:
        """
        Форматирует информацию для отправки в AI модель.
        
        Args:
            target: Имя файла, класса или функции
            detail: Уровень детализации
            include_related: Включить связанные файлы (импорты, ссылки)
        
        Returns:
            Отформатированная строка для контекста AI
        
        Examples:
            >>> context = reader.format_for_ai("auth.py")
            >>> context = reader.format_for_ai("AuthService", detail=DetailLevel.FULL)
        """
        # Пробуем найти как файл
        file_data = self.get_file(target)
        if file_data:
            return self._format_file_for_ai(file_data, detail, include_related)
        
        # Пробуем найти как класс
        class_data = self.get_class(target)
        if class_data and "multiple_matches" not in class_data:
            return self._format_class_for_ai(class_data, detail)
        
        # Пробуем найти как функцию
        func_data = self.get_function(target)
        if func_data and "multiple_matches" not in func_data:
            return self._format_function_for_ai(func_data, detail)
        
        # Не нашли — возвращаем сообщение
        return f"[Не найдено в индексе: {target}]"
    
    def _format_file_for_ai(
        self, 
        data: Dict, 
        detail: DetailLevel,
        include_related: bool
    ) -> str:
        """Форматирует информацию о файле"""
        lines = []
        
        # Заголовок
        lines.append(f"## Файл: {data.get('name')}")
        lines.append(f"**Путь:** `{data.get('path')}`")
        lines.append(f"**Описание:** {data.get('description')}")
        
        if detail in (DetailLevel.STANDARD, DetailLevel.FULL):
            lines.append(f"**Токенов:** {data.get('tokens_total', 0):,} | **Строк:** {data.get('lines_total', 0)}")
        
        # Импорты
        if detail == DetailLevel.FULL:
            imports_data = data.get("imports") or {}
            modules = imports_data.get("modules", [])
            if modules:
                lines.append(f"\n**Импорты:** {', '.join(modules[:15])}")
                if len(modules) > 15:
                    lines.append(f"  ... и ещё {len(modules) - 15}")
        
        # Глобальные переменные
        if detail == DetailLevel.FULL:
            globals_data = data.get("globals") or {}
            names = globals_data.get("names", [])
            if names:
                lines.append(f"**Глобальные:** {', '.join(names)}")
        
        # Классы
        classes = data.get("classes", [])
        if classes:
            lines.append(f"\n### Классы ({len(classes)})")
            for cls in classes:
                lines.append(self._format_class_brief(cls, detail))
        
        # Функции
        functions = data.get("functions", [])
        if functions:
            lines.append(f"\n### Функции ({len(functions)})")
            for func in functions:
                lines.append(self._format_function_brief(func, detail))
        
        # Связанные файлы
        if include_related:
            deps = self.find_dependencies(data.get("name", ""))
            
            local_imports = [i for i in deps.get("imports", []) if i.get("is_local")]
            if local_imports:
                lines.append(f"\n### Импортирует из проекта")
                for imp in local_imports[:5]:
                    lines.append(f"- `{imp['file_path']}`")
            
            used_by = deps.get("used_by", [])
            if used_by:
                lines.append(f"\n### Используется в")
                for use in used_by[:5]:
                    lines.append(f"- `{use['file_path']}`")
        
        return "\n".join(lines)
    
    def _format_class_for_ai(self, data: Dict, detail: DetailLevel) -> str:
        """Форматирует информацию о классе"""
        lines = []
        
        lines.append(f"## Класс: {data.get('name')}")
        lines.append(f"**Файл:** `{data.get('file_path')}`")
        lines.append(f"**Описание:** {data.get('description')}")
        
        if detail in (DetailLevel.STANDARD, DetailLevel.FULL):
            lines.append(f"**Строки:** {data.get('lines')} | **Токенов:** {data.get('tokens', 0):,}")
        
        # Методы
        methods = data.get("methods", [])
        if methods:
            if detail == DetailLevel.MINIMAL:
                lines.append(f"**Методы:** {', '.join(methods[:10])}")
                if len(methods) > 10:
                    lines.append(f"  ... и ещё {len(methods) - 10}")
            else:
                lines.append(f"\n**Методы ({len(methods)}):** {'; '.join(methods)}")
        
        # Ссылки
        if detail in (DetailLevel.STANDARD, DetailLevel.FULL):
            refs = data.get("references", [])
            if refs:
                lines.append(f"\n**Использует:** {', '.join(refs[:15])}")
                if len(refs) > 15:
                    lines.append(f"  ... и ещё {len(refs) - 15}")
        
        # Контекст файла
        if detail == DetailLevel.FULL:
            lines.append(f"\n**О файле:** {data.get('file_description', '')[:200]}")
        
        return "\n".join(lines)
    
    def _format_function_for_ai(self, data: Dict, detail: DetailLevel) -> str:
        """Форматирует информацию о функции"""
        lines = []
        
        lines.append(f"## Функция: {data.get('name')}")
        lines.append(f"**Файл:** `{data.get('file_path')}`")
        lines.append(f"**Описание:** {data.get('description')}")
        
        if detail in (DetailLevel.STANDARD, DetailLevel.FULL):
            lines.append(f"**Строки:** {data.get('lines')} | **Токенов:** {data.get('tokens', 0):,}")
        
        if detail in (DetailLevel.STANDARD, DetailLevel.FULL):
            refs = data.get("references", [])
            if refs:
                lines.append(f"\n**Использует:** {', '.join(refs)}")
        
        return "\n".join(lines)
    
    def _format_class_brief(self, cls: Dict, detail: DetailLevel) -> str:
        """Краткое описание класса для списка"""
        name = cls.get("name", "")
        desc = cls.get("description", "")[:100]
        methods = cls.get("methods", [])
        
        if detail == DetailLevel.MINIMAL:
            return f"- **{name}**: {desc}"
        
        methods_str = f" | Методы: {', '.join(methods[:5])}" if methods else ""
        if len(methods) > 5:
            methods_str += f" (+{len(methods) - 5})"
        
        return f"- **{name}** ({cls.get('tokens', 0)} tok): {desc}{methods_str}"
    
    def _format_function_brief(self, func: Dict, detail: DetailLevel) -> str:
        """Краткое описание функции для списка"""
        name = func.get("name", "")
        desc = func.get("description", "")[:100]
        
        if detail == DetailLevel.MINIMAL:
            return f"- **{name}**: {desc}"
        
        return f"- **{name}** ({func.get('tokens', 0)} tok): {desc}"
    
    def get_project_summary(self, max_files: int = 50) -> str:
        """
        Получить краткую сводку о всём проекте для AI.
        
        Args:
            max_files: Максимум файлов в выводе
        
        Returns:
            Отформатированная строка с обзором проекта
        """
        stats = self.stats
        files = self.list_files()[:max_files]
        
        lines = [
            "# Обзор проекта",
            f"**Путь:** `{self._index.get('root_path')}`",
            f"**Файлов:** {stats['total_files']} | **Токенов:** {stats['total_tokens']:,}",
            f"**Классов:** {stats['total_classes']} | **Функций:** {stats['total_functions']}",
            f"**Обновлён:** {self._index.get('updated_at', 'N/A')[:19]}",
            "",
            "## Файлы",
        ]
        
        for f in files:
            desc = f['description'][:60] + "..." if len(f['description']) > 60 else f['description']
            lines.append(
                f"- `{f['path']}` ({f['tokens']} tok): {desc}"
            )
        
        if len(self.list_files()) > max_files:
            lines.append(f"\n... и ещё {len(self.list_files()) - max_files} файлов")
        
        return "\n".join(lines)
    
    def get_class_context(self, class_name: str, include_file: bool = True) -> str:
        """
        Получить полный контекст для работы с классом.
        Включает информацию о классе, его файле и связях.
        
        Args:
            class_name: Имя класса
            include_file: Включать информацию о файле
        
        Returns:
            Контекст для AI
        """
        class_data = self.get_class(class_name)
        if not class_data:
            return f"[Класс не найден: {class_name}]"
        
        if "multiple_matches" in class_data:
            lines = [f"Найдено несколько классов '{class_name}':"]
            for match in class_data["matches"]:
                lines.append(f"- `{match['file_path']}`: {match['description'][:80]}")
            lines.append("\nУточните файл с помощью file_hint.")
            return "\n".join(lines)
        
        lines = [self._format_class_for_ai(class_data, DetailLevel.STANDARD)]
        
        if include_file:
            file_data = self.get_file(class_data["file_path"])
            if file_data:
                # Добавляем информацию о других классах в файле
                other_classes = [
                    c for c in file_data.get("classes", [])
                    if c.get("name") != class_name
                ]
                if other_classes:
                    lines.append(f"\n### Другие классы в файле")
                    for cls in other_classes:
                        lines.append(f"- **{cls['name']}**: {cls['description'][:80]}")
                
                # Импорты
                imports_data = file_data.get("imports") or {}
                modules = imports_data.get("modules", [])
                if modules:
                    lines.append(f"\n### Импорты файла")
                    lines.append(", ".join(modules[:20]))
        
        # Кто ссылается на этот класс
        refs = self.find_references_to(class_name)
        if refs:
            lines.append(f"\n### Используется в ({len(refs)} мест)")
            for ref in refs[:5]:
                lines.append(f"- {ref['type']} `{ref['name']}` в `{ref['file_path']}`")
        
        return "\n".join(lines)


# ==================== УТИЛИТЫ ====================

def quick_search(project_path: str, query: str) -> List[SearchResult]:
    """
    Быстрый поиск по проекту (удобная функция).
    
    Examples:
        >>> results = quick_search("/path/to/project", "auth")
    """
    reader = IndexReader(project_path)
    return reader.search(query)


def get_file_info(project_path: str, filename: str) -> Optional[Dict]:
    """
    Быстрое получение информации о файле.
    
    Examples:
        >>> info = get_file_info("/path/to/project", "auth.py")
    """
    reader = IndexReader(project_path)
    return reader.get_file(filename)


# ==================== CLI ====================

def main():
    """CLI для работы с индексом"""
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python index_reader.py <project_path> <command> [args]")
        print("\nCommands:")
        print("  stats                    - Show index statistics")
        print("  files                    - List all files")
        print("  classes                  - List all classes")
        print("  file <name>              - Get file info")
        print("  class <name>             - Get class info")
        print("  function <name>          - Get function info")
        print("  search <query>           - Search in index")
        print("  refs <name>              - Find references to class/function")
        print("  deps <filename>          - Show file dependencies")
        print("  context <name>           - Get AI context for file/class")
        print("  summary                  - Get project summary")
        sys.exit(1)
    
    project_path = sys.argv[1]
    command = sys.argv[2]
    args = sys.argv[3:] if len(sys.argv) > 3 else []
    
    try:
        reader = IndexReader(project_path)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)
    
    if command == "stats":
        stats = reader.stats
        print("📊 Index Statistics")
        print(f"   Version: {stats['version']}")
        print(f"   Updated: {stats['updated_at']}")
        print(f"   Files: {stats['total_files']}")
        print(f"   Tokens: {stats['total_tokens']:,}")
        print(f"   Classes: {stats['total_classes']}")
        print(f"   Functions: {stats['total_functions']}")
    
    elif command == "files":
        files = reader.list_files()
        print(f"📁 Files ({len(files)})\n")
        for f in files:
            print(f"  {f['path']}")
            print(f"    {f['tokens']} tok | {f['classes_count']} cls | {f['functions_count']} fn")
            print(f"    {f['description'][:70]}...")
            print()
    
    elif command == "classes":
        filter_arg = args[0] if args else None
        classes = reader.list_classes(filter_arg)
        print(f"📦 Classes ({len(classes)})\n")
        for c in classes:
            print(f"  {c['name']} ({c['file_name']})")
            print(f"    {c['tokens']} tok | methods: {', '.join(c['methods'][:5])}")
            print(f"    {c['description'][:70]}")
            print()
    
    elif command == "file" and args:
        data = reader.get_file(args[0])
        if data:
            print(reader.format_for_ai(args[0], DetailLevel.FULL))
        else:
            print(f"❌ File not found: {args[0]}")
    
    elif command == "class" and args:
        data = reader.get_class(args[0])
        if data:
            if "multiple_matches" in data:
                print(f"⚠️ Multiple matches for '{args[0]}':")
                for m in data["matches"]:
                    print(f"  - {m['file_path']}")
            else:
                print(reader._format_class_for_ai(data, DetailLevel.FULL))
        else:
            print(f"❌ Class not found: {args[0]}")
    
    elif command == "function" and args:
        data = reader.get_function(args[0])
        if data:
            print(reader._format_function_for_ai(data, DetailLevel.FULL))
        else:
            print(f"❌ Function not found: {args[0]}")
    
    elif command == "search" and args:
        query = " ".join(args)
        results = reader.search(query, limit=15)
        print(f"🔍 Search: '{query}' ({len(results)} results)\n")
        for r in results:
            print(f"  [{r.type}] {r.name}")
            print(f"    📁 {r.file_path}")
            print(f"    📝 {r.description[:70]}")
            print(f"    ⭐ relevance: {r.relevance:.2f}")
            print()
    
    elif command == "refs" and args:
        refs = reader.find_references_to(args[0])
        print(f"🔗 References to '{args[0]}' ({len(refs)})\n")
        for r in refs:
            print(f"  [{r['type']}] {r['name']} in {r['file_path']}")
            print(f"    refs: {r['references']}")
            print()
    
    elif command == "deps" and args:
        deps = reader.find_dependencies(args[0])
        if "error" in deps:
            print(f"❌ {deps['error']}")
        else:
            print(f"📦 Dependencies for '{deps['file']}'\n")
            print("  Imports:")
            for imp in deps["imports"]:
                local = "📁" if imp.get("is_local") else "📦"
                print(f"    {local} {imp['module']}")
            print("\n  Used by:")
            for use in deps["used_by"]:
                print(f"    ← {use['file_path']}")
    
    elif command == "context" and args:
        # Пробуем как класс, потом как файл
        class_data = reader.get_class(args[0])
        if class_data and "multiple_matches" not in class_data:
            print(reader.get_class_context(args[0]))
        else:
            print(reader.format_for_ai(args[0], DetailLevel.FULL, include_related=True))
    
    elif command == "summary":
        print(reader.get_project_summary())
    
    else:
        print(f"❌ Unknown command or missing args: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()