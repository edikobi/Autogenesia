#!/usr/bin/env python3
# scripts/test_orchestrator_raw.py
"""
Диагностический скрипт для анализа сырого вывода Orchestrator.

ЦЕЛЬ: Понять, где теряется инструкция — при генерации или при парсинге.

Выводит:
1. Сырой ответ модели (raw_response) - без обработки
2. Распарсенный analysis
3. Распарсенную instruction
4. Сравнение длин

Без Code Generator — только до оркестратора.
"""

from __future__ import annotations
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import re

# Добавляем корень проекта в путь
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# ЦВЕТА
# ============================================================================

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"


def print_header(text: str, char: str = "="):
    width = 80
    print(f"\n{Colors.CYAN}{char * width}")
    print(f"{Colors.BOLD}{text.center(width)}")
    print(f"{char * width}{Colors.RESET}\n")


def print_section(title: str):
    print(f"\n{Colors.YELLOW}{'─' * 60}")
    print(f"{Colors.BOLD}  {title}")
    print(f"{'─' * 60}{Colors.RESET}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")


def print_info(text: str):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")


def print_metric(label: str, value: Any, threshold: Optional[int] = None):
    """Выводит метрику с цветовой индикацией"""
    if threshold and isinstance(value, (int, float)):
        color = Colors.GREEN if value >= threshold else Colors.RED
    else:
        color = Colors.RESET
    print(f"  • {label}: {color}{value}{Colors.RESET}")


# ============================================================================
# ТЕСТОВЫЕ ЗАПРОСЫ
# ============================================================================

TEST_QUERIES = [
    {
        "id": "gemini_integration",
        "query": """Я хочу внедрить еще одну модель ИИ для Оркестратора (просто для выбора пользователя, как Deepseek V3.2 рассуждающий), а именно Gemini 3.0 pro. Проанализируй файлы проекта, особенно settings.py, orchestrator.py, api_client.py и укажи, учитывая особенность этой модели при работе с инструментами (надо парсить и возвращать Thought Signatures назад), то как аккуратно внедрить эту модель, чтобы не испортить работу остальных моделей. Важно (!) ищи в Интернете официальную документацию именно на модель Gemini 3.0 pro! После анализа, напиши код решения и укажи, куда его вставить.""",
        "description": "Сложный запрос: интеграция новой модели с web_search",
        "expected_scope": "C",
    },
    {
        "id": "simple_bug",
        "query": "Найди и исправь баг в функции calculate_average в файле app/utils/math_helpers.py - она падает при пустом списке",
        "description": "Простой запрос: исправление конкретного бага",
        "expected_scope": "A",
    },
    {
        "id": "add_logging",
        "query": "Добавь логирование во все методы класса LLMClient в файле app/llm/api_client.py",
        "description": "Средний запрос: добавление функциональности",
        "expected_scope": "B",
    },
]


# ============================================================================
# ДИАГНОСТИЧЕСКИЕ ФУНКЦИИ
# ============================================================================

