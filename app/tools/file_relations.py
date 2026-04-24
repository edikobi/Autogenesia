# app/tools/file_relations.py
"""
Show file relations in the project: dependencies (imports), usage (imported by), tests, and siblings.
Supports Python, JavaScript, TypeScript, Go, and Java.
Helps understand the architectural role of a file and the impact of its changes.
"""

import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
import logging
from app.services.tree_sitter_parser import get_multi_language_parser, get_parser

logger = logging.getLogger(__name__)


def show_file_relations_tool(
    file_path: str,
    project_dir: str,
    virtual_fs: Optional[Any] = None,
    include_tests: bool = True,
    include_siblings: bool = True,
    max_relations: int = 20,
    element_name: Optional[str] = None,
    element_type: str = "all"
) -> str:
    """
    Analyzes and displays structural relationships of the target file or a specific element within it.
    Works with Python, JS/TS, Go, and Java.
    
    Args:
        file_path: Relative path to the file to analyze.
        project_dir: Project root directory.
        virtual_fs: VirtualFileSystem instance (optional, for staged changes).
        include_tests: Whether to find associated test files.
        include_siblings: Whether to include files in the same directory.
        max_relations: Maximum results per category (0 = all).
        
    Returns:
        XML-formatted list of file relations.
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
        element_name=element_name,
        element_type=element_type,
    )
    
    return _format_relations(normalized_path, relations)


def _analyze_file_relations(
    file_path: str,
    project_dir: str,
    virtual_fs: Optional[Any],
    include_tests: bool,
    include_siblings: bool,
    max_relations: int,
    element_name: Optional[str] = None,
    element_type: str = "all",
) -> Dict[str, Any]:
    """
    Analyzes all types of relations for a file.
    relations = {
        "imports": [],      # Что этот файл импортирует
        "imported_by": [],  # Кто импортирует этот файл
        "tests": [],        # Тестовые файлы
        "siblings": [],     # Файлы в той же директории
    }
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
            
            # 3. Who imports this file (reverse dependencies)
            if virtual_fs is not None:
                relations["imported_by"] = _find_importers(file_path, project_dir, virtual_fs)[:max_relations]
            
            # 4. Element-specific relations
            if element_name and virtual_fs is not None:
                relations["element_relations"] = _analyze_element_relations(
                    file_path, element_name, element_type, content, project_dir, virtual_fs, max_relations
                )
        
        # 5. Test files
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
    """Извлекает импорты из содержимого файла (поддержка нескольких языков)."""
    imports = []
    
    # Python: import X
    import_pattern = r'^import\s+([\w.]+)'
    for match in re.finditer(import_pattern, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # Python: from X import Y
    from_pattern = r'^from\s+([\w.]+)\s+import'
    for match in re.finditer(from_pattern, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # JavaScript/TypeScript: import ... from 'X' or "X"
    js_import_from = r'''import\s+.*?\s+from\s+['"]([^'"]+)['"]'''
    for match in re.finditer(js_import_from, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # JavaScript/TypeScript: import 'X' or "X" (side-effect import)
    js_import_direct = r'''import\s+['"]([^'"]+)['"]'''
    for match in re.finditer(js_import_direct, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # JavaScript: require('X') or require("X")
    js_require = r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)'''
    for match in re.finditer(js_require, content):
        imports.append(match.group(1))
    
    # Go: import "X" or import ( "X" )
    go_import = r'''import\s+["']([^"']+)["']'''
    for match in re.finditer(go_import, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # Go: import block
    go_import_block = r'''import\s*\(\s*([^)]+)\s*\)'''
    for block_match in re.finditer(go_import_block, content, re.DOTALL):
        block = block_match.group(1)
        for line_match in re.finditer(r'''["']([^"']+)["']''', block):
            imports.append(line_match.group(1))
    
    # Java: import X.Y.Z;
    java_import = r'^import\s+([\w.]+);'
    for match in re.finditer(java_import, content, re.MULTILINE):
        imports.append(match.group(1))
    
    # Remove duplicates while preserving order
    seen = set()
    unique_imports = []
    for imp in imports:
        if imp not in seen:
            seen.add(imp)
            unique_imports.append(imp)
    
    return unique_imports


def _find_importers(
    file_path: str, 
    project_dir: str, 
    virtual_fs: Any
) -> List[str]:
    """Finds files that import/use this file (Reverse Dependencies)."""
    # Detect language
    lang = _detect_language(file_path)
    if not lang:
        return []
        
    importers = []
    # Get all supported source files in project
    if hasattr(virtual_fs, 'get_all_supported_files'):
        source_files = virtual_fs.get_all_supported_files()
    else:
        # Fallback to python files
        source_files = virtual_fs.get_all_python_files()
    
    # Get possible names/paths used to import this file
    import_names = _get_possible_import_names(file_path, lang, virtual_fs)
    if not import_names:
        return []
    
    for other_file in source_files:
        if other_file == file_path:
            continue
        
        content = virtual_fs.read_file(other_file)
        if not content:
            continue
        
        # Check if this file imports any of the target names
        for name in import_names:
            if _file_imports_name(content, name, lang):
                importers.append(other_file)
                break
    
    return importers


def _find_test_files(file_path: str, virtual_fs: Any) -> List[str]:
    """Finds associated test files for the given file."""
    # Use VFS method if available
    if hasattr(virtual_fs, 'find_test_files'):
        vfs_tests = virtual_fs.find_test_files(file_path)
        if vfs_tests:
            return vfs_tests
    
    test_files = []
    stem = Path(file_path).stem
    ext = Path(file_path).suffix.lower()
    parent = Path(file_path).parent
    
    # Language-specific patterns
    patterns = []
    
    if ext == '.py':
        patterns = [
            f"test_{stem}.py", f"{stem}_test.py",
            str(parent / f"test_{stem}.py"),
            str(parent / "tests" / f"test_{stem}.py"),
            str(parent / "test" / f"test_{stem}.py"),
        ]
    elif ext in ('.js', '.jsx', '.ts', '.tsx'):
        # JS/TS common patterns: mod.test.ts, mod.spec.ts, __tests__/mod.ts
        patterns = [
            f"{stem}.test{ext}", f"{stem}.spec{ext}",
            str(parent / f"{stem}.test{ext}"),
            str(parent / f"{stem}.spec{ext}"),
            str(parent / "__tests__" / f"{stem}{ext}"),
            str(parent / "tests" / f"{stem}.test{ext}"),
        ]
    elif ext == '.go':
        # Go: strictly mod_test.go in same dir
        patterns = [f"{stem}_test.go", str(parent / f"{stem}_test.go")]
    elif ext == '.java':
        # Java: ClassTest.java or ClassTests.java
        patterns = [
            f"{stem}Test.java", f"{stem}Tests.java",
            # Standard Maven/Gradle structure search (simplified)
            str(parent).replace("src/main/java", "src/test/java") + f"/{stem}Test.java",
        ]
    
    for pattern in patterns:
        normalized = pattern.replace('\\', '/')
        if virtual_fs.file_exists(normalized):
            test_files.append(normalized)
    
    return sorted(list(set(test_files)))


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


def _detect_language(file_path: str) -> Optional[str]:
    """Detects language from extension."""
    ext = Path(file_path).suffix.lower()
    if ext == '.py': return 'python'
    if ext in ('.js', '.jsx'): return 'javascript'
    if ext in ('.ts', '.tsx'): return 'typescript'
    if ext == '.go': return 'go'
    if ext == '.java': return 'java'
    return None


def _get_possible_import_names(file_path: str, lang: str, virtual_fs: Any) -> List[str]:
    """Returns possible names or path fragments used to import the given file."""
    names = []
    path_obj = Path(file_path)
    stem = path_obj.stem
    
    if lang == 'python':
        mod = file_path[:-3].replace('/', '.')
        if mod.endswith('.__init__'): mod = mod[:-9]
        names.append(mod)
        # Handle case where it's imported from direct parent
        if len(path_obj.parts) > 1:
            names.append(stem)
            
    elif lang in ('javascript', 'typescript'):
        # JS/TS: usually relative. Search for path suffix (smart suffix).
        # src/components/Button.tsx -> look for "components/Button"
        parts = list(path_obj.parent.parts) + [stem]
        if 'src' in parts:
            idx = parts.index('src')
            names.append("/".join(parts[idx+1:]))
        if len(parts) >= 2:
            names.append("/".join(parts[-2:]))
        names.append(stem)
        
    elif lang == 'go':
        # Go: import "path/to/pkg". Usually the path from project root or since src.
        content = virtual_fs.read_file(file_path)
        if content:
            match = re.search(r'package\s+(\w+)', content)
            if match:
                names.append(match.group(1))
        # Also use directory path as package path
        parent_dir = str(path_obj.parent).replace('\\', '/')
        if parent_dir and parent_dir != '.':
            names.append(parent_dir)
            
    elif lang == 'java':
        # Java: need FQCN. Package declaration + ClassName.
        content = virtual_fs.read_file(file_path)
        if content:
            match = re.search(r'package\s+([\w.]+);', content)
            if match:
                pkg = match.group(1)
                names.append(f"{pkg}.{stem}")
        # Secondary: class name alone
        names.append(stem)
    
    return list(set(names))


def _file_imports_name(content: str, name: str, lang: str) -> bool:
    """Checks if content imports the given name/path for specific language."""
    if not name: return False
    
    if lang == 'python':
        escaped = re.escape(name)
        patterns = [
            rf'^import\s+{escaped}(?:\s|,|$)',
            rf'^from\s+{escaped}\s+import',
        ]
        # Also handle "from parent import name"
        if '.' in name:
            parent, child = name.rsplit('.', 1)
            patterns.append(rf'^from\s+{re.escape(parent)}\s+import\s+.*\b{re.escape(child)}\b')
        else:
            patterns.append(rf'^import\s+.*\b{re.escape(name)}\b')
            
    elif lang in ('javascript', 'typescript'):
        # JS/TS: look for name in quotes (covers relative and aliased imports)
        # Example: import ... from '@/services/api' or require('./api')
        # We look for the "smart suffix" inside quotes
        escaped = re.escape(name)
        patterns = [
            rf'''from\s+['"].*?{escaped}['"]''',
            rf'''import\s*\(?\s*['"].*?{escaped}['"]''',
            rf'''require\s*\(\s*['"].*?{escaped}['"]'''
        ]
        
    elif lang == 'go':
        # Go imports are in quotes.
        escaped = re.escape(name)
        patterns = [rf'''["']\s*.*?{escaped}\s*["']''']
        
    elif lang == 'java':
        # Java: import com.pkg.Class; or usage of class name
        escaped = re.escape(name)
        patterns = [
            rf'^import\s+(?:static\s+)?{escaped};',
            rf'\b{re.escape(name.split(".")[-1])}\b' # Class name usage
        ]
    
    for p in patterns:
        if re.search(p, content, re.MULTILINE):
            return True
    return False


def _analyze_element_relations(
    file_path: str,
    element_name: str,
    element_type: str,
    file_content: str,
    project_dir: str,
    virtual_fs: Any,
    max_relations: int
) -> Dict[str, Any]:
    """Analyzes dependencies and usage of a specific code element."""
    lang = _detect_language(file_path)
    if not lang:
        return {"name": element_name, "type": element_type, "dependencies": [], "usages": []}
    
    # Choose parser based on language
    if lang == "python":
        parser = get_parser()
    else:
        parser = get_multi_language_parser()
        
    element = parser.find_element(file_content, lang, element_name, element_type)
    
    if not element:
        logger.warning(f"Element {element_name} ({element_type}) not found in {file_path}")
        return {"name": element_name, "type": element_type, "dependencies": [], "usages": []}
    
    # 1. External dependencies of this element
    # Get all identifiers used in the element's body
    used_ids = parser.get_used_identifiers(element['content'], lang)
    
    # Get all file-level imports and filter used identifiers against them
    file_imports = _extract_imports(file_content)
    # Also get all defined elements in the same file to show internal dependencies
    local_elements = parser.get_defined_elements(file_content, lang)
    
    dependencies = []
    # Heuristic: if a used identifier is part of an import string, it's a dependency
    for imp in file_imports:
        # Check if any part of the import is used
        for uid in used_ids:
            if uid in imp:
                dependencies.append(imp)
                break
    
    # Also add local dependencies if they are classes or functions
    for loc in local_elements:
        if loc in used_ids and loc != element_name:
            dependencies.append(f"local:{loc}")
            
    # 2. Project-wide usage of this element
    usages, total_count = _find_element_usages(
        element_name, element_type, file_path, lang, project_dir, virtual_fs, max_relations
    )
    
    return {
        "name": element_name,
        "type": element_type,
        "dependencies": sorted(list(set(dependencies))),
        "usages": usages,
        "total_usages": total_count
    }


def _find_element_usages(
    name: str,
    elem_type: str,
    file_path: str,
    lang: str,
    project_dir: str,
    virtual_fs: Any,
    max_results: int
) -> Tuple[List[str], int]:
    """Finds project-wide usages of a specific class or method using smart patterns."""
    source_files = virtual_fs.get_all_supported_files() if hasattr(virtual_fs, 'get_all_supported_files') else []
    if not source_files:
        return [], 0
        
    usages = []
    total_count = 0
    
    # Build patterns based on type and language
    # Example: Class.method, instance.method, methodName(
    escaped_name = re.escape(name)
    patterns = []
    
    if elem_type == "class":
        patterns = [
            rf'\b{escaped_name}\b',             # Static call or type reference
            rf'new\s+{escaped_name}\b',        # Initialization (JS/Java)
            rf'{escaped_name}\s*\('            # Initialization (Python)
        ]
    elif elem_type in ("method", "function"):
        patterns = [
            rf'\.\s*{escaped_name}\s*\(',      # method call: .name(
            rf'\b{escaped_name}\s*\(',         # direct call: name(
        ]
        if lang in ("javascript", "typescript"):
            patterns.append(rf'{escaped_name}\s*:') # object property/method
            
    for other_file in source_files:
        if other_file == file_path: continue
        
        content = virtual_fs.read_file(other_file)
        if not content: continue
        
        found = False
        for p in patterns:
            if re.search(p, content):
                found = True
                break
        
        if found:
            total_count += 1
            if len(usages) < max_results:
                usages.append(other_file)
                
    return usages, total_count


def _format_relations(file_path: str, relations: Dict[str, Any]) -> str:
    """Formats relations as XML with consistent escaping and structure."""
    res = []
    
    # Summary comments
    res.append(f"<!-- FILE RELATIONS: {file_path} -->")
    res.append(f"<!-- Total imports: {len(relations['imports'])} | Imported by: {len(relations['imported_by'])} -->")
    res.append(f"<!-- Test files: {len(relations['tests'])} | Sibling files: {len(relations['siblings'])} -->")
    res.append("")
    
    res.append(f'<file_relations path="{_escape_xml(file_path)}">')
    
    # Imports
    res.append(f'  <imports count="{len(relations["imports"])}">')
    for imp in relations["imports"]:
        res.append(f'    <import>{_escape_xml(imp)}</import>')
    res.append('  </imports>')
    
    # Imported by
    res.append(f'  <imported_by count="{len(relations["imported_by"])}">')
    for importer in relations["imported_by"]:
        res.append(f'    <importer>{_escape_xml(importer)}</importer>')
    res.append('  </imported_by>')
    
    # Test files
    res.append(f'  <tests count="{len(relations["tests"])}">')
    for test in relations["tests"]:
        res.append(f'    <test>{_escape_xml(test)}</test>')
    res.append('  </tests>')
    
    # Siblings
    res.append(f'  <siblings count="{len(relations["siblings"])}">')
    for sibling in relations["siblings"]:
        res.append(f'    <sibling>{_escape_xml(sibling)}</sibling>')
    res.append('  </siblings>')
    
    # Element relations (NEW)
    if "element_relations" in relations:
        elem = relations["element_relations"]
        res.append(f'  <element_relations name="{_escape_xml(elem["name"])}" type="{_escape_xml(elem["type"])}">')
        
        # Dependencies of this element
        deps = elem.get("dependencies", [])
        res.append(f'    <dependencies count="{len(deps)}">')
        for dep in deps:
            res.append(f'      <dependency>{_escape_xml(dep)}</dependency>')
        res.append('    </dependencies>')
        
        # Usages of this element
        usages = elem.get("usages", [])
        total_usages = elem.get("total_usages", len(usages))
        trunc_attr = f' total_found="{total_usages}"' if total_usages > len(usages) else ""
        res.append(f'    <usages count="{len(usages)}"{trunc_attr}>')
        for usage in usages:
            res.append(f'      <usage>{_escape_xml(usage)}</usage>')
        res.append('    </usages>')
        
        if total_usages > len(usages):
            res.append(f'    <!-- NOTE: Only first {len(usages)} of {total_usages} usages are shown. -->')
            
        res.append('  </element_relations>')
    
    res.append('</file_relations>')
    
    return "\n".join(res)


def _format_error(message: str) -> str:
    """Форматирует сообщение об ошибке."""
    return f"""<!-- FILE RELATIONS ERROR -->
<error>
  <message>{_escape_xml(message)}</message>
  <suggestion>Проверьте путь к файлу и убедитесь, что он существует в проекте.</suggestion>
</error>"""


def _escape_xml(text: str) -> str:
    """Escapes special characters for XML."""
    if not isinstance(text, str):
        return str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&apos;")