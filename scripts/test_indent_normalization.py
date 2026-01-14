#!/usr/bin/env python3
"""
Комплексный тест нормализации отступов в FileModifier.

РЕАЛЬНЫЕ ПРОВЕРКИ:
1. Проверка отступа КАЖДОЙ строки результата
2. Проверка что существующий код не испорчен
3. Проверка конкретных сценариев ошибок Generator
4. Детальный вывод при провале теста
"""

import sys
import os
import tempfile
import shutil
import unittest
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import ast
import re

# Добавляем корень проекта в путь
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.file_modifier import FileModifier, ModifyInstruction, ModifyMode


def get_line_indent(line: str) -> int:
    """Возвращает количество пробелов в начале строки"""
    if not line.strip():
        return -1  # Пустая строка
    return len(line) - len(line.lstrip())


def analyze_indents(code: str) -> List[Tuple[int, int, str]]:
    """
    Анализирует отступы в коде.
    
    Returns:
        List of (line_number, indent, stripped_content)
    """
    result = []
    for i, line in enumerate(code.splitlines(), 1):
        indent = get_line_indent(line)
        stripped = line.strip()
        result.append((i, indent, stripped))
    return result


def find_line_starting_with(code: str, prefix: str) -> Tuple[int, int, str]:
    """
    Находит строку, начинающуюся с prefix (после strip).
    
    Returns:
        (line_number, indent, full_line) или (0, 0, "") если не найдено
    """
    for i, line in enumerate(code.splitlines(), 1):
        if line.strip().startswith(prefix):
            return (i, get_line_indent(line), line)
    return (0, 0, "")


