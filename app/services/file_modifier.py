# app/services/file_modifier.py
"""
File Modifier для Agent Mode.

Отвечает за применение сгенерированного кода к файлам:
- Вставка методов в классы
- Замена существующих методов/функций
- Добавление кода в конец файла
- Создание новых файлов
- Корректная обработка отступов

Интегрируется с VirtualFileSystem для staging изменений.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum
import textwrap
  

# После строки: from enum import Enum
# Добавить:

# Fault-tolerant parser (lazy import)
_tree_sitter_parser = None

def _get_tree_sitter_parser():
    """Ленивый импорт tree-sitter парсера."""
    global _tree_sitter_parser
    if _tree_sitter_parser is None:
        try:
            from app.services.tree_sitter_parser import FaultTolerantParser
            _tree_sitter_parser = FaultTolerantParser()
        except ImportError:
            _tree_sitter_parser = False  # Маркер что недоступен
    return _tree_sitter_parser if _tree_sitter_parser else None


if TYPE_CHECKING:
    from app.services.virtual_fs import VirtualFileSystem
    from app.agents.feedback_handler import StagingErrorType  



logger = logging.getLogger(__name__)


def classify_staging_error(error_message: str, mode: Optional[str] = None) -> "StagingErrorType":
    """Classifies a staging error message into a StagingErrorType for structured feedback."""
    # Lazy import to avoid circular dependency
    from app.agents.feedback_handler import StagingErrorType
    
    error_lower = error_message.lower()
    
    # Check patterns in order
    if "class" in error_lower and "not found" in error_lower:
        return StagingErrorType.CLASS_NOT_FOUND
    elif "method" in error_lower and "not found" in error_lower:
        return StagingErrorType.METHOD_NOT_FOUND
    elif "function" in error_lower and "not found" in error_lower:
        return StagingErrorType.FUNCTION_NOT_FOUND
    elif "pattern" in error_lower and "not found" in error_lower:
        return StagingErrorType.INSERT_PATTERN_NOT_FOUND
    elif "target_class" in error_lower and "required" in error_lower:
        return StagingErrorType.MISSING_TARGET_CLASS
    elif "target_method" in error_lower and "required" in error_lower:
        return StagingErrorType.MISSING_TARGET_METHOD
    elif "target_function" in error_lower and "required" in error_lower:
        return StagingErrorType.MISSING_TARGET_FUNCTION
    elif "unknown mode" in error_lower or "valid modes" in error_lower:
        return StagingErrorType.INVALID_MODE
    elif "parser" in error_lower and "not available" in error_lower:
        return StagingErrorType.PARSER_UNAVAILABLE
    # Check for new REPLACE_IN_* modes
    elif mode and mode.startswith("REPLACE_IN_"):
        if "class" in error_lower and "not found" in error_lower:
            return StagingErrorType.CLASS_NOT_FOUND
        elif "method" in error_lower and "not found" in error_lower:
            return StagingErrorType.METHOD_NOT_FOUND
        elif "function" in error_lower and "not found" in error_lower:
            return StagingErrorType.FUNCTION_NOT_FOUND
        elif "pattern" in error_lower and "not found" in error_lower:
            return StagingErrorType.INSERT_PATTERN_NOT_FOUND
        elif "attribute" in error_lower and "not found" in error_lower:
            return StagingErrorType.INSERT_PATTERN_NOT_FOUND
    elif mode == "ADD_NEW_FUNCTION" or mode == "CREATE_FUNCTION":
        if "must be a function definition" in error_lower:
            return StagingErrorType.INVALID_CODE_FORMAT
    
    return StagingErrorType.UNKNOWN


# ============================================================================
# ENUMS & DATA STRUCTURES
# ============================================================================

class ModifyMode(Enum):
    """Режимы модификации файла"""
    REPLACE_FILE = "replace_file"       # Заменить весь файл
    INSERT_INTO_CLASS = "insert_class"  # Вставить метод в класс
    INSERT_INTO_FILE = "insert_file"    # Вставить в файл (после импортов)
    APPEND_TO_FILE = "append_file"      # Добавить в конец файла
    REPLACE_METHOD = "replace_method"   # Заменить существующий метод
    REPLACE_FUNCTION = "replace_func"   # Заменить существующую функцию
    REPLACE_CLASS = "replace_class"     # Заменить существующий класс
    INSERT_IMPORT = "insert_import"     # Добавить импорт
    REPLACE_IMPORT = "replace_import"   # Заменить существующий импорт
    PATCH_METHOD = "patch_method"       # Вставить код внутрь существующего метода
    INSERT_IN_CLASS = "insert_in_class"        # Вставить атрибут в тело класса
    REPLACE_IN_CLASS = "replace_in_class"      # Заменить атрибут в теле класса
    REPLACE_IN_METHOD = "replace_in_method"    # Заменить строку внутри метода
    INSERT_IN_FUNCTION = "insert_in_function"  # Вставить код внутрь функции
    REPLACE_IN_FUNCTION = "replace_in_function" # Заменить код внутри функции
    ADD_NEW_FUNCTION = "add_new_function"      # Добавить новую функцию
    REPLACE_GLOBAL = "replace_global"          # Заменить глобальную переменную/константу


@dataclass
class ModifyInstruction:
    """Инструкция для модификации файла"""
    mode: ModifyMode
    code: str
    target_class: Optional[str] = None      # Для INSERT_INTO_CLASS, REPLACE_METHOD
    target_method: Optional[str] = None     # Для REPLACE_METHOD
    target_function: Optional[str] = None   # Для REPLACE_FUNCTION
    target_attribute: Optional[str] = None  # ⭐ НОВОЕ: Для замены атрибута в классе
    insert_after: Optional[str] = None      # Вставить после указанного элемента
    insert_before: Optional[str] = None     # Вставить перед указанным элементом
    replace_pattern: Optional[str] = None   # Паттерн для поиска и замены
    preserve_imports: bool = True           # Сохранять существующие импорты
    auto_format: bool = True                # Автоформатирование отступов



@dataclass
class ModifyResult:
    """Результат модификации"""
    success: bool
    new_content: str
    message: str
    changes_made: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "changes_made": self.changes_made,
            "warnings": self.warnings,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
        }



@dataclass
class ApplyResult:
    """Result of applying a CodeBlock (Agent Mode)"""
    success: bool
    message: str
    file_path: str
    mode: str
    changes_made: List[str]
    error_type: Optional[StagingErrorType] = None
    new_content: Optional[str] = None


# ============================================================================
# PARSED CODE BLOCK (from Generator Agent Mode output)
# ============================================================================

@dataclass
class ParsedCodeBlock:
    """
    Распарсенный CODE_BLOCK из ответа Генератора (Agent Mode).
    
    Формат CODE_BLOCK:
        ### CODE_BLOCK
        FILE: path/to/file.py
        MODE: REPLACE_METHOD
        TARGET_CLASS: ClassName
        TARGET_METHOD: method_name
        
        ```python
        def method_name(self):
            ...
        ```
        ### END_CODE_BLOCK
    
    Attributes:
        file_path: Путь к файлу
        mode: Режим модификации (строка, маппится на ModifyMode)
        code: Сгенерированный код
        target_class: Целевой класс (для методов)
        target_method: Целевой метод
        target_function: Целевая функция
        insert_after: Вставить после элемента
        insert_before: Вставить перед элементом
        replace_pattern: Паттерн для поиска и замены
    """
    file_path: str
    mode: str
    code: str
    target_class: Optional[str] = None
    target_method: Optional[str] = None
    target_function: Optional[str] = None
    target_attribute: Optional[str] = None  # ⭐ НОВОЕ: Для замены атрибута
    insert_after: Optional[str] = None
    insert_before: Optional[str] = None
    replace_pattern: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "mode": self.mode,
            "code": self.code,
            "target_class": self.target_class,
            "target_method": self.target_method,
            "target_function": self.target_function,
            "target_attribute": self.target_attribute,
            "insert_after": self.insert_after,
            "insert_before": self.insert_before,
            "replace_pattern": self.replace_pattern,
        }

# ============================================================================
# MAIN CLASS
# ============================================================================

class FileModifier:
    """
    Модификатор файлов для Agent Mode.
    
    Применяет код к файлам с учётом:
    - Структуры файла (классы, методы, функции)
    - Отступов (определяет автоматически)
    - Существующих импортов
    
    Example:
        >>> modifier = FileModifier()
        >>> 
        >>> # Вставка метода в класс
        >>> result = modifier.apply(
        ...     existing_content=old_code,
        ...     instruction=ModifyInstruction(
        ...         mode=ModifyMode.INSERT_INTO_CLASS,
        ...         code=new_method_code,
        ...         target_class="MyClass",
        ...         insert_after="__init__"
        ...     )
        ... )
        >>> print(result.new_content)
    """
    
    DEFAULT_INDENT = 4
    

    # ========================================================================
    # MODE MAPPING: CODE_BLOCK MODE → ModifyMode
    # ========================================================================
    
    MODE_MAPPING: Dict[str, ModifyMode] = {
        # Полная замена файла (новый файл или перезапись)
        "REPLACE_FILE": ModifyMode.REPLACE_FILE,
        "CREATE_FILE": ModifyMode.REPLACE_FILE,  # Алиас
        
        # Вставка в класс
        "INSERT_CLASS": ModifyMode.INSERT_INTO_CLASS,
        "ADD_METHOD": ModifyMode.INSERT_INTO_CLASS,  # Алиас
        
        # Вставка в файл (после импортов)
        "INSERT_FILE": ModifyMode.INSERT_INTO_FILE,
        "ADD_FUNCTION": ModifyMode.INSERT_INTO_FILE,  # Алиас
        
        # Добавление в конец файла
        "APPEND_FILE": ModifyMode.APPEND_TO_FILE,
        "APPEND": ModifyMode.APPEND_TO_FILE,  # Алиас
        
        # Замена существующего метода
        "REPLACE_METHOD": ModifyMode.REPLACE_METHOD,
        "MODIFY_METHOD": ModifyMode.REPLACE_METHOD,  # Алиас
        
        # Замена существующей функции
        "REPLACE_FUNCTION": ModifyMode.REPLACE_FUNCTION,
        "MODIFY_FUNCTION": ModifyMode.REPLACE_FUNCTION,  # Алиас
        
        # Замена существующего класса
        "REPLACE_CLASS": ModifyMode.REPLACE_CLASS,
        "MODIFY_CLASS": ModifyMode.REPLACE_CLASS,  # Алиас
        
        # Добавление импортов
        "INSERT_IMPORT": ModifyMode.INSERT_IMPORT,
        "ADD_IMPORT": ModifyMode.INSERT_IMPORT,  # Алиас
        
        # Замена импортов
        "REPLACE_IMPORT": ModifyMode.REPLACE_IMPORT,
        
        # Внутрь метода:
        "PATCH_METHOD": ModifyMode.PATCH_METHOD,
        "INSERT_INTO_METHOD": ModifyMode.PATCH_METHOD,  # Алиас
        "ADD_LINES": ModifyMode.PATCH_METHOD,           # Алиас
        
        # Работа с телом класса (атрибуты)
        "INSERT_IN_CLASS": ModifyMode.INSERT_IN_CLASS,
        "ADD_ATTRIBUTE": ModifyMode.INSERT_IN_CLASS,  # Алиас
        "REPLACE_IN_CLASS": ModifyMode.REPLACE_IN_CLASS,
        "MODIFY_ATTRIBUTE": ModifyMode.REPLACE_IN_CLASS,  # Алиас
        
        # Работа внутри методов
        "REPLACE_IN_METHOD": ModifyMode.REPLACE_IN_METHOD,
        "MODIFY_METHOD_LINE": ModifyMode.REPLACE_IN_METHOD,  # Алиас
        
        # Работа внутри функций
        "INSERT_IN_FUNCTION": ModifyMode.INSERT_IN_FUNCTION,
        "PATCH_FUNCTION": ModifyMode.INSERT_IN_FUNCTION,  # Алиас
        "REPLACE_IN_FUNCTION": ModifyMode.REPLACE_IN_FUNCTION,
        
        # Новые функции
        "ADD_NEW_FUNCTION": ModifyMode.ADD_NEW_FUNCTION,
        "CREATE_FUNCTION": ModifyMode.ADD_NEW_FUNCTION,  # Алиас
        
        # Глобальные переменные
        "REPLACE_GLOBAL": ModifyMode.REPLACE_GLOBAL,
        "MODIFY_GLOBAL": ModifyMode.REPLACE_GLOBAL,  # Алиас
        "REPLACE_CONSTANT": ModifyMode.REPLACE_GLOBAL,  # Алиас
    }
    


    def __init__(self, default_indent: int = 4):
        """
        Args:
            default_indent: Размер отступа по умолчанию (пробелы)
        """
        self.default_indent = default_indent
    
    def apply(
        self,
        existing_content: str,
        instruction: ModifyInstruction,
    ) -> ModifyResult:
        """
        Применяет инструкцию модификации к содержимому файла.
        
        Args:
            existing_content: Текущее содержимое файла (пустая строка для новых)
            instruction: Инструкция модификации
            
        Returns:
            ModifyResult с новым содержимым
        """
        mode = instruction.mode
        
        try:
            if mode == ModifyMode.REPLACE_FILE:
                result = self._replace_file(existing_content, instruction)
            
            elif mode == ModifyMode.INSERT_INTO_CLASS:
                result = self._insert_into_class(existing_content, instruction)
            
            elif mode == ModifyMode.INSERT_INTO_FILE:
                result = self._insert_into_file(existing_content, instruction)
            
            elif mode == ModifyMode.APPEND_TO_FILE:
                result = self._append_to_file(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_METHOD:
                result = self._replace_method(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_FUNCTION:
                result = self._replace_function(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_CLASS:
                result = self._replace_class(existing_content, instruction)
            
            elif mode == ModifyMode.INSERT_IMPORT:
                result = self._insert_import(existing_content, instruction)
                
            elif mode == ModifyMode.REPLACE_IMPORT:
                result = self._replace_import(existing_content, instruction)
            
            elif mode == ModifyMode.PATCH_METHOD:
                result = self._patch_method(existing_content, instruction)
            
            elif mode == ModifyMode.PATCH_METHOD:
                result = self._patch_method(existing_content, instruction)
            
            # ====== НОВЫЕ ОБРАБОТЧИКИ ======
            elif mode == ModifyMode.INSERT_IN_CLASS:
                result = self._insert_in_class(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_IN_CLASS:
                result = self._replace_in_class(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_IN_METHOD:
                result = self._replace_in_method(existing_content, instruction)
            
            elif mode == ModifyMode.INSERT_IN_FUNCTION:
                result = self._insert_in_function(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_IN_FUNCTION:
                result = self._replace_in_function(existing_content, instruction)
            
            elif mode == ModifyMode.ADD_NEW_FUNCTION:
                result = self._add_new_function(existing_content, instruction)
            
            elif mode == ModifyMode.REPLACE_GLOBAL:
                result = self._replace_global(existing_content, instruction)
            
            else:
                result = ModifyResult(
                    success=False,
                    new_content=existing_content,
                    message=f"Unknown modify mode: {mode}",
                )
                
            # === AUTO-CORRECTION LOGIC ===
            if not result.success:
                corrected = self._try_auto_correct(existing_content, instruction, result)
                if corrected and corrected.success:
                    corrected.message += " (Auto-corrected)"
                    corrected.changes_made.append(f"Auto-corrected mode from {mode.name}")
                    return corrected
            
            return result
                
        except Exception as e:
            logger.error(f"Modification failed: {e}", exc_info=True)
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Modification error: {e}",
                warnings=[str(e)],
            )
    
    
    def _try_auto_correct(
        self,
        existing_content: str,
        instruction: ModifyInstruction,
        failed_result: ModifyResult
    ) -> Optional[ModifyResult]:
        """
        Attempts to auto-correct common staging errors (e.g. Function vs Method confusion).
        Only triggers if the original modification failed with a 'not found' error.
        """
        # Only attempt correction for REPLACE operations
        if instruction.mode not in (ModifyMode.REPLACE_METHOD, ModifyMode.REPLACE_FUNCTION):
            return None
            
        # Check if failure is due to missing target
        if "not found" not in failed_result.message.lower():
            return None

        # Get parser
        ts_parser = _get_tree_sitter_parser()
        if not ts_parser:
            return None

        try:
            parse_result = ts_parser.parse(existing_content)
        except Exception as e:
            logger.warning(f"Auto-correct parse failed: {e}")
            return None

        # SCENARIO 1: Requested REPLACE_METHOD, but might be a global FUNCTION
        if instruction.mode == ModifyMode.REPLACE_METHOD:
            target_method = instruction.target_method
            if not target_method:
                return None
                
            # Check if this exists as a global function
            func_info = parse_result.get_function(target_method)
            if func_info:
                logger.info(f"Auto-correction: '{target_method}' is a function, not a method. Switching to REPLACE_FUNCTION.")
                
                new_instruction = ModifyInstruction(
                    mode=ModifyMode.REPLACE_FUNCTION,
                    code=instruction.code,
                    target_function=target_method,
                    # Copy other fields
                    insert_after=instruction.insert_after,
                    insert_before=instruction.insert_before,
                    replace_pattern=instruction.replace_pattern,
                    preserve_imports=instruction.preserve_imports,
                    auto_format=instruction.auto_format
                )
                return self._replace_function(existing_content, new_instruction)

        # SCENARIO 2: Requested REPLACE_FUNCTION, but might be a METHOD in a class
        elif instruction.mode == ModifyMode.REPLACE_FUNCTION:
            target_function = instruction.target_function
            if not target_function:
                return None
                
            # Check if this exists as a method in ANY class
            # We need a unique match to be safe
            candidates = []
            for cls in parse_result.classes:
                for method in cls.methods:
                    if method.name == target_function:
                        candidates.append(cls.name)
            
            if len(candidates) == 1:
                class_name = candidates[0]
                logger.info(f"Auto-correction: '{target_function}' is a method in '{class_name}'. Switching to REPLACE_METHOD.")
                
                new_instruction = ModifyInstruction(
                    mode=ModifyMode.REPLACE_METHOD,
                    code=instruction.code,
                    target_class=class_name,
                    target_method=target_function,
                    # Copy other fields
                    insert_after=instruction.insert_after,
                    insert_before=instruction.insert_before,
                    replace_pattern=instruction.replace_pattern,
                    preserve_imports=instruction.preserve_imports,
                    auto_format=instruction.auto_format
                )
                return self._replace_method(existing_content, new_instruction)
            
            elif len(candidates) > 1:
                logger.warning(f"Auto-correction ambiguous: '{target_function}' found in multiple classes: {candidates}")
        
        # SCENARIO 3: Requested REPLACE_IN_CLASS, but might be a METHOD in the same class
        elif instruction.mode == ModifyMode.REPLACE_IN_CLASS:
            target_class = instruction.target_class
            target_attribute = instruction.target_attribute
            
            if not target_class or not target_attribute:
                return None
            
            # Check if this exists as a method in the class
            method_info = parse_result.get_method(target_class, target_attribute)
            if method_info:
                logger.info(f"Auto-correction: '{target_attribute}' is a method, not an attribute. Switching to REPLACE_METHOD.")
                
                new_instruction = ModifyInstruction(
                    mode=ModifyMode.REPLACE_METHOD,
                    code=instruction.code,
                    target_class=target_class,
                    target_method=target_attribute,
                     # Copy other fields
                    insert_after=instruction.insert_after,
                    insert_before=instruction.insert_before,
                    replace_pattern=instruction.replace_pattern,
                    preserve_imports=instruction.preserve_imports,
                    auto_format=instruction.auto_format
                )
                return self._replace_method(existing_content, new_instruction)

        return None
    
    
    def apply_to_vfs(
        self,
        vfs: 'VirtualFileSystem',
        file_path: str,
        instruction: ModifyInstruction,
    ) -> ModifyResult:
        """
        Применяет модификацию и стейджит результат в VFS.
        
        Args:
            vfs: VirtualFileSystem instance
            file_path: Путь к файлу
            instruction: Инструкция модификации
            
        Returns:
            ModifyResult
        """
        # Импорт здесь для избежания circular import
        from app.services.virtual_fs import ChangeType
        
        # Читаем текущее содержимое через VFS
        existing_content = vfs.read_file(file_path) or ""
        
        # Применяем модификацию
        result = self.apply(existing_content, instruction)
        
        # Если успешно — стейджим
        if result.success:
            # Используем ChangeType enum, не строку!
            change_type = ChangeType.CREATE if not existing_content else ChangeType.MODIFY
            vfs.stage_change(file_path, result.new_content, change_type)
            logger.info(f"Staged modification to {file_path}")
        
        return result    
    
    # ========================================================================
    # CODE_BLOCK APPLICATION (Agent Mode)
    # ========================================================================
    
    def apply_code_block(self, existing_content: str, block: ParsedCodeBlock) -> ApplyResult:
        """
        Apply a single code block to content (VFS-aware).
        Delegates actual modification logic to self.apply().
        
        Args:
            existing_content: Current file content from VFS
            block: ParsedCodeBlock with mode and code
        
        Returns:
            ApplyResult with success status, new_content, and error info
        """
        result = ApplyResult(
            success=False,
            message="",
            file_path=block.file_path,
            mode=block.mode,
            changes_made=[],
            error_type=None,
            new_content=None
        )
        
        # Validate mode
        if block.mode not in self.MODE_MAPPING:
            result.message = f"Unknown mode: {block.mode}. Valid modes: {', '.join(self.MODE_MAPPING.keys())}"
            result.error_type = classify_staging_error(result.message, block.mode)
            return result
        
        try:
            # Map ParsedCodeBlock to ModifyInstruction
            instruction = ModifyInstruction(
                mode=self.MODE_MAPPING[block.mode],
                code=block.code,
                target_class=block.target_class,
                target_method=block.target_method,
                target_function=block.target_function,
                target_attribute=block.target_attribute, 
                insert_after=block.insert_after,
                insert_before=block.insert_before,
                replace_pattern=block.replace_pattern,
                preserve_imports=True,  # Always preserve imports
                auto_format=True        # Always auto-format
            )
            
            # Delegate to unified apply logic (includes Tree-sitter & auto-correct)
            modify_result = self.apply(existing_content, instruction)
            
            # Map ModifyResult to ApplyResult
            result.success = modify_result.success
            result.message = modify_result.message
            result.changes_made = modify_result.changes_made
            result.new_content = modify_result.new_content
            
            if not result.success:
                result.error_type = classify_staging_error(result.message, block.mode)
                
        except Exception as e:
            result.message = f"Failed to apply block: {str(e)}"
            result.error_type = classify_staging_error(result.message, block.mode)
            
        return result
    
    
    
    def apply_code_block_to_vfs(
        self,
        vfs: 'VirtualFileSystem',
        block: ParsedCodeBlock,
    ) -> ModifyResult:
        """
        Применяет CODE_BLOCK и стейджит результат в VFS.
        
        Args:
            vfs: VirtualFileSystem instance
            block: Распарсенный CODE_BLOCK
            
        Returns:
            ModifyResult
        """
        # Импорт здесь для избежания circular import
        from app.services.virtual_fs import ChangeType
        
        # Читаем текущее содержимое через VFS
        existing_content = vfs.read_file(block.file_path) or ""
        
        # Применяем модификацию
        result = self.apply_code_block(existing_content, block)
        
        # Если успешно — стейджим
        if result.success:
            # Используем ChangeType enum, не строку!
            change_type = ChangeType.CREATE if not existing_content else ChangeType.MODIFY
            vfs.stage_change(block.file_path, result.new_content, change_type)
            logger.info(f"Staged CODE_BLOCK modification to {block.file_path}")
        
        return result    
    
    # ========================================================================
    # MODIFICATION MODES
    # ========================================================================
    
    def _replace_file(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """Заменяет весь файл"""
        new_content = instruction.code
        
        # Опционально сохраняем импорты
        if instruction.preserve_imports and existing_content:
            existing_imports = self._extract_imports(existing_content)
            new_imports = self._extract_imports(new_content)
            
            missing = set(existing_imports) - set(new_imports)
            if missing:
                import_block = '\n'.join(sorted(missing))
                new_content = import_block + '\n\n' + new_content
        
        old_lines = len(existing_content.splitlines()) if existing_content else 0
        new_lines = len(new_content.splitlines())
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message="File content replaced",
            changes_made=["Replaced entire file content"],
            lines_added=max(0, new_lines - old_lines),
            lines_removed=max(0, old_lines - new_lines),
        )
  
  
    
    def _insert_into_class(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Вставляет метод в класс.
        Использует Tree-sitter для парсинга.
        """
        target_class = instruction.target_class
        code = instruction.code
        insert_after = instruction.insert_after
        
        if not target_class:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_class is required for INSERT_INTO_CLASS",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        class_info = parse_result.get_class(target_class)
        
        if class_info is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Class '{target_class}' not found",
            )
        
        # Определяем отступ метода
        method_indent = None
        if class_info.methods:
            first_method = class_info.methods[0]
            if first_method.span.start_line - 1 < len(lines):
                # Используем сохраненный indent или вычисляем
                method_indent = first_method.indent
        
        if method_indent is None:
            # Fallback: отступ класса + default
            class_indent = class_info.indent
            method_indent = class_indent + self.default_indent
        
        # Позиция вставки
        insert_line = None
        if insert_after and class_info.methods:
            for method in class_info.methods:
                if method.name == insert_after:
                    insert_line = method.span.end_line
                    break
        
        if insert_line is None:
            insert_line = class_info.span.end_line
        
        # === Анализируем и нормализуем код с правильным отступом ===
        formatted_code = self._analyze_and_normalize_indent(code, method_indent)
        
        # Добавляем пустую строку перед методом
        prefix = '\n'
        if insert_line > 0 and insert_line <= len(lines):
            prev_line = lines[insert_line - 1]
            if not prev_line.strip():
                prefix = ''
        
        # Вставляем
        insert_content = prefix + formatted_code + '\n'
        lines.insert(insert_line, insert_content)
        new_content = ''.join(lines)
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        added_lines = len(formatted_code.splitlines())
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Inserted code into class '{target_class}'",
            changes_made=[f"Added method to {target_class} at line {insert_line + 1}"],
            lines_added=added_lines,
        )
    
    
    def _insert_into_file(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Вставляет код после импортов.
        
        УЛУЧШЕНО: Автоматически определяет отступ (обычно 0 для module-level).
        """
        code = instruction.code
        
        if not existing_content.strip():
            # Новый файл — вставляем как есть с нормализацией
            formatted_code = self._analyze_and_normalize_indent(code, 0)
            return ModifyResult(
                success=True,
                new_content=formatted_code + '\n',
                message="Created new file",
                changes_made=["Created new file with provided code"],
                lines_added=len(formatted_code.splitlines()),
            )
        
        lines = existing_content.splitlines(keepends=True)
        insert_line = self._find_imports_end(lines)
        
        # Для module-level кода отступ = 0
        formatted_code = self._analyze_and_normalize_indent(code, 0)
        
        # Добавляем пустые строки вокруг для PEP8
        insert_content = '\n\n' + formatted_code.strip() + '\n'
        
        lines.insert(insert_line, insert_content)
        new_content = ''.join(lines)
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Inserted code at line {insert_line + 1}",
            changes_made=[f"Added code after imports at line {insert_line + 1}"],
            lines_added=len(code.splitlines()),
        )
    
    
    def _append_to_file(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """Добавляет код в конец файла"""
        code = instruction.code.strip()
        
        # Определяем разделитель
        if existing_content:
            if existing_content.endswith('\n\n'):
                separator = ''
            elif existing_content.endswith('\n'):
                separator = '\n'
            else:
                separator = '\n\n'
        else:
            separator = ''
        
        new_content = existing_content + separator + code + '\n'
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message="Appended code to end of file",
            changes_made=["Appended code to end of file"],
            lines_added=len(code.splitlines()),
        )
   
    
    def _replace_method(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет метод в классе используя Tree-sitter.
        """
        target_class = instruction.target_class
        target_method = instruction.target_method
        code = instruction.code
        
        if not target_class or not target_method:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_class and target_method required",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        
        method_info = parse_result.get_method(target_class, target_method)
        if method_info is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Method '{target_method}' not found in class '{target_class}'",
            )
        
        # Tree-sitter span — 1-indexed
        method_start = method_info.span.start_line - 1
        method_end = method_info.span.end_line
        
        # Определяем отступ
        method_indent = method_info.indent
        
        # === Анализируем и нормализуем код с правильным отступом ===
        formatted_code = self._analyze_and_normalize_indent(code, method_indent)
        
        old_lines_count = method_end - method_start
        
        if not formatted_code.endswith('\n'):
            formatted_code += '\n'
        
        new_lines = lines[:method_start] + [formatted_code] + lines[method_end:]
        new_content = ''.join(new_lines)
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        new_lines_count = len(formatted_code.splitlines())
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Replaced method '{target_method}' in class '{target_class}'",
            changes_made=[f"Replaced {target_class}.{target_method}"],
            lines_added=max(0, new_lines_count - old_lines_count),
            lines_removed=max(0, old_lines_count - new_lines_count),
        )
   
    
    def _replace_function(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет функцию на уровне модуля используя Tree-sitter.
        """
        target_function = instruction.target_function
        code = instruction.code
        
        if not target_function:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_function required",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        func_info = parse_result.get_function(target_function)
        
        if func_info is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Function '{target_function}' not found",
            )
        
        func_start = func_info.span.start_line - 1
        func_end = func_info.span.end_line
        func_indent = func_info.indent
        
        # === Анализируем и нормализуем код с правильным отступом ===
        formatted_code = self._analyze_and_normalize_indent(code, func_indent)
        
        old_lines_count = func_end - func_start
        
        if not formatted_code.endswith('\n\n'):
            formatted_code = formatted_code.rstrip('\n') + '\n\n'
        
        new_lines = lines[:func_start] + [formatted_code] + lines[func_end:]
        new_content = ''.join(new_lines)
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        new_lines_count = len(formatted_code.splitlines())
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Replaced function '{target_function}'",
            changes_made=[f"Replaced function {target_function}"],
            lines_added=max(0, new_lines_count - old_lines_count),
            lines_removed=max(0, old_lines_count - new_lines_count),
        )
    
    
    def _replace_class(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет класс.
        
        УЛУЧШЕНО: Использует Tree-sitter для fault-tolerant парсинга.
        """
        target_class = instruction.target_class
        code = instruction.code
        
        if not target_class:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_class required",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        class_info = parse_result.get_class(target_class)
        
        if class_info is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Class '{target_class}' not found",
                warnings=[f"Errors: {parse_result.errors[:1]}"] if parse_result.has_errors else []
            )
        
        class_start = class_info.span.start_line - 1
        class_end = class_info.span.end_line
        
        # Определяем отступ из распарсенного дерева
        class_indent = class_info.indent
        
        # === Анализируем и нормализуем код с правильным отступом ===
        formatted_code = self._analyze_and_normalize_indent(code, class_indent)
        
        old_lines_count = class_end - class_start
        
        # Добавляем пустые строки после класса для PEP8
        if not formatted_code.endswith('\n\n'):
            formatted_code = formatted_code.rstrip('\n') + '\n\n'
        
        new_lines = lines[:class_start] + [formatted_code] + lines[class_end:]
        new_content = ''.join(new_lines)
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        new_lines_count = len(formatted_code.splitlines())
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Replaced class '{target_class}'",
            changes_made=[f"Replaced class {target_class}"],
            lines_added=max(0, new_lines_count - old_lines_count),
            lines_removed=max(0, old_lines_count - new_lines_count),
        )
   
    
    def _insert_import(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """Добавляет импорт если его ещё нет"""
        import_statement = instruction.code.strip()
        
        # Проверяем, есть ли уже такой импорт
        if import_statement in existing_content:
            return ModifyResult(
                success=True,
                new_content=existing_content,
                message="Import already exists",
                warnings=["Import already present, skipped"],
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        # Находим конец импортов (ПОСЛЕ всех импортов, включая многострочные)
        insert_line = self._find_imports_end(lines)
        
        # Вставляем новый импорт ПОСЛЕ блока импортов
        # НЕ ищем строки с 'import '/'from ' — это ломает многострочные импорты!
        if insert_line == 0:
            # Нет импортов — вставляем в начало
            lines.insert(0, import_statement + '\n')
        else:
            # Вставляем после последнего импорта
            lines.insert(insert_line, import_statement + '\n')
        
        new_content = ''.join(lines)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Added import: {import_statement}",
            changes_made=[f"Added import: {import_statement}"],
            lines_added=1,
        )
    
    
    def _replace_import(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет существующий импорт.
        
        Использует replace_pattern для поиска импорта в распарсенном дереве.
        """
        replace_pattern = instruction.replace_pattern
        new_import = instruction.code.strip()
        
        if not replace_pattern:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="replace_pattern required for REPLACE_IMPORT",
            )
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        try:
            parse_result = ts_parser.parse(existing_content)
            
            # Ищем импорт, содержащий паттерн
            target_import = None
            for imp in parse_result.parsed_imports:
                if replace_pattern in imp.text:
                    target_import = imp
                    break
            
            if not target_import:
                return ModifyResult(
                    success=False,
                    new_content=existing_content,
                    message=f"Import matching pattern '{replace_pattern}' not found",
                )
            
            # Получаем строки
            lines = existing_content.splitlines(keepends=True)
            
            # Tree-sitter использует 1-based indexing для линий
            start_idx = target_import.span.start_line - 1
            end_idx = target_import.span.end_line
            
            # Формируем новый контент
            # Если новый импорт пустой - это удаление
            if not new_import:
                new_lines = lines[:start_idx] + lines[end_idx:]
                msg = f"Removed import matching '{replace_pattern}'"
            else:
                new_lines = lines[:start_idx] + [new_import + '\n'] + lines[end_idx:]
                msg = f"Replaced import matching '{replace_pattern}'"
            
            new_content = ''.join(new_lines)
            
            return ModifyResult(
                success=True,
                new_content=new_content,
                message=msg,
                changes_made=[msg],
                lines_added=1 if new_import else 0,
                lines_removed=end_idx - start_idx
            )
            
        except Exception as e:
            logger.error(f"Import replacement failed: {e}")
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Error replacing import: {e}",
            )
    
    def _patch_method(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Вставляет код внутрь существующего метода.
        
        УЛУЧШЕНО: Робастный поиск якорей (игнорирует пробелы, предпочитает точное совпадение).
        """
        target_class = instruction.target_class
        target_method = instruction.target_method
        code = instruction.code
        insert_after = instruction.insert_after
        insert_before = instruction.insert_before
        
        if not target_method:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_method required for PATCH_METHOD",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        
        if target_class:
            method_info = parse_result.get_method(target_class, target_method)
            if method_info is None:
                if parse_result.get_class(target_class) is None:
                    return ModifyResult(
                        success=False,
                        new_content=existing_content,
                        message=f"Class '{target_class}' not found",
                    )
                return ModifyResult(
                    success=False,
                    new_content=existing_content,
                    message=f"Method '{target_method}' not found in class '{target_class}'",
                )
        else:
            method_info = parse_result.get_function(target_method)
            if method_info is None:
                return ModifyResult(
                    success=False,
                    new_content=existing_content,
                    message=f"Function '{target_method}' not found",
                )
        
        method_start = method_info.span.start_line - 1
        method_end = method_info.span.end_line
        
        # Получаем строки метода
        method_lines = lines[method_start:method_end]

        # === IDEMPOTENCY CHECK: Try to find and replace matching lines ===
        code_lines = [l.strip() for l in code.strip().splitlines() if l.strip()]
        method_lines_stripped = [l.strip() for l in method_lines]
        
        if code_lines:
            first_code_line = code_lines[0]
            match_start_offset = None
            for i, method_line in enumerate(method_lines_stripped):
                if first_code_line == method_line:
                    # Found potential match - verify consecutive lines match
                    match_count = 0
                    for j, code_line in enumerate(code_lines):
                        if i + j < len(method_lines_stripped):
                            if code_line == method_lines_stripped[i + j]:
                                match_count += 1
                            else:
                                break
                        else:
                            break
                    
                    if match_count == len(code_lines):
                        match_start_offset = i
                        break
            
            if match_start_offset is not None:
                matched_line = method_lines[match_start_offset]
                body_indent = len(matched_line) - len(matched_line.lstrip())
                formatted_code = self._analyze_and_normalize_indent(code, body_indent)
                
                replace_start = method_start + match_start_offset
                replace_end = replace_start + len(code_lines)
                
                new_lines = lines[:replace_start] + [formatted_code + '\n'] + lines[replace_end:]
                new_content = ''.join(new_lines)
                
                new_content = self._validate_and_fix_syntax(new_content)
                target_name = f"{target_class}.{target_method}" if target_class else target_method
                
                return ModifyResult(
                    success=True,
                    new_content=new_content,
                    message=f"Replaced {len(code_lines)} lines in method '{target_name}'",
                    changes_made=[f"Replaced lines {replace_start + 1}-{replace_end} in {target_name}"],
                )
        # === END IDEMPOTENCY CHECK ===
        
        # Get method body indent using Tree-sitter when possible
        body_base_indent = self._get_method_body_indent(method_info, lines, method_start)
        
        insert_line_offset = None
        context_line_for_indent = None
        is_block_node = False
        node_info = None
        
        # Helper to find robust match
        def find_best_match(pattern: str, lines: List[str]) -> Tuple[Optional[int], Optional[str]]:
            if not pattern:
                return None, None
            
            pattern_stripped = pattern.strip()
            
            # Pass 1: Exact match of stripped content
            for i, line in enumerate(lines):
                if line.strip() == pattern_stripped:
                    return i, line
            
            # Pass 2: Substring match (robust to spacing)
            for i, line in enumerate(lines):
                # Normalize spaces to single space for comparison
                line_norm = ' '.join(line.split())
                pattern_norm = ' '.join(pattern.split())
                if pattern_norm in line_norm:
                    return i, line
            
            # Pass 3: Fallback loose substring
            for i, line in enumerate(lines):
                if pattern_stripped in line:
                    return i, line
                    
            return None, None

        # 1. Determine insertion point
        method_text = ''.join(method_lines)
        
        if insert_after:
            # Try Tree-sitter first
            node_info, line_offset, matched_line = self._find_statement_node(
                method_text, insert_after, lines, method_lines, method_start
            )
            
            if node_info:
                insert_line_offset = line_offset + 1
                context_line_for_indent = matched_line
                is_block_node = self._is_block_statement(node_info['node_type'])
            else:
                # Fallback to text search
                idx, match_line = find_best_match(insert_after, method_lines)
                if idx is not None:
                    insert_line_offset = idx + 1
                    context_line_for_indent = match_line
                else:
                    return ModifyResult(
                        success=False,
                        new_content=existing_content,
                        message=f"Pattern '{insert_after}' not found in method '{target_method}'",
                    )
        
        elif insert_before:
            # Try Tree-sitter first
            node_info, line_offset, matched_line = self._find_statement_node(
                method_text, insert_before, lines, method_lines, method_start
            )
            
            if node_info:
                insert_line_offset = line_offset
                context_line_for_indent = matched_line
                is_block_node = self._is_block_statement(node_info['node_type'])
            else:
                # Fallback to text search
                idx, match_line = find_best_match(insert_before, method_lines)
                if idx is not None:
                    insert_line_offset = idx
                    context_line_for_indent = match_line
                else:
                    return ModifyResult(
                        success=False,
                        new_content=existing_content,
                        message=f"Pattern '{insert_before}' not found in method '{target_method}'",
                    )
        
        else:
            # Default: before first return or at end
            last_return_offset = None
            last_return_line = None
            
            for i, line in enumerate(method_lines):
                stripped = line.strip()
                if stripped.startswith('return ') or stripped == 'return':
                    last_return_offset = i
                    last_return_line = line
            
            if last_return_offset is not None:
                insert_line_offset = last_return_offset
                context_line_for_indent = last_return_line
            else:
                insert_line_offset = len(method_lines)
                for line in reversed(method_lines):
                    if line.strip():
                        context_line_for_indent = line
                        break
        
        # 2. Determine indentation (REFACTORED: prioritize AST over text heuristics)
        if context_line_for_indent:
            if node_info:
                # AST-PRIORITY: Use exact indent from Tree-sitter
                base_indent = node_info['indent']
                # Determine block start based on node type and operation
                is_block_node = self._is_block_statement(node_info['node_type'])
                # Only add indent when inserting AFTER a block statement
                is_block_start = is_block_node and insert_after
            else:
                # FALLBACK: Text-based heuristics (only when AST not available)
                line_stripped = context_line_for_indent.strip()
                is_block_start = line_stripped.endswith(':')
                base_indent = len(context_line_for_indent) - len(context_line_for_indent.lstrip())
            
            if is_block_start:
                # Inside a block: indent one level deeper than the block statement
                body_indent = base_indent + self.default_indent
            else:
                # Same level as context line
                body_indent = base_indent
        else:
            # No context line, use method body base indent
            body_indent = body_base_indent
        
        # 3. Analyze and normalize indent
        formatted_code = self._analyze_and_normalize_indent(code, body_indent)
        
        # 4. Calculate absolute position
        absolute_insert_line = method_start + insert_line_offset
        
        # Add spacing
        prefix = ''
        if absolute_insert_line > 0 and absolute_insert_line <= len(lines):
            prev_line = lines[absolute_insert_line - 1]
            if prev_line.strip():
                prefix = '\n'
        
        # Insert
        insert_content = prefix + formatted_code + '\n'
        lines.insert(absolute_insert_line, insert_content)
        new_content = ''.join(lines)
        
        new_content = self._validate_and_fix_syntax(new_content)
        
        added_lines = len(formatted_code.splitlines())
        target_name = f"{target_class}.{target_method}" if target_class else target_method
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Patched method '{target_name}' at line {absolute_insert_line + 1}",
            changes_made=[f"Inserted {added_lines} lines into {target_name}"],
            lines_added=added_lines,
        )
    
    
    def _get_method_body_indent(self, method_info: Any, lines: List[str], method_start: int) -> int:
        """Determines the actual indent of method body using Tree-sitter or fallback to heuristic."""
        try:
            parser = _get_tree_sitter_parser()
            if parser and hasattr(method_info, 'body_start_line'):
                body_line_idx = method_info.body_start_line - 1
                if body_line_idx < len(lines):
                    line = lines[body_line_idx]
                    stripped = line.strip()
                    if stripped and not stripped.startswith(('"""', "'''")):
                        return len(line) - len(line.lstrip())
        except Exception as e:
            logger.debug(f"Tree-sitter body indent detection failed: {e}")
        
        # Fallback 1: Use method indent + default
        fallback_indent = method_info.indent + self.default_indent
        
        # Fallback 2: Find first non-empty line after method definition
        for i in range(method_start + 1, min(method_start + 10, len(lines))):
            line = lines[i]
            stripped = line.strip()
            if stripped and not stripped.startswith(('"""', "'''")):
                return len(line) - len(line.lstrip())
        
        # Default: Return fallback indent
        return fallback_indent
    
    
    def _is_block_statement(self, node_type: str) -> bool:
        """Checks if a Tree-sitter node type represents a block statement (if/for/while/with/try)."""
        block_types = {
            'if_statement',
            'for_statement',
            'while_statement',
            'with_statement',
            'try_statement',
            'match_statement'
        }
        
        if node_type in block_types:
            return True
        
        if node_type.endswith('_clause'):
            return True
        
        return False
    
    def _find_statement_node(
        self,
        method_text: str,
        pattern: str,
        lines: List[str],
        method_lines: List[str],
        method_start: int
    ) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        """Finds a statement node matching pattern using Tree-sitter, returns node info, line offset, and matched line."""
        parser = _get_tree_sitter_parser()
        if not parser:
            return None, None, None
        
        try:
            parse_result = parser.parse(method_text)
            if not parse_result.root_node:
                return None, None, None
            
            # Normalize pattern for comparison
            pattern_normalized = ' '.join(pattern.split()).strip()
            
            def traverse_ast(node):
                """Recursively traverse AST to find statement nodes."""
                if 'statement' in node.type or 'expression' in node.type:
                    node_text = method_text[node.start_byte:node.end_byte]
                    node_text_normalized = ' '.join(node_text.split()).strip()
                    
                    if node_text_normalized == pattern_normalized:
                        return {
                            'node_type': node.type,
                            'start_line': node.start_point[0],
                            'indent': node.start_point[1],
                            'text': node_text
                        }
                
                for child in node.children:
                    result = traverse_ast(child)
                    if result:
                        return result
                
                return None
            
            node_info = traverse_ast(parse_result.root_node)
            
            if node_info:
                line_offset = node_info['start_line']
                if line_offset < len(method_lines):
                    matched_line = method_lines[line_offset]
                    return node_info, line_offset, matched_line
            
            return None, None, None
            
        except Exception as e:
            logger.debug(f"Tree-sitter statement search failed: {e}")
            return None, None, None
    
    
    # ========================================================================
    # НОВЫЕ РЕЖИМЫ РЕАЛИЗАЦИЯ
    # ========================================================================
    
    def _insert_in_class(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Вставляет атрибут (поле) в тело класса.
        
        Args:
            instruction:
                - target_class: Имя класса
                - insert_after: Якорь - поле, после которого вставить
                - code: Строка атрибута (например: "name = Column(String)")
        """
        target_class = instruction.target_class
        insert_after = instruction.insert_after
        code = instruction.code.strip()
        
        if not target_class:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_class is required for INSERT_IN_CLASS",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        class_info = parse_result.get_class(target_class)
        
        if class_info is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Class '{target_class}' not found",
            )
        
        # Получаем отступ класса
        class_indent = class_info.indent
        body_indent = class_indent + self.default_indent
        
        # ⭐ ДОБАВЛЯЕМ: Проверка идемпотентности - не добавлять если уже существует
        code_stripped = code.strip()
        if code_stripped:
            # Проверяем, есть ли уже такая строка в теле класса
            class_start = class_info.span.start_line
            class_end = class_info.span.end_line
            # Формируем то, как будет выглядеть добавленный код (с отступом)
            expected_line = ' ' * body_indent + code_stripped
            
            for i in range(class_start - 1, class_end - 1):  # -1 т.к. 1-indexed -> 0-indexed
                line = lines[i].rstrip('\n')
                # Сравниваем с отступом и без
                if line == expected_line or line.lstrip() == code_stripped:
                    return ModifyResult(
                        success=True,
                        new_content=existing_content,
                        message=f"Attribute already exists in class '{target_class}'",
                        changes_made=["Attribute already present, skipped"],
                    )
            
        # Находим позицию для вставки
        insert_line = class_info.span.end_line - 1  # Перед закрывающей строкой класса
        found_anchor = False
        
        if insert_after:
            # Ищем якорь в теле класса
            class_start = class_info.span.start_line - 1
            class_end = class_info.span.end_line - 1
            
            for i in range(class_start, class_end):
                line = lines[i].rstrip('\n')
                if insert_after in line and len(line) - len(line.lstrip()) == body_indent:
                    insert_line = i + 1
                    found_anchor = True
                    break
        
        # Если якорь не найден, вставляем перед последней строкой класса
        if not found_anchor:
            # Ищем первую строку метода (чтобы вставить перед методами)
            for method in class_info.methods:
                method_start = method.span.start_line - 1
                if method_start > class_info.span.start_line:
                    insert_line = method_start
                    break
        
        # Форматируем код с правильным отступом
        formatted_code = self._analyze_and_normalize_indent(code, body_indent)
        if not formatted_code.endswith('\n'):
            formatted_code += '\n'
        
        # Вставляем
        if '\n' in formatted_code.rstrip('\n'):
            # Многострочная вставка
            for i, line in enumerate(reversed(formatted_code.splitlines(keepends=True))):
                lines.insert(insert_line, line)
            lines_added = len(formatted_code.splitlines())
        else:
            lines.insert(insert_line, formatted_code)
            lines_added = 1
        
        new_content = ''.join(lines)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Inserted attribute into class '{target_class}'",
            changes_made=[f"Added attribute to {target_class} at line {insert_line + 1}"],
            lines_added=1,
        )
    
    def _replace_in_class(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет атрибут в теле класса.
        
        Args:
            instruction:
                - target_class: Имя класса
                - replace_pattern: Старая строка атрибута (полностью или частично)
                - code: Новая строка атрибута
        """
        target_class = instruction.target_class
        replace_pattern = instruction.replace_pattern
        target_attribute = instruction.target_attribute
        code = instruction.code.strip()
        
        if not target_class or (not replace_pattern and not target_attribute):
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_class and (replace_pattern or target_attribute) required for REPLACE_IN_CLASS",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        class_info = parse_result.get_class(target_class)
        
        if class_info is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Class '{target_class}' not found",
            )
        
        # Получаем отступ класса
        class_indent = class_info.indent
        body_indent = class_indent + self.default_indent
        
        # Ищем заменяемую строку в теле класса
        class_start = class_info.span.start_line - 1
        class_end = class_info.span.end_line - 1
        
        target_line_idx = -1
        for i in range(class_start, class_end):
            line = lines[i]
            line_stripped = line.strip()
            line_indent = len(line) - len(line.lstrip())

            # Проверяем что строка на правильном уровне отступа и содержит паттерн
            if line_indent == body_indent:
                # Если указано имя атрибута, ищем строку с определением этого атрибута
                if target_attribute and (line_stripped.startswith(f"{target_attribute} = ") or
                                         line_stripped.startswith(f"{target_attribute}:")):
                    target_line_idx = i
                    break
                # Если указан паттерн, ищем по содержанию
                elif replace_pattern and replace_pattern in line_stripped:
                    target_line_idx = i
                    break
        
        if target_line_idx == -1:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Pattern '{replace_pattern}' not found in class '{target_class}'",
            )
        
        # Заменяем строку
        old_line = lines[target_line_idx]
        formatted_code = self._analyze_and_normalize_indent(code, body_indent)
        if not formatted_code.endswith('\n'):
            formatted_code += '\n'
        
        old_lines_count = 1
        if '\n' in formatted_code.rstrip('\n'):
            # Многострочная замена
            lines.pop(target_line_idx)
            for i, line in enumerate(formatted_code.splitlines(keepends=True)):
                lines.insert(target_line_idx + i, line)
            new_lines_count = len(formatted_code.splitlines())
            lines_added = max(0, new_lines_count - old_lines_count)
            lines_removed = max(0, old_lines_count - new_lines_count)
        else:
            # Однострочная замена
            lines[target_line_idx] = formatted_code
            lines_added = 1
            lines_removed = 1
        
        new_content = ''.join(lines)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Replaced attribute in class '{target_class}'",
            changes_made=[f"Replaced line {target_line_idx + 1} in {target_class}"],
            lines_added=1,
            lines_removed=1,
        )
    
    def _replace_in_method(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет конкретную строку или блок внутри метода.
        
        Args:
            instruction:
                - target_class: Имя класса (опционально)
                - target_method: Имя метода
                - replace_pattern: Что заменять
                - code: На что заменять
        """
        target_class = instruction.target_class
        target_method = instruction.target_method
        replace_pattern = instruction.replace_pattern
        code = instruction.code
        
        if not target_method or not replace_pattern:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_method and replace_pattern required for REPLACE_IN_METHOD",
            )
        
        if not target_class:
            # Делегируем _replace_in_function
            return self._replace_in_function(existing_content, instruction)
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        
        # Получаем информацию о методе
        method_info = None
        if target_class:
            method_info = parse_result.get_method(target_class, target_method)
        else:
            # Ищем как функцию
            method_info = parse_result.get_function(target_method)
        
        if method_info is None:
            target_name = f"{target_class}.{target_method}" if target_class else target_method
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Method/function '{target_name}' not found",
            )
        
        # Находим строку для замены внутри метода
        method_start = method_info.span.start_line - 1
        method_end = method_info.span.end_line
        
        target_line_idx = -1
        for i in range(method_start, method_end):
            if replace_pattern in lines[i]:
                target_line_idx = i
                break
        
        if target_line_idx == -1:
            target_name = f"{target_class}.{target_method}" if target_class else target_method
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Pattern '{replace_pattern}' not found in '{target_name}'",
            )
        
        # Определяем отступ строки
        old_line = lines[target_line_idx]
        line_indent = len(old_line) - len(old_line.lstrip())
        
        # 🔧 ИЗМЕНЕНИЕ 1: Заменяем сложную логику нормализации на вызов _analyze_and_normalize_indent
        formatted_code = self._analyze_and_normalize_indent(code, line_indent)
        if not formatted_code.endswith('\n'):
            formatted_code += '\n'
        
        # 🔧 ИЗМЕНЕНИЕ 2: Улучшаем проверку на многострочность
        if '\n' in formatted_code.rstrip('\n'):
            # Замена блока - удаляем старую строку, вставляем новые
            lines.pop(target_line_idx)
            for i, line in enumerate(formatted_code.splitlines(keepends=True)):
                lines.insert(target_line_idx + i, line)
        else:
            # Замена одной строки
            lines[target_line_idx] = formatted_code
        
        new_content = ''.join(lines)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Replaced code in {target_method}",
            changes_made=[f"Replaced line in {target_method}"],
            lines_added=len(formatted_code.splitlines()),
            lines_removed=1,
        )
    
            
    def _insert_in_function(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Вставляет код внутрь функции.
        Делегирует в _patch_method, адаптируя target_function -> target_method.
        """
        if not instruction.target_function:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_function is required for INSERT_IN_FUNCTION",
            )

        # Создаем копию инструкции, маппим target_function -> target_method
        patch_instruction = ModifyInstruction(
            mode=ModifyMode.PATCH_METHOD,
            code=instruction.code,
            target_method=instruction.target_function, # ВАЖНО: маппим сюда!
            target_class=None,                         # Класса нет
            insert_after=instruction.insert_after,
            insert_before=instruction.insert_before,
            replace_pattern=instruction.replace_pattern,
            preserve_imports=instruction.preserve_imports,
            auto_format=instruction.auto_format
        )
        
        return self._patch_method(existing_content, patch_instruction)

    def _replace_in_function(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет строку внутри функции.
        Делегирует в _replace_in_method, адаптируя target_function -> target_method.
        """
        if not instruction.target_function or not instruction.replace_pattern:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="target_function and replace_pattern required for REPLACE_IN_FUNCTION",
            )

        # Создаем копию инструкции, маппим target_function -> target_method
        replace_instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_IN_METHOD,
            code=instruction.code,
            target_method=instruction.target_function, # ВАЖНО: маппим сюда!
            target_class=None,                         # Класса нет
            replace_pattern=instruction.replace_pattern,
            preserve_imports=instruction.preserve_imports,
            auto_format=instruction.auto_format
        )

        return self._replace_in_method(existing_content, replace_instruction)

    
    def _add_new_function(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Добавляет новую функцию в файл.
        
        Args:
            instruction:
                - insert_after: После какой функции/класса вставить
                - code: Код новой функции (полностью, начиная с def)
        """
        code = instruction.code.strip()
        insert_after = instruction.insert_after
        
        if not code.startswith(('def ', 'async def ')):
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Code must be a function definition starting with 'def' or 'async def'",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        
        insert_line = len(lines)  # По умолчанию в конец файла
        
        if insert_after:
            # Ищем функцию или класс для вставки после
            found = False
            
            # Проверяем функции
            for func in parse_result.functions:
                if func.name == insert_after:
                    insert_line = func.span.end_line
                    found = True
                    break
            
            # Проверяем классы
            if not found:
                for cls in parse_result.classes:
                    if cls.name == insert_after:
                        insert_line = cls.span.end_line
                        found = True
                        break
        
        # Нормализуем отступ функции (должен быть 0 для module-level)
        formatted_code = self._analyze_and_normalize_indent(code, 0)
        
        # Добавляем пустые строки для PEP8
        prefix = '\n\n'
        if insert_line == 0 or (insert_line > 0 and not lines[insert_line - 1].strip()):
            prefix = '\n'
        
        formatted_code = prefix + formatted_code + '\n'
        
        # Вставляем
        lines.insert(insert_line, formatted_code)
        new_content = ''.join(lines)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Added new function",
            changes_made=[f"Added function at line {insert_line + 1}"],
            lines_added=len(formatted_code.splitlines()),
        )
    
    def _replace_global(
        self,
        existing_content: str,
        instruction: ModifyInstruction
    ) -> ModifyResult:
        """
        Заменяет строку в глобальной области (вне классов и функций).
        
        Args:
            instruction:
                - replace_pattern: Что заменять
                - code: На что заменять
        """
        replace_pattern = instruction.replace_pattern
        code = instruction.code.strip()
        
        if not replace_pattern:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="replace_pattern required for REPLACE_GLOBAL",
            )
        
        lines = existing_content.splitlines(keepends=True)
        
        ts_parser = _get_tree_sitter_parser()
        if ts_parser is None:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message="Tree-sitter parser not available",
            )
        
        parse_result = ts_parser.parse(existing_content)
        
        # Находим глобальную строку (не внутри классов и не внутри функций)
        target_line_idx = -1
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            if not line_stripped or line_stripped.startswith('#'):
                continue
            
            # Проверяем что строка не находится внутри класса или функции
            is_inside = False
            for cls in parse_result.classes:
                if cls.span.start_line - 1 <= i < cls.span.end_line:
                    is_inside = True
                    break
            
            if not is_inside:
                for func in parse_result.functions:
                    if func.span.start_line - 1 <= i < func.span.end_line:
                        is_inside = True
                        break
            
            if not is_inside and replace_pattern in line_stripped:
                target_line_idx = i
                break
        
        if target_line_idx == -1:
            return ModifyResult(
                success=False,
                new_content=existing_content,
                message=f"Pattern '{replace_pattern}' not found in global scope",
            )
        
        # Определяем отступ строки
        old_line = lines[target_line_idx]
        line_indent = len(old_line) - len(old_line.lstrip())
        
        # Форматируем новый код с сохранением отступа
        formatted_code = self._analyze_and_normalize_indent(code, line_indent)
        if not formatted_code.endswith('\n'):
            formatted_code += '\n'
        
        # Заменяем
        old_lines_count = 1
        if '\n' in formatted_code.rstrip('\n'):
            # Многострочная замена
            lines.pop(target_line_idx)
            for i, line in enumerate(formatted_code.splitlines(keepends=True)):
                lines.insert(target_line_idx + i, line)
            new_lines_count = len(formatted_code.splitlines())
            lines_added = max(0, new_lines_count - old_lines_count)
            lines_removed = max(0, old_lines_count - new_lines_count)
        else:
            # Однострочная замена
            lines[target_line_idx] = formatted_code
            lines_added = 1
            lines_removed = 1
        
        
        new_content = ''.join(lines)
        
        return ModifyResult(
            success=True,
            new_content=new_content,
            message=f"Replaced global line",
            changes_made=[f"Replaced global line {target_line_idx + 1}"],
            lines_added=1,
            lines_removed=1,
        )    
    
    
    # ========================================================================
    # HELPER METHODS (большинство удалено)
    # ========================================================================
    