def analyze_raw_response(raw_response: str) -> Dict[str, Any]:
    """
    Детальный анализ сырого ответа оркестратора.
    
    Returns:
        Dict с метриками и флагами проблем
    """
    analysis = {
        "total_length": len(raw_response),
        "line_count": raw_response.count('\n'),
        "has_analysis_header": bool(re.search(r'##\s*Analysis', raw_response, re.IGNORECASE)),
        "has_instruction_header": bool(re.search(r'##\s*Instruction', raw_response, re.IGNORECASE)),
        "has_scope": bool(re.search(r'\*\*SCOPE:\*\*', raw_response)),
        "has_task": bool(re.search(r'\*\*Task:\*\*', raw_response)),
        "has_file_block": bool(re.search(r'###\s*FILE:', raw_response)),
        "has_file_alt": bool(re.search(r'\*\*File:\*\*', raw_response)),
        "has_action_block": bool(re.search(r'####\s*(MODIFY_|ADD_|CREATE_|DELETE)', raw_response)),
        "has_changes": bool(re.search(r'\*\*Changes:\*\*', raw_response)),
        "truncation_markers": [],
        "potential_issues": [],
    }
    
    # Проверка на обрезание
    truncation_patterns = [
        (r'---\s*\n\s*#\s*\n\s*---', "Пустой заголовок между разделителями"),
        (r'\*\*SCOPE:\*\*\s*[A-D]\s*\n\s*\*\*Task:\*\*[^\n]+\n\s*---\s*$', "Обрезано после Task"),
        (r'###\s*FILE:[^\n]*\n\s*$', "Обрезано после FILE"),
        (r'\n\s*$', None),  # Просто пустая строка в конце - не проблема
    ]
    
    for pattern, message in truncation_patterns:
        if message and re.search(pattern, raw_response):
            analysis["truncation_markers"].append(message)
    
    # Проверка структуры
    if analysis["has_instruction_header"]:
        # Найдём позицию начала инструкции
        match = re.search(r'##\s*Instruction[^\n]*\n', raw_response, re.IGNORECASE)
        if match:
            instruction_start = match.end()
            instruction_content = raw_response[instruction_start:]
            analysis["instruction_content_length"] = len(instruction_content)
            
            # Проверяем, есть ли реальный контент
            if len(instruction_content.strip()) < 100:
                analysis["potential_issues"].append("Инструкция слишком короткая (<100 символов)")
    else:
        analysis["potential_issues"].append("Отсутствует заголовок ## Instruction")
    
    # Проверка формата
    if not analysis["has_scope"] and not analysis["has_task"]:
        analysis["potential_issues"].append("Нет ни **SCOPE:**, ни **Task:**")
    
    if not analysis["has_file_block"] and not analysis["has_file_alt"]:
        analysis["potential_issues"].append("Нет указания файла (ни ### FILE:, ни **File:**)")
    
    if not analysis["has_action_block"] and not analysis["has_changes"]:
        analysis["potential_issues"].append("Нет блоков действий (#### ACTION или **Changes:**)")
    
    return analysis


def extract_sections(raw_response: str) -> Dict[str, str]:
    """
    Извлекает секции из сырого ответа для сравнения.
    """
    sections = {
        "before_analysis": "",
        "analysis": "",
        "instruction": "",
        "after_instruction": "",
    }
    
    # Ищем Analysis
    analysis_match = re.search(
        r'##\s*Analysis\s*\n(.*?)(?=##\s*Instruction|##\s*Setup|$)',
        raw_response,
        re.DOTALL | re.IGNORECASE
    )
    if analysis_match:
        sections["analysis"] = analysis_match.group(1).strip()
        sections["before_analysis"] = raw_response[:analysis_match.start()].strip()
    
    # Ищем Instruction
    instruction_match = re.search(
        r'##\s*Instruction[^\n]*\n(.*?)(?=##[^#]|$)',
        raw_response,
        re.DOTALL | re.IGNORECASE
    )
    if instruction_match:
        sections["instruction"] = instruction_match.group(1).strip()
    
    return sections


# ============================================================================
# СОХРАНЕНИЕ ОТЧЁТА
# ============================================================================