class IndentNormalizationTestCase(unittest.TestCase):
    """Базовый класс для тестов нормализации отступов"""
    
    @classmethod
    def setUpClass(cls):
        cls.modifier = FileModifier(default_indent=4)
        print(f"\n{'='*70}")
        print("ТЕСТИРОВАНИЕ НОРМАЛИЗАЦИИ ОТСТУПОВ - ДЕТАЛЬНАЯ ПРОВЕРКА")
        print(f"{'='*70}\n")
    
    def assert_valid_python(self, code: str, msg: str = ""):
        """Проверяет, что код является валидным Python"""
        try:
            ast.parse(code)
        except SyntaxError as e:
            lines = code.splitlines()
            error_line = e.lineno - 1 if e.lineno else 0
            start = max(0, error_line - 3)
            end = min(len(lines), error_line + 4)
            
            context = "\n".join(
                f"{'>>>' if i == error_line else '   '} {i+1:4d} | {line}"
                for i, line in enumerate(lines[start:end], start)
            )
            
            self.fail(
                f"{msg}\n"
                f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}\n"
                f"Context:\n{context}\n\n"
                f"Full code:\n{code}"
            )
    
    def assert_line_indent(self, code: str, line_prefix: str, expected_indent: int, msg: str = ""):
        """
        Проверяет что строка, начинающаяся с prefix, имеет ожидаемый отступ.
        """
        line_num, actual_indent, full_line = find_line_starting_with(code, line_prefix)
        
        if line_num == 0:
            self.fail(f"Line starting with '{line_prefix}' not found in:\n{code}")
        
        if actual_indent != expected_indent:
            self.fail(
                f"{msg}\n"
                f"Line {line_num} starting with '{line_prefix}':\n"
                f"  Expected indent: {expected_indent}\n"
                f"  Actual indent:   {actual_indent}\n"
                f"  Line content:    '{full_line}'"
            )
    
    def assert_method_structure(
        self, 
        code: str, 
        method_name: str, 
        expected_def_indent: int,
        expected_body_indent: int,
        msg: str = ""
    ):
        """
        Проверяет структуру метода: отступ def и отступ тела.
        """
        lines = code.splitlines()
        
        # Находим def
        def_line_idx = None
        for i, line in enumerate(lines):
            if re.match(rf'\s*def {method_name}\s*\(', line) or \
               re.match(rf'\s*async def {method_name}\s*\(', line):
                def_line_idx = i
                break
        
        if def_line_idx is None:
            self.fail(f"Method '{method_name}' not found in:\n{code}")
        
        def_line = lines[def_line_idx]
        actual_def_indent = get_line_indent(def_line)
        
        if actual_def_indent != expected_def_indent:
            self.fail(
                f"{msg}\n"
                f"Method '{method_name}' def line has wrong indent:\n"
                f"  Expected: {expected_def_indent}\n"
                f"  Actual:   {actual_def_indent}\n"
                f"  Line:     '{def_line}'"
            )
        
        # Находим первую строку тела (не пустую, не docstring открытие)
        body_line_idx = None
        in_docstring = False
        docstring_char = None
        
        for i in range(def_line_idx + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            
            if not stripped:
                continue
            
            # Обработка docstring
            if not in_docstring:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    docstring_char = '"""' if '"""' in stripped else "'''"
                    # Однострочный docstring
                    if stripped.count(docstring_char) >= 2:
                        continue
                    in_docstring = True
                    continue
                else:
                    # Это первая строка тела
                    body_line_idx = i
                    break
            else:
                # Внутри docstring
                if docstring_char in stripped:
                    in_docstring = False
                continue
        
        if body_line_idx is None:
            self.fail(f"No body found for method '{method_name}' in:\n{code}")
        
        body_line = lines[body_line_idx]
        actual_body_indent = get_line_indent(body_line)
        
        if actual_body_indent != expected_body_indent:
            self.fail(
                f"{msg}\n"
                f"Method '{method_name}' body has wrong indent:\n"
                f"  Expected: {expected_body_indent}\n"
                f"  Actual:   {actual_body_indent}\n"
                f"  Def line:  '{def_line}'\n"
                f"  Body line: '{body_line}'"
            )
    
    def assert_existing_code_unchanged(self, original: str, result: str, preserve_elements: List[str]):
        """
        Проверяет что указанные элементы из оригинала сохранились в результате.
        """
        for element in preserve_elements:
            if element not in result:
                self.fail(
                    f"Original element '{element}' was lost or corrupted.\n"
                    f"Original:\n{original}\n\n"
                    f"Result:\n{result}"
                )


# ============================================================================
# ТЕСТЫ INSERT_INTO_CLASS - ДЕТАЛЬНЫЕ
# ============================================================================

class TestInsertIntoClassDetailed(IndentNormalizationTestCase):
    """Детальные тесты вставки методов в класс"""
    
    def get_base_class(self) -> str:
        return '''class MyClass:
    """Тестовый класс"""
    
    def __init__(self):
        self.value = 0
    
    def existing_method(self):
        return self.value
'''
    
    def test_correct_indent_preserved(self):
        """Код с правильными отступами НЕ должен измениться"""
        base = self.get_base_class()
        
        # Правильный код: def 0, body 4
        new_method = '''def new_method(self):
    """Новый метод"""
    x = 1
    return self.value * x'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="MyClass"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # ДЕТАЛЬНАЯ ПРОВЕРКА: def должен быть на 4, body на 8
        self.assert_method_structure(
            result.new_content, 
            "new_method", 
            expected_def_indent=4,
            expected_body_indent=8,
            msg="Correct indent was corrupted"
        )
        
        # Проверяем что существующий код не испорчен
        self.assert_existing_code_unchanged(
            base, result.new_content,
            ["def __init__(self):", "self.value = 0", "def existing_method(self):"]
        )
    
    def test_generator_error_8_space_body_fixed(self):
        """Ошибка Generator: def 0, body 8 → должно стать def 4, body 8"""
        base = self.get_base_class()
        
        # ОШИБКА Generator: def без отступа, тело с 8 пробелами
        bad_method = '''def broken_method(self):
        """Docstring с 8 пробелами"""
        x = self.value
        return x * 2'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=bad_method,
            target_class="MyClass"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # ДЕТАЛЬНАЯ ПРОВЕРКА: после нормализации def=4, body=8
        self.assert_method_structure(
            result.new_content,
            "broken_method",
            expected_def_indent=4,
            expected_body_indent=8,
            msg="Generator error not fixed"
        )
        
        # Проверяем конкретные строки
        self.assert_line_indent(result.new_content, "x = self.value", 8)
        self.assert_line_indent(result.new_content, "return x * 2", 8)
    
    def test_nested_blocks_indent_chain(self):
        """Вложенные блоки: проверяем цепочку отступов"""
        base = self.get_base_class()
        
        new_method = '''def complex(self, items):
    for item in items:
        if item > 0:
            try:
                result = item * 2
            except:
                pass
    return None'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="MyClass"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем цепочку отступов: 4 → 8 → 12 → 16 → 20
        self.assert_line_indent(result.new_content, "def complex(self, items):", 4)
        self.assert_line_indent(result.new_content, "for item in items:", 8)
        self.assert_line_indent(result.new_content, "if item > 0:", 12)
        self.assert_line_indent(result.new_content, "try:", 16)
        self.assert_line_indent(result.new_content, "result = item * 2", 20)
        self.assert_line_indent(result.new_content, "except:", 16)
        self.assert_line_indent(result.new_content, "pass", 20)
        self.assert_line_indent(result.new_content, "return None", 8)
    
    def test_decorator_with_method(self):
        """Декоратор должен иметь тот же отступ что и def"""
        base = self.get_base_class()
        
        new_method = '''@property
def value_prop(self):
    return self._value'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="MyClass"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Декоратор и def на одном уровне
        self.assert_line_indent(result.new_content, "@property", 4)
        self.assert_line_indent(result.new_content, "def value_prop(self):", 4)
        self.assert_line_indent(result.new_content, "return self._value", 8)


# ============================================================================
# ТЕСТЫ REPLACE_METHOD - ДЕТАЛЬНЫЕ
# ============================================================================

class TestReplaceMethodDetailed(IndentNormalizationTestCase):
    """Детальные тесты замены методов"""
    
    def get_base(self) -> str:
        return '''class Calculator:
    def __init__(self):
        self.result = 0
    
    def add(self, x):
        """Добавляет x"""
        self.result += x
        return self.result
    
    def subtract(self, x):
        self.result -= x
        return self.result
'''
    
    def test_replace_preserves_context(self):
        """Замена метода не должна ломать окружающий код"""
        base = self.get_base()
        
        new_method = '''def add(self, x, y=0):
    """Новый add"""
    total = x + y
    self.result += total
    return self.result'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_METHOD,
            code=new_method,
            target_class="Calculator",
            target_method="add"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Новый метод правильно отформатирован
        self.assert_method_structure(result.new_content, "add", 4, 8)
        
        # Существующие методы не тронуты
        self.assert_method_structure(result.new_content, "__init__", 4, 8)
        self.assert_method_structure(result.new_content, "subtract", 4, 8)
    
    def test_replace_with_bad_indent(self):
        """Замена кодом с плохими отступами должна исправить их"""
        base = self.get_base()
        
        # Плохой код: def 0, body 8
        bad_method = '''def add(self, x):
        """Плохие отступы"""
        self.result += x
        return self.result'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_METHOD,
            code=bad_method,
            target_class="Calculator",
            target_method="add"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Отступы должны быть исправлены
        self.assert_method_structure(result.new_content, "add", 4, 8)


# ============================================================================
# ТЕСТЫ PATCH_METHOD - ДЕТАЛЬНЫЕ
# ============================================================================

class TestPatchMethodDetailed(IndentNormalizationTestCase):
    """Детальные тесты патчинга методов"""
    
    def get_base(self) -> str:
        return '''class Processor:
    def process(self, data):
        """Обрабатывает данные"""
        result = []
        for item in data:
            result.append(item * 2)
        return result
'''
    
    def test_patch_inserts_with_correct_indent(self):
        """Патч должен вставить код с правильным отступом тела метода"""
        base = self.get_base()
        
        patch = '''if not data:
    return []
print("Processing...")'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.PATCH_METHOD,
            code=patch,
            target_class="Processor",
            target_method="process",
            insert_after='"""Обрабатывает данные"""'
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # Вставленный код должен быть на уровне тела метода (8)
        self.assert_line_indent(result.new_content, "if not data:", 8)
        self.assert_line_indent(result.new_content, "return []", 12)
        self.assert_line_indent(result.new_content, 'print("Processing...")', 8)
    
    def test_patch_with_wrong_indent_code(self):
        """Патч с неправильными отступами должен быть нормализован"""
        base = self.get_base()
        
        # Код с отступом 4 (как будто верхний уровень функции)
        bad_patch = '''    if not data:
        return []'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.PATCH_METHOD,
            code=bad_patch,
            target_class="Processor",
            target_method="process",
            insert_after='"""Обрабатывает данные"""'
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Должно быть нормализовано к уровню 8
        self.assert_line_indent(result.new_content, "if not data:", 8)
        self.assert_line_indent(result.new_content, "return []", 12)


# ============================================================================
# ТЕСТЫ REPLACE_FUNCTION - ДЕТАЛЬНЫЕ
# ============================================================================

class TestReplaceFunctionDetailed(IndentNormalizationTestCase):
    """Детальные тесты замены функций"""
    
    def get_base(self) -> str:
        return '''"""Module"""

import os

def helper(x):
    """Helper function"""
    return x * 2

class MyClass:
    pass
'''
    
    def test_replace_function_at_level_0(self):
        """Функция на уровне модуля должна иметь отступ 0"""
        base = self.get_base()
        
        new_func = '''def helper(x, y=1):
    """Updated helper"""
    result = x * y
    return result'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_FUNCTION,
            code=new_func,
            target_function="helper"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # def на уровне 0, body на уровне 4
        self.assert_method_structure(result.new_content, "helper", 0, 4)
    
    def test_replace_function_with_class_indent(self):
        """Функция с отступом как у метода класса должна быть нормализована к 0"""
        base = self.get_base()
        
        # Ошибка: функция с отступом 4 (как метод)
        bad_func = '''    def helper(x):
        """С неправильным отступом"""
        return x * 3'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_FUNCTION,
            code=bad_func,
            target_function="helper"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Должно быть нормализовано к уровню 0
        self.assert_method_structure(result.new_content, "helper", 0, 4)


# ============================================================================
# ТЕСТЫ APPEND_TO_FILE - ДЕТАЛЬНЫЕ
# ============================================================================

class TestAppendToFileDetailed(IndentNormalizationTestCase):
    """Детальные тесты добавления в конец файла"""
    
    def test_append_class_with_wrong_indent(self):
        """Класс с неправильным отступом должен быть добавлен как есть (APPEND не нормализует)"""
        base = '''"""Module"""
'''
        
        # Класс с отступом — это ошибка, но APPEND_TO_FILE просто добавляет
        new_class = '''class NewClass:
    def __init__(self):
        self.x = 1'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.APPEND_TO_FILE,
            code=new_class
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем структуру
        self.assert_line_indent(result.new_content, "class NewClass:", 0)
        self.assert_method_structure(result.new_content, "__init__", 4, 8)


# ============================================================================
# ТЕСТЫ КРАЙНИХ СЛУЧАЕВ
# ============================================================================

class TestEdgeCasesDetailed(IndentNormalizationTestCase):
    """Тесты крайних случаев"""
    
    def test_empty_class_insert(self):
        """Вставка в класс с только pass"""
        base = '''class Empty:
    pass
'''
        
        new_method = '''def method(self):
    return 42'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="Empty"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        self.assert_method_structure(result.new_content, "method", 4, 8)
    
    def test_multiline_signature(self):
        """Метод с многострочной сигнатурой"""
        base = '''class Service:
    pass
'''
        
        new_method = '''def complex_method(
    self,
    arg1: int,
    arg2: str = "default"
) -> bool:
    """Многострочная сигнатура"""
    return True'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="Service"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Все части сигнатуры должны быть правильно отступлены
        self.assert_line_indent(result.new_content, "def complex_method(", 4)
        self.assert_line_indent(result.new_content, "self,", 8)
        self.assert_line_indent(result.new_content, "return True", 8)
    
    def test_async_method_with_context_managers(self):
        """Асинхронный метод с контекстными менеджерами"""
        base = '''class AsyncService:
    pass
'''
        
        new_method = '''async def fetch(self, url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            return data'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="AsyncService"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем цепочку отступов
        self.assert_line_indent(result.new_content, "async def fetch(self, url):", 4)
        self.assert_line_indent(result.new_content, "async with aiohttp.ClientSession()", 8)
        self.assert_line_indent(result.new_content, "async with session.get(url)", 12)
        self.assert_line_indent(result.new_content, "data = await response.json()", 16)
        self.assert_line_indent(result.new_content, "return data", 16)


# ============================================================================
# ТЕСТЫ РЕАЛЬНЫХ ОШИБОК GENERATOR
# ============================================================================

class TestRealGeneratorErrors(IndentNormalizationTestCase):
    """Тесты реальных ошибок, которые делает Code Generator"""
    
    def test_error_def_0_body_8(self):
        """Самая частая ошибка: def без отступа, тело +8"""
        base = '''class MyClass:
    def __init__(self):
        self.x = 0
'''
        
        # Реальная ошибка Generator
        bad_code = '''def calculate(self, value):
        """Calculate something."""
        result = value * 2
        if result > 100:
            result = 100
        return result'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=bad_code,
            target_class="MyClass"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем что ВСЕ строки имеют правильные отступы
        self.assert_line_indent(result.new_content, "def calculate(self, value):", 4)
        self.assert_line_indent(result.new_content, '"""Calculate something."""', 8)
        self.assert_line_indent(result.new_content, "result = value * 2", 8)
        self.assert_line_indent(result.new_content, "if result > 100:", 8)
        self.assert_line_indent(result.new_content, "result = 100", 12)
        self.assert_line_indent(result.new_content, "return result", 8)
    
    def test_error_inconsistent_nesting(self):
        """Ошибка: непоследовательные отступы во вложенных блоках"""
        base = '''class Handler:
    pass
'''
        
        # Ошибка: отступы 0, 8, 12, 8 (неконсистентно)
        bad_code = '''def handle(self, event):
        if event.type == "click":
            self.on_click()
        else:
            self.on_other()'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=bad_code,
            target_class="Handler"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем правильную структуру
        self.assert_line_indent(result.new_content, "def handle(self, event):", 4)
        self.assert_line_indent(result.new_content, 'if event.type == "click":', 8)
        self.assert_line_indent(result.new_content, "self.on_click()", 12)
        self.assert_line_indent(result.new_content, "else:", 8)
        self.assert_line_indent(result.new_content, "self.on_other()", 12)
    
    def test_preserves_relative_structure(self):
        """Проверка что относительная структура сохраняется"""
        base = '''class DataService:
    pass
'''
        
        # Код с правильной относительной структурой, но неправильным базовым отступом
        code = '''def process(self, items):
        results = {}
        for item in items:
            if item.valid:
                key = item.id
                value = item.data
                results[key] = value
            else:
                continue
        return results'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=code,
            target_class="DataService"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем что структура сохранилась
        self.assert_line_indent(result.new_content, "def process(self, items):", 4)
        self.assert_line_indent(result.new_content, "results = {}", 8)
        self.assert_line_indent(result.new_content, "for item in items:", 8)
        self.assert_line_indent(result.new_content, "if item.valid:", 12)
        self.assert_line_indent(result.new_content, "key = item.id", 16)
        self.assert_line_indent(result.new_content, "else:", 12)
        self.assert_line_indent(result.new_content, "continue", 16)
        self.assert_line_indent(result.new_content, "return results", 8)


# ============================================================================
# RUNNER
# ============================================================================

def run_tests():
    """Запускает все тесты"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    test_classes = [
        TestInsertIntoClassDetailed,
        TestReplaceMethodDetailed,
        TestPatchMethodDetailed,
        TestReplaceFunctionDetailed,
        TestAppendToFileDetailed,
        TestEdgeCasesDetailed,
        TestRealGeneratorErrors,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    
    result = runner.run(suite)
    
    print("\n" + "="*70)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("="*70)
    print(f"Всего тестов: {result.testsRun}")
    print(f"Успешно: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Провалено: {len(result.failures)}")
    print(f"Ошибок: {len(result.errors)}")
    
    if result.failures:
        print("\nПРОВАЛЕННЫЕ ТЕСТЫ:")
        for test, traceback in result.failures:
            print(f"  - {test}")
    
    if result.errors:
        print("\nОШИБКИ:")
        for test, traceback in result.errors:
            print(f"  - {test}")
    
    print("="*70)
    
    return len(result.failures) == 0 and len(result.errors) == 0


# ============================================================================
# ТЕСТЫ СЛОЖНЫХ СЛУЧАЕВ - БОЛЬШОЙ КОД В БОЛЬШОЙ КОД
# ============================================================================

class TestComplexLargeCodeInsertion(IndentNormalizationTestCase):
    """Тесты вставки большого кода в большой существующий код"""
    
    def get_large_base_file(self) -> str:
        """Большой реалистичный файл с несколькими классами"""
        return '''"""
Service module for handling business logic.

This module contains core services for data processing,
validation, and transformation.
"""

from __future__ import annotations

import logging
import asyncio
from typing import Dict, List, Optional, Any, Union, Callable
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Base exception for service errors"""
    
    def __init__(self, message: str, code: int = 500):
        self.message = message
        self.code = code
        super().__init__(message)


class ValidationError(ServiceError):
    """Validation failed"""
    
    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"Validation error on {field}: {message}", code=400)


@dataclass
class ServiceConfig:
    """Configuration for services"""
    timeout: int = 30
    max_retries: int = 3
    batch_size: int = 100
    enable_cache: bool = True
    cache_ttl: int = 3600
    debug_mode: bool = False


class BaseService(ABC):
    """Abstract base class for all services"""
    
    def __init__(self, config: Optional[ServiceConfig] = None):
        self.config = config or ServiceConfig()
        self._initialized = False
        self._cache: Dict[str, Any] = {}
        self._stats = {
            "requests": 0,
            "errors": 0,
            "cache_hits": 0,
        }
    
    async def initialize(self) -> None:
        """Initialize the service"""
        if self._initialized:
            logger.warning(f"{self.__class__.__name__} already initialized")
            return
        
        logger.info(f"Initializing {self.__class__.__name__}")
        await self._do_initialize()
        self._initialized = True
    
    @abstractmethod
    async def _do_initialize(self) -> None:
        """Subclass initialization logic"""
        pass
    
    @abstractmethod
    async def process(self, data: Any) -> Any:
        """Process data - must be implemented by subclass"""
        pass
    
    def get_stats(self) -> Dict[str, int]:
        """Return service statistics"""
        return self._stats.copy()
    
    def _update_stats(self, key: str, increment: int = 1) -> None:
        """Update statistics counter"""
        if key in self._stats:
            self._stats[key] += increment


class DataProcessor(BaseService):
    """Service for processing data transformations"""
    
    def __init__(self, config: Optional[ServiceConfig] = None):
        super().__init__(config)
        self._transformers: List[Callable] = []
        self._validators: List[Callable] = []
    
    async def _do_initialize(self) -> None:
        """Initialize data processor"""
        logger.info("Loading default transformers")
        self._load_default_transformers()
    
    def _load_default_transformers(self) -> None:
        """Load default transformation functions"""
        self._transformers = [
            self._normalize_strings,
            self._convert_types,
            self._apply_defaults,
        ]
    
    def _normalize_strings(self, data: Dict) -> Dict:
        """Normalize string values"""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = value.strip().lower()
            else:
                result[key] = value
        return result
    
    def _convert_types(self, data: Dict) -> Dict:
        """Convert types where needed"""
        return data
    
    def _apply_defaults(self, data: Dict) -> Dict:
        """Apply default values for missing fields"""
        defaults = {
            "status": "pending",
            "priority": 0,
            "tags": [],
        }
        for key, default in defaults.items():
            if key not in data:
                data[key] = default
        return data
    
    async def process(self, data: Any) -> Any:
        """Process data through all transformers"""
        self._update_stats("requests")
        
        if not isinstance(data, dict):
            self._update_stats("errors")
            raise ValidationError("data", "Expected dictionary")
        
        result = data.copy()
        for transformer in self._transformers:
            try:
                result = transformer(result)
            except Exception as e:
                self._update_stats("errors")
                logger.error(f"Transformer {transformer.__name__} failed: {e}")
                raise ServiceError(f"Processing failed: {e}")
        
        return result
    
    def add_transformer(self, func: Callable) -> None:
        """Add custom transformer"""
        self._transformers.append(func)
    
    def add_validator(self, func: Callable) -> None:
        """Add custom validator"""
        self._validators.append(func)


class CacheService(BaseService):
    """Service for caching data"""
    
    def __init__(self, config: Optional[ServiceConfig] = None):
        super().__init__(config)
        self._storage: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
    
    async def _do_initialize(self) -> None:
        """Initialize cache service"""
        logger.info("Cache service initialized")
    
    async def process(self, data: Any) -> Any:
        """Not applicable for cache service"""
        raise NotImplementedError("Use get/set methods instead")
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if key in self._storage:
            self._update_stats("cache_hits")
            return self._storage[key]
        return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache"""
        import time
        self._storage[key] = value
        self._timestamps[key] = time.time()
    
    async def delete(self, key: str) -> bool:
        """Delete value from cache"""
        if key in self._storage:
            del self._storage[key]
            del self._timestamps[key]
            return True
        return False
    
    async def clear(self) -> None:
        """Clear all cache"""
        self._storage.clear()
        self._timestamps.clear()


# Module-level utilities
def create_service(service_type: str, config: Optional[ServiceConfig] = None) -> BaseService:
    """Factory function to create services"""
    services = {
        "processor": DataProcessor,
        "cache": CacheService,
    }
    
    if service_type not in services:
        raise ValueError(f"Unknown service type: {service_type}")
    
    return services[service_type](config)


async def run_pipeline(services: List[BaseService], data: Any) -> Any:
    """Run data through a pipeline of services"""
    result = data
    for service in services:
        result = await service.process(result)
    return result
'''
    
    def test_insert_large_method_into_dataprocessor(self):
        """Вставка большого метода с множеством вложенных блоков в DataProcessor"""
        base = self.get_large_base_file()
        
        # Большой сложный метод для вставки
        large_method = '''def validate_and_transform_batch(
    self,
    items: List[Dict[str, Any]],
    schema: Dict[str, type],
    strict_mode: bool = False,
    on_error: str = "skip"
) -> Dict[str, Any]:
    """
    Validate and transform a batch of items according to schema.
    
    Args:
        items: List of dictionaries to process
        schema: Expected schema {field_name: expected_type}
        strict_mode: If True, fail on any validation error
        on_error: Action on error - "skip", "null", or "raise"
        
    Returns:
        Dictionary with results and statistics
    """
    results = []
    errors = []
    skipped = 0
    processed = 0
    
    for idx, item in enumerate(items):
        try:
            # Validate against schema
            validated_item = {}
            for field_name, expected_type in schema.items():
                if field_name not in item:
                    if strict_mode:
                        raise ValidationError(
                            field_name,
                            f"Missing required field at index {idx}"
                        )
                    elif on_error == "null":
                        validated_item[field_name] = None
                    else:
                        continue
                else:
                    value = item[field_name]
                    if not isinstance(value, expected_type):
                        try:
                            value = expected_type(value)
                        except (ValueError, TypeError) as e:
                            if strict_mode:
                                raise ValidationError(
                                    field_name,
                                    f"Type conversion failed: {e}"
                                )
                            elif on_error == "raise":
                                raise
                            elif on_error == "null":
                                value = None
                            else:
                                skipped += 1
                                continue
                    validated_item[field_name] = value
            
            # Apply transformers
            for transformer in self._transformers:
                try:
                    validated_item = transformer(validated_item)
                except Exception as e:
                    logger.warning(
                        f"Transformer {transformer.__name__} failed "
                        f"on item {idx}: {e}"
                    )
                    if strict_mode:
                        raise ServiceError(
                            f"Transformation failed: {e}"
                        )
            
            results.append(validated_item)
            processed += 1
            
        except ValidationError as e:
            errors.append({
                "index": idx,
                "field": e.field,
                "message": e.message,
            })
            if on_error == "raise":
                raise
        except Exception as e:
            errors.append({
                "index": idx,
                "field": None,
                "message": str(e),
            })
            logger.error(f"Unexpected error processing item {idx}: {e}")
            if strict_mode:
                raise ServiceError(f"Batch processing failed: {e}")
    
    # Update statistics
    self._update_stats("requests", processed)
    self._update_stats("errors", len(errors))
    
    return {
        "results": results,
        "errors": errors,
        "statistics": {
            "total": len(items),
            "processed": processed,
            "skipped": skipped,
            "failed": len(errors),
        }
    }'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=large_method,
            target_class="DataProcessor"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # Проверяем структуру вставленного метода
        self.assert_line_indent(result.new_content, "def validate_and_transform_batch(", 4)
        self.assert_line_indent(result.new_content, "self,", 8)
        self.assert_line_indent(result.new_content, "items: List[Dict[str, Any]],", 8)
        self.assert_line_indent(result.new_content, ") -> Dict[str, Any]:", 4)
        
        # Проверяем тело метода
        self.assert_line_indent(result.new_content, "results = []", 8)
        self.assert_line_indent(result.new_content, "for idx, item in enumerate(items):", 8)
        self.assert_line_indent(result.new_content, "try:", 12)
        self.assert_line_indent(result.new_content, "validated_item = {}", 16)
        self.assert_line_indent(result.new_content, "for field_name, expected_type in schema.items():", 16)
        self.assert_line_indent(result.new_content, "if field_name not in item:", 20)
        self.assert_line_indent(result.new_content, "if strict_mode:", 24)
        
        # Проверяем что существующие методы не испорчены
        self.assert_method_structure(result.new_content, "_do_initialize", 4, 8)
        self.assert_method_structure(result.new_content, "_normalize_strings", 4, 8)
        self.assert_method_structure(result.new_content, "process", 4, 8)
    
    def test_insert_large_method_with_generator_error(self):
        """Вставка большого метода с типичной ошибкой Generator (def 0, body 8)"""
        base = self.get_large_base_file()
        
        # Метод с ошибкой: def без отступа, тело с 8 пробелами
        bad_large_method = '''def advanced_cache_lookup(
        self,
        keys: List[str],
        fallback_loader: Optional[Callable] = None,
        batch_size: int = 50
) -> Dict[str, Any]:
        """
        Advanced cache lookup with batch processing and fallback.
        
        Performs efficient batch lookups with automatic fallback
        to loader function for cache misses.
        """
        results = {}
        misses = []
        
        # Process in batches for efficiency
        for i in range(0, len(keys), batch_size):
            batch = keys[i:i + batch_size]
            
            for key in batch:
                cached = self._cache.get(key)
                if cached is not None:
                    results[key] = cached
                    self._update_stats("cache_hits")
                else:
                    misses.append(key)
        
        # Handle cache misses with fallback
        if misses and fallback_loader:
            try:
                loaded = fallback_loader(misses)
                if isinstance(loaded, dict):
                    for key, value in loaded.items():
                        results[key] = value
                        self._cache[key] = value
            except Exception as e:
                logger.error(f"Fallback loader failed: {e}")
                self._update_stats("errors")
        
        return results'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=bad_large_method,
            target_class="CacheService"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # Проверяем что отступы исправлены
        self.assert_line_indent(result.new_content, "def advanced_cache_lookup(", 4)
        self.assert_line_indent(result.new_content, "results = {}", 8)
        self.assert_line_indent(result.new_content, "for i in range(0, len(keys), batch_size):", 8)
        self.assert_line_indent(result.new_content, "batch = keys[i:i + batch_size]", 12)
        self.assert_line_indent(result.new_content, "for key in batch:", 12)
        self.assert_line_indent(result.new_content, "cached = self._cache.get(key)", 16)
        self.assert_line_indent(result.new_content, "if misses and fallback_loader:", 8)
        self.assert_line_indent(result.new_content, "try:", 12)
    
    def test_replace_large_method_preserving_context(self):
        """Замена большого метода в середине большого класса"""
        base = self.get_large_base_file()
        
        # Новая версия метода process в DataProcessor
        new_process = '''async def process(self, data: Any) -> Any:
    """
    Process data through validation and transformation pipeline.
    
    Enhanced version with detailed logging, metrics collection,
    and comprehensive error handling.
    
    Args:
        data: Input data to process (must be dict)
        
    Returns:
        Processed and transformed data
        
    Raises:
        ValidationError: If data validation fails
        ServiceError: If processing fails
    """
    import time
    start_time = time.time()
    self._update_stats("requests")
    
    # Input validation
    if data is None:
        self._update_stats("errors")
        raise ValidationError("data", "Cannot process None")
    
    if not isinstance(data, dict):
        self._update_stats("errors")
        raise ValidationError(
            "data",
            f"Expected dictionary, got {type(data).__name__}"
        )
    
    # Check cache first if enabled
    if self.config.enable_cache:
        cache_key = self._compute_cache_key(data)
        cached_result = self._cache.get(cache_key)
        if cached_result is not None:
            self._update_stats("cache_hits")
            logger.debug(f"Cache hit for key: {cache_key[:20]}...")
            return cached_result
    
    result = data.copy()
    
    # Run validators first
    for validator in self._validators:
        try:
            is_valid, error_msg = validator(result)
            if not is_valid:
                self._update_stats("errors")
                raise ValidationError("data", error_msg)
        except ValidationError:
            raise
        except Exception as e:
            logger.warning(f"Validator {validator.__name__} error: {e}")
            if self.config.debug_mode:
                raise
    
    # Apply transformers
    for transformer in self._transformers:
        try:
            logger.debug(f"Applying transformer: {transformer.__name__}")
            result = transformer(result)
        except Exception as e:
            self._update_stats("errors")
            error_msg = f"Transformer {transformer.__name__} failed: {e}"
            logger.error(error_msg)
            if self.config.debug_mode:
                raise ServiceError(error_msg)
            # In non-debug mode, continue with partial result
            logger.warning("Continuing with partial result")
    
    # Cache the result
    if self.config.enable_cache:
        self._cache[cache_key] = result
    
    # Log processing time
    elapsed = time.time() - start_time
    logger.info(f"Processed data in {elapsed:.3f}s")
    
    return result'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_METHOD,
            code=new_process,
            target_class="DataProcessor",
            target_method="process"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # Проверяем новый метод
        self.assert_line_indent(result.new_content, "async def process(self, data: Any) -> Any:", 4)
        self.assert_line_indent(result.new_content, "import time", 8)
        self.assert_line_indent(result.new_content, "start_time = time.time()", 8)
        self.assert_line_indent(result.new_content, "if data is None:", 8)
        self.assert_line_indent(result.new_content, 'raise ValidationError("data", "Cannot process None")', 12)
        self.assert_line_indent(result.new_content, "if self.config.enable_cache:", 8)
        self.assert_line_indent(result.new_content, "cache_key = self._compute_cache_key(data)", 12)
        
        # Проверяем что соседние методы не испорчены
        self.assert_method_structure(result.new_content, "_do_initialize", 4, 8)
        self.assert_method_structure(result.new_content, "_load_default_transformers", 4, 8)
        self.assert_method_structure(result.new_content, "_normalize_strings", 4, 8)
        self.assert_method_structure(result.new_content, "add_transformer", 4, 8)
    
    def test_insert_entire_new_class_into_large_file(self):
        """Вставка целого нового класса в большой файл"""
        base = self.get_large_base_file()
        
        # Новый класс для вставки
        new_class = '''class EventService(BaseService):
    """
    Service for handling events and notifications.
    
    Supports multiple event types, handlers, and async processing
    with configurable retry logic and dead-letter queues.
    """
    
    def __init__(
        self,
        config: Optional[ServiceConfig] = None,
        max_handlers_per_event: int = 10
    ):
        super().__init__(config)
        self._handlers: Dict[str, List[Callable]] = {}
        self._event_queue: List[Dict] = []
        self._max_handlers = max_handlers_per_event
        self._processing = False
        self._dead_letter_queue: List[Dict] = []
    
    async def _do_initialize(self) -> None:
        """Initialize event service"""
        logger.info("Event service initializing")
        self._register_default_handlers()
        logger.info(f"Registered {len(self._handlers)} event types")
    
    def _register_default_handlers(self) -> None:
        """Register default event handlers"""
        self.register_handler("error", self._handle_error_event)
        self.register_handler("warning", self._handle_warning_event)
        self.register_handler("info", self._handle_info_event)
    
    async def _handle_error_event(self, event: Dict) -> None:
        """Handle error events"""
        logger.error(f"Error event: {event.get('message', 'Unknown error')}")
        self._update_stats("errors")
    
    async def _handle_warning_event(self, event: Dict) -> None:
        """Handle warning events"""
        logger.warning(f"Warning event: {event.get('message', 'Unknown warning')}")
    
    async def _handle_info_event(self, event: Dict) -> None:
        """Handle info events"""
        logger.info(f"Info event: {event.get('message', 'No message')}")
    
    def register_handler(
        self,
        event_type: str,
        handler: Callable,
        priority: int = 0
    ) -> bool:
        """
        Register an event handler.
        
        Args:
            event_type: Type of event to handle
            handler: Async callable to handle the event
            priority: Handler priority (higher = called first)
            
        Returns:
            True if registered, False if max handlers reached
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        
        if len(self._handlers[event_type]) >= self._max_handlers:
            logger.warning(
                f"Max handlers ({self._max_handlers}) reached for {event_type}"
            )
            return False
        
        self._handlers[event_type].append((priority, handler))
        self._handlers[event_type].sort(key=lambda x: -x[0])
        return True
    
    async def emit(
        self,
        event_type: str,
        data: Dict[str, Any],
        wait: bool = False
    ) -> Optional[List[Any]]:
        """
        Emit an event to all registered handlers.
        
        Args:
            event_type: Type of event
            data: Event data
            wait: If True, wait for all handlers to complete
            
        Returns:
            List of handler results if wait=True, else None
        """
        self._update_stats("requests")
        
        event = {
            "type": event_type,
            "data": data,
            "timestamp": asyncio.get_event_loop().time(),
        }
        
        handlers = self._handlers.get(event_type, [])
        
        if not handlers:
            logger.debug(f"No handlers for event type: {event_type}")
            return [] if wait else None
        
        if wait:
            results = []
            for priority, handler in handlers:
                try:
                    result = await handler(event)
                    results.append(result)
                except Exception as e:
                    logger.error(f"Handler failed for {event_type}: {e}")
                    self._dead_letter_queue.append({
                        "event": event,
                        "handler": handler.__name__,
                        "error": str(e),
                    })
                    self._update_stats("errors")
            return results
        else:
            # Fire and forget
            for priority, handler in handlers:
                asyncio.create_task(self._safe_call(handler, event))
            return None
    
    async def _safe_call(self, handler: Callable, event: Dict) -> None:
        """Safely call a handler with error handling"""
        try:
            await handler(event)
        except Exception as e:
            logger.error(f"Async handler error: {e}")
            self._dead_letter_queue.append({
                "event": event,
                "handler": handler.__name__,
                "error": str(e),
            })
            self._update_stats("errors")
    
    async def process(self, data: Any) -> Any:
        """Process an event through the system"""
        if not isinstance(data, dict):
            raise ValidationError("data", "Event must be a dictionary")
        
        event_type = data.get("type", "info")
        return await self.emit(event_type, data, wait=True)
    
    def get_dead_letters(self) -> List[Dict]:
        """Get failed events from dead letter queue"""
        return self._dead_letter_queue.copy()
    
    def clear_dead_letters(self) -> int:
        """Clear dead letter queue and return count"""
        count = len(self._dead_letter_queue)
        self._dead_letter_queue.clear()
        return count'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.APPEND_TO_FILE,
            code=new_class
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # Проверяем структуру нового класса
        self.assert_line_indent(result.new_content, "class EventService(BaseService):", 0)
        self.assert_method_structure(result.new_content, "_do_initialize", 4, 8)
        self.assert_method_structure(result.new_content, "_register_default_handlers", 4, 8)
        self.assert_method_structure(result.new_content, "register_handler", 4, 8)
        self.assert_method_structure(result.new_content, "emit", 4, 8)
        self.assert_method_structure(result.new_content, "_safe_call", 4, 8)
        
        # Проверяем вложенную структуру в emit
        self.assert_line_indent(result.new_content, "if wait:", 8)
        self.assert_line_indent(result.new_content, "for priority, handler in handlers:", 12)
        self.assert_line_indent(result.new_content, "try:", 16)
        self.assert_line_indent(result.new_content, "result = await handler(event)", 20)
        
        # Проверяем что существующие классы не испорчены
        self.assert_method_structure(result.new_content, "initialize", 4, 8)  # BaseService
        self.assert_method_structure(result.new_content, "_normalize_strings", 4, 8)  # DataProcessor
    
    def test_patch_large_method_with_complex_code(self):
        """Вставка сложного кода в середину большого метода"""
        base = self.get_large_base_file()
        
        # Код для вставки в метод process класса DataProcessor
        patch_code = '''# Performance monitoring
import time
perf_start = time.perf_counter()
logger.debug(f"Starting processing for data with {len(data)} keys")

# Pre-validation hooks
for hook in getattr(self, '_pre_hooks', []):
    try:
        data = hook(data)
    except Exception as e:
        logger.warning(f"Pre-hook {hook.__name__} failed: {e}")

# Deep copy for safety
import copy
original_data = copy.deepcopy(data)'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.PATCH_METHOD,
            code=patch_code,
            target_class="DataProcessor",
            target_method="process",
            insert_after='self._update_stats("requests")'
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success, f"Failed: {result.message}")
        self.assert_valid_python(result.new_content)
        
        # Проверяем что код вставлен с правильными отступами
        self.assert_line_indent(result.new_content, "# Performance monitoring", 8)
        self.assert_line_indent(result.new_content, "import time", 8)
        self.assert_line_indent(result.new_content, "perf_start = time.perf_counter()", 8)
        self.assert_line_indent(result.new_content, "for hook in getattr(self, '_pre_hooks', []):", 8)
        self.assert_line_indent(result.new_content, "try:", 12)
        self.assert_line_indent(result.new_content, "data = hook(data)", 16)
        self.assert_line_indent(result.new_content, "except Exception as e:", 12)


class TestMultipleConsecutiveModifications(IndentNormalizationTestCase):
    """Тесты нескольких последовательных модификаций"""
    
    def get_base(self) -> str:
        return '''class MultiModifyTest:
    """Test class for multiple modifications"""
    
    def __init__(self):
        self.value = 0
    
    def method_a(self):
        return "a"
    
    def method_b(self):
        return "b"
    
    def method_c(self):
        return "c"
'''
    
    def test_three_consecutive_inserts(self):
        """Три последовательные вставки методов"""
        content = self.get_base()
        
        methods = [
            '''def new_method_1(self):
    """First new method"""
    x = 1
    return x''',
            '''def new_method_2(self):
    """Second new method"""
    if self.value > 0:
        return True
    return False''',
            '''def new_method_3(self):
    """Third new method"""
    results = []
    for i in range(10):
        results.append(i * 2)
    return results'''
        ]
        
        for i, method in enumerate(methods):
            instruction = ModifyInstruction(
                mode=ModifyMode.INSERT_INTO_CLASS,
                code=method,
                target_class="MultiModifyTest"
            )
            result = self.modifier.apply(content, instruction)
            self.assertTrue(result.success, f"Insert {i+1} failed: {result.message}")
            self.assert_valid_python(result.new_content)
            content = result.new_content
        
        # Проверяем все методы
        self.assert_method_structure(content, "__init__", 4, 8)
        self.assert_method_structure(content, "method_a", 4, 8)
        self.assert_method_structure(content, "method_b", 4, 8)
        self.assert_method_structure(content, "method_c", 4, 8)
        self.assert_method_structure(content, "new_method_1", 4, 8)
        self.assert_method_structure(content, "new_method_2", 4, 8)
        self.assert_method_structure(content, "new_method_3", 4, 8)
    
    def test_insert_then_replace_then_patch(self):
        """Вставка, затем замена, затем патч"""
        content = self.get_base()
        
        # 1. Вставляем новый метод
        new_method = '''def helper(self, x):
    return x * 2'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=new_method,
            target_class="MultiModifyTest"
        )
        result = self.modifier.apply(content, instruction)
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        content = result.new_content
        
        # 2. Заменяем method_a
        replacement = '''def method_a(self):
    """Updated method_a"""
    result = self.helper(5)
    return f"a: {result}"'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.REPLACE_METHOD,
            code=replacement,
            target_class="MultiModifyTest",
            target_method="method_a"
        )
        result = self.modifier.apply(content, instruction)
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        content = result.new_content
        
        # 3. Патчим method_b
        patch = '''logger.info("method_b called")
self.value += 1'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.PATCH_METHOD,
            code=patch,
            target_class="MultiModifyTest",
            target_method="method_b",
            insert_before='return "b"'
        )
        result = self.modifier.apply(content, instruction)
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        content = result.new_content
        
        # Проверяем итоговую структуру
        self.assert_method_structure(content, "helper", 4, 8)
        self.assert_method_structure(content, "method_a", 4, 8)
        self.assert_line_indent(content, 'result = self.helper(5)', 8)
        self.assert_line_indent(content, 'logger.info("method_b called")', 8)
        self.assert_line_indent(content, 'self.value += 1', 8)


class TestExtremeNestingDepth(IndentNormalizationTestCase):
    """Тесты с экстремальной глубиной вложенности"""
    
    def test_deeply_nested_code_7_levels(self):
        """Код с 7 уровнями вложенности"""
        base = '''class DeepNester:
    pass
