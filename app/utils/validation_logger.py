# app/utils/validation_logger.py
"""
Специализированный логгер для отслеживания процесса валидации и генерации кода.
Записывает детальную информацию в отдельные файлы для диагностики.
"""

import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

# Директория для логов валидации
VALIDATION_LOG_DIR = Path("logs/validation")
VALIDATION_LOG_DIR.mkdir(parents=True, exist_ok=True)


class ValidationLogger:
    """
    Логгер для детального отслеживания этапов pipeline.
    Создаёт отдельные файлы для каждой сессии.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.start_time = datetime.now()
        
        # Основной файл сессии
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        self.session_file = VALIDATION_LOG_DIR / f"session_{timestamp}_{session_id[:8]}.log"
        
        # Файл для ошибок сессии
        self.error_file = VALIDATION_LOG_DIR / f"errors_{timestamp}_{session_id[:8]}.log"
        
        # Настраиваем логгер
        self.logger = logging.getLogger(f"validation.{session_id[:8]}")
        self.logger.setLevel(logging.DEBUG)
        
        # Убираем старые хендлеры если есть
        self.logger.handlers.clear()
        
        # Хендлер для файла сессии — ИСПРАВЛЕН ФОРМАТ ДАТЫ
        file_handler = logging.FileHandler(self.session_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S'  # Убрали .%f — он не поддерживается strftime
        ))
        self.logger.addHandler(file_handler)
        
        # Хендлер для ошибок — ИСПРАВЛЕН ФОРМАТ
        error_handler = logging.FileHandler(self.error_file, encoding='utf-8')
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S'
        ))
        self.logger.addHandler(error_handler)
        
        # Отключаем propagation чтобы не дублировать в root logger
        self.logger.propagate = False
        
        self.logger.info(f"{'='*60}")
        self.logger.info(f"VALIDATION SESSION STARTED: {session_id}")
        self.logger.info(f"{'='*60}")    
    
    def log_stage(self, stage: str, message: str, data: Optional[Dict] = None):
        """Логирует этап pipeline"""
        self.logger.info(f"\n[STAGE: {stage}] {message}")
        if data:
            try:
                self.logger.debug(f"  Data: {json.dumps(data, indent=2, default=str, ensure_ascii=False)[:2000]}")
            except Exception:
                self.logger.debug(f"  Data: {str(data)[:2000]}")
    
    def log_orchestrator(self, user_request: str, model: str, result: Any):
        """Логирует результат оркестратора"""
        self.logger.info(f"\n{'='*40}")
        self.logger.info("[ORCHESTRATOR]")
        self.logger.info(f"  Model: {model}")
        self.logger.info(f"  User request: {user_request[:200]}...")
        if hasattr(result, 'analysis'):
            self.logger.info(f"  Analysis length: {len(result.analysis)} chars")
        if hasattr(result, 'instruction'):
            self.logger.info(f"  Instruction length: {len(result.instruction)} chars")
            self.logger.debug(f"  Instruction preview: {result.instruction[:500]}...")
        if hasattr(result, 'tool_calls'):
            self.logger.info(f"  Tool calls: {len(result.tool_calls)}")
            for tc in result.tool_calls:
                self.logger.debug(f"    - {tc.name}: {tc.arguments}")
    
    def log_code_generation(self, instruction: str, blocks: List[Any]):
        """Логирует результат генерации кода"""
        self.logger.info(f"\n{'='*40}")
        self.logger.info("[CODE GENERATION]")
        self.logger.info(f"  Instruction length: {len(instruction)} chars")
        self.logger.info(f"  Generated blocks: {len(blocks)}")
        for i, block in enumerate(blocks):
            self.logger.info(f"  Block {i+1}:")
            self.logger.info(f"    File: {block.file_path}")
            self.logger.info(f"    Mode: {block.mode}")
            self.logger.info(f"    Code length: {len(block.code)} chars")
            self.logger.debug(f"    Code preview:\n{block.code[:500]}...")
    
    def log_validation(self, stage: str, result: Any):
        """Логирует результат валидации"""
        self.logger.info(f"\n{'='*40}")
        self.logger.info(f"[VALIDATION: {stage}]")
        
        if hasattr(result, 'success'):
            self.logger.info(f"  Success: {result.success}")
        if hasattr(result, 'error_count'):
            self.logger.info(f"  Errors: {result.error_count}")
        if hasattr(result, 'warning_count'):
            self.logger.info(f"  Warnings: {result.warning_count}")
        if hasattr(result, 'levels_passed'):
            self.logger.info(f"  Levels passed: {[l.value for l in result.levels_passed]}")
        if hasattr(result, 'levels_failed'):
            self.logger.info(f"  Levels failed: {[l.value for l in result.levels_failed]}")
        if hasattr(result, 'issues'):
            self.logger.info(f"  Issues ({len(result.issues)}):")
            for issue in result.issues[:10]:
                self.logger.info(f"    - [{issue.level.value}] {issue.file_path}: {issue.message[:100]}")
            if len(result.issues) > 10:
                self.logger.info(f"    ... and {len(result.issues) - 10} more")
    
    def log_ai_validation(self, result: Any):
        """Логирует результат AI валидации"""
        self.logger.info(f"\n{'='*40}")
        self.logger.info("[AI VALIDATION]")
        
        if hasattr(result, 'approved'):
            self.logger.info(f"  Approved: {result.approved}")
        if hasattr(result, 'confidence'):
            self.logger.info(f"  Confidence: {result.confidence:.2%}")
        if hasattr(result, 'verdict'):
            self.logger.info(f"  Verdict: {result.verdict[:200]}...")
        if hasattr(result, 'critical_issues'):
            self.logger.info(f"  Critical issues: {result.critical_issues}")
    
    def log_subprocess(self, cmd: List[str], returncode: int, stdout: str, stderr: str):
        """Логирует вызов subprocess"""
        self.logger.debug(f"\n[SUBPROCESS]")
        self.logger.debug(f"  Command: {' '.join(cmd[:5])}...")
        self.logger.debug(f"  Return code: {returncode}")
        if stdout:
            self.logger.debug(f"  Stdout ({len(stdout)} chars): {stdout[:500]}")
        if stderr:
            self.logger.debug(f"  Stderr ({len(stderr)} chars): {stderr[:500]}")
    
    def log_error(self, stage: str, error: Exception, context: Optional[Dict] = None):
        """Логирует ошибку с полным traceback"""
        self.logger.error(f"\n{'!'*60}")
        self.logger.error(f"[ERROR in {stage}]")
        self.logger.error(f"  Type: {type(error).__name__}")
        self.logger.error(f"  Message: {str(error)}")
        if context:
            self.logger.error(f"  Context: {context}")
        self.logger.error(f"  Traceback:\n{traceback.format_exc()}")
        self.logger.error(f"{'!'*60}")
        
        # Также пишем в отдельный файл ошибок
        with open(self.error_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"STAGE: {stage}\n")
            f.write(f"TIME: {datetime.now().isoformat()}\n")
            f.write(f"ERROR: {type(error).__name__}: {error}\n")
            if context:
                f.write(f"CONTEXT: {context}\n")
            f.write(f"TRACEBACK:\n{traceback.format_exc()}\n")
    
    def log_complete(self, success: bool, duration_ms: float):
        """Логирует завершение сессии"""
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"SESSION COMPLETED")
        self.logger.info(f"  Success: {success}")
        self.logger.info(f"  Duration: {duration_ms:.0f}ms")
        self.logger.info(f"  Log file: {self.session_file}")
        self.logger.info(f"{'='*60}")


# Глобальный экземпляр для текущей сессии
_current_logger: Optional[ValidationLogger] = None


def get_validation_logger(session_id: str) -> ValidationLogger:
    """Получает или создаёт логгер для сессии"""
    global _current_logger
    if _current_logger is None or _current_logger.session_id != session_id:
        _current_logger = ValidationLogger(session_id)
    return _current_logger


def log_validation_error(stage: str, error: Exception, context: Optional[Dict] = None):
    """Быстрый метод для логирования ошибок"""
    if _current_logger:
        _current_logger.log_error(stage, error, context)
    else:
        # Fallback если логгер не инициализирован
        logging.error(f"Validation error in {stage}: {error}", exc_info=True)