def save_diagnostic_report(
    project_dir: str,
    query_id: str,
    user_query: str,
    model_used: str,
    raw_response: str,
    parsed_analysis: str,
    parsed_instruction: str,
    raw_analysis: Dict[str, Any],
    sections: Dict[str, str],
    tool_calls: List[Any],
    duration: float,
) -> Path:
    """Сохраняет детальный диагностический отчёт"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(project_dir) / ".ai-agent" / "diagnostic_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = report_dir / f"orchestrator_diag_{query_id}_{timestamp}.md"
    
    lines = []
    
    # === ЗАГОЛОВОК ===
    lines.append("# 🔬 Диагностический отчёт Orchestrator")
    lines.append("")
    lines.append(f"**Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(f"**Проект:** `{project_dir}`")
    lines.append(f"**Query ID:** `{query_id}`")
    lines.append(f"**Модель:** `{model_used}`")
    lines.append(f"**Время выполнения:** {duration:.2f} сек.")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === ЗАПРОС ===
    lines.append("## 📝 Запрос пользователя")
    lines.append("")
    lines.append("```")
    lines.append(user_query)
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === МЕТРИКИ АНАЛИЗА ===
    lines.append("## 📊 Метрики сырого ответа")
    lines.append("")
    lines.append("| Метрика | Значение |")
    lines.append("|---------|----------|")
    lines.append(f"| Общая длина | {raw_analysis['total_length']} символов |")
    lines.append(f"| Количество строк | {raw_analysis['line_count']} |")
    lines.append(f"| Есть ## Analysis | {'✅' if raw_analysis['has_analysis_header'] else '❌'} |")
    lines.append(f"| Есть ## Instruction | {'✅' if raw_analysis['has_instruction_header'] else '❌'} |")
    lines.append(f"| Есть **SCOPE:** | {'✅' if raw_analysis['has_scope'] else '❌'} |")
    lines.append(f"| Есть **Task:** | {'✅' if raw_analysis['has_task'] else '❌'} |")
    lines.append(f"| Есть ### FILE: | {'✅' if raw_analysis['has_file_block'] else '❌'} |")
    lines.append(f"| Есть **File:** (alt) | {'✅' if raw_analysis['has_file_alt'] else '❌'} |")
    lines.append(f"| Есть #### ACTION | {'✅' if raw_analysis['has_action_block'] else '❌'} |")
    lines.append(f"| Есть **Changes:** | {'✅' if raw_analysis['has_changes'] else '❌'} |")
    lines.append("")
    
    # === ПРОБЛЕМЫ ===
    if raw_analysis["potential_issues"] or raw_analysis["truncation_markers"]:
        lines.append("## ⚠️ Обнаруженные проблемы")
        lines.append("")
        for issue in raw_analysis["potential_issues"]:
            lines.append(f"- 🔴 {issue}")
        for marker in raw_analysis["truncation_markers"]:
            lines.append(f"- 🟡 Возможное обрезание: {marker}")
        lines.append("")
        lines.append("---")
        lines.append("")
    
    # === СРАВНЕНИЕ ДЛИН ===
    lines.append("## 📏 Сравнение длин секций")
    lines.append("")
    lines.append("| Секция | Длина (сырой) | Длина (парсер) | Разница |")
    lines.append("|--------|---------------|----------------|---------|")
    
    raw_analysis_len = len(sections.get("analysis", ""))
    raw_instruction_len = len(sections.get("instruction", ""))
    parsed_analysis_len = len(parsed_analysis)
    parsed_instruction_len = len(parsed_instruction)
    
    lines.append(f"| Analysis | {raw_analysis_len} | {parsed_analysis_len} | {raw_analysis_len - parsed_analysis_len} |")
    lines.append(f"| Instruction | {raw_instruction_len} | {parsed_instruction_len} | {raw_instruction_len - parsed_instruction_len} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === ВЫЗОВЫ ИНСТРУМЕНТОВ ===
    if tool_calls:
        lines.append("## 🛠️ Вызовы инструментов")
        lines.append("")
        for i, tc in enumerate(tool_calls, 1):
            status = "✅" if getattr(tc, 'success', True) else "❌"
            name = getattr(tc, 'name', 'unknown')
            args = getattr(tc, 'arguments', {})
            args_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in list(args.items())[:3])
            lines.append(f"{i}. {status} **{name}**(`{args_str}`)")
        lines.append("")
        lines.append("---")
        lines.append("")
    
    # === СЫРОЙ ОТВЕТ (ПОЛНЫЙ) ===
    lines.append("## 📄 Сырой ответ модели (raw_response)")
    lines.append("")
    lines.append("```markdown")
    lines.append(raw_response)
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === РАСПАРСЕННЫЙ ANALYSIS ===
    lines.append("## 🔍 Распарсенный Analysis")
    lines.append("")
    lines.append(f"**Длина:** {parsed_analysis_len} символов")
    lines.append("")
    lines.append("```markdown")
    lines.append(parsed_analysis if parsed_analysis else "[ПУСТО]")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === РАСПАРСЕННАЯ INSTRUCTION ===
    lines.append("## 📋 Распарсенная Instruction")
    lines.append("")
    lines.append(f"**Длина:** {parsed_instruction_len} символов")
    lines.append("")
    lines.append("```markdown")
    lines.append(parsed_instruction if parsed_instruction else "[ПУСТО]")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # === ИЗВЛЕЧЁННАЯ INSTRUCTION ИЗ СЫРОГО ===
    lines.append("## 🔧 Instruction извлечённая из сырого ответа (для сравнения)")
    lines.append("")
    lines.append(f"**Длина:** {raw_instruction_len} символов")
    lines.append("")
    lines.append("```markdown")
    lines.append(sections.get("instruction", "[НЕ НАЙДЕНО]"))
    lines.append("```")
    lines.append("")
    
    # Сохранение
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    
    return report_path


# ============================================================================
# ОСНОВНОЙ ТЕСТ
# ============================================================================

async def run_orchestrator_diagnostic(
    project_dir: str,
    query_info: Dict[str, str],
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Запускает оркестратор и собирает диагностику.
    """
    from config.settings import cfg
    from app.agents.pre_filter import pre_filter_chunks
    from app.agents.orchestrator import orchestrate
    from app.services.index_manager import load_semantic_index
    from app.services.project_map_builder import get_project_map_for_prompt
    from app.builders.semantic_index_builder import create_chunks_list_auto
    
    query_id = query_info["id"]
    user_query = query_info["query"]
    
    print_section(f"Тест: {query_info['description']}")
    print_info(f"Query ID: {query_id}")
    print(f"{Colors.DIM}Запрос: {user_query[:100]}...{Colors.RESET}")
    
    result = {
        "query_id": query_id,
        "success": False,
        "error": None,
    }
    
    start_time = time.time()
    
    try:
        # Загрузка данных
        print_info("Загрузка индекса...")
        index = load_semantic_index(project_dir)
        if index is None:
            raise ValueError("Индекс не найден")
        
        project_map = get_project_map_for_prompt(project_dir)
        compact_index = create_chunks_list_auto(index)
        
        # Pre-filter
        print_info("Запуск Pre-filter...")
        prefilter_result = await pre_filter_chunks(
            user_query=user_query,
            index=index,
            project_dir=project_dir,
        )
        print_success(f"Выбрано {len(prefilter_result.selected_chunks)} чанков")
        
        # Выбор модели
        if model_override:
            orchestrator_model = model_override
        else:
            from app.agents.router import route_request
            router_result = await route_request(user_query)
            orchestrator_model = router_result.orchestrator_model
        
        print_info(f"Модель: {cfg.get_model_display_name(orchestrator_model)}")
        
        # Orchestrate
        print_info("Запуск Orchestrator...")
        orchestrator_result = await orchestrate(
            user_query=user_query,
            selected_chunks=prefilter_result.selected_chunks,
            compact_index=compact_index,
            history=[],
            orchestrator_model=orchestrator_model,
            project_dir=project_dir,
            index=index,
            project_map=project_map,
        )
        
        duration = time.time() - start_time
        
        # Анализ сырого ответа
        raw_response = orchestrator_result.raw_response
        raw_analysis = analyze_raw_response(raw_response)
        sections = extract_sections(raw_response)
        
        # Вывод метрик
        print_section("Метрики")
        print_metric("Длина raw_response", raw_analysis["total_length"], threshold=500)
        print_metric("Длина parsed analysis", len(orchestrator_result.analysis), threshold=100)
        print_metric("Длина parsed instruction", len(orchestrator_result.instruction), threshold=50)
        print_metric("Есть ## Instruction", "Да" if raw_analysis["has_instruction_header"] else "Нет")
        print_metric("Есть ### FILE:", "Да" if raw_analysis["has_file_block"] else "Нет")
        print_metric("Tool calls", len(orchestrator_result.tool_calls))
        
        # Проблемы
        if raw_analysis["potential_issues"]:
            print_section("⚠️ Проблемы")
            for issue in raw_analysis["potential_issues"]:
                print_error(issue)
        
        if raw_analysis["truncation_markers"]:
            print_section("🔴 Возможное обрезание")
            for marker in raw_analysis["truncation_markers"]:
                print_warning(marker)
        
        # Сохранение отчёта
        report_path = save_diagnostic_report(
            project_dir=project_dir,
            query_id=query_id,
            user_query=user_query,
            model_used=cfg.get_model_display_name(orchestrator_model),
            raw_response=raw_response,
            parsed_analysis=orchestrator_result.analysis,
            parsed_instruction=orchestrator_result.instruction,
            raw_analysis=raw_analysis,
            sections=sections,
            tool_calls=orchestrator_result.tool_calls,
            duration=duration,
        )
        
        print_success(f"Отчёт сохранён: {report_path}")
        
        result["success"] = True
        result["report_path"] = str(report_path)
        result["metrics"] = raw_analysis
        result["duration"] = duration
        
    except Exception as e:
        result["error"] = str(e)
        print_error(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    return result


# ============================================================================
# ИНТЕРАКТИВНОЕ МЕНЮ
# ============================================================================

def select_directory() -> Optional[str]:
    """Выбор директории проекта"""
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    
    suggestions = [
        str(PROJECT_ROOT),
        str(Path.cwd()),
    ]
    
    print(f"\n{Colors.CYAN}Выберите директорию проекта:{Colors.RESET}")
    for i, path in enumerate(suggestions, 1):
        exists = "✓" if Path(path).exists() else "✗"
        has_index = "📑" if (Path(path) / ".ai-agent" / "semantic_index.json").exists() else "  "
        print(f"  {i}. [{exists}] {has_index} {path}")
    
    print(f"\n{Colors.DIM}Введите номер или полный путь:{Colors.RESET}")
    choice = input("> ").strip()
    
    if choice.isdigit() and 1 <= int(choice) <= len(suggestions):
        return suggestions[int(choice) - 1]
    elif choice:
        return choice
    return suggestions[0]


def select_model() -> Optional[str]:
    """Выбор модели"""
    from config.settings import cfg
    
    print(f"\n{Colors.CYAN}Выберите модель Orchestrator:{Colors.RESET}")
    print("  0. Автоматический Router")
    
    models = cfg.get_available_orchestrator_models()
    for i, model in enumerate(models, 1):
        print(f"  {i}. {cfg.get_model_display_name(model)}")
    
    choice = input(f"\n{Colors.DIM}Ваш выбор [0]:{Colors.RESET} ").strip()
    
    if choice == "" or choice == "0":
        return None
    
    if choice.isdigit() and 1 <= int(choice) <= len(models):
        return models[int(choice) - 1]
    
    return None


def select_queries() -> List[Dict[str, str]]:
    """Выбор тестовых запросов"""
    print(f"\n{Colors.CYAN}Выберите тестовые запросы:{Colors.RESET}")
    print("  0. Все запросы")
    
    for i, q in enumerate(TEST_QUERIES, 1):
        print(f"  {i}. [{q['id']}] {q['description']}")
    
    choice = input(f"\n{Colors.DIM}Ваш выбор (через запятую) [0]:{Colors.RESET} ").strip()
    
    if choice == "" or choice == "0":
        return TEST_QUERIES
    
    selected = []
    for idx in choice.split(","):
        idx = idx.strip()
        if idx.isdigit() and 1 <= int(idx) <= len(TEST_QUERIES):
            selected.append(TEST_QUERIES[int(idx) - 1])
    
    return selected if selected else TEST_QUERIES


# ============================================================================
# MAIN
# ============================================================================

async def main():
    print_header("🔬 ДИАГНОСТИКА ORCHESTRATOR", "═")
    print("""
Этот скрипт анализирует сырой вывод Orchestrator для выявления проблем
с генерацией инструкций.

Что проверяется:
• Полнота сырого ответа (raw_response)
• Корректность парсинга секций
• Наличие всех обязательных элементов
• Признаки обрезания ответа
""")
    
    # Выбор параметров
    project_dir = select_directory()
    if not project_dir or not Path(project_dir).exists():
        print_error("Директория не найдена")
        return
    
    model = select_model()
    queries = select_queries()
    
    print_header("ЗАПУСК ДИАГНОСТИКИ")
    print_info(f"Проект: {project_dir}")
    print_info(f"Модель: {model or 'Router (авто)'}")
    print_info(f"Запросов: {len(queries)}")
    
    # Запуск тестов
    results = []
    for query_info in queries:
        result = await run_orchestrator_diagnostic(
            project_dir=project_dir,
            query_info=query_info,
            model_override=model,
        )
        results.append(result)
        print()
    
    # Итоговая сводка
    print_header("ИТОГОВАЯ СВОДКА", "═")
    
    success_count = sum(1 for r in results if r["success"])
    print(f"\nУспешно: {success_count}/{len(results)}")
    
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"  {status} {r['query_id']}")
        if r.get("report_path"):
            print(f"      📄 {r['report_path']}")
        if r.get("error"):
            print(f"      ❌ {r['error']}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Прервано{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}Ошибка: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()