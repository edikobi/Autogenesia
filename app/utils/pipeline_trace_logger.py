# app/utils/pipeline_trace_logger.py
"""
Pipeline Trace Logger - записывает каждый запрос в реальном времени.
При ошибке файл сохраняется для анализа.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# Директория для логов
TRACE_LOG_DIR = Path("logs/pipeline_traces")
TRACE_LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ToolCallTrace:
    """Краткая информация о вызове инструмента"""
    name: str
    target: str  # file_path, query, chunk_name — БЕЗ содержимого
    success: bool
    timestamp: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class IterationTrace:
    """Трейс одной итерации цикла"""
    iteration_number: int
    timestamp: str = ""
    
    # Инструменты (кратко)
    tool_calls: List[ToolCallTrace] = field(default_factory=list)
    
    # Инструкция от Оркестратора (подробно)
    orchestrator_instruction: str = ""
    
    # Код от Генератора (подробно)
    generated_code: List[Dict[str, str]] = field(default_factory=list)
    
    # Техническая валидация (кратко)
    tech_validation_success: bool = False
    tech_validation_errors: int = 0
    tech_validation_summary: str = ""
    
    # NEW: Runtime тестирование
    runtime_files_checked: int = 0
    runtime_files_passed: int = 0
    runtime_files_failed: int = 0
    runtime_files_skipped: int = 0
    runtime_summary: str = ""  # Тип остаётся str, но теперь это JSON-строка
    
    
    # AI Validator (подробно)
    ai_validator_approved: bool = False
    ai_validator_confidence: float = 0.0
    ai_validator_verdict: str = ""
    ai_validator_issues: List[str] = field(default_factory=list)
    
    # Решение Оркестратора по AI Validator
    orchestrator_decision: str = ""
    orchestrator_reasoning: str = ""
    
    # Тесты (подробно)
    tests_run: bool = False
    tests_passed: bool = False
    tests_output: str = ""
    failed_tests: List[str] = field(default_factory=list)
    
    # Staging errors
    staging_errors: List[Dict[str, Any]] = field(default_factory=list)
    
    # NEW: Автоформатирование
    auto_format_ran: bool = False
    auto_format_success: bool = False
    auto_format_message: str = ""
    auto_format_fixes: List[str] = field(default_factory=list)
    auto_format_tools: Dict[str, bool] = field(default_factory=dict)
    
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass 
class PipelineTrace:
    """Полный трейс одного запроса к pipeline"""
    request_id: str
    user_request: str
    project_dir: str
    model: str
    started_at: str = ""
    
    iterations: List[IterationTrace] = field(default_factory=list)
    
    # Финальный результат
    success: bool = False
    final_status: str = ""
    total_duration_ms: float = 0
    error_message: str = ""
    
    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now().isoformat()
        if not self.request_id:
            self.request_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")


class PipelineTraceLogger:
    """
    Логгер для записи pipeline trace в реальном времени.
    
    Использование:
        trace = PipelineTraceLogger(user_request, project_dir, model)
        
        # В начале итерации
        trace.start_iteration(1)
        
        # Добавление данных
        trace.add_tool_call("read_file", "app/auth.py", success=True)
        trace.set_instruction("Add login method...")
        trace.add_generated_code("app/auth.py", "INSERT_METHOD", code)
        trace.set_tech_validation(success=False, errors=2, summary="Import error")
        trace.set_ai_validation(approved=False, confidence=0.3, verdict="...")
        trace.set_tests(passed=False, output="...", failed=["test_login"])
        
        # При ошибке - сохраняется автоматически
        trace.set_error("Pipeline crashed: ...")
        
        # При успехе
        trace.complete(success=True, status="completed")
    """
    
    def __init__(
        self,
        user_request: str,
        project_dir: str,
        model: str,
    ):
        self.trace = PipelineTrace(
            request_id="",
            user_request=user_request[:500],  # Ограничиваем размер
            project_dir=project_dir,
            model=model,
        )
        self._current_iteration: Optional[IterationTrace] = None
        self._file_path: Optional[Path] = None
        
        # Создаём файл сразу
        self._init_file()
    
    def _init_file(self):
        """Создаёт файл трейса"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._file_path = TRACE_LOG_DIR / f"trace_{timestamp}_{self.trace.request_id[:8]}.json"
        self._save()
    
    def _save(self):
        """Сохраняет текущее состояние в файл"""
        if not self._file_path:
            return
        
        try:
            data = asdict(self.trace)
            with open(self._file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save pipeline trace: {e}")
    
    # === Управление итерациями ===
    
    def start_iteration(self, iteration_number: int):
        """Начинает новую итерацию"""
        self._current_iteration = IterationTrace(iteration_number=iteration_number)
        self.trace.iterations.append(self._current_iteration)
        self._save()
    
    # === Инструменты (кратко) ===
    
    def add_tool_call(self, name: str, target: str, success: bool):
        """
        Добавляет вызов инструмента.
        
        Args:
            name: Название инструмента (read_file, search_code, etc.)
            target: Краткое описание цели (путь файла, запрос поиска) — БЕЗ содержимого
            success: Успешность вызова
        """
        if not self._current_iteration:
            self.start_iteration(1)
        
        self._current_iteration.tool_calls.append(ToolCallTrace(
            name=name,
            target=target[:200],  # Ограничиваем
            success=success,
        ))
        self._save()
    
    # === Инструкция (подробно) ===
    
    def set_instruction(self, instruction: str):
        """Устанавливает инструкцию от Оркестратора"""
        if not self._current_iteration:
            self.start_iteration(1)
        
        self._current_iteration.orchestrator_instruction = instruction
        self._save()
    
    # === Код (подробно) ===
    
    def add_generated_code(self, file_path: str, mode: str, code: str):
        """Добавляет сгенерированный код"""
        if not self._current_iteration:
            self.start_iteration(1)
        
        self._current_iteration.generated_code.append({
            "file": file_path,
            "mode": mode,
            "code": code,
        })
        self._save()
    
    
    def add_staging_error(self, file_path: str, mode: str, error: str, error_type: Optional[str] = None, target_class: Optional[str] = None, target_method: Optional[str] = None, target_function: Optional[str] = None, code_preview: Optional[str] = None) -> None:
        """
        Log a staging error (file modification failure).
        
        Args:
            file_path: Path to the file that failed to stage
            mode: Operation mode (REPLACE_METHOD, ADD_FUNCTION, etc.)
            error: Error message
            error_type: Type of error (optional)
            target_class: Target class name (optional)
            target_method: Target method name (optional)
            target_function: Target function name (optional)
            code_preview: Preview of the code that failed (optional)
        """
        if not self._current_iteration:
            self.start_iteration(1)
        
        error_dict = {
            "file_path": file_path,
            "mode": mode,
            "error": error,
        }
        
        if error_type is not None:
            error_dict["error_type"] = error_type
        if target_class is not None:
            error_dict["target_class"] = target_class
        if target_method is not None:
            error_dict["target_method"] = target_method
        if target_function is not None:
            error_dict["target_function"] = target_function
        if code_preview is not None:
            error_dict["code_preview"] = code_preview
        
        self._current_iteration.staging_errors.append(error_dict)
    
    
    def dump_staging_error_report(self, error_data: Dict[str, Any]) -> None:
        """Saves a detailed staging error report (with full code) to a separate JSON file."""
        if not self._file_path:
            return
        
        try:
            import json
            from datetime import datetime
            from pathlib import Path
            
            # Generate timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            
            # Generate filename
            filename = f"staging_error_{timestamp}.json"
            
            # Construct path
            TRACE_LOG_DIR = Path(self._file_path).parent
            file_path = TRACE_LOG_DIR / filename
            
            # Create report dict
            report = {
                "timestamp": datetime.now().isoformat(),
                "request_id": self.trace.request_id,
                "project_dir": self.trace.project_dir,
                "error_data": error_data,
            }
            
            # Write to file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            # Log info
            logger.info(f"Staging error report dumped: {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to dump staging error report: {e}")
    
    
    # === Техническая валидация (кратко) ===
    
    def set_tech_validation(
        self,
        success: bool,
        errors: int = 0,
        summary: str = "",
    ):
        """Устанавливает результат технической валидации"""
        if not self._current_iteration:
            return
        
        self._current_iteration.tech_validation_success = success
        self._current_iteration.tech_validation_errors = errors
        self._current_iteration.tech_validation_summary = summary[:500]
        self._save()
    
    def set_auto_format_status(
        self,
        ran: bool,
        success: bool,
        message: str,
        fixes: Optional[List[str]] = None,
        tools: Optional[Dict[str, bool]] = None,
    ) -> None:
        """
        Log auto-formatting status to current iteration trace.
        
        Args:
            ran: Whether auto-formatting was attempted
            success: Whether auto-formatting succeeded
            message: Status message
            fixes: List of fixes applied
            tools: Dict of tool availability status
        """
        if not self._current_iteration:
            return
        
        self._current_iteration.auto_format_ran = ran
        self._current_iteration.auto_format_success = success
        self._current_iteration.auto_format_message = message
        self._current_iteration.auto_format_fixes = fixes or []
        self._current_iteration.auto_format_tools = tools or {}
        
        self._save()
    
    
    # === AI Validator (подробно) ===
    
    def set_ai_validation(
        self,
        approved: bool,
        confidence: float,
        verdict: str,
        issues: Optional[List[str]] = None,
    ):
        """Устанавливает результат AI Validator"""
        if not self._current_iteration:
            return
        
        self._current_iteration.ai_validator_approved = approved
        self._current_iteration.ai_validator_confidence = confidence
        self._current_iteration.ai_validator_verdict = verdict
        self._current_iteration.ai_validator_issues = issues or []
        self._save()
    
    # === Решение Оркестратора ===
    
    def set_orchestrator_decision(self, decision: str, reasoning: str):
        """Устанавливает решение Оркестратора по AI Validator"""
        if not self._current_iteration:
            return
        
        self._current_iteration.orchestrator_decision = decision
        self._current_iteration.orchestrator_reasoning = reasoning
        self._save()
    
    # === Тесты (подробно) ===
    
    def set_tests(
        self,
        passed: bool,
        output: str = "",
        failed: Optional[List[str]] = None,
    ):
        """Устанавливает результаты тестов"""
        if not self._current_iteration:
            return
        
        self._current_iteration.tests_run = True
        self._current_iteration.tests_passed = passed
        self._current_iteration.tests_output = output[:2000]  # Ограничиваем
        self._current_iteration.failed_tests = failed or []
        self._save()
    
    # === Завершение ===
    
    def set_error(self, error_message: str):
        """Устанавливает ошибку и сохраняет файл"""
        self.trace.success = False
        self.trace.error_message = error_message
        self.trace.final_status = "error"
        self._save()
        
        logger.info(f"Pipeline trace saved (ERROR): {self._file_path}")
    
    def complete(
        self,
        success: bool,
        status: str,
        duration_ms: float = 0,
    ):
        """Завершает трейс"""
        self.trace.success = success
        self.trace.final_status = status
        self.trace.total_duration_ms = duration_ms
        self._save()
        
        # При ошибке оставляем файл, при успехе — опционально удаляем старые
        if success:
            self._cleanup_old_traces()
        
        logger.info(f"Pipeline trace saved ({'OK' if success else 'FAIL'}): {self._file_path}")
    
    def _cleanup_old_traces(self, keep_count: int = 50):
        """Удаляет старые успешные трейсы, оставляя последние N"""
        try:
            traces = sorted(TRACE_LOG_DIR.glob("trace_*.json"), reverse=True)
            for old_trace in traces[keep_count:]:
                # Проверяем что это успешный трейс
                try:
                    with open(old_trace, 'r') as f:
                        data = json.load(f)
                    if data.get("success"):
                        old_trace.unlink()
                except:
                    pass
        except Exception as e:
            logger.warning(f"Failed to cleanup old traces: {e}")
    
    @property
    def file_path(self) -> Optional[Path]:
        """Путь к файлу трейса"""
        return self._file_path
    
    
    def set_runtime_results(
        self,
        files_checked: int,
        files_passed: int,
        files_failed: int,
        files_skipped: int = 0,
        summary: Optional[Dict[str, Any]] = None,
    ):
        """
        Устанавливает результаты runtime тестирования.
        
        Args:
            files_checked: Количество проверенных файлов
            files_passed: Количество успешных
            files_failed: Количество неуспешных
            files_skipped: Количество пропущенных
            summary: Полный словарь RuntimeTestSummary.to_dict() (опционально)
        """
        if not self._current_iteration:
            return
        
        self._current_iteration.runtime_files_checked = files_checked
        self._current_iteration.runtime_files_passed = files_passed
        self._current_iteration.runtime_files_failed = files_failed
        self._current_iteration.runtime_files_skipped = files_skipped
        
        # === ИСПРАВЛЕНИЕ: Сохраняем структурированно, не str(dict) ===
        if summary:
            try:
                # Извлекаем только важную информацию для трейса
                failures_info = []
                for r in summary.get("results", []):
                    if r.get("status") in ("failed", "error", "timeout"):
                        failure = {
                            "file": r.get("file_path", "unknown"),
                            "status": r.get("status"),
                            "message": (r.get("message") or "")[:200],
                        }
                        # Добавляем детали только для ошибок (ограничиваем размер)
                        if r.get("details"):
                            # Берём последние строки traceback
                            details_lines = r["details"].strip().split('\n')
                            failure["traceback_tail"] = '\n'.join(details_lines[-8:])[:500]
                        failures_info.append(failure)
                
                structured_summary = {
                    "success": summary.get("success", False),
                    "total": files_checked,
                    "passed": files_passed,
                    "failed": files_failed,
                    "skipped": files_skipped,
                    "failures": failures_info[:5],  # Максимум 5 ошибок в трейсе
                }
                
                self._current_iteration.runtime_summary = json.dumps(
                    structured_summary, 
                    ensure_ascii=False,
                    indent=None,  # Компактный формат
                )
            except Exception as e:
                # Fallback на простое описание
                self._current_iteration.runtime_summary = f"Error formatting summary: {e}"
        
        self._save()
        
        
        