'''
        
        deep_method = '''def ultra_deep(self, data):
    """Method with extreme nesting"""
    for item in data:
        if item.active:
            try:
                with item.lock:
                    for sub in item.children:
                        if sub.valid:
                            for element in sub.elements:
                                if element.ready:
                                    result = element.process()
                                    if result:
                                        yield result
            except Exception as e:
                logger.error(f"Error: {e}")
                raise'''
        
        instruction = ModifyInstruction(
            mode=ModifyMode.INSERT_INTO_CLASS,
            code=deep_method,
            target_class="DeepNester"
        )
        
        result = self.modifier.apply(base, instruction)
        
        self.assertTrue(result.success)
        self.assert_valid_python(result.new_content)
        
        # Проверяем цепочку отступов: 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48
        self.assert_line_indent(result.new_content, "def ultra_deep(self, data):", 4)
        self.assert_line_indent(result.new_content, "for item in data:", 8)
        self.assert_line_indent(result.new_content, "if item.active:", 12)
        self.assert_line_indent(result.new_content, "try:", 16)
        self.assert_line_indent(result.new_content, "with item.lock:", 20)
        self.assert_line_indent(result.new_content, "for sub in item.children:", 24)
        self.assert_line_indent(result.new_content, "if sub.valid:", 28)
        self.assert_line_indent(result.new_content, "for element in sub.elements:", 32)
        self.assert_line_indent(result.new_content, "if element.ready:", 36)
        self.assert_line_indent(result.new_content, "result = element.process()", 40)
        self.assert_line_indent(result.new_content, "if result:", 44)
        self.assert_line_indent(result.new_content, "yield result", 48)


# Добавляем новые тестовые классы в runner
def run_tests():
    """Запускает все тесты"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    test_classes = [
        TestInsertIntoClassDetailed,
        TestReplaceMethodDetailed,
        TestPatchMethodDetailed,
        TestReplaceFunctionDetailed,
        TestAppendToFileDetailed,
        TestEdgeCasesDetailed,
        TestRealGeneratorErrors,
        # Новые сложные тесты
        TestComplexLargeCodeInsertion,
        TestMultipleConsecutiveModifications,
        TestExtremeNestingDepth,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    
    result = runner.run(suite)
    
    print("\n" + "="*70)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("="*70)
    print(f"Всего тестов: {result.testsRun}")
    print(f"Успешно: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Провалено: {len(result.failures)}")
    print(f"Ошибок: {len(result.errors)}")
    
    if result.failures:
        print("\nПРОВАЛЕННЫЕ ТЕСТЫ:")
        for test, traceback in result.failures:
            print(f"  - {test}")
    
    if result.errors:
        print("\nОШИБКИ:")
        for test, traceback in result.errors:
            print(f"  - {test}")
    
    print("="*70)
    
    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)