# This method and the following two helper methods will be deleted:
# - _find_class
# - _get_node_indent
# - _find_method_end
# They are no longer needed as we use Tree-sitter exclusively.
    
    
# This method and the following two helper methods will be deleted:
# - _find_class
# - _get_node_indent
# - _find_method_end
# They are no longer needed as we use Tree-sitter exclusively.
    
    def _find_imports_end(self, lines: List[str]) -> int:
        """Находит номер строки после блока импортов"""
        last_import_line = 0
        in_multiline = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if not stripped or stripped.startswith('#'):
                if last_import_line == 0:
                    continue
            
            if stripped.startswith(('import ', 'from ')):
                last_import_line = i + 1
                in_multiline = stripped.endswith(('(', '\\'))
            elif in_multiline:
                last_import_line = i + 1
                if stripped.endswith(')') or not stripped.endswith('\\'):
                    in_multiline = False
            elif last_import_line > 0:
                break
        
        return last_import_line
    
    def _indent_code(self, code: str, indent: int) -> str:
        """
        Применяет отступ к коду.
        
        Нормализует существующий отступ и добавляет требуемый.
        """
        lines = code.splitlines()
        
        if not lines:
            return code
        
        # Находим минимальный отступ (игнорируя пустые строки)
        min_indent = float('inf')
        for line in lines:
            if line.strip():
                line_indent = len(line) - len(line.lstrip())
                min_indent = min(min_indent, line_indent)
        
        if min_indent == float('inf'):
            min_indent = 0
        
        # Нормализуем и добавляем новый отступ
        result_lines = []
        indent_str = ' ' * indent
        
        for line in lines:
            if line.strip():
                # Убираем старый отступ, добавляем новый
                normalized = line[int(min_indent):] if len(line) >= min_indent else line.lstrip()
                result_lines.append(indent_str + normalized)
            else:
                result_lines.append('')
        
        return '\n'.join(result_lines)
    
    
    def _normalize_and_indent_code(self, code: str, target_indent: int) -> str:
        """
        Нормализует отступы в коде для безопасной вставки.
        
        КЛЮЧЕВОЙ ПРИНЦИП: Сохраняем относительную структуру отступов,
        но сдвигаем весь код так, чтобы минимальный отступ стал равен target_indent.
        
        Алгоритм:
        1. Находим минимальный отступ среди непустых строк (base_indent)
        2. Для каждой строки вычисляем relative_indent = current - base_indent
        3. Новый отступ = target_indent + relative_indent
        
        Это работает для ВСЕХ сценариев:
        - Целый метод/класс (def/class на target_indent, тело на target_indent + 4)
        - Несколько строк кода (все сдвигаются относительно первой)
        - Одна строка (просто получает target_indent)
        
        Args:
            code: Исходный код (возможно с неправильными отступами от Генератора)
            target_indent: Целевой отступ для минимально-отступленной строки
            
        Returns:
            Код с нормализованными отступами
        """
        if not code or not code.strip():
            return code
        
        lines = code.splitlines()
        
        # Шаг 1: Найти минимальный отступ среди непустых строк
        min_indent = float('inf')
        for line in lines:
            stripped = line.lstrip()
            if stripped:  # Непустая строка
                current_indent = len(line) - len(stripped)
                min_indent = min(min_indent, current_indent)
        
        # Если все строки пустые
        if min_indent == float('inf'):
            return code
        
        # Шаг 2: Нормализовать каждую строку
        result_lines = []
        for line in lines:
            stripped = line.lstrip()
            
            if not stripped:
                # Пустая строка — оставляем пустой (без trailing spaces)
                result_lines.append('')
            else:
                # Вычисляем текущий и относительный отступ
                current_indent = len(line) - len(stripped)
                relative_indent = current_indent - min_indent
                
                # Новый отступ = целевой + относительный
                new_indent = target_indent + relative_indent
                
                # Применяем новый отступ
                result_lines.append(' ' * new_indent + stripped)
        
        return '\n'.join(result_lines)


    def _analyze_and_normalize_indent(self, code: str, target_indent: int) -> str:
        """Analyzes code indentation using Tree-sitter. If correct, returns as is; otherwise normalizes."""
        # 1. If code is empty/whitespace, return it
        if not code or not code.strip():
            return code
        
        # 2. Get parser via _get_tree_sitter_parser()
        parser = _get_tree_sitter_parser()
        
        # 3. If parser exists
        if parser:
            try:
                # a. Parse code
                result = parser.parse(code)
                
                # b. Check result.root_node
                if result.root_node:
                    # c. Iterate children of root (skipping purely empty ones)
                    children = result.root_node.children
                    
                    if children:
                        # Find first significant node
                        for child in children:
                            if child.type not in ('ERROR', ''):
                                # Get the start column (indentation) of first significant node
                                current_indent = child.start_point[1]
                                
                                # d. If current_indent == target_indent, return code as is
                                if current_indent == target_indent:
                                    return code
                                break
            except Exception as e:
                logger.debug(f"Tree-sitter analysis failed: {e}")
        
        # 4. Fallback: Return normalized code
        return self._normalize_and_indent_code(code, target_indent)


    def _detect_insertion_context_indent(
        self, 
        lines: List[str], 
        insert_line: int, 
        is_inside_block: bool = False
    ) -> int:
        """
        Определяет правильный отступ для вставки кода на основе контекста.
        
        Анализирует строки вокруг точки вставки и определяет,
        какой отступ должен быть у вставляемого кода.
        
        Args:
            lines: Все строки файла
            insert_line: Номер строки, ПОСЛЕ которой будет вставка (0-indexed)
            is_inside_block: True если вставка внутрь блока (после if/for/while:)
            
        Returns:
            Отступ в пробелах для вставляемого кода
        """
        if insert_line < 0:
            return 0
        
        # Ищем ближайшую непустую строку перед точкой вставки
        context_indent = 0
        context_line = None
        
        for i in range(min(insert_line, len(lines) - 1), -1, -1):
            line = lines[i]
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                context_indent = len(line) - len(line.lstrip())
                context_line = stripped
                break
        
        # Если предыдущая строка заканчивается на : — это начало блока
        if context_line and context_line.endswith(':'):
            return context_indent + self.default_indent
        
        # Если явно указано что вставка внутрь блока
        if is_inside_block:
            return context_indent + self.default_indent
        
        return context_indent


    def _validate_and_fix_syntax(self, content: str) -> str:
        """
        Проверяет синтаксис через Tree-sitter.
        """
        ts_parser = _get_tree_sitter_parser()
        if not ts_parser:
            return content
            
        result = ts_parser.parse(content)
        if not result.has_errors:
            return content
            
        # Код невалиден — пробуем исправить
        logger.warning(f"Syntax error detected after modification. Errors: {result.errors[:1]}")
        
        # Пробуем через SyntaxChecker (если доступен)
        try:
            from app.services.syntax_checker import SyntaxChecker
            checker = SyntaxChecker(use_black=False, use_autopep8=True)
            
            fix_result = checker.check_python(content, auto_fix=True)
            
            if fix_result.was_auto_fixed and fix_result.fixed_content:
                # Проверяем что исправление валидно
                new_parse = ts_parser.parse(fix_result.fixed_content)
                if not new_parse.has_errors:
                    logger.info("Auto-fix successful via SyntaxChecker")
                    return fix_result.fixed_content
                else:
                    logger.warning("Auto-fix produced invalid code, using original")
        except ImportError:
            logger.debug("SyntaxChecker not available for fallback")
        except Exception as e:
            logger.warning(f"Auto-fix failed: {e}")
        
        return content

    def _is_declaration_line(self, stripped_line: str) -> bool:
        """
        Проверяет, является ли строка объявлением (def/class/async def).
        
        Args:
            stripped_line: Строка без начальных/конечных пробелов
            
        Returns:
            True если это объявление функции/класса
        """
        declaration_patterns = [
            'def ',
            'async def ',
            'class ',
        ]
        
        for pattern in declaration_patterns:
            if stripped_line.startswith(pattern):
                return True
        
        # Проверяем декораторы (они предшествуют объявлениям)
        if stripped_line.startswith('@'):
            return True
        
        return False


    def _analyze_code_indents(
        self, 
        lines: List[str], 
        first_content_idx: int,
        is_declaration: bool
    ) -> Dict[str, Any]:
        """
        Анализирует структуру отступов в коде.
        
        Определяет:
        - base_indent: базовый отступ (первая значимая строка)
        - body_indent: отступ тела (для def/class)
        - expected_body_offset: ожидаемый сдвиг тела относительно объявления
        - has_anomaly: есть ли аномальные отступы
        - indent_map: карта уровней отступов
        
        Returns:
            Dict с результатами анализа
        """
        analysis = {
            'base_indent': 0,
            'body_indent': None,
            'expected_body_offset': self.default_indent,
            'has_anomaly': False,
            'decorator_lines': [],  # Индексы строк с декораторами
            'actual_declaration_idx': first_content_idx,  # Реальное объявление (после декораторов)
        }
        
        if first_content_idx < 0 or first_content_idx >= len(lines):
            return analysis
        
        # Находим базовый отступ
        first_line = lines[first_content_idx]
        analysis['base_indent'] = len(first_line) - len(first_line.lstrip())
        
        # Обрабатываем декораторы
        if is_declaration:
            # Проверяем, есть ли декораторы перед объявлением
            decorator_idx = first_content_idx
            while decorator_idx < len(lines):
                stripped = lines[decorator_idx].strip()
                if stripped.startswith('@'):
                    analysis['decorator_lines'].append(decorator_idx)
                    decorator_idx += 1
                elif stripped.startswith(('def ', 'async def ', 'class ')):
                    analysis['actual_declaration_idx'] = decorator_idx
                    break
                elif stripped and not stripped.startswith('#'):
                    # Не декоратор и не объявление — что-то другое
                    break
                else:
                    decorator_idx += 1
        
        # Находим отступ тела (первая строка после объявления)
        if is_declaration:
            decl_idx = analysis['actual_declaration_idx']
            
            for i in range(decl_idx + 1, len(lines)):
                line = lines[i]
                stripped = line.strip()
                
                # Пропускаем пустые строки и комментарии
                if not stripped or stripped.startswith('#'):
                    continue
                
                # Пропускаем docstring (начинается с """ или ''')
                if stripped.startswith(('"""', "'''")):
                    # Ищем конец docstring
                    if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                        # Однострочный docstring
                        continue
                    else:
                        # Многострочный docstring — пропускаем до конца
                        docstring_char = '"""' if '"""' in stripped else "'''"
                        for j in range(i + 1, len(lines)):
                            if docstring_char in lines[j]:
                                i = j
                                break
                        continue
                
                # Нашли первую значимую строку тела
                analysis['body_indent'] = len(line) - len(line.lstrip())
                break
            
            # Вычисляем фактический сдвиг тела относительно объявления
            if analysis['body_indent'] is not None:
                decl_line = lines[decl_idx]
                decl_indent = len(decl_line) - len(decl_line.lstrip())
                actual_offset = analysis['body_indent'] - decl_indent
                
                # Проверяем на аномалию
                if actual_offset != self.default_indent and actual_offset > 0:
                    analysis['has_anomaly'] = True
                    analysis['expected_body_offset'] = actual_offset
        
        # Проверяем на другие аномалии: строки с меньшим отступом чем базовый
        base = analysis['base_indent']
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                current = len(line) - len(line.lstrip())
                if current < base and i > first_content_idx:
                    analysis['has_anomaly'] = True
                    break
        
        return analysis


    def _calculate_new_indent(
        self,
        line_index: int,
        current_indent: int,
        first_content_idx: int,
        target_indent: int,
        indent_analysis: Dict[str, Any],
        is_declaration: bool
    ) -> int:
        """
        Вычисляет новый отступ для строки.
        
        Args:
            line_index: Индекс текущей строки
            current_indent: Текущий отступ строки
            first_content_idx: Индекс первой значимой строки
            target_indent: Целевой отступ
            indent_analysis: Результаты анализа отступов
            is_declaration: Является ли код объявлением
            
        Returns:
            Новый отступ в пробелах
        """
        base_indent = indent_analysis['base_indent']
        body_indent = indent_analysis['body_indent']
        actual_decl_idx = indent_analysis['actual_declaration_idx']
        decorator_lines = indent_analysis['decorator_lines']
        
        # Декораторы — такой же отступ как target_indent
        if line_index in decorator_lines:
            return target_indent
        
        # Строка объявления (def/class) — ВСЕГДА target_indent
        # Это ключевое исправление: независимо от текущего отступа в исходном коде,
        # строка def/class должна быть на уровне target_indent
        if line_index == actual_decl_idx:
            return target_indent
        
        # Строки до первого контента (например, комментарии в начале)
        if line_index < first_content_idx:
            # Сохраняем относительный отступ
            relative = current_indent - base_indent
            return target_indent + max(0, relative)
        
        # Для объявлений: тело должно быть на target_indent + default_indent
        if is_declaration and body_indent is not None:
            # Вычисляем относительный отступ от тела
            if current_indent >= body_indent:
                # Строка внутри тела — вычисляем вложенность
                nesting = current_indent - body_indent
                return target_indent + self.default_indent + nesting
            elif current_indent > base_indent and line_index > actual_decl_idx:
                # Строка между объявлением и телом (например, docstring на неправильном уровне)
                # или строка с неправильным отступом от Генератора
                # Нормализуем к уровню тела
                return target_indent + self.default_indent
            else:
                # Строка на уровне объявления (маловероятно в валидном коде после actual_decl_idx)
                # Но если это происходит — это скорее всего ошибка Генератора
                # Возвращаем target_indent для безопасности
                return target_indent
        
        # Для statements: все относительно первой строки
        relative = current_indent - base_indent
        return target_indent + max(0, relative)




    def _detect_indent_from_context(self, lines: List[str], line_index: int, direction: str = "before") -> int:
        """
        Определяет отступ из контекста (соседних строк).
        
        Args:
            lines: Все строки файла
            line_index: Индекс строки, около которой ищем контекст
            direction: "before" — искать выше, "after" — искать ниже
            
        Returns:
            Отступ в пробелах
        """
        search_range = range(line_index - 1, -1, -1) if direction == "before" else range(line_index, len(lines))
        
        for i in search_range:
            if 0 <= i < len(lines):
                line = lines[i]
                stripped = line.strip()
                
                # Ищем непустую строку, которая не является комментарием или docstring
                if stripped and not stripped.startswith('#') and not stripped.startswith('"""') and not stripped.startswith("'''"):
                    return len(line) - len(line.lstrip())
        
        # Fallback: используем default_indent
        return self.default_indent    
    
    
    def _extract_imports(self, content: str) -> List[str]:
        """Извлекает все строки импортов используя Tree-sitter"""
        ts_parser = _get_tree_sitter_parser()
        
        # Если парсер доступен
        if ts_parser:
            try:
                result = ts_parser.parse(content)
                if result.imports:
                    return result.imports
            except Exception as e:
                logger.warning(f"Tree-sitter import extraction failed: {e}")
        
        # Fallback: regex (простой)
        imports = []
        for line in content.splitlines():
            if line.strip().startswith(('import ', 'from ')):
                imports.append(line)
        return imports

    
    def detect_indent_style(self, content: str) -> Tuple[int, str]:
        """
        Определяет стиль отступов в файле.
        
        Returns:
            Tuple (размер_отступа, "spaces"|"tabs")
        """
        lines = content.splitlines()
        
        space_indents = []
        tab_count = 0
        
        for line in lines:
            if not line.strip():
                continue
            
            leading = len(line) - len(line.lstrip())
            if leading == 0:
                continue
            
            if line[0] == '\t':
                tab_count += 1
            else:
                space_indents.append(leading)
        
        if tab_count > len(space_indents):
            return (1, "tabs")
        
        if space_indents:
            # Находим GCD всех отступов
            from math import gcd
            from functools import reduce
            indent_size = reduce(gcd, space_indents)
            return (indent_size, "spaces")
        
        return (self.default_indent, "spaces")
    
    # ========================================================================
    # HIGH-LEVEL API
    # ========================================================================
    
    def smart_apply(
        self,
        existing_content: str,
        code: str,
        context: Optional[str] = None,
        target_element: Optional[str] = None,
    ) -> ModifyResult:
        """
        Умное применение кода с автоопределением режима (Tree-sitter).
        """
        ts_parser = _get_tree_sitter_parser()
        
        # Анализируем новый код
        has_class = False
        has_function = False
        func_name = None
        
        if ts_parser:
            try:
                new_tree = ts_parser.parse(code)
                has_class = len(new_tree.classes) > 0
                has_function = len(new_tree.functions) > 0
                if has_function:
                    func_name = new_tree.functions[0].name
            except Exception:
                pass
        
        # Если не удалось распарсить или нет парсера - используем эвристику или fallback
        if not ts_parser:
             # Fallback logic could go here, but we assume TS is available
             pass

        # Если это целый класс и указан target_element
        if has_class and target_element:
            return self.apply(existing_content, ModifyInstruction(
                mode=ModifyMode.REPLACE_CLASS,
                code=code,
                target_class=target_element,
            ))
        
        # Если это функция/метод
        if has_function:
            if context:
                # Это метод для вставки/замены в класс
                if target_element:
                    return self.apply(existing_content, ModifyInstruction(
                        mode=ModifyMode.REPLACE_METHOD,
                        code=code,
                        target_class=context,
                        target_method=target_element or func_name,
                    ))
                else:
                    return self.apply(existing_content, ModifyInstruction(
                        mode=ModifyMode.INSERT_INTO_CLASS,
                        code=code,
                        target_class=context,
                    ))
            else:
                # Функция на уровне модуля
                if target_element:
                    return self.apply(existing_content, ModifyInstruction(
                        mode=ModifyMode.REPLACE_FUNCTION,
                        code=code,
                        target_function=target_element or func_name,
                    ))
                else:
                    return self.apply(existing_content, ModifyInstruction(
                        mode=ModifyMode.INSERT_INTO_FILE,
                        code=code,
                    ))
        
        # По умолчанию — добавляем в конец
        return self.apply(existing_content, ModifyInstruction(
            mode=ModifyMode.APPEND_TO_FILE,
            code=code,
        ))

    
    def __repr__(self) -> str:
        return f"FileModifier(default_indent={self.default_indent})"