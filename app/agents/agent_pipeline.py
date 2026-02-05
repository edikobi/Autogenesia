# app/agents/agent_pipeline.py
"""
Agent Pipeline - Central coordinator for Agent Mode.

Orchestrates the complete flow:
1. Orchestrator analyzes request and generates instruction
2. Code Generator creates code based on instruction
3. Change Validator validates syntax, imports, types, integration
4. AI Validator checks if code addresses the request
5. User confirms changes
6. Changes are applied via VFS + BackupManager

Key principles:
- NO changes to real files until user confirms
- All validation happens on VirtualFileSystem
- Feedback loop for rejected code
- Full history and audit trail
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, TYPE_CHECKING

from config.settings import cfg

# Core services
from app.services.virtual_fs import VirtualFileSystem, ChangeType, CommitResult
from app.services.backup_manager import BackupManager
from app.services.change_validator import ChangeValidator, ValidationResult, ValidationLevel

# Agents
from app.agents.orchestrator import orchestrate_agent, OrchestratorResult
from app.agents.code_generator import generate_code_agent_mode, CodeGeneratorResult
from app.agents.validator import AIValidator, AIValidationResult, AIValidationRequest
from app.agents.pre_filter import pre_filter_chunks, PreFilterResult

# Feedback system
from app.agents.feedback_handler import (
    FeedbackHandler,
    FeedbackSource,
    ValidatorFeedback,
    TestRunFeedback,
)
from app.agents.feedback_loop import (
    FeedbackLoopState,
    FeedbackAction,
    create_feedback_loop,
)

from app.services.file_modifier import FileModifier, ParsedCodeBlock, ModifyResult

# History
from app.history.manager import HistoryManager


from app.tools.dependency_manager import DependencyManager
from app.agents.feedback_prompt_loader import reset_feedback_loader

# Utils
# (больше не использую) from app.utils.compact_index import get_compact_index_for_orchestrator
from app.utils.token_counter import TokenCounter
from app.services.project_map_builder import get_project_map_for_prompt

# Validation logging
from app.utils.validation_logger import get_validation_logger, log_validation_error

from app.services.change_validator import ChangeValidator, ValidationResult, ValidationLevel, IssueSeverity
if TYPE_CHECKING:
    from app.history.storage import Thread

logger = logging.getLogger(__name__)


def _load_compact_index_md(project_dir: str) -> str:
    """
    Загружает compact_index.md для Orchestrator.
    
    Это компактная карта проекта (~3-5k токенов), а не полный JSON индекс.
    
    Args:
        project_dir: Путь к директории проекта
        
    Returns:
        Содержимое compact_index.md или fallback-сообщение
    """
    compact_md_path = Path(project_dir) / ".ai-agent" / "compact_index.md"
    
    if compact_md_path.exists():
        try:
            content = compact_md_path.read_text(encoding="utf-8")
            logger.info(f"Loaded compact_index.md ({len(content)} chars, ~{len(content)//4} tokens)")
            return content
        except Exception as e:
            logger.warning(f"Failed to read compact_index.md: {e}")
    
    # Fallback — файл не найден
    logger.warning(f"compact_index.md not found at {compact_md_path}")
    return "[Project index not available. Please run indexing first.]"


# ============================================================================
# ENUMS & DATA STRUCTURES
# ============================================================================

class PipelineMode(Enum):
    """Pipeline execution mode"""
    AGENT = "agent"           # Full autonomous mode with file modifications
    ASK = "ask"               # Analysis only, no modifications
    NEW_PROJECT = "new_project"  # Create new project


@dataclass
class OrchestratorFeedbackDecision:
    """Result of Orchestrator's decision on validator feedback"""
    decision: str  # "ACCEPT" or "OVERRIDE"
    reasoning: str
    new_instruction: Optional[str] = None  # Only if ACCEPT
    new_analysis: Optional[str] = None

class PipelineStatus(Enum):
    """Current pipeline status"""
    IDLE = "idle"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    VALIDATING = "validating"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PendingDeletion:
    """Deletion to be applied after tests pass."""
    target_name: str
    target_type: str  # METHOD | FUNCTION | CLASS
    file_path: str
    parent_class: Optional[str] = None
    reason: str = ""


def extract_deletions_from_instruction(instruction: str) -> tuple[str, List[PendingDeletion]]:
    """
    Extract DELETE sections from Orchestrator instruction.
    
    Supports two formats:
    
    1. Element deletion (method/function/class):
       ```
       #### DELETE: `element_name`
       **Type:** METHOD | FUNCTION | CLASS
       **In class:** `ClassName` (optional, for methods)
       **File:** `path/to/file.py`
       **Reason:** Why this should be removed
       ```
    
    2. Whole file deletion:
       ```
       ### FILE: `DELETE: path/to/file.py`
       **Reason:** Why this file should be removed
       ```
    
    Args:
        instruction: Full Orchestrator instruction text
        
    Returns:
        Tuple of:
        - Clean instruction with DELETE blocks removed
        - List of PendingDeletion objects
    """
    import re
    
    deletions: List[PendingDeletion] = []
    
    # === PATTERN 1: Element deletion (method/function/class) ===
    element_delete_pattern = re.compile(
        r'####\s*DELETE:\s*`([^`]+)`\s*\n'
        r'\*\*Type:\*\*\s*(METHOD|FUNCTION|CLASS)\s*\n'
        r'(?:\*\*In\s+class:\*\*\s*`([^`]+)`\s*\n)?'
        r'\*\*File:\*\*\s*`([^`]+)`\s*\n'
        r'\*\*Reason:\*\*\s*(.+?)(?=\n####|\n###|\n##|\n---|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    
    for match in element_delete_pattern.finditer(instruction):
        deletions.append(PendingDeletion(
            target_name=match.group(1).strip(),
            target_type=match.group(2).upper(),
            parent_class=match.group(3).strip() if match.group(3) else None,
            file_path=match.group(4).strip(),
            reason=match.group(5).strip(),
        ))
    
    # === PATTERN 2: Whole file deletion ===
    # Format: ### FILE: `DELETE: path/to/file.py`
    file_delete_pattern = re.compile(
        r'###\s*FILE:\s*`DELETE:\s*([^`]+)`\s*\n'
        r'\*\*Reason:\*\*\s*(.+?)(?=\n###|\n##|\n---|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    
    for match in file_delete_pattern.finditer(instruction):
        file_path = match.group(1).strip()
        reason = match.group(2).strip()
        
        deletions.append(PendingDeletion(
            target_name=file_path,  # For files, target_name is the path
            target_type="FILE",
            parent_class=None,
            file_path=file_path,
            reason=reason,
        ))
    
    # === CLEAN INSTRUCTION ===
    # Remove all DELETE blocks from instruction
    clean_instruction = element_delete_pattern.sub('', instruction)
    clean_instruction = file_delete_pattern.sub('', clean_instruction)
    
    # Remove "## Deletions Required" section header if present
    clean_instruction = re.sub(
        r'---\s*\n##\s*Deletions?\s+Required\s*\n*',
        '',
        clean_instruction,
        flags=re.IGNORECASE
    )
    
    # Clean up excessive newlines
    clean_instruction = re.sub(r'\n{3,}', '\n\n', clean_instruction)
    
    return clean_instruction.strip(), deletions


def parse_response_type(response: str) -> tuple[str, Optional[str]]:
    """
    Parse RESPONSE_TYPE from Orchestrator response.
    
    FIXED: More robust detection - check for ANY code generation indicators.
    Reduced false positives for DIRECT_ANSWER.
    
    Returns:
        Tuple of (response_type, direct_answer_content)
        response_type: "DIRECT_ANSWER" | "CODE_INSTRUCTION"
        direct_answer_content: Content if DIRECT_ANSWER, else None
    """
    import re
    
    if not response or not response.strip():
        return "DIRECT_ANSWER", ""
    
    # === 1. Check for explicit RESPONSE_TYPE marker (highest priority) ===
    type_match = re.search(
        r'\*\*RESPONSE_TYPE:\*\*\s*(DIRECT_ANSWER|CODE_INSTRUCTION)',
        response,
        re.IGNORECASE
    )
    
    if type_match:
        response_type = type_match.group(1).upper()
        
        if response_type == "DIRECT_ANSWER":
            # Extract ## Answer section
            answer_match = re.search(
                r'##\s*(?:Answer|Ответ)\s*\n(.*?)(?=\n##|\Z)',
                response,
                re.DOTALL | re.IGNORECASE
            )
            answer_content = answer_match.group(1).strip() if answer_match else response
            return "DIRECT_ANSWER", answer_content
        else:
            return "CODE_INSTRUCTION", None
    
    # === 2. Check for instruction section headers ===
    instruction_header_patterns = [
        # English
        r'##\s*Instruction\s+for\s+Code\s+Generator',
        r'##\s*Instruction[:\s]',
        r'##\s*Code\s+Changes',
        r'##\s*Implementation\s',
        r'##\s*Changes?\s+Required',
        r'##\s*Modifications?\s',
        
        # Russian
        r'##\s*Инструкция\s+для\s+Code\s+Generator',
        r'##\s*Инструкция\s+для\s+генератора',
        r'##\s*Инструкция[:\s]',
        r'##\s*Изменения\s+кода',
        r'##\s*Необходимые\s+изменения',
    ]
    
    for pattern in instruction_header_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            return "CODE_INSTRUCTION", None
    
    # === 3. Check for CODE_BLOCK markers ===
    code_block_patterns = [
        r'###\s*CODE_BLOCK',
        r'```python\s*\n(?:#\s*)?FILE:',
        r'```python\s*\nFILE:',
    ]
    
    for pattern in code_block_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            return "CODE_INSTRUCTION", None
    
    # === 4. Check for code generation directives ===
    directive_patterns = [
        (r'FILE:\s*[`"]?[\w/\\.-]+\.py[`"]?', 'FILE'),
        (r'MODE:\s*(REPLACE_|INSERT_|APPEND_|PATCH_|CREATE_|MODIFY_)', 'MODE'),
        (r'TARGET_CLASS:\s*[`"]?\w+[`"]?', 'TARGET_CLASS'),
        (r'TARGET_METHOD:\s*[`"]?\w+[`"]?', 'TARGET_METHOD'),
        (r'TARGET_FUNCTION:\s*[`"]?\w+[`"]?', 'TARGET_FUNCTION'),
        (r'\*\*SCOPE:\*\*', 'SCOPE'),
        (r'\*\*Task:\*\*', 'Task'),
        (r'\*\*File:\*\*', 'File'),
        (r'\*\*Changes:\*\*', 'Changes'),
    ]
    
    directive_count = 0
    found_directives = []
    for pattern, name in directive_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            directive_count += 1
            found_directives.append(name)
    
    # If 2+ directives found, definitely code instruction
    if directive_count >= 2:
        logger.debug(f"parse_response_type: CODE_INSTRUCTION via directives: {found_directives}")
        return "CODE_INSTRUCTION", None
    
    # === 5. Check for substantial Python code ===
    code_blocks = re.findall(r'```python\s*\n(.+?)```', response, re.DOTALL)
    if code_blocks:
        # Count actual code lines (not comments, not empty)
        total_code_lines = 0
        for block in code_blocks:
            for line in block.strip().split('\n'):
                stripped = line.strip()
                if stripped and not stripped.startswith('#'):
                    total_code_lines += 1
        
        # More than 5 actual code lines
        if total_code_lines > 5:
            logger.debug(f"parse_response_type: CODE_INSTRUCTION via code blocks ({total_code_lines} lines)")
            return "CODE_INSTRUCTION", None
    
    # === 6. Check for action keywords in context ===
    action_patterns = [
        r'(?:add|create|modify|update|change|implement|write|insert|replace|delete|remove)\s+(?:a\s+)?(?:method|function|class|file)',
        r'(?:добавить|создать|изменить|обновить|реализовать|написать|вставить|заменить|удалить)\s+(?:метод|функцию|класс|файл)',
    ]
    
    for pattern in action_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            # Only if combined with code or file reference
            if '```python' in response or re.search(r'`[^`]+\.py`', response):
                return "CODE_INSTRUCTION", None
    
    # === 7. Check for explicit "no code needed" indicators ===
    no_code_patterns = [
        r'no\s+code\s+changes?\s+(?:are\s+)?(?:needed|required|necessary)',
        r'не\s+требуется\s+(?:никаких\s+)?изменени',
        r'код\s+менять\s+не\s+нужно',
        r'изменения\s+кода\s+не\s+требуются',
        r'this\s+is\s+(?:just\s+)?(?:a\s+)?(?:question|clarification)',
        r'это\s+(?:просто\s+)?вопрос',
        r'\*\*RESPONSE_TYPE:\*\*\s*DIRECT_ANSWER',  # Even without full match above
    ]
    
    for pattern in no_code_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            logger.debug(f"parse_response_type: DIRECT_ANSWER via explicit indicator")
            return "DIRECT_ANSWER", response
    
    # === 8. Check response structure ===
    has_analysis = bool(re.search(r'##\s*(?:Analysis|Анализ)', response, re.IGNORECASE))
    has_answer = bool(re.search(r'##\s*(?:Answer|Ответ)', response, re.IGNORECASE))
    
    # If has ## Analysis but no ## Answer, and response is long with code references
    if has_analysis and not has_answer:
        # Check for any file references
        has_file_ref = bool(re.search(r'`[^`]+\.py`', response))
        # Check for def/class in response
        has_code_def = bool(re.search(r'\b(?:def|class|async\s+def)\s+\w+', response))
        
        if has_file_ref or has_code_def:
            logger.debug("parse_response_type: CODE_INSTRUCTION via structure (analysis + file refs)")
            return "CODE_INSTRUCTION", None
    
    # === 9. Final check: Does it have ## Answer section? ===
    if has_answer:
        answer_match = re.search(
            r'##\s*(?:Answer|Ответ)\s*\n(.*?)(?=\n##|\Z)',
            response,
            re.DOTALL | re.IGNORECASE
        )
        if answer_match:
            return "DIRECT_ANSWER", answer_match.group(1).strip()
    
    # === 10. Default: Assume CODE_INSTRUCTION if response is substantial ===
    # This is a change from previous behavior - we now default to CODE_INSTRUCTION
    # if the response is long enough and doesn't explicitly say "no code needed"
    if len(response) > 500:
        logger.debug("parse_response_type: Defaulting to CODE_INSTRUCTION (long response without explicit DIRECT_ANSWER)")
        return "CODE_INSTRUCTION", None
    
    # Short response without clear indicators - treat as direct answer
    return "DIRECT_ANSWER", response



@dataclass
class PendingChange:
    """A pending change awaiting user confirmation"""
    file_path: str
    code_block: ParsedCodeBlock
    modify_result: ModifyResult
    validation_passed: bool
    ai_validation_passed: bool


@dataclass
class PipelineResult:
    """Result of pipeline execution"""
    success: bool
    status: PipelineStatus
    
    # Analysis
    analysis: str = ""
    instruction: str = ""
    
    # Generated code
    code_blocks: List[ParsedCodeBlock] = field(default_factory=list)
    
    # Validation
    validation_result: Optional[Dict[str, Any]] = None
    ai_validation_result: Optional[Dict[str, Any]] = None
    
    # NEW: Orchestrator's decision on validator feedback
    orchestrator_decision: Optional['OrchestratorFeedbackDecision'] = None
    
    # Pending changes (before user confirmation)
    pending_changes: List[PendingChange] = field(default_factory=list)
    diffs: Dict[str, str] = field(default_factory=dict)
    
    # Applied changes (after confirmation)
    applied_files: List[str] = field(default_factory=list)
    
    # Feedback loop info
    feedback_iterations: int = 0
    
    # Errors
    errors: List[str] = field(default_factory=list)
    
    # Timing
    duration_ms: float = 0.0
    
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "analysis": self.analysis,
            "instruction": self.instruction,
            "code_blocks_count": len(self.code_blocks),
            "pending_changes_count": len(self.pending_changes),
            "applied_files": self.applied_files,
            "feedback_iterations": self.feedback_iterations,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
        }



@dataclass
class ApplyResult:
    """Result of applying changes"""
    success: bool
    applied_files: List[str]
    created_files: List[str]
    errors: List[str]
    backup_session_id: Optional[str] = None


# ============================================================================
# CALLBACKS TYPE
# ============================================================================

# Callback types for UI updates
OnThinkingCallback = Callable[[str], None]
OnToolCallCallback = Callable[[str, Dict, str, bool], None]
OnValidationCallback = Callable[[Dict[str, Any]], None]
OnStatusCallback = Callable[[PipelineStatus, str], None]

OnStageCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]

OnUserDecisionCallback = Callable[[str, Dict[str, Any]], str]


def _count_skipped_reasons(runtime_summary: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """
    Count reasons for skipped runtime files.
    
    Args:
        runtime_summary: Runtime test summary dict from RuntimeTestSummary.to_dict()
        
    Returns:
        Dict mapping reason -> count
    """
    if not runtime_summary:
        return {}
    
    reasons: Dict[str, int] = {}
    for r in runtime_summary.get("results", []):
        if r.get("status") == "skipped":
            msg = r.get("message", "Unknown")
            # Simplify/categorize reason
            msg_lower = msg.lower()
            if "web app" in msg_lower or "flask" in msg_lower or "fastapi" in msg_lower or "django" in msg_lower:
                reason = "Web приложения (Flask/FastAPI/Django)"
            elif "gui" in msg_lower or "pygame" in msg_lower or "tkinter" in msg_lower or "pyqt" in msg_lower:
                reason = "GUI приложения (headless)"
            elif "insufficient time" in msg_lower or "time budget" in msg_lower:
                reason = "Недостаточно времени"
            elif "no test" in msg_lower:
                reason = "Нет тестов для типа"
            elif "postgres" in msg_lower:
                reason = "PostgreSQL (нет test URL)"
            else:
                # Truncate long reasons
                reason = msg[:40] + "..." if len(msg) > 40 else msg
            
            reasons[reason] = reasons.get(reason, 0) + 1
    
    return reasons




# ============================================================================
# MAIN PIPELINE CLASS
# ============================================================================

class AgentPipeline:
    """
    Central coordinator for Agent Mode.
    
    Manages the complete lifecycle:
    - Request analysis via Orchestrator
    - Code generation via Code Generator
    - Multi-level validation (syntax, imports, types, AI)
    - User confirmation before applying
    - Backup and apply via VFS
    
    Example:
        pipeline = AgentPipeline(
            project_dir="/path/to/project",
            history_manager=history_manager,
            thread_id="thread-123",
        )
        
        result = await pipeline.process_request(
            user_request="Add login method to AuthService",
            history=[...],
            mode=PipelineMode.AGENT,
        )
        
        if result.pending_changes:
            # Show diff to user
            print(result.diffs)
            
            # If user confirms
            apply_result = await pipeline.apply_pending_changes()
    """
    
    # Configuration
    MAX_FEEDBACK_ITERATIONS = 35
    MAX_VALIDATION_RETRIES = 35
    
    def __init__(
        self,
        project_dir: Optional[str] = None,
        history_manager: Optional[HistoryManager] = None,
        thread_id: Optional[str] = None,
        project_index: Optional[Dict[str, Any]] = None,
        enable_type_checking: bool = False,
        generator_model: Optional[str] = None,  # NEW: модель генератора
    ):
        """
        Initialize Agent Pipeline.
        
        Args:
            project_dir: Path to project root
            history_manager: HistoryManager for saving history
            thread_id: Current thread ID
            project_index: Pre-loaded project index (optional)
            enable_type_checking: Enable mypy type checking
            generator_model: Model to use for code generation (optional)
        """
        self.project_dir = project_dir or "."
        self.history_manager = history_manager
        self.thread_id = thread_id
        self.project_index = project_index or {}
        
        self._enable_type_checking = enable_type_checking
        
        # [NEW] Модель оркестратора (если None — используется роутер)
        self._orchestrator_model: Optional[str] = None
        
        # [NEW] Модель генератора
        self._generator_model: Optional[str] = generator_model
        
        # Initialize services
        self.vfs = VirtualFileSystem(self.project_dir)
        self.backup_manager = BackupManager(self.project_dir)
        self.ai_validator = AIValidator()
        self.token_counter = TokenCounter()
        self.dependency_manager = DependencyManager(Path(self.project_dir))
        
        # Ensure formatting tools are available in project venv
        try:
            formatting_result = self.dependency_manager.ensure_formatting_tools()
            installed_tools = [t for t, success in formatting_result.items() if success]
            if installed_tools:
                logger.info(f"Formatting tools available in project venv: {installed_tools}")
        except Exception as e:
            logger.warning(f"Could not ensure formatting tools: {e}")
        
        # Store the project python path for later use
        self._project_python_path = self.dependency_manager._python_path
        
        # Initialize FileModifier with project python path for auto-formatting
        self.file_modifier = FileModifier(project_python_path=self._project_python_path)
        
        # State
        self.status = PipelineStatus.IDLE
        self.feedback_loop: Optional[FeedbackLoopState] = None
        self.current_session_id: Optional[str] = None
        
        # Pending changes (awaiting user confirmation)
        self._pending_changes: List[PendingChange] = []
        self._pending_deletions: List[PendingDeletion] = []
        self._pending_user_request: str = ""
        self._pending_orchestrator_instruction: str = ""
        self._current_generated_code: str = ""
        
        # Callbacks
        self._on_thinking: Optional[OnThinkingCallback] = None
        self._on_tool_call: Optional[OnToolCallCallback] = None
        self._on_validation: Optional[OnValidationCallback] = None
        self._on_status: Optional[OnStatusCallback] = None
        
        self._on_stage: Optional[Callable] = None
        self._on_user_decision: Optional[Callable] = None
        
        logger.info(f"AgentPipeline initialized: project_dir={self.project_dir}, generator_model={generator_model}")
    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================
    
    def set_orchestrator_model(self, model: Optional[str]):
        """
        Set the orchestrator model to use.
        
        If model is None, the router will automatically select a model.
        If model is set, the router is bypassed.
        
        Args:
            model: Model identifier (e.g., cfg.MODEL_SONNET_4_5) or None for auto-routing
        """
        self._orchestrator_model = model
        if model:
            logger.info(f"AgentPipeline: orchestrator model set to {cfg.get_model_display_name(model)}")
        else:
            logger.info("AgentPipeline: orchestrator model set to auto (router)")    
   
   
    def set_generator_model(self, model: Optional[str]):
        """
        Set the generator model to use.
        
        If model is None, the default from config will be used.
        
        Args:
            model: Model identifier (e.g., cfg.MODEL_GLM_4_7) or None for default
        """
        self._generator_model = model
        if model:
            logger.info(f"AgentPipeline: generator model set to {cfg.get_model_display_name(model)}")
        else:
            logger.info("AgentPipeline: generator model set to default (from config)")    
    
    async def process_request(
        self,
        user_request: str,
        history: List[Dict[str, str]],
        mode: PipelineMode = PipelineMode.AGENT,
        on_thinking: Optional[OnThinkingCallback] = None,
        on_tool_call: Optional[OnToolCallCallback] = None,
        on_validation: Optional[OnValidationCallback] = None,
        on_status: Optional[OnStatusCallback] = None,
        on_stage: Optional[OnStageCallback] = None,
        on_user_decision: Optional[OnUserDecisionCallback] = None,
    ) -> PipelineResult:
        """
        Process a user request through the complete pipeline with feedback loops.
        
        Flow:
        1. Orchestrator analyzes → generates instruction
        2. Code Generator writes code
        3. Technical Validation (syntax, imports, integration)
        - On error → feedback to Orchestrator → loop from step 1
        4. AI Validator checks semantic correctness
        - On rejection → Orchestrator decides (accept/override)
        - If Orchestrator accepts → loop from step 1
        - If Orchestrator overrides → user decides
        5. Tests + Runtime run in VFS
        - On error → feedback to Orchestrator → loop from step 1
        6. User confirmation → apply to real files
        
        History is preserved across all iterations!
        """
        import time
        from app.utils.pipeline_trace_logger import PipelineTraceLogger
        
        start_time = time.time()
        
        # Store callbacks
        self._on_thinking = on_thinking
        self._on_tool_call = on_tool_call
        self._on_validation = on_validation
        self._on_status = on_status
        self._on_stage = on_stage
        self._on_user_decision = on_user_decision
        
        # Initialize result
        result = PipelineResult(
            success=False,
            status=PipelineStatus.ANALYZING,
        )
        
        # Generate session ID
        self.current_session_id = f"session-{uuid.uuid4().hex[:12]}"
        
        # Initialize validation logger
        vlog = get_validation_logger(self.current_session_id)
        vlog.log_stage("INIT", f"Pipeline started for request: {user_request[:100]}...", {
            "mode": mode.value,
            "project_dir": self.project_dir,
            "orchestrator_model": self._orchestrator_model or "auto (router)",
        })
        
        # === Initialize trace logger ===
        trace = PipelineTraceLogger(
            user_request=user_request,
            project_dir=self.project_dir or ".",
            model=self._orchestrator_model or "router",
        )
        
        # Initialize feedback loop
        self.feedback_loop = create_feedback_loop(
            session_id=self.current_session_id,
            user_request=user_request,
            max_validator_retries=self.MAX_VALIDATION_RETRIES,
            max_orchestrator_revisions=self.MAX_FEEDBACK_ITERATIONS,
        )
        
        # Clear state
        self._pending_changes = []
        self._pending_user_request = user_request
        self._current_generated_code = ""  # Track generated code for feedback
        self.vfs.discard_all()
        
        # Reset feedback loader for new session
        reset_feedback_loader()
        
        # Working history - accumulates feedback across iterations
        working_history = history.copy()
        
        try:
            # If ASK mode, just run orchestrator once and return
            if mode == PipelineMode.ASK:
                self._update_status(PipelineStatus.ANALYZING, "Analyzing request...")
                orchestrator_result = await self._run_orchestrator(
                    user_request=user_request,
                    history=working_history,
                    orchestrator_model=self._orchestrator_model,
                )
                result.analysis = orchestrator_result.analysis
                result.instruction = orchestrator_result.instruction
                result.success = True
                result.status = PipelineStatus.COMPLETED
                result.duration_ms = (time.time() - start_time) * 1000
                trace.complete(success=True, status="completed_ask_mode", duration_ms=result.duration_ms)
                return result
            
            # ================================================================
            # MAIN LOOP - runs until success or max iterations
            # ================================================================
            
            iteration = 0
            while iteration < self.MAX_FEEDBACK_ITERATIONS:
                iteration += 1
                self._notify_stage("ITERATION", f"Итерация {iteration}/{self.MAX_FEEDBACK_ITERATIONS}", {
                    "iteration": iteration,
                })
                
                # NOTE: VFS is NOT cleared between iterations!
                # Both Orchestrator and Code Generator see the same staged files.
                # This allows Code Generator to fix the exact code that Orchestrator analyzed.
                # VFS is only cleared at session start (line 765: vfs.discard_all())
                self._pending_deletions = []
                self._current_generated_code = ""  # Clear previous generated code
                # ==============================================================
                # STEP 1: ORCHESTRATOR
                # ==============================================================
                self._update_status(PipelineStatus.ANALYZING, f"Analyzing request (iteration {iteration})...")
                self._notify_stage("ORCHESTRATOR", "Анализ запроса...", {"iteration": iteration})
                
                try:
                    orchestrator_result = await self._run_orchestrator(
                        user_request=user_request,
                        history=working_history,
                        orchestrator_model=self._orchestrator_model,
                    )
                    vlog.log_orchestrator(user_request, self._orchestrator_model or "auto", orchestrator_result)
                    
                    # === Trace: log tool calls ===
                    for tc in orchestrator_result.tool_calls:
                        target = tc.arguments.get("file_path") or tc.arguments.get("query") or tc.arguments.get("chunk_name") or ""
                        trace.add_tool_call(tc.name, str(target)[:200], tc.success)
                    
                except Exception as e:
                    vlog.log_error("ORCHESTRATOR", e, {"user_request": user_request[:200]})
                    trace.set_error(f"Orchestrator error: {e}")
                    raise
                
                result.analysis = orchestrator_result.analysis
                result.instruction = orchestrator_result.instruction
                self._pending_orchestrator_instruction = orchestrator_result.instruction
                
                # === CHECK FOR DIRECT_ANSWER (no code generation needed) ===
                response_type, direct_answer = parse_response_type(orchestrator_result.raw_response)
                
                # === SAFETY CHECK: If orchestrator has instruction, override DIRECT_ANSWER ===
                
                if response_type == "DIRECT_ANSWER" and orchestrator_result.instruction and orchestrator_result.instruction.strip():
                    logger.warning(
                        f"parse_response_type returned DIRECT_ANSWER but orchestrator has instruction "
                        f"({len(orchestrator_result.instruction)} chars). Overriding to CODE_INSTRUCTION."
                    )
                    response_type = "CODE_INSTRUCTION"
                    direct_answer = None
                
                if response_type == "DIRECT_ANSWER":
                    if response_type == "DIRECT_ANSWER":
                        if iteration > 1:
                            logger.warning(
                                f"Iteration {iteration}: Orchestrator returned DIRECT_ANSWER after code generation started. "
                                f"This is likely an error. Requesting proper instruction."
                            )
                            # Формируем feedback для повторного запроса
                            error_feedback = (
                                "[ERROR] Your response did not contain a proper instruction.\n\n"
                                "We are in the middle of a code generation cycle (iteration > 1).\n"
                                "You MUST provide a `## Instruction for Code Generator` section.\n\n"
                                "If you believe no code changes are needed, explicitly state:\n"
                                "**RESPONSE_TYPE:** DIRECT_ANSWER\n"
                                "## Answer\n"
                                "[Your explanation why no code is needed]\n\n"
                                "Otherwise, provide the instruction for fixing the previous issues."
                            )
                            
                            working_history.append({
                                "role": "user",
                                "content": error_feedback,
                            })
                            
                            # Записываем в trace
                            trace.set_error(f"Iteration {iteration}: Empty instruction (DIRECT_ANSWER fallback)")
                            
                            # Продолжаем цикл — следующая итерация
                            continue
                        
                        # Первая итерация — действительно direct_answer
                        logger.info("Orchestrator provided DIRECT_ANSWER — skipping code generation")
                        self._notify_stage("DIRECT_ANSWER", "Оркестратор отвечает напрямую (без генерации кода)", {
                            "answer_preview": direct_answer[:200] if direct_answer else "",
                        })
                        
                        # Return successful result with direct answer
                        result.success = True
                        result.status = PipelineStatus.COMPLETED
                        result.analysis = direct_answer or orchestrator_result.analysis
                        result.instruction = ""  # No instruction for code generator
                        result.duration_ms = (time.time() - start_time) * 1000
                        
                        trace.complete(success=True, status="direct_answer", duration_ms=result.duration_ms)
                        vlog.log_complete(True, result.duration_ms)
                        return result
            
                    # === EXTRACT DELETIONS FROM INSTRUCTION ===
                    if orchestrator_result.instruction:
                        clean_instruction, new_deletions = extract_deletions_from_instruction(
                            orchestrator_result.instruction
                        )
                        if new_deletions:
                            self._pending_deletions.extend(new_deletions)
                            logger.info(f"Extracted {len(new_deletions)} pending deletions")
                            self._notify_stage("DELETIONS", f"Обнаружено {len(new_deletions)} удалений (будут применены после тестов)", {
                                "deletions": [{"target": d.target_name, "file": d.file_path} for d in new_deletions],
                            })
                        # Use clean instruction (without DELETE blocks) for code generator
                        result.instruction = clean_instruction
                        self._pending_orchestrator_instruction = clean_instruction                
                
                # === Trace: log instruction ===
                trace.set_instruction(orchestrator_result.instruction or "[No instruction]")
                
                # Report thinking
                for tc in orchestrator_result.tool_calls:
                    if tc.thinking and self._on_thinking:
                        self._on_thinking(tc.thinking)
                
                if orchestrator_result.instruction:
                    self._notify_stage("INSTRUCTION", "Инструкция для Code Generator", {
                        "instruction": orchestrator_result.instruction[:500],
                    })
                
                # Check if instruction is empty
                if not orchestrator_result.instruction.strip():
                    result.errors.append("Orchestrator did not generate an instruction")
                    result.status = PipelineStatus.FAILED
                    result.duration_ms = (time.time() - start_time) * 1000
                    trace.set_error("Orchestrator did not generate an instruction")
                    return result
                
                # ==============================================================
                # STEP 2: CODE GENERATOR
                # ==============================================================
                self._update_status(PipelineStatus.GENERATING, "Generating code...")
                self._notify_stage("CODE_GEN", "Генерация кода...", None)
                
                try:
                    code_blocks, raw_response = await self._run_code_generator(
                        instruction=orchestrator_result.instruction,
                        target_file=orchestrator_result.target_file,
                        target_files=orchestrator_result.target_files,
                    )
                    vlog.log_code_generation(orchestrator_result.instruction, code_blocks)
                    
                    # === Trace: log generated code ===
                    for block in code_blocks:
                        trace.add_generated_code(block.file_path, block.mode, block.code)
                    
                    # Store generated code for feedback context
                    self._current_generated_code = self._format_generated_code_for_context(code_blocks)
                        
                except Exception as e:
                    vlog.log_error("CODE_GENERATOR", e)
                    trace.set_error(f"Code Generator error: {e}")
                    raise
                
                if not code_blocks:
                    # Code Generator не сгенерировал код — проверяем, есть ли что-то в VFS
                    staged_files = self.vfs.get_staged_files()
                    
                    
                    if staged_files:
                        # В VFS есть файлы от предыдущих итераций — продолжаем валидацию
                        logger.info(
                            f"Code Generator did not produce new code, but VFS has {len(staged_files)} staged files. "
                            f"Continuing with validation."
                        )
                        self._notify_stage("CODE_GEN", 
                            f"⚠️ Генератор не создал новый код, но в VFS есть {len(staged_files)} файл(ов) — продолжаем валидацию", 
                            {"staged_files": staged_files}
                        )
                        # code_blocks остаётся пустым, но мы продолжаем
                        result.code_blocks = []
                    else:
                        # VFS тоже пуст — это нормально, если Оркестратор решил что код не нужен
                        logger.info("Code Generator did not produce code and VFS is empty. No changes to validate.")
                        self._notify_stage("CODE_GEN", 
                            "ℹ️ Генератор не создал код (возможно, изменения не требуются)", 
                            None
                        )
                        
                        # Возвращаем успешный результат без изменений
                        result.success = True
                        result.status = PipelineStatus.COMPLETED
                        result.code_blocks = []
                        result.pending_changes = []
                        result.duration_ms = (time.time() - start_time) * 1000
                        
                        self._notify_stage("COMPLETE", "✅ Завершено без изменений кода", {
                            "files": [],
                            "iterations": iteration,
                            "duration_ms": result.duration_ms,
                            "no_code_generated": True,
                        })
                        
                        trace.complete(success=True, status="completed_no_code", duration_ms=result.duration_ms)
                        vlog.log_complete(True, result.duration_ms)
                        return result
                else:
                    result.code_blocks = code_blocks
                    
                    self._notify_stage("CODE_GEN", f"Сгенерировано {len(code_blocks)} блок(ов) кода", {
                        "files": [b.file_path for b in code_blocks],
                    })
                
                # ==============================================================
                # STEP 3: APPLY TO VFS (WITH STAGING CORRECTION LOOP)
                # ==============================================================
                staging_attempt = 0
                MAX_STAGING_ATTEMPTS = 3
                apply_errors_data = []
                
                while staging_attempt < MAX_STAGING_ATTEMPTS:
                    apply_errors_data = await self._stage_code_blocks(code_blocks)
                    
                    if not apply_errors_data:
                        # Success!
                        break
                    
                    staging_attempt += 1
                    
                    # Log staging errors to trace immediately (for every attempt)
                    for err_item in apply_errors_data:
                        trace.add_staging_error(
                            file_path=err_item.get("file_path", ""),
                            mode=err_item.get("mode", ""),
                            error=err_item.get("error", ""),
                            error_type=str(err_item.get("error_type")) if err_item.get("error_type") else None,
                            target_class=err_item.get("target_class"),
                            target_method=err_item.get("target_method"),
                            target_function=err_item.get("target_function"),
                            code_preview=err_item.get("code_preview"),
                        )
                        
                        # Dump full report to separate file
                        trace.dump_staging_error_report(err_item)
                    
                    # 1. Log and notify
                    self._notify_stage("STAGING", f"❌ Ошибки стейджинга (попытка {staging_attempt}/{MAX_STAGING_ATTEMPTS})", {
                        "errors": apply_errors_data,  # Pass full error objects with targets/code
                        "error_count": len(apply_errors_data),
                    })
                    
                    # If we reached max attempts, stop trying and let main loop handle it
                    if staging_attempt >= MAX_STAGING_ATTEMPTS:
                        break
                        
                    # 2. Add to FeedbackHandler
                    for err_item in apply_errors_data:
                        self.feedback_loop.feedback_handler.add_staging_error(
                            file_path=err_item["file_path"],
                            mode=err_item["mode"],
                            error=err_item["error"],
                            error_type=err_item.get("error_type")
                        )
                    
                    # 3. Get formatted feedback
                    feedback_dump = self.feedback_loop.get_feedback_for_orchestrator()
                    staging_text = feedback_dump.get("staging_errors", "")
                    self.feedback_loop.feedback_handler.clear_feedback()
                    
                    # 4. Update history and request fix
                    working_history.append({
                        "role": "user",
                        "content": f"[STAGING ERRORS - REVISE TARGETS]\n{staging_text}\n\nPlease revise your instruction to fix these targeting errors. Do not change logic, just fix the targets."
                    })
                    
                    self._notify_stage("ORCHESTRATOR", "Оркестратор исправляет ошибки стейджинга...", None)
                    
                    try:
                        # Run Orchestrator for fix
                        orchestrator_result = await self._run_orchestrator(
                            user_request=user_request,
                            history=working_history,
                            orchestrator_model=self._orchestrator_model,
                        )
                        
                        # Update pending instruction
                        self._pending_orchestrator_instruction = orchestrator_result.instruction
                        
                        # Add response to history
                        working_history.append({
                            "role": "assistant",
                            "content": orchestrator_result.raw_response
                        })
                        
                        if not orchestrator_result.instruction.strip():
                            logger.warning("Orchestrator returned empty instruction during staging fix")
                            break
                            
                        # 5. Generate new code
                        self._notify_stage("CODE_GEN", "Генерация исправленного кода...", None)
                        code_blocks, raw_response = await self._run_code_generator(
                            instruction=orchestrator_result.instruction,
                            target_file=orchestrator_result.target_file,
                            target_files=orchestrator_result.target_files,
                        )
                        
                        # Continue loop to stage new blocks
                        
                    except Exception as e:
                        logger.error(f"Error in staging correction loop: {e}")
                        break

                if apply_errors_data:
                    # 1. Логируем в консоль/уведомления
                    error_msgs = [f"{e['file_path']}: {e['error']}" for e in apply_errors_data]
                    result.errors.extend(error_msgs)
                    
                    self._notify_stage("STAGING", f"❌ Не удалось исправить ошибки стейджинга: {len(apply_errors_data)}", {
                        "errors": apply_errors_data,  # Pass full error objects with targets/code
                    })
                    
                    # 2. Добавляем в FeedbackHandler (для следующей большой итерации)
                    for err_item in apply_errors_data:
                        self.feedback_loop.feedback_handler.add_staging_error(
                            file_path=err_item["file_path"],
                            mode=err_item["mode"],
                            error=err_item["error"],
                            error_type=err_item.get("error_type")
                        )
                        
                        # Dump full report to separate file
                        trace.dump_staging_error_report(err_item)
                    
                    # 3. Логируем в трейс
                    for err_item in apply_errors_data:
                        trace.add_staging_error(
                            file_path=err_item.get("file_path", ""),
                            mode=err_item.get("mode", ""),
                            error=err_item.get("error", ""),
                            error_type=str(err_item.get("error_type")) if err_item.get("error_type") else None,
                            target_class=err_item.get("target_class"),
                            target_method=err_item.get("target_method"),
                            target_function=err_item.get("target_function"),
                            code_preview=err_item.get("code_preview"),
                        )
                    
                    # 4. Формируем сообщение для истории
                    feedback_dump = self.feedback_loop.get_feedback_for_orchestrator()
                    staging_text = feedback_dump.get("staging_errors", "")
                    
                    working_history.append({
                        "role": "user",
                        "content": f"[CRITICAL ERRORS - STAGING FAILED]\n\n{staging_text}\n\nPlease revise your instruction.",
                    })
                    
                    # 5. Записываем ревизию и идем на следующий круг (основной цикл)
                    self.feedback_loop.record_orchestrator_revision(
                        reason=f"Staging failed after {staging_attempt} attempts: {len(apply_errors_data)} errors",
                        previous_instruction=self._pending_orchestrator_instruction or "",
                        new_instruction="[pending - next iteration]",
                    )
                    
                    # Очищаем перед следующим циклом
                    self.feedback_loop.feedback_handler.clear_feedback()
                    
                    continue
                
# ==============================================================
                # STEP 3.5: PRE-INSTALL REQUIREMENTS.TXT DEPENDENCIES
                # ==============================================================
                try:
                    req_install_result = self.dependency_manager.install_from_requirements()
                    if req_install_result.successful > 0:
                        self._notify_stage(
                            "DEPENDENCY",
                            f"✅ Установлено {req_install_result.successful} пакетов из requirements.txt",
                            {
                                "source": "requirements.txt",
                                "installed": req_install_result.successful,
                                "failed": req_install_result.failed,
                                "skipped": req_install_result.skipped,
                            }
                        )
                        logger.info(f"Pre-installed {req_install_result.successful} packages from requirements.txt")
                    elif req_install_result.failed > 0:
                        self._notify_stage(
                            "DEPENDENCY",
                            f"⚠️ Не удалось установить {req_install_result.failed} пакетов из requirements.txt",
                            {"failed": req_install_result.failed}
                        )
                except Exception as e:
                    logger.warning(f"Failed to pre-install from requirements.txt: {e}")
                
                # ==============================================================
                # STEP 4: TECHNICAL VALIDATION (without tests/runtime)
                # ==============================================================
                self._update_status(PipelineStatus.VALIDATING, "Validating code...")
                self._notify_stage("VALIDATION", "Техническая проверка кода...", {
                    "levels": ["syntax", "imports", "integration"]
                })
                
                try:
                    validation_result = await self._run_validation(include_tests=False)
                    vlog.log_validation("TECHNICAL", validation_result)
                    
                    # === Trace: log technical validation ===
                    trace.set_tech_validation(
                        success=validation_result.success,
                        errors=validation_result.error_count,
                        summary="; ".join(str(i) for i in validation_result.issues[:3]),
                    )
                    
                    if validation_result.auto_format_stats:
                        stats_data = validation_result.auto_format_stats
                        counts = stats_data.get("stats", {})
                        tools = stats_data.get("tools", {})
                        fixed_list = stats_data.get("fixed_files", [])
                        failed_list = stats_data.get("failed_files", [])
                        
                        # Determine message
                        missing_tools = [t for t, avail in tools.items() if not avail]
                        
                        if counts.get("fixed", 0) > 0:
                            msg = f"Auto-formatted {counts['fixed']} file(s)"
                            success_flag = True
                        elif counts.get("failed", 0) > 0:
                            msg = f"Failed to fix {counts['failed']} file(s)"
                            if missing_tools:
                                msg += f" (Missing tools: {', '.join(missing_tools)})"
                            else:
                                msg += " (Tools ran but failed)"
                            success_flag = False
                        elif counts.get("with_errors", 0) == 0:
                            msg = "No syntax errors found (Validation OK)"
                            success_flag = True
                        else:
                            msg = "Auto-formatting skipped"
                            success_flag = False
                            
                        trace.set_auto_format_status(
                            ran=True,
                            success=success_flag,
                            message=msg,
                            fixes=[f"{f['file']}: {f['fixes']}" for f in fixed_list],
                            tools=tools
                        )
                    
                except Exception as e:
                    vlog.log_error("VALIDATION", e)
                    trace.set_error(f"Validation error: {e}")
                    raise
                
                result.validation_result = validation_result.to_dict()
                
                if self._on_validation:
                    self._on_validation(validation_result.to_dict())
                
                self._notify_stage("VALIDATION", "Результаты технической валидации", {
                    "success": validation_result.success,
                    "error_count": validation_result.error_count,
                    "warning_count": validation_result.warning_count,
                })
                
                # ============================================================
                # STEP 4.1: Auto-install missing dependencies (after ANY error)
                # ============================================================
                if validation_result.error_count > 0:
                    self._notify_stage(
                        "DEPENDENCY", 
                        "Сканирование проекта на недостающие зависимости...", 
                        None
                    )
                    
                    try:
                        # Scan ALL project files and install missing dependencies
                        install_result = self.dependency_manager.scan_and_install_all_dependencies()
                        
                        if install_result and install_result.successful > 0:
                            self._notify_stage(
                                "DEPENDENCY", 
                                f"✅ Установлено {install_result.successful} пакетов", 
                                {"installed": install_result.successful, "failed": install_result.failed}
                            )
                            # Re-validate after installation
                            validation_result = await self._run_validation(include_tests=False)
                            if self._on_validation:
                                self._on_validation(validation_result.to_dict())
                            result.validation_result = validation_result.to_dict()
                            
                            # Update notification with new results
                            self._notify_stage("VALIDATION", "Результаты после установки зависимостей", {
                                "success": validation_result.success,
                                "error_count": validation_result.error_count,
                                "warning_count": validation_result.warning_count,
                            })
                        elif install_result and install_result.failed > 0:
                            self._notify_stage(
                                "DEPENDENCY", 
                                f"⚠️ Не удалось установить {install_result.failed} пакетов", 
                                {"failed": install_result.failed}
                            )
                    except Exception as e:
                        logger.error(f"Dependency scan failed: {e}")
                
                # ==============================================================
                # STEP 4.5: CHECK FOR BLOCKING ERRORS
                # ==============================================================
                if validation_result.error_count > 0:
                    blocking_errors = [
                        issue for issue in validation_result.issues
                        if issue.severity == IssueSeverity.ERROR
                        and issue.level in (
                            ValidationLevel.SYNTAX, 
                            ValidationLevel.IMPORTS,
                            ValidationLevel.INTEGRATION,
                        )
                    ]
                    
                    if blocking_errors:
                        self._notify_stage(
                            "VALIDATION", 
                            f"❌ {len(blocking_errors)} критических ошибок обнаружено", 
                            {
                                "blocking": True,
                                "error_count": len(blocking_errors),
                                "errors": [
                                    {
                                        "file": e.file_path, 
                                        "line": e.line, 
                                        "message": e.message, 
                                        "level": e.level.value if hasattr(e.level, 'value') else str(e.level)
                                    } 
                                    for e in blocking_errors[:10]
                                ],
                            }
                        )
                        
                        # Include generated code in feedback
                        feedback_msg = self._format_errors_for_orchestrator(blocking_errors, code_blocks)
                        
                        working_history.append({
                            "role": "user",
                            "content": feedback_msg,
                        })
                        
                        # Записываем в feedback loop
                        self.feedback_loop.add_validation_errors(validation_result.to_dict())
                        self.feedback_loop.record_orchestrator_revision(
                            reason=f"Validation failed: {len(blocking_errors)} blocking errors",
                            previous_instruction=self._pending_orchestrator_instruction or "",
                            new_instruction="[pending - next iteration]",
                        )
                        
                        logger.warning(
                            f"Iteration {iteration}: Validation failed with {len(blocking_errors)} errors, "
                            f"returning to Orchestrator"
                        )
                        
                        # Возврат к Оркестратору — следующая итерация цикла
                        continue
                
                # ==============================================================
                # STEP 5: AI VALIDATION
                # ==============================================================
                self._notify_stage("AI_VALIDATION", "AI Validator проверяет соответствие задаче...", None)
                
                try:
                    ai_result = await self._run_ai_validation(
                        user_request=user_request,
                        instruction=orchestrator_result.instruction,
                        code_blocks=code_blocks,
                    )
                    vlog.log_ai_validation(ai_result)
                    
                    # === Trace: log AI validation ===
                    trace.set_ai_validation(
                        approved=ai_result.approved,
                        confidence=ai_result.confidence,
                        verdict=ai_result.verdict,
                        issues=ai_result.critical_issues,
                    )
                    
                except Exception as e:
                    vlog.log_error("AI_VALIDATION", e)
                    trace.set_error(f"AI Validation error: {e}")
                    raise
                
                result.ai_validation_result = ai_result.to_dict()
                
                self._notify_stage("AI_VALIDATION",
                    "✅ ОДОБРЕНО" if ai_result.approved else "❌ ОТКЛОНЕНО",
                    {
                        "approved": ai_result.approved,
                        "confidence": ai_result.confidence,
                        "verdict": ai_result.verdict,
                        "critical_issues": ai_result.critical_issues,
                    }
                )
                
                # ==============================================================
                # STEP 5.5: ORCHESTRATOR DECISION (if AI rejected)
                # ==============================================================
                
                # === ПРОВЕРКА НА PARSE ERROR ===
                if ai_result.parse_error:
                    logger.warning(
                        f"AI Validator had parse error: {ai_result.parse_error}. "
                        f"Skipping orchestrator decision, proceeding to tests."
                    )
                    self._notify_stage(
                        "AI_VALIDATION", 
                        "⚠️ Валидатор не смог сформировать ответ — переходим к тестам",
                        {
                            "parse_error": ai_result.parse_error,
                            "raw_response_preview": ai_result.raw_response[:200] if ai_result.raw_response else "",
                        }
                    )
                
                elif not ai_result.approved:
                    logger.info("AI Validator rejected code, asking Orchestrator for decision")
                    self._notify_stage("FEEDBACK", "Отправка критики AI Validator Оркестратору...", None)
                    
                    # Orchestrator decides: ACCEPT (revise) or OVERRIDE (disagree)
                    try:
                        orchestrator_decision = await self._get_orchestrator_decision_on_ai_feedback(
                            ai_result=ai_result,
                            code_blocks=code_blocks,
                            working_history=working_history,
                        )
                    except Exception as e:
                        logger.error(f"Orchestrator decision error: {e}")
                        orchestrator_decision = OrchestratorFeedbackDecision(
                            decision="OVERRIDE",
                            reasoning=f"Error getting decision: {e}. Proceeding to tests.",
                        )
                    
                    result.orchestrator_decision = orchestrator_decision
                    
                    # === Trace: log orchestrator decision ===
                    trace.set_orchestrator_decision(
                        orchestrator_decision.decision,
                        orchestrator_decision.reasoning,
                    )
                    
                    self._notify_stage("ORCHESTRATOR_DECISION",
                        f"Решение Оркестратора: {orchestrator_decision.decision}",
                        {
                            "decision": orchestrator_decision.decision,
                            "reasoning": orchestrator_decision.reasoning,
                        }
                    )
                    
                    # === ACCEPT: Оркестратор СОГЛАСЕН с валидатором ===
                    if orchestrator_decision.decision == "ACCEPT":
                        logger.info("Orchestrator accepts AI Validator feedback, looping back")
                        
                        # Include generated code in feedback
                        feedback_msg = self._format_ai_validator_feedback(ai_result, code_blocks)
                        working_history.append({
                            "role": "user",
                            "content": feedback_msg,
                        })
                        
                        self.feedback_loop.add_validator_feedback(
                            approved=ai_result.approved,
                            confidence=ai_result.confidence,
                            verdict=ai_result.verdict,
                            critical_issues=ai_result.critical_issues,
                            model_used=ai_result.model_used,
                        )
                        
                        # Continue to next iteration
                        continue
                    
                    # === OVERRIDE: Оркестратор НЕ СОГЛАСЕН с валидатором ===
                    elif orchestrator_decision.decision == "OVERRIDE":
                        logger.info("Orchestrator wants to override AI Validator")
                        
                        if self._on_user_decision:
                            user_choice = await self._on_user_decision(
                                "orchestrator_override",
                                {
                                    "ai_result": ai_result.to_dict(),
                                    "orchestrator_reasoning": orchestrator_decision.reasoning,
                                    "code_blocks": [{"file": b.file_path, "mode": b.mode} for b in code_blocks],
                                }
                            )
                            
                            logger.info(f"User decision on override: {user_choice}")
                            
                            if user_choice == "cancel":
                                await self.discard_pending_changes()
                                result.status = PipelineStatus.CANCELLED
                                result.errors.append("User cancelled request")
                                result.duration_ms = (time.time() - start_time) * 1000
                                trace.complete(success=False, status="cancelled_by_user", duration_ms=result.duration_ms)
                                return result
                            
                            elif user_choice == "force_fix":
                                # Include generated code in feedback
                                feedback_msg = self._format_ai_validator_feedback(ai_result, code_blocks)
                                working_history.append({
                                    "role": "user",
                                    "content": f"[USER OVERRIDE - MUST FIX]\n{feedback_msg}",
                                })
                                continue
                            
                            elif user_choice == "proceed":
                                logger.info("User trusts Orchestrator, proceeding to tests")
                                self.feedback_loop.validator_overrides_count += 1
                            
                            else:
                                logger.warning(f"Unknown user choice: {user_choice}, proceeding to tests")
                                self.feedback_loop.validator_overrides_count += 1
                        
                        else:
                            logger.info("No user decision callback, auto-proceeding to tests")
                            self.feedback_loop.validator_overrides_count += 1
                    
                    else:
                        logger.warning(f"Unknown orchestrator decision: {orchestrator_decision.decision}, proceeding to tests")
                        self.feedback_loop.validator_overrides_count += 1
                        
                # ==============================================================
                # STEP 6: TESTS + RUNTIME
                # ==============================================================
                self._update_status(PipelineStatus.VALIDATING, "Running tests...")
                self._notify_stage("TESTS", "Запуск тестов и runtime проверок...", None)
                
                try:
                    validation_with_tests = await self._run_validation(include_tests=True)
                    vlog.log_validation("WITH_TESTS", validation_with_tests)
                    
                    # === NEW: Trace runtime results ===
                    runtime_skipped = getattr(validation_with_tests, 'runtime_files_skipped', 0)
                    # Calculate skipped if not available
                    if runtime_skipped == 0 and validation_with_tests.runtime_test_summary:
                        results_list = validation_with_tests.runtime_test_summary.get("results", [])
                        runtime_skipped = sum(1 for r in results_list if r.get("status") == "skipped")
                    
                    trace.set_runtime_results(
                        files_checked=validation_with_tests.runtime_files_checked,
                        files_passed=validation_with_tests.runtime_files_passed,
                        files_failed=validation_with_tests.runtime_files_failed,
                        files_skipped=runtime_skipped,
                        summary=validation_with_tests.runtime_test_summary,  # Передаём dict, не str!
                    )
                    
                    
                except Exception as e:
                    vlog.log_error("TESTS", e)
                    trace.set_error(f"Tests error: {e}")
                    raise
                
                result.validation_result = validation_with_tests.to_dict()
                
                if self._on_validation:
                    self._on_validation(validation_with_tests.to_dict())
                
                # === NEW: Notify RUNTIME results separately ===
                runtime_skipped = getattr(validation_with_tests, 'runtime_files_skipped', 0)
                if runtime_skipped == 0 and validation_with_tests.runtime_test_summary:
                    results_list = validation_with_tests.runtime_test_summary.get("results", [])
                    runtime_skipped = sum(1 for r in results_list if r.get("status") == "skipped")
                
                # Build failures list for display
                runtime_failures = []
                if validation_with_tests.runtime_test_summary:
                    for r in validation_with_tests.runtime_test_summary.get("results", []):
                        if r.get("status") in ("failed", "error", "timeout"):
                            runtime_failures.append({
                                "file_path": r.get("file_path", "?"),
                                "message": r.get("message", "")[:100],
                                "status": r.get("status"),
                            })
                
                self._notify_stage("RUNTIME", "Результаты runtime тестирования", {
                    "files_checked": validation_with_tests.runtime_files_checked,
                    "files_passed": validation_with_tests.runtime_files_passed,
                    "files_failed": validation_with_tests.runtime_files_failed,
                    "files_skipped": runtime_skipped,
                    "success": validation_with_tests.runtime_files_failed == 0,
                    "failures": runtime_failures[:5],
                    "skipped_reasons": _count_skipped_reasons(validation_with_tests.runtime_test_summary),
                })
                
                # ==============================================================
                # STEP 6.5: CHECK TEST AND RUNTIME RESULTS
                # ==============================================================
                test_errors = [
                    i for i in validation_with_tests.issues 
                    if i.level == ValidationLevel.TESTS and i.severity == IssueSeverity.ERROR
                ]
                runtime_errors = [
                    i for i in validation_with_tests.issues 
                    if i.level == ValidationLevel.RUNTIME and i.severity == IssueSeverity.ERROR
                ]
                
                all_blocking_errors = test_errors + runtime_errors
                
                # === Trace: log test results ===
                trace.set_tests(
                    passed=len(all_blocking_errors) == 0,
                    output=f"Tests run: {validation_with_tests.tests_run}, passed: {validation_with_tests.tests_passed}, failed: {validation_with_tests.tests_failed}. Runtime: {validation_with_tests.runtime_files_passed}/{validation_with_tests.runtime_files_checked} passed",
                    failed=[i.message for i in all_blocking_errors],
                )
                
                tests_expected_but_not_run = (
                    validation_with_tests.tests_run == 0 
                    and len(getattr(validation_with_tests, 'test_files_found', [])) > 0
                )
                
                if all_blocking_errors or tests_expected_but_not_run:
                    error_details = []
                    
                    if test_errors:
                        error_details.append(f"{len(test_errors)} test failures")
                    if runtime_errors:
                        error_details.append(f"{len(runtime_errors)} runtime errors")
                    if tests_expected_but_not_run:
                        error_details.append("tests found but not executed")
                    
                    self._notify_stage(
                        "TESTS", 
                        f"❌ Ошибки: {', '.join(error_details)}", 
                        {
                            "success": False,
                            "test_failures": len(test_errors),
                            "runtime_errors": len(runtime_errors),
                            "tests_run": validation_with_tests.tests_run,
                            "tests_passed": validation_with_tests.tests_passed,
                            "tests_failed": validation_with_tests.tests_failed,
                            "test_files_found": len(getattr(validation_with_tests, 'test_files_found', [])),
                            "runtime_files_checked": validation_with_tests.runtime_files_checked,
                            "runtime_files_passed": validation_with_tests.runtime_files_passed,
                            "runtime_files_failed": validation_with_tests.runtime_files_failed,
                            "runtime_failures": runtime_failures[:5],
                        }
                    )
                    
                    # Include generated code in test feedback
                    feedback_msg = self._format_test_errors_for_orchestrator(
                        test_errors=test_errors,
                        runtime_errors=runtime_errors,
                        validation_result=validation_with_tests,
                        code_blocks=code_blocks,
                    )
                    
                    working_history.append({
                        "role": "user",
                        "content": feedback_msg,
                    })
                    
                    self.feedback_loop.add_test_failures({
                        "test_errors": [e.to_dict() for e in test_errors],
                        "runtime_errors": [e.to_dict() for e in runtime_errors],
                        "tests_run": validation_with_tests.tests_run,
                        "tests_passed": validation_with_tests.tests_passed,
                        "tests_failed": validation_with_tests.tests_failed,
                        "runtime_checked": validation_with_tests.runtime_files_checked,
                        "runtime_passed": validation_with_tests.runtime_files_passed,
                        "runtime_failed": validation_with_tests.runtime_files_failed,
                    })
                    
                    self.feedback_loop.record_orchestrator_revision(
                        reason=f"Tests/Runtime failed: {len(test_errors)} test errors, {len(runtime_errors)} runtime errors",
                        previous_instruction=self._pending_orchestrator_instruction,
                        new_instruction="[pending - next iteration]",
                    )
                    
                    logger.warning(
                        f"Iteration {iteration}: Tests/Runtime failed, returning to Orchestrator. "
                        f"Test errors: {len(test_errors)}, Runtime errors: {len(runtime_errors)}"
                    )
                    
                    continue
                
                # === SUCCESS — тесты прошли ===
                self._notify_stage(
                    "TESTS", 
                    f"✅ Тесты пройдены", 
                    {
                        "success": True,
                        "tests_run": validation_with_tests.tests_run,
                        "tests_passed": validation_with_tests.tests_passed,
                        "tests_failed": validation_with_tests.tests_failed,
                        "test_files_found": len(getattr(validation_with_tests, 'test_files_found', [])),
                        "runtime_files_checked": validation_with_tests.runtime_files_checked,
                        "runtime_files_passed": validation_with_tests.runtime_files_passed,
                        "runtime_files_failed": validation_with_tests.runtime_files_failed,
                        "runtime_files_skipped": runtime_skipped,
                    }
                )
                
                logger.info(
                    f"Iteration {iteration}: All tests passed "
                    f"(pytest: {validation_with_tests.tests_passed}/{validation_with_tests.tests_run}, "
                    f"runtime: {validation_with_tests.runtime_files_passed}/{validation_with_tests.runtime_files_checked})"
                )
                
                # ==============================================================
                # STEP 7: SUCCESS - PREPARE FOR USER CONFIRMATION
                # ==============================================================
                self._update_status(PipelineStatus.AWAITING_CONFIRMATION, "Ready for your review")
                
                self._pending_changes = await self._build_pending_changes(
                    code_blocks=result.code_blocks,
                    validation_passed=validation_with_tests.success,
                    ai_validation_passed=ai_result.approved if ai_result else True,
                )
                
                result.pending_changes = self._pending_changes
                result.diffs = self.vfs.get_all_diffs()
                
                # === APPLY PENDING DELETIONS (comment out code) ===
                if self._pending_deletions:
                    self._notify_stage("DELETIONS", f"Применение {len(self._pending_deletions)} удалений...", None)
                    deletion_results = await self._apply_pending_deletions()
                    
                    result.diffs["__deletions__"] = deletion_results
                    
                    self._notify_stage("DELETIONS", 
                        f"✅ Удалено (закомментировано): {sum(1 for d in deletion_results if d['success'])} элементов",
                        {"results": deletion_results}
                    )
                                        
                result.success = True
                result.feedback_iterations = iteration
                
                # Build runtime summary for COMPLETE stage
                runtime_summary_for_complete = {
                    "checked": validation_with_tests.runtime_files_checked,
                    "passed": validation_with_tests.runtime_files_passed,
                    "failed": validation_with_tests.runtime_files_failed,
                    "skipped": runtime_skipped,
                }
                
                tests_summary_for_complete = {
                    "run": validation_with_tests.tests_run,
                    "passed": validation_with_tests.tests_passed,
                    "failed": validation_with_tests.tests_failed,
                }
                
                self._notify_stage("COMPLETE", f"✅ Готово! {len(self._pending_changes)} файл(ов) к изменению", {
                    "files": [c.file_path for c in self._pending_changes],
                    "iterations": iteration,
                    "duration_ms": (time.time() - start_time) * 1000,
                    "runtime_summary": runtime_summary_for_complete,
                    "tests_summary": tests_summary_for_complete,
                })
                
                result.duration_ms = (time.time() - start_time) * 1000
                vlog.log_complete(result.success, result.duration_ms)
                
                trace.complete(success=True, status="completed", duration_ms=result.duration_ms)
                
                return result
            
            # ==============================================================
            # MAX ITERATIONS REACHED (outside while loop)
            # ==============================================================
            result.status = PipelineStatus.FAILED
            result.errors.append(f"Max iterations ({self.MAX_FEEDBACK_ITERATIONS}) reached without success")
            result.duration_ms = (time.time() - start_time) * 1000
            vlog.log_complete(False, result.duration_ms)
            
            trace.set_error(f"Max iterations ({self.MAX_FEEDBACK_ITERATIONS}) reached")
            trace.complete(success=False, status="max_iterations", duration_ms=result.duration_ms)
            
            return result
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            vlog.log_error("PIPELINE", e)
            result.errors.append(str(e))
            result.status = PipelineStatus.FAILED
            result.duration_ms = (time.time() - start_time) * 1000
            
            trace.set_error(f"Pipeline error: {e}")
            trace.complete(success=False, status="error", duration_ms=result.duration_ms)
            
        return result
        
    async def _validation_loop(
        self,
        user_request: str,
        history: List[Dict[str, str]],
        initial_instruction: str,
        target_file: Optional[str] = None,
        target_files: Optional[List[str]] = None,
        vlog: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Unified validation loop that handles all error types.
        
        Loop continues until:
        - All validations pass (success)
        - Max iterations reached (failure)
        - User cancels (failure)
        
        Returns:
            Dict with code_blocks, validation_result, ai_validation_result, orchestrator_decision
            or None if failed
        """
        current_instruction = initial_instruction
        iteration = 0
        max_iterations = self.MAX_FEEDBACK_ITERATIONS + self.MAX_VALIDATION_RETRIES
        
        while iteration < max_iterations:
            iteration += 1
            
            self._notify_stage("LOOP", f"Итерация {iteration}/{max_iterations}", {"iteration": iteration})
            
            # ============================================================
            # STEP A: Generate code
            # ============================================================
            self._update_status(PipelineStatus.GENERATING, f"Generating code (iteration {iteration})...")
            self._notify_stage("CODE_GEN", f"Генерация кода (итерация {iteration})...", None)
            
            try:
                code_blocks, raw_response = await self._run_code_generator(
                    instruction=current_instruction,
                    target_file=target_file,
                    target_files=target_files,
                )
                if vlog:
                    vlog.log_code_generation(current_instruction, code_blocks)
            except Exception as e:
                if vlog:
                    vlog.log_error("CODE_GENERATOR", e)
                self._notify_stage("CODE_GEN", f"❌ Ошибка: {e}", None)
                return None
            
            if not code_blocks:
                self._notify_stage("CODE_GEN", "❌ Code Generator не вернул код", None)
                # Try to get orchestrator to fix
                if self.feedback_loop.can_revise_instruction():
                    current_instruction = await self._request_instruction_fix(
                        user_request=user_request,
                        history=history,
                        error="Code Generator did not produce any code blocks",
                    )
                    continue
                return None
            
            self._notify_stage("CODE_GEN", f"✅ Сгенерировано {len(code_blocks)} блок(ов)", {
                "files": [b.file_path for b in code_blocks],
            })
            
            # ============================================================
            # STEP B: Stage to VFS
            # ============================================================
            self.vfs.discard_all()  # Clear previous attempt
            apply_errors_data = await self._stage_code_blocks(code_blocks)
            
            if apply_errors_data:
                self._notify_stage("STAGING", f"⚠️ Ошибки стейджинга: {len(apply_errors_data)}", None)
                
                # Добавляем в хендлер
                for err_item in apply_errors_data:
                    self.feedback_loop.feedback_handler.add_staging_error(
                        file_path=err_item["file_path"],
                        mode=err_item["mode"],
                        error=err_item["error"]
                    )
                
                # Запрашиваем исправление (аналогично валидации)
                if self.feedback_loop.can_revise_instruction():
                    feedback_dump = self.feedback_loop.get_feedback_for_orchestrator()
                    staging_text = feedback_dump.get("staging_errors", "Staging failed")
                    
                    current_instruction = await self._request_instruction_fix(
                        user_request=user_request,
                        history=history,
                        error=f"Staging errors:\n{staging_text}",
                        feedback=feedback_dump
                    )
                    # Очищаем
                    self.feedback_loop.feedback_handler.clear_feedback()
                    continue
                else:
                    return None            
            
            
            # ============================================================
            # STEP C: Technical Validation (syntax, imports, integration)
            # ============================================================
            self._update_status(PipelineStatus.VALIDATING, "Validating code...")
            self._notify_stage("VALIDATION", "Техническая валидация...", None)
            
            try:
                validation_result = await self._run_validation(include_tests=False)
                if vlog:
                    vlog.log_validation("TECHNICAL", validation_result)
            except Exception as e:
                if vlog:
                    vlog.log_error("VALIDATION", e)
                self._notify_stage("VALIDATION", f"❌ Ошибка: {e}", None)
                return None
            
            if self._on_validation:
                self._on_validation(validation_result.to_dict())
            
            self._notify_stage("VALIDATION", 
                "✅ Валидация пройдена" if validation_result.success else f"⚠️ Ошибок: {validation_result.error_count}",
                {"success": validation_result.success, "error_count": validation_result.error_count}
            )
            
            # ============================================================
            # STEP C.1: Auto-install missing dependencies
            # ============================================================
            if not validation_result.success:
                import_issues = [
                    issue for issue in validation_result.issues
                    if issue.level == ValidationLevel.IMPORTS
                    and "not found" in issue.message.lower()
                ]
                
                if import_issues:
                    self._notify_stage(
                        "DEPENDENCY", 
                        f"Обнаружено {len(import_issues)} проблем с импортами. Сканирование проекта...", 
                        None
                    )
                    
                    try:
                        # Scan ALL project files and install missing dependencies
                        install_result = self.dependency_manager.scan_and_install_all_dependencies()
                        
                        if install_result and install_result.successful > 0:
                            self._notify_stage(
                                "DEPENDENCY", 
                                f"✅ Установлено {install_result.successful} пакетов", 
                                {"installed": install_result.successful, "failed": install_result.failed}
                            )
                            # Re-validate after installation
                            validation_result = await self._run_validation(include_tests=False)
                            if self._on_validation:
                                self._on_validation(validation_result.to_dict())
                        elif install_result and install_result.failed > 0:
                            self._notify_stage(
                                "DEPENDENCY", 
                                f"⚠️ Не удалось установить {install_result.failed} пакетов", 
                                {"failed": install_result.failed}
                            )
                    except Exception as e:
                        logger.error(f"Dependency scan and install failed: {e}")
            
            # ============================================================
            # STEP C.2: Handle blocking validation errors
            # ============================================================
            syntax_errors = [
                issue for issue in validation_result.issues
                if issue.level == ValidationLevel.SYNTAX and issue.severity == IssueSeverity.ERROR
            ]
            
            if syntax_errors:
                self._notify_stage("VALIDATION", f"❌ {len(syntax_errors)} критических ошибок синтаксиса", None)
                
                if not self.feedback_loop.can_revise_instruction():
                    self._notify_stage("LOOP", "❌ Достигнут лимит попыток", None)
                    return None
                
                # Send errors to Orchestrator
                self.feedback_loop.add_validation_errors(validation_result.to_dict())
                current_instruction = await self._request_instruction_fix(
                    user_request=user_request,
                    history=history,
                    error=f"Syntax errors:\n" + "\n".join(e.message for e in syntax_errors[:5]),
                    feedback=self.feedback_loop.get_feedback_for_orchestrator(),
                )
                continue  # Retry from code generation
            
            # ============================================================
            # STEP D: AI Validation
            # ============================================================
            self._notify_stage("AI_VALIDATION", "AI Validator проверяет соответствие задаче...", None)
            
            try:
                ai_result = await self._run_ai_validation(
                    user_request=user_request,
                    instruction=current_instruction,
                    code_blocks=code_blocks,
                )
                if vlog:
                    vlog.log_ai_validation(ai_result)
            except Exception as e:
                if vlog:
                    vlog.log_error("AI_VALIDATION", e)
                self._notify_stage("AI_VALIDATION", f"⚠️ Ошибка AI Validator: {e}", None)
                # Continue without AI validation
                ai_result = None
            
            orchestrator_decision = None
            
            if ai_result:
                self._notify_stage("AI_VALIDATION", 
                    "✅ ОДОБРЕНО" if ai_result.approved else "❌ ОТКЛОНЕНО",
                    {"approved": ai_result.approved, "confidence": ai_result.confidence, "verdict": ai_result.verdict}
                )
                
                # === ПРОВЕРКА НА PARSE ERROR ===
                # Если валидатор не смог распарсить ответ — это технический сбой, не реальное отклонение
                if ai_result.parse_error:
                    logger.warning(
                        f"AI Validator had parse error: {ai_result.parse_error}. "
                        f"Skipping orchestrator decision, proceeding to tests."
                    )
                    self._notify_stage(
                        "AI_VALIDATION", 
                        "⚠️ Валидатор не смог сформировать ответ — переходим к тестам",
                        {
                            "parse_error": ai_result.parse_error,
                            "raw_response_preview": ai_result.raw_response[:200] if ai_result.raw_response else "",
                        }
                    )
                    # Пропускаем блок с Orchestrator decision, сразу идём к тестам
                    # ai_result.approved уже True из fallback
                
                elif not ai_result.approved:
                    # ============================================================
                    # STEP D.1: Handle AI rejection
                    # ============================================================
                    if not self.feedback_loop.can_revise_instruction():
                        self._notify_stage("LOOP", "❌ Достигнут лимит попыток", None)
                        # Return what we have - user will decide
                        return {
                            "code_blocks": code_blocks,
                            "validation_result": validation_result,
                            "ai_validation_result": ai_result,
                            "orchestrator_decision": None,
                        }
                    
                    # Add validator feedback
                    self.feedback_loop.add_validator_feedback(
                        approved=ai_result.approved,
                        confidence=ai_result.confidence,
                        verdict=ai_result.verdict,
                        critical_issues=ai_result.critical_issues,
                        model_used=ai_result.model_used,
                    )
                    
                    # Ask Orchestrator to decide
                    self._notify_stage("ORCHESTRATOR", 
                        f"Оркестратор анализирует критику (итерация {self.feedback_loop.orchestrator_revisions + 1})...", 
                        None
                    )
                    
                    decision_result = await self._get_orchestrator_decision(
                        user_request=user_request,
                        history=history,
                        ai_result=ai_result,
                    )
                    
                    orchestrator_decision = decision_result
                    
                    self._notify_stage("ORCHESTRATOR_DECISION", 
                        f"Решение: {orchestrator_decision.decision}",
                        {"decision": orchestrator_decision.decision, "reasoning": orchestrator_decision.reasoning}
                    )
                    
                    if orchestrator_decision.decision == "OVERRIDE":
                        # Orchestrator disagrees - proceed to tests
                        self._notify_stage("ORCHESTRATOR_DECISION", 
                            "Оркестратор НЕ согласен с валидатором — переходим к тестам", 
                            None
                        )
                        self.feedback_loop.validator_overrides_count += 1
                    else:
                        # ACCEPT - revise and retry
                        self._notify_stage("ORCHESTRATOR_DECISION", 
                            "Оркестратор согласен — генерируем исправленный код", 
                            None
                        )
                        
                        if orchestrator_decision.new_instruction:
                            current_instruction = orchestrator_decision.new_instruction
                        else:
                            current_instruction = await self._request_instruction_fix(
                                user_request=user_request,
                                history=history,
                                error=f"AI Validator rejected: {ai_result.verdict}",
                                feedback=self.feedback_loop.get_feedback_for_orchestrator(),
                            )
                        
                        self.feedback_loop.record_orchestrator_revision(
                            reason=f"AI Validator rejected: {ai_result.verdict}",
                            previous_instruction=self._pending_orchestrator_instruction,
                            new_instruction=current_instruction,
                        )
                        self._pending_orchestrator_instruction = current_instruction
                        
                        # Show new instruction
                        self._notify_stage("INSTRUCTION", 
                            f"Новая инструкция (итерация {self.feedback_loop.orchestrator_revisions})", 
                            {"instruction": current_instruction[:500]}
                        )
                        
                        continue  # Retry from code generation
            
            # ============================================================
            # STEP E: Run tests
            # ============================================================
            self._notify_stage("TESTS", "Запуск тестов проекта...", None)
            
            try:
                validation_with_tests = await self._run_validation(include_tests=True)
                if vlog:
                    vlog.log_validation("WITH_TESTS", validation_with_tests)
            except Exception as e:
                if vlog:
                    vlog.log_error("TESTS", e)
                self._notify_stage("TESTS", f"⚠️ Ошибка тестов: {e}", None)
                validation_with_tests = validation_result  # Use previous result
            
            if self._on_validation:
                self._on_validation(validation_with_tests.to_dict())
            
            test_issues = [i for i in validation_with_tests.issues if i.level == ValidationLevel.TESTS]
            
            if test_issues:
                self._notify_stage("TESTS", f"❌ {len(test_issues)} тестов провалено", {
                    "failed_tests": [i.message for i in test_issues[:5]],
                })
                
                # Check if we can retry
                if not self.feedback_loop.can_revise_instruction():
                    self._notify_stage("LOOP", "❌ Достигнут лимит попыток", None)
                    # Return with failed tests - user will decide
                    return {
                        "code_blocks": code_blocks,
                        "validation_result": validation_with_tests,
                        "ai_validation_result": ai_result,
                        "orchestrator_decision": orchestrator_decision,
                    }
                
                # Send test errors to Orchestrator
                test_error_messages = [i.message for i in test_issues[:5]]
                current_instruction = await self._request_instruction_fix(
                    user_request=user_request,
                    history=history,
                    error=f"Tests failed:\n" + "\n".join(test_error_messages),
                )
                
                self.feedback_loop.record_orchestrator_revision(
                    reason=f"Tests failed: {len(test_issues)} failures",
                    previous_instruction=self._pending_orchestrator_instruction,
                    new_instruction=current_instruction,
                )
                self._pending_orchestrator_instruction = current_instruction
                
                continue  # Retry from code generation
            else:
                self._notify_stage("TESTS", "✅ Все тесты прошли", None)
            
            # ============================================================
            # SUCCESS: All validations passed
            # ============================================================
            self._notify_stage("LOOP", f"✅ Все проверки пройдены (итерация {iteration})", None)
            
            return {
                "code_blocks": code_blocks,
                "validation_result": validation_with_tests,
                "ai_validation_result": ai_result,
                "orchestrator_decision": orchestrator_decision,
            }
        
        # Max iterations reached
        self._notify_stage("LOOP", f"❌ Достигнут лимит итераций ({max_iterations})", None)
        return None

    async def run_feedback_cycle(
        self,
        user_feedback: str,
        history: List[Dict[str, str]],
    ) -> PipelineResult:
        """
        Run a feedback cycle based on user critique.
        
        UPDATED: Now continues the main pipeline loop instead of running
        a separate validation loop. This ensures:
        - VFS is NOT cleared (staged files preserved)
        - Iteration counters are NOT reset
        - Same staging correction loop (3 attempts) is used
        - Full validation pipeline (technical, AI, tests) is applied
        
        Args:
            user_feedback: User's critique/feedback
            history: Conversation history
            
        Returns:
            PipelineResult with new pending_changes or errors
        """
        import time
        from app.utils.pipeline_trace_logger import PipelineTraceLogger
        
        start_time = time.time()
        
        logger.info(f"run_feedback_cycle: Starting cycle with user feedback (preserving VFS)")
        
        # Initialize validation logger
        vlog = get_validation_logger(self.current_session_id or "feedback-cycle")
        vlog.log_stage("USER_FEEDBACK", f"User feedback cycle started: {user_feedback[:100]}...", None)
        
        # === CRITICAL: Use existing feedback_loop, do NOT create new one ===
        if self.feedback_loop:
            self.feedback_loop.add_user_feedback(user_feedback, replaces_validator=True)
            logger.info(f"run_feedback_cycle: Using existing feedback_loop (revisions: {self.feedback_loop.orchestrator_revisions})")
        else:
            # Fallback: create new feedback loop only if none exists
            logger.warning("run_feedback_cycle: No existing feedback_loop, creating new one")
            self.feedback_loop = create_feedback_loop(
                session_id=self.current_session_id or f"feedback-{uuid.uuid4().hex[:8]}",
                user_request=self._pending_user_request,
                max_validator_retries=self.MAX_VALIDATION_RETRIES,
                max_orchestrator_revisions=self.MAX_FEEDBACK_ITERATIONS,
            )
            self.feedback_loop.add_user_feedback(user_feedback, replaces_validator=True)
        
        # === CRITICAL: Do NOT clear VFS — preserve staged files from previous iterations ===
        # self.vfs.discard_all()  # REMOVED
        # self._pending_changes = []  # REMOVED
        
        # Build enhanced history with user feedback
        working_history = history.copy()
        working_history.append({
            "role": "user",
            "content": f"""[USER FEEDBACK - MANDATORY]

{user_feedback}

⚠️ This is direct feedback from the user. You MUST address it.
Even if you disagree, attempt the user's request and explain your concerns.

Please analyze this feedback and provide a revised instruction.""",
        })
        
        # Initialize result
        result = PipelineResult(
            success=False,
            status=PipelineStatus.ANALYZING,
        )
        
        # === MAIN LOOP — same as process_request ===
        iteration = 0
        max_iterations = self.MAX_FEEDBACK_ITERATIONS
        
        while iteration < max_iterations:
            iteration += 1
            
            # Check if we can still revise
            if not self.feedback_loop.can_revise_instruction():
                logger.warning(f"run_feedback_cycle: Max revisions reached")
                result.status = PipelineStatus.FAILED
                result.errors.append("Max revision iterations reached")
                result.duration_ms = (time.time() - start_time) * 1000
                return result
            
            self._notify_stage("ITERATION", f"Итерация обратной связи {iteration}/{max_iterations}", {
                "iteration": iteration,
                "total_revisions": self.feedback_loop.orchestrator_revisions,
            })
            
            # ==============================================================
            # STEP 1: ORCHESTRATOR
            # ==============================================================
            self._update_status(PipelineStatus.ANALYZING, f"Processing user feedback (iteration {iteration})...")
            self._notify_stage("ORCHESTRATOR", "Оркестратор анализирует вашу критику...", None)
            
            try:
                orchestrator_result = await self._run_orchestrator(
                    user_request=self._pending_user_request,
                    history=working_history,
                    orchestrator_model=self._orchestrator_model,
                )
                vlog.log_orchestrator(self._pending_user_request, self._orchestrator_model or "auto", orchestrator_result)
            except Exception as e:
                vlog.log_error("ORCHESTRATOR", e)
                result.errors.append(f"Orchestrator error: {e}")
                result.status = PipelineStatus.FAILED
                result.duration_ms = (time.time() - start_time) * 1000
                return result
            
            result.analysis = orchestrator_result.analysis
            result.instruction = orchestrator_result.instruction
            self._pending_orchestrator_instruction = orchestrator_result.instruction
            
            # Check for empty instruction
            if not orchestrator_result.instruction.strip():
                logger.warning("run_feedback_cycle: Orchestrator returned empty instruction")
                working_history.append({
                    "role": "user",
                    "content": "[ERROR] Your response did not contain a proper instruction. Please provide a `## Instruction for Code Generator` section.",
                })
                continue
            
            # Record revision
            self.feedback_loop.record_orchestrator_revision(
                reason=f"User feedback: {user_feedback[:100]}...",
                previous_instruction=self._pending_orchestrator_instruction or "",
                new_instruction=orchestrator_result.instruction,
            )
            
            self._notify_stage("INSTRUCTION", "Новая инструкция от Оркестратора", {
                "instruction": orchestrator_result.instruction[:500],
            })
            
            # ==============================================================
            # STEP 2: CODE GENERATOR
            # ==============================================================
            self._update_status(PipelineStatus.GENERATING, "Generating code...")
            self._notify_stage("CODE_GEN", "Генерация кода...", None)
            
            try:
                code_blocks, raw_response = await self._run_code_generator(
                    instruction=orchestrator_result.instruction,
                    target_file=orchestrator_result.target_file,
                    target_files=orchestrator_result.target_files,
                )
                vlog.log_code_generation(orchestrator_result.instruction, code_blocks)
                self._current_generated_code = self._format_generated_code_for_context(code_blocks)
            except Exception as e:
                vlog.log_error("CODE_GENERATOR", e)
                result.errors.append(f"Code Generator error: {e}")
                result.status = PipelineStatus.FAILED
                result.duration_ms = (time.time() - start_time) * 1000
                return result
            
            if not code_blocks:
                staged_files = self.vfs.get_staged_files()
                if staged_files:
                    logger.info(f"Code Generator did not produce new code, but VFS has {len(staged_files)} staged files")
                    result.code_blocks = []
                else:
                    logger.warning("Code Generator did not produce code and VFS is empty")
                    working_history.append({
                        "role": "user",
                        "content": "[ERROR] Code Generator did not produce any code. Please provide a valid instruction.",
                    })
                    continue
            else:
                result.code_blocks = code_blocks
                self._notify_stage("CODE_GEN", f"Сгенерировано {len(code_blocks)} блок(ов) кода", {
                    "files": [b.file_path for b in code_blocks],
                })
            
            # ==============================================================
            # STEP 3: STAGING CORRECTION LOOP (3 attempts)
            # ==============================================================
            staging_attempt = 0
            MAX_STAGING_ATTEMPTS = 3
            apply_errors_data = []
            
            while staging_attempt < MAX_STAGING_ATTEMPTS:
                apply_errors_data = await self._stage_code_blocks(code_blocks)
                
                if not apply_errors_data:
                    break
                
                staging_attempt += 1
                
                self._notify_stage("STAGING", f"❌ Ошибки стейджинга (попытка {staging_attempt}/{MAX_STAGING_ATTEMPTS})", {
                    "errors": apply_errors_data,
                    "error_count": len(apply_errors_data),
                })
                
                if staging_attempt >= MAX_STAGING_ATTEMPTS:
                    break
                
                # Add staging errors to feedback
                for err_item in apply_errors_data:
                    self.feedback_loop.feedback_handler.add_staging_error(
                        file_path=err_item["file_path"],
                        mode=err_item["mode"],
                        error=err_item["error"],
                        error_type=err_item.get("error_type")
                    )
                
                feedback_dump = self.feedback_loop.get_feedback_for_orchestrator()
                staging_text = feedback_dump.get("staging_errors", "")
                self.feedback_loop.feedback_handler.clear_feedback()
                
                working_history.append({
                    "role": "user",
                    "content": f"[STAGING ERRORS - REVISE TARGETS]\n{staging_text}\n\nPlease revise your instruction to fix these targeting errors."
                })
                
                self._notify_stage("ORCHESTRATOR", "Оркестратор исправляет ошибки стейджинга...", None)
                
                try:
                    # Run Orchestrator for fix
                    orchestrator_result = await self._run_orchestrator(
                        user_request=self._pending_user_request,
                        history=working_history,
                        orchestrator_model=self._orchestrator_model,
                    )
                    
                    # Update pending instruction
                    self._pending_orchestrator_instruction = orchestrator_result.instruction
                    
                    # Add response to history
                    working_history.append({
                        "role": "assistant",
                        "content": orchestrator_result.raw_response
                    })
                    
                    if not orchestrator_result.instruction.strip():
                        logger.warning("Orchestrator returned empty instruction during staging fix")
                        break
                        
                    # 5. Generate new code
                    self._notify_stage("CODE_GEN", "Генерация исправленного кода...", None)
                    code_blocks, raw_response = await self._run_code_generator(
                        instruction=orchestrator_result.instruction,
                        target_file=orchestrator_result.target_file,
                        target_files=orchestrator_result.target_files,
                    )
                    
                    # Continue loop to stage new blocks
                    
                except Exception as e:
                    logger.error(f"Error in staging correction loop: {e}")
                    break

            if apply_errors_data:
                # 1. Логируем в консоль/уведомления
                error_msgs = [f"{e['file_path']}: {e['error']}" for e in apply_errors_data]
                result.errors.extend(error_msgs)
                
                self._notify_stage("STAGING", f"❌ Не удалось исправить ошибки стейджинга: {len(apply_errors_data)}", {
                    "errors": apply_errors_data,
                })
                
                # 2. Добавляем в FeedbackHandler (для следующей большой итерации)
                for err_item in apply_errors_data:
                    self.feedback_loop.feedback_handler.add_staging_error(
                        file_path=err_item["file_path"],
                        mode=err_item["mode"],
                        error=err_item["error"],
                        error_type=err_item.get("error_type")
                    )
                
                # 3. Формируем сообщение для истории
                feedback_dump = self.feedback_loop.get_feedback_for_orchestrator()
                staging_text = feedback_dump.get("staging_errors", "")
                
                working_history.append({
                    "role": "user",
                    "content": f"[CRITICAL ERRORS - STAGING FAILED]\n\n{staging_text}\n\nPlease revise your instruction.",
                })
                
                # 4. Записываем ревизию и идем на следующий круг (основной цикл)
                self.feedback_loop.record_orchestrator_revision(
                    reason=f"Staging failed after {staging_attempt} attempts: {len(apply_errors_data)} errors",
                    previous_instruction=self._pending_orchestrator_instruction or "",
                    new_instruction="[pending - next iteration]",
                )
                
                # Очищаем перед следующим циклом
                self.feedback_loop.feedback_handler.clear_feedback()
                
                continue
            
# ==============================================================
                # STEP 3.5: PRE-INSTALL REQUIREMENTS.TXT DEPENDENCIES
                # ==============================================================
                try:
                    req_install_result = self.dependency_manager.install_from_requirements()
                    if req_install_result.successful > 0:
                        self._notify_stage(
                            "DEPENDENCY",
                            f"✅ Установлено {req_install_result.successful} пакетов из requirements.txt",
                            {
                                "source": "requirements.txt",
                                "installed": req_install_result.successful,
                                "failed": req_install_result.failed,
                                "skipped": req_install_result.skipped,
                            }
                        )
                        logger.info(f"Pre-installed {req_install_result.successful} packages from requirements.txt")
                    elif req_install_result.failed > 0:
                        self._notify_stage(
                            "DEPENDENCY",
                            f"⚠️ Не удалось установить {req_install_result.failed} пакетов из requirements.txt",
                            {"failed": req_install_result.failed}
                        )
                except Exception as e:
                    logger.warning(f"Failed to pre-install from requirements.txt: {e}")
            
            # ==============================================================
            # STEP 4: TECHNICAL VALIDATION (without tests/runtime)
            # ==============================================================
            self._update_status(PipelineStatus.VALIDATING, "Validating code...")
            self._notify_stage("VALIDATION", "Техническая проверка кода...", {
                "levels": ["syntax", "imports", "integration"]
            })
            
            try:
                validation_result = await self._run_validation(include_tests=False)
                vlog.log_validation("TECHNICAL", validation_result)
            except Exception as e:
                vlog.log_error("VALIDATION", e)
                result.errors.append(f"Validation error: {e}")
                result.status = PipelineStatus.FAILED
                result.duration_ms = (time.time() - start_time) * 1000
                return result
            
            result.validation_result = validation_result.to_dict()
            
            if self._on_validation:
                self._on_validation(validation_result.to_dict())
            
            self._notify_stage("VALIDATION", "Результаты технической валидации", {
                "success": validation_result.success,
                "error_count": validation_result.error_count,
                "warning_count": validation_result.warning_count,
            })
            
            # ==============================================================
            # STEP 4.5: CHECK FOR BLOCKING ERRORS
            # ==============================================================
            if validation_result.error_count > 0:
                blocking_errors = [
                    issue for issue in validation_result.issues
                    if issue.severity == IssueSeverity.ERROR
                    and issue.level in (
                        ValidationLevel.SYNTAX, 
                        ValidationLevel.IMPORTS,
                        ValidationLevel.INTEGRATION,
                    )
                ]
                
                if blocking_errors:
                    self._notify_stage(
                        "VALIDATION", 
                        f"❌ {len(blocking_errors)} критических ошибок обнаружено", 
                        {
                            "blocking": True,
                            "error_count": len(blocking_errors),
                            "errors": [
                                {
                                    "file": e.file_path, 
                                    "line": e.line, 
                                    "message": e.message, 
                                    "level": e.level.value if hasattr(e.level, 'value') else str(e.level)
                                } 
                                for e in blocking_errors[:10]
                            ],
                        }
                    )
                    
                    # Include generated code in feedback
                    feedback_msg = self._format_errors_for_orchestrator(blocking_errors, code_blocks)
                    
                    working_history.append({
                        "role": "user",
                        "content": feedback_msg,
                    })
                    
                    # Записываем в feedback loop
                    self.feedback_loop.add_validation_errors(validation_result.to_dict())
                    self.feedback_loop.record_orchestrator_revision(
                        reason=f"Validation failed: {len(blocking_errors)} blocking errors",
                        previous_instruction=self._pending_orchestrator_instruction or "",
                        new_instruction="[pending - next iteration]",
                    )
                    
                    logger.warning(
                        f"Iteration {iteration}: Validation failed with {len(blocking_errors)} errors, "
                        f"returning to Orchestrator"
                    )
                    
                    # Возврат к Оркестратору — следующая итерация цикла
                    continue
            
            # ==============================================================
            # STEP 5: AI VALIDATION
            # ==============================================================
            self._notify_stage("AI_VALIDATION", "AI Validator проверяет соответствие задаче...", None)
            
            try:
                ai_result = await self._run_ai_validation(
                    user_request=self._pending_user_request,
                    instruction=orchestrator_result.instruction,
                    code_blocks=code_blocks,
                )
                vlog.log_ai_validation(ai_result)
            except Exception as e:
                vlog.log_error("AI_VALIDATION", e)
                result.errors.append(f"AI Validation error: {e}")
                result.status = PipelineStatus.FAILED
                result.duration_ms = (time.time() - start_time) * 1000
                return result
            
            result.ai_validation_result = ai_result.to_dict()
            
            self._notify_stage("AI_VALIDATION",
                "✅ ОДОБРЕНО" if ai_result.approved else "❌ ОТКЛОНЕНО",
                {
                    "approved": ai_result.approved,
                    "confidence": ai_result.confidence,
                    "verdict": ai_result.verdict,
                    "critical_issues": ai_result.critical_issues,
                }
            )
            
            # ==============================================================
            # STEP 5.5: ORCHESTRATOR DECISION (if AI rejected)
            # ==============================================================
            
            # === ПРОВЕРКА НА PARSE ERROR ===
            if ai_result.parse_error:
                logger.warning(
                    f"AI Validator had parse error: {ai_result.parse_error}. "
                    f"Skipping orchestrator decision, proceeding to tests."
                )
                self._notify_stage(
                    "AI_VALIDATION", 
                    "⚠️ Валидатор не смог сформировать ответ — переходим к тестам",
                    {
                        "parse_error": ai_result.parse_error,
                        "raw_response_preview": ai_result.raw_response[:200] if ai_result.raw_response else "",
                    }
                )
            
            elif not ai_result.approved:
                logger.info("AI Validator rejected code, asking Orchestrator for decision")
                self._notify_stage("FEEDBACK", "Отправка критики AI Validator Оркестратору...", None)
                
                # Orchestrator decides: ACCEPT (revise) or OVERRIDE (disagree)
                try:
                    orchestrator_decision = await self._get_orchestrator_decision_on_ai_feedback(
                        ai_result=ai_result,
                        code_blocks=code_blocks,
                        working_history=working_history,
                    )
                except Exception as e:
                    logger.error(f"Orchestrator decision error: {e}")
                    orchestrator_decision = OrchestratorFeedbackDecision(
                        decision="OVERRIDE",
                        reasoning=f"Error getting decision: {e}. Proceeding to tests.",
                    )
                
                result.orchestrator_decision = orchestrator_decision
                
                self._notify_stage("ORCHESTRATOR_DECISION",
                    f"Решение Оркестратора: {orchestrator_decision.decision}",
                    {
                        "decision": orchestrator_decision.decision,
                        "reasoning": orchestrator_decision.reasoning,
                    }
                )
                
                # === ACCEPT: Оркестратор СОГЛАСЕН с валидатором ===
                if orchestrator_decision.decision == "ACCEPT":
                    logger.info("Orchestrator accepts AI Validator feedback, looping back")
                    
                    # Include generated code in feedback
                    feedback_msg = self._format_ai_validator_feedback(ai_result, code_blocks)
                    working_history.append({
                        "role": "user",
                        "content": feedback_msg,
                    })
                    
                    self.feedback_loop.add_validator_feedback(
                        approved=ai_result.approved,
                        confidence=ai_result.confidence,
                        verdict=ai_result.verdict,
                        critical_issues=ai_result.critical_issues,
                        model_used=ai_result.model_used,
                    )
                    
                    # Continue to next iteration
                    continue
                
                # === OVERRIDE: Оркестратор НЕ СОГЛАСЕН с валидатором ===
                elif orchestrator_decision.decision == "OVERRIDE":
                    logger.info("Orchestrator wants to override AI Validator")
                    self.feedback_loop.validator_overrides_count += 1
            
            # ==============================================================
            # STEP 6: TESTS + RUNTIME
            # ==============================================================
            self._update_status(PipelineStatus.VALIDATING, "Running tests...")
            self._notify_stage("TESTS", "Запуск тестов и runtime проверок...", None)
            
            try:
                validation_with_tests = await self._run_validation(include_tests=True)
                vlog.log_validation("WITH_TESTS", validation_with_tests)
            except Exception as e:
                vlog.log_error("TESTS", e)
                result.errors.append(f"Tests error: {e}")
                result.status = PipelineStatus.FAILED
                result.duration_ms = (time.time() - start_time) * 1000
                return result
            
            result.validation_result = validation_with_tests.to_dict()
            
            if self._on_validation:
                self._on_validation(validation_with_tests.to_dict())
            
            # === NEW: Notify RUNTIME results separately ===
            runtime_skipped = getattr(validation_with_tests, 'runtime_files_skipped', 0)
            if runtime_skipped == 0 and validation_with_tests.runtime_test_summary:
                results_list = validation_with_tests.runtime_test_summary.get("results", [])
                runtime_skipped = sum(1 for r in results_list if r.get("status") == "skipped")
            
            # Build failures list for display
            runtime_failures = []
            if validation_with_tests.runtime_test_summary:
                for r in validation_with_tests.runtime_test_summary.get("results", []):
                    if r.get("status") in ("failed", "error", "timeout"):
                        runtime_failures.append({
                            "file_path": r.get("file_path", "?"),
                            "message": r.get("message", "")[:100],
                            "status": r.get("status"),
                        })
            
            self._notify_stage("RUNTIME", "Результаты runtime тестирования", {
                "files_checked": validation_with_tests.runtime_files_checked,
                "files_passed": validation_with_tests.runtime_files_passed,
                "files_failed": validation_with_tests.runtime_files_failed,
                "files_skipped": runtime_skipped,
                "success": validation_with_tests.runtime_files_failed == 0,
                "failures": runtime_failures[:5],
                "skipped_reasons": _count_skipped_reasons(validation_with_tests.runtime_test_summary),
            })
            
            # ==============================================================
            # STEP 6.5: CHECK TEST AND RUNTIME RESULTS
            # ==============================================================
            test_errors = [
                i for i in validation_with_tests.issues 
                if i.level == ValidationLevel.TESTS and i.severity == IssueSeverity.ERROR
            ]
            runtime_errors = [
                i for i in validation_with_tests.issues 
                if i.level == ValidationLevel.RUNTIME and i.severity == IssueSeverity.ERROR
            ]
            
            all_blocking_errors = test_errors + runtime_errors
            
            tests_expected_but_not_run = (
                validation_with_tests.tests_run == 0 
                and len(getattr(validation_with_tests, 'test_files_found', [])) > 0
            )
            
            if all_blocking_errors or tests_expected_but_not_run:
                error_details = []
                
                if test_errors:
                    error_details.append(f"{len(test_errors)} test failures")
                if runtime_errors:
                    error_details.append(f"{len(runtime_errors)} runtime errors")
                if tests_expected_but_not_run:
                    error_details.append("tests found but not executed")
                
                self._notify_stage(
                    "TESTS", 
                    f"❌ Ошибки: {', '.join(error_details)}", 
                    {
                        "success": False,
                        "test_failures": len(test_errors),
                        "runtime_errors": len(runtime_errors),
                        "tests_run": validation_with_tests.tests_run,
                        "tests_passed": validation_with_tests.tests_passed,
                        "tests_failed": validation_with_tests.tests_failed,
                        "test_files_found": len(getattr(validation_with_tests, 'test_files_found', [])),
                        "runtime_files_checked": validation_with_tests.runtime_files_checked,
                        "runtime_files_passed": validation_with_tests.runtime_files_passed,
                        "runtime_files_failed": validation_with_tests.runtime_files_failed,
                        "runtime_failures": runtime_failures[:5],
                    }
                )
                
                # Include generated code in test feedback
                feedback_msg = self._format_test_errors_for_orchestrator(
                    test_errors=test_errors,
                    runtime_errors=runtime_errors,
                    validation_result=validation_with_tests,
                    code_blocks=code_blocks,
                )
                
                working_history.append({
                    "role": "user",
                    "content": feedback_msg,
                })
                
                self.feedback_loop.add_test_failures({
                    "test_errors": [e.to_dict() for e in test_errors],
                    "runtime_errors": [e.to_dict() for e in runtime_errors],
                    "tests_run": validation_with_tests.tests_run,
                    "tests_passed": validation_with_tests.tests_passed,
                    "tests_failed": validation_with_tests.tests_failed,
                    "runtime_checked": validation_with_tests.runtime_files_checked,
                    "runtime_passed": validation_with_tests.runtime_files_passed,
                    "runtime_failed": validation_with_tests.runtime_files_failed,
                })
                
                self.feedback_loop.record_orchestrator_revision(
                    reason=f"Tests/Runtime failed: {len(test_errors)} test errors, {len(runtime_errors)} runtime errors",
                    previous_instruction=self._pending_orchestrator_instruction,
                    new_instruction="[pending - next iteration]",
                )
                
                logger.warning(
                    f"Iteration {iteration}: Tests/Runtime failed, returning to Orchestrator. "
                    f"Test errors: {len(test_errors)}, Runtime errors: {len(runtime_errors)}"
                )
                
                continue
            
            # === SUCCESS — тесты прошли ===
            self._notify_stage(
                "TESTS", 
                f"✅ Тесты пройдены", 
                {
                    "success": True,
                    "tests_run": validation_with_tests.tests_run,
                    "tests_passed": validation_with_tests.tests_passed,
                    "tests_failed": validation_with_tests.tests_failed,
                    "test_files_found": len(getattr(validation_with_tests, 'test_files_found', [])),
                    "runtime_files_checked": validation_with_tests.runtime_files_checked,
                    "runtime_files_passed": validation_with_tests.runtime_files_passed,
                    "runtime_files_failed": validation_with_tests.runtime_files_failed,
                    "runtime_files_skipped": runtime_skipped,
                }
            )
            
            logger.info(
                f"Iteration {iteration}: All tests passed "
                f"(pytest: {validation_with_tests.tests_passed}/{validation_with_tests.tests_run}, "
                f"runtime: {validation_with_tests.runtime_files_passed}/{validation_with_tests.runtime_files_checked})"
            )
            
            # ==============================================================
            # STEP 7: SUCCESS - PREPARE FOR USER CONFIRMATION
            # ==============================================================
            self._update_status(PipelineStatus.AWAITING_CONFIRMATION, "Ready for your review")
            
            self._pending_changes = await self._build_pending_changes(
                code_blocks=result.code_blocks,
                validation_passed=validation_with_tests.success,
                ai_validation_passed=ai_result.approved if ai_result else True,
            )
            
            result.pending_changes = self._pending_changes
            result.diffs = self.vfs.get_all_diffs()
            result.success = True
            result.feedback_iterations = iteration
            
            # Build runtime summary for COMPLETE stage
            runtime_summary_for_complete = {
                "checked": validation_with_tests.runtime_files_checked,
                "passed": validation_with_tests.runtime_files_passed,
                "failed": validation_with_tests.runtime_files_failed,
                "skipped": runtime_skipped,
            }
            
            tests_summary_for_complete = {
                "run": validation_with_tests.tests_run,
                "passed": validation_with_tests.tests_passed,
                "failed": validation_with_tests.tests_failed,
            }
            
            self._notify_stage("COMPLETE", f"✅ Готово! {len(self._pending_changes)} файл(ов) к изменению", {
                "files": [c.file_path for c in self._pending_changes],
                "iterations": iteration,
                "duration_ms": (time.time() - start_time) * 1000,
                "runtime_summary": runtime_summary_for_complete,
                "tests_summary": tests_summary_for_complete,
            })
            
            result.duration_ms = (time.time() - start_time) * 1000
            vlog.log_complete(result.success, result.duration_ms)
            
            return result
        
        # ==============================================================
        # MAX ITERATIONS REACHED (outside while loop)
        # ==============================================================
        result.status = PipelineStatus.FAILED
        result.errors.append(f"Max iterations ({self.MAX_FEEDBACK_ITERATIONS}) reached without success")
        result.duration_ms = (time.time() - start_time) * 1000
        vlog.log_complete(False, result.duration_ms)
        
        return result


    async def _request_instruction_fix(
        self,
        user_request: str,
        history: List[Dict[str, str]],
        error: str,
        feedback: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Request Orchestrator to fix instruction based on error.
        
        Returns:
            New instruction
        """
        enhanced_history = history.copy()
        
        # Add error as feedback
        feedback_text = f"[ERROR - MUST FIX]\n\n{error}"
        
        if feedback:
            if feedback.get("validator_feedback"):
                feedback_text += f"\n\n[VALIDATOR FEEDBACK]\n{feedback['validator_feedback']}"
            if feedback.get("validation_errors"):
                feedback_text += f"\n\n[VALIDATION ERRORS]\n{feedback['validation_errors']}"
        
        enhanced_history.append({
            "role": "user",
            "content": feedback_text + "\n\nPlease provide a revised instruction to fix these issues.",
        })
        
        # Re-run orchestrator
        orchestrator_result = await self._run_orchestrator(
            user_request=user_request,
            history=enhanced_history,
            orchestrator_model=self._orchestrator_model,
        )
        
        return orchestrator_result.instruction

    async def _get_orchestrator_decision(
        self,
        user_request: str,
        history: List[Dict[str, str]],
        ai_result: 'AIValidationResult',
    ) -> OrchestratorFeedbackDecision:
        """
        Ask Orchestrator to decide on AI Validator feedback.
        
        Returns:
            OrchestratorFeedbackDecision with ACCEPT or OVERRIDE
        """
        feedback = self.feedback_loop.get_feedback_for_orchestrator()
        
        enhanced_history = history.copy()
        enhanced_history.append({
            "role": "user",
            "content": f"""[VALIDATOR FEEDBACK]

{feedback.get('validator_feedback', ai_result.verdict)}

Please analyze this feedback and decide:
1. If you AGREE with the validator's critique → respond with **My decision:** ACCEPT and provide revised instruction
2. If you DISAGREE with the validator → respond with **My decision:** OVERRIDE and explain why the code is actually correct

Remember: You can override the validator if you believe the critique is incorrect.""",
        })
        
        orchestrator_result = await self._run_orchestrator(
            user_request=user_request,
            history=enhanced_history,
            orchestrator_model=self._orchestrator_model,
        )
        
        return self._parse_orchestrator_feedback_decision(orchestrator_result.raw_response)    
    
    
    def _notify_stage(self, stage: str, message: str, details: Optional[Dict[str, Any]] = None):
        """NEW: Notify about pipeline stage."""
        logger.info(f"[PIPELINE:{stage}] {message}")
        if self._on_stage:
            try:
                self._on_stage(stage, message, details)
            except Exception as e:
                logger.warning(f"Stage callback error: {e}")
    
    def _dict_to_validation_result(self, data: Dict[str, Any]) -> ValidationResult:
        """Convert dict to ValidationResult."""
        from app.services.change_validator import ValidationResult, ValidationLevel
        
        result = ValidationResult(
            success=data.get("success", False),
            checked_files=data.get("checked_files", []),
        )
        result.levels_passed = [ValidationLevel(l) for l in data.get("levels_passed", [])]
        result.levels_failed = [ValidationLevel(l) for l in data.get("levels_failed", [])]
        return result
                        
    # ========================================================================
    # APPLY / DISCARD
    # ========================================================================
    
    async def apply_pending_changes(self) -> ApplyResult:
        """
        Apply all pending changes to real files.
        
        This is called AFTER user confirmation.
        Creates backup before applying and records full change history.
        
        Returns:
            ApplyResult with success status and file lists
        """
        logger.info(f"apply_pending_changes: starting, pending_changes={len(self._pending_changes)}")
        logger.info(f"apply_pending_changes: VFS has_pending={self.vfs.has_pending_changes()}, staged={self.vfs.get_staged_files()}")
        
        # Проверяем наличие изменений
        if not self._pending_changes and not self.vfs.has_pending_changes():
            logger.warning("apply_pending_changes: No pending changes!")
            return ApplyResult(
                success=False,
                applied_files=[],
                created_files=[],
                errors=["No pending changes to apply"],
            )
        
        self._update_status(PipelineStatus.APPLYING, "Applying changes...")
        
        # === СОХРАНЯЕМ ОРИГИНАЛЬНОЕ СОДЕРЖИМОЕ ДО COMMIT ===
        # Это важно, потому что после commit_all файлы уже изменены
        original_contents: Dict[str, Optional[str]] = {}
        for change in self._pending_changes:
            original_contents[change.file_path] = self.vfs.read_file_original(change.file_path)
        
        # === СОЗДАЁМ СЕССИЮ БЭКАПА С ОПИСАНИЕМ ===
        backup_session_id = None
        if self.backup_manager:
            files_to_change = [c.file_path for c in self._pending_changes]
            description = f"Agent Mode: {len(files_to_change)} files"
            if self._pending_user_request:
                request_preview = self._pending_user_request[:50].replace('\n', ' ')
                description = f"{request_preview}... ({len(files_to_change)} files)"
            
            session = self.backup_manager.start_session(description=description)
            backup_session_id = session.session_id
            logger.info(f"apply_pending_changes: Started backup session {backup_session_id}")
        
        try:
            logger.info(f"apply_pending_changes: Calling vfs.commit_all")
            
            # Commit VFS changes with backup
            commit_result = await self.vfs.commit_all(
                backup_manager=self.backup_manager,
                history_manager=None,  # Мы сами запишем в историю с полными данными
                thread_id=self.thread_id,
                session_id=self.current_session_id,
            )
            
            logger.info(
                f"apply_pending_changes: VFS commit result: "
                f"success={commit_result.success}, "
                f"applied={commit_result.applied_files}, "
                f"created={commit_result.created_files}, "
                f"backups={list(commit_result.backups.keys())}, "
                f"errors={commit_result.errors}"
            )
            
            if commit_result.success:
                self._update_status(PipelineStatus.COMPLETED, "Changes applied!")
                
                applied_files = list(commit_result.applied_files)
                created_files = list(commit_result.created_files)
                
                # === ЗАПИСЬ В ИСТОРИЮ С ПОЛНЫМИ ДАННЫМИ ===
                change_record_ids = []
                if self.history_manager and self.thread_id:
                    for change in self._pending_changes:
                        try:
                            # Получаем оригинальное содержимое (сохранили до commit)
                            original = original_contents.get(change.file_path)
                            
                            # Получаем путь к бэкапу
                            backup_path = commit_result.backups.get(change.file_path)
                            
                            # Определяем тип изменения
                            if hasattr(change.code_block, 'mode'):
                                change_type = change.code_block.mode.lower()
                                # Нормализуем тип для БД
                                if change_type in ('replace_file', 'create_file'):
                                    change_type = 'create' if original is None else 'modify'
                                elif change_type not in ('create', 'modify', 'delete'):
                                    change_type = 'modify'
                            else:
                                change_type = 'create' if original is None else 'modify'
                            
                            # Получаем новое содержимое
                            new_content = ""
                            if hasattr(change.code_block, 'code'):
                                new_content = change.code_block.code
                            
                            # Получаем статистику строк
                            lines_added = 0
                            lines_removed = 0
                            if change.modify_result:
                                lines_added = getattr(change.modify_result, 'lines_added', 0)
                                lines_removed = getattr(change.modify_result, 'lines_removed', 0)
                            
                            change_record = await self.history_manager.record_file_change(
                                thread_id=self.thread_id,
                                session_id=self.current_session_id or backup_session_id or "unknown",
                                file_path=change.file_path,
                                change_type=change_type,
                                original_content=original,
                                new_content=new_content,
                                backup_path=backup_path,
                                lines_added=lines_added,
                                lines_removed=lines_removed,
                                validation_passed=change.validation_passed,
                                metadata={
                                    "mode": change.code_block.mode if hasattr(change.code_block, 'mode') else None,
                                    "ai_validation_passed": change.ai_validation_passed,
                                    "user_request": self._pending_user_request[:200] if self._pending_user_request else None,
                                }
                            )
                            change_record_ids.append(change_record.id)
                            logger.debug(f"Recorded change: {change.file_path} -> {change_record.id}")
                            
                        except Exception as e:
                            logger.warning(f"Failed to record change in history for {change.file_path}: {e}")
                    
                    # Отмечаем все изменения как применённые
                    if change_record_ids:
                        await self.history_manager.mark_changes_applied(
                            change_record_ids, 
                            user_confirmed=True
                        )
                        logger.info(f"Marked {len(change_record_ids)} changes as applied")
                
                # Очищаем pending changes
                self._pending_changes = []
                
                logger.info(
                    f"apply_pending_changes: SUCCESS - "
                    f"applied {len(applied_files)}, created {len(created_files)}, "
                    f"recorded {len(change_record_ids)} changes in history"
                )
                
                return ApplyResult(
                    success=True,
                    applied_files=applied_files,
                    created_files=created_files,
                    errors=[],
                    backup_session_id=backup_session_id,
                )
            
            
            else:
                logger.error(f"apply_pending_changes: VFS commit FAILED: {commit_result.errors}")
                return ApplyResult(
                    success=False,
                    applied_files=[],
                    created_files=[],
                    errors=commit_result.errors,
                )
                
        except Exception as e:
            logger.error(f"apply_pending_changes: Exception: {e}", exc_info=True)
            return ApplyResult(
                success=False,
                applied_files=[],
                created_files=[],
                errors=[str(e)],
            )
        
        finally:
            # === ГАРАНТИРОВАННО ЗАВЕРШАЕМ СЕССИЮ БЭКАПА ===
            if self.backup_manager:
                session = self.backup_manager.end_session()
                if session:
                    logger.info(
                        f"apply_pending_changes: Ended backup session {session.session_id} "
                        f"with {len(session.backups)} backups"
                    )
          
          
                                    
    async def discard_pending_changes(self):
        """Discard all pending changes without applying"""
        self.vfs.discard_all()
        self._pending_changes = []
        self._update_status(PipelineStatus.CANCELLED, "Changes discarded")
        logger.info("Pending changes discarded")
    
    # ========================================================================
    # INTERNAL: ORCHESTRATOR
    # ========================================================================
    
    async def _run_orchestrator(
        self,
        user_request: str,
        history: List[Dict[str, str]],
        orchestrator_model: Optional[str] = None,
    ) -> OrchestratorResult:
        """
        Run Orchestrator to analyze request and generate instruction.
        
        Args:
            user_request: User's request text
            history: Conversation history
            orchestrator_model: Pre-selected model (если None, используется роутер)
        """
        from app.agents.router import route_request
        
        # Определяем модель: либо переданную, либо через роутер
        if orchestrator_model:
            model = orchestrator_model
            logger.info(f"Pipeline: Using pre-selected model {cfg.get_model_display_name(model)}")
        else:
            routing = await route_request(user_request, self.project_index)
            model = routing.orchestrator_model
            logger.info(f"Pipeline: Router selected {cfg.get_model_display_name(model)} "
                       f"(complexity: {routing.complexity_level})")
        
        # Pre-filter chunks
        pre_filter_result = await pre_filter_chunks(
            user_query=user_request,
            index=self.project_index,
            project_dir=self.project_dir,
            orchestrator_model=model,
        )
        
        # === ИСПРАВЛЕНИЕ: Загружаем compact_index.md вместо генерации JSON ===
        compact_index = _load_compact_index_md(self.project_dir)
        
        # Project map
        project_map = get_project_map_for_prompt(self.project_dir)
        
        # DEBUG: логируем размеры контекста
        compact_tokens = len(compact_index) // 4
        map_tokens = len(project_map) // 4
        logger.info(f"Context sizes: compact_index={compact_tokens} tokens, project_map={map_tokens} tokens")
        
        # [FIXED] Синхронный tool executor с callback-уведомлениями
        def tool_executor_with_callbacks(name: str, args: Dict) -> str:
            """
            Синхронный executor для инструментов.
            orchestrate_agent вызывает его синхронно, поэтому async здесь не нужен.
            
            UPDATED: Passes VFS to executor so read_file returns staged content.
            """
            from app.tools.tool_executor import ToolExecutor
            executor = ToolExecutor(
                project_dir=self.project_dir, 
                index=self.project_index,
                virtual_fs=self.vfs,  # NEW: Pass VFS for staged file access
            )
            result = executor.execute(name, args)
            
            # Уведомляем callback о вызове инструмента
            success = not result.startswith("<!-- ERROR")
            if self._on_tool_call:
                self._on_tool_call(name, args, result[:500], success)
            
            return result
        
        # Run orchestrator
        result = await orchestrate_agent(
            user_query=user_request,
            selected_chunks=pre_filter_result.selected_chunks,
            compact_index=compact_index,
            history=history,
            orchestrator_model=model,
            project_dir=self.project_dir,
            index=self.project_index,
            project_map=project_map,
            tool_executor=tool_executor_with_callbacks,
        )
        
        # Report thinking
        for tc in result.tool_calls:
            if tc.thinking and self._on_thinking:
                self._on_thinking(tc.thinking)
        
        return result
    
    
    
    # ========================================================================
    # INTERNAL: CODE GENERATOR
    # ========================================================================
    
    async def _run_code_generator(
        self,
        instruction: str,
        target_file: Optional[str] = None,
        target_files: Optional[List[str]] = None,
    ) -> tuple[List[ParsedCodeBlock], str]:
        """
        Run Code Generator to produce code blocks.
        
        Now supports multiple target files for multi-file changes.
        Uses configured generator model if set.
        
        UPDATED: Also includes all staged files from VFS so Generator can see
        files created in previous iterations.
        """
        file_contents: Dict[str, str] = {}
        
        # Build list of files to read
        files_to_read: List[str] = []
        
        # 1. From explicit target_files (NEW)
        if target_files:
            files_to_read.extend(target_files)
        
        # 2. From single target_file (legacy compatibility)
        if target_file and target_file not in files_to_read:
            files_to_read.append(target_file)
        
        # 3. Extract additional files from instruction text (NEW)
        extracted = self._extract_files_from_instruction(instruction)
        for f in extracted:
            if f not in files_to_read:
                files_to_read.append(f)
        
        # 4. NEW: Add all staged files from VFS (created/modified in previous iterations)
        # This ensures Generator sees files it created before when fixing errors
        staged_files = self.vfs.get_staged_files()
        for staged_file in staged_files:
            if staged_file not in files_to_read:
                files_to_read.append(staged_file)
                logger.debug(f"Code Generator: added staged file {staged_file} to context")
        
        # Read all files
        for file_path in files_to_read:
            content = self.vfs.read_file(file_path)
            if content:
                file_contents[file_path] = content
                logger.debug(f"Code Generator: loaded {file_path} ({len(content)} chars)")
            else:
                # File doesn't exist - may be new file
                logger.debug(f"Code Generator: {file_path} not found (may be new file)")
        
        logger.info(f"Code Generator: {len(file_contents)} file(s) in context: {list(file_contents.keys())}")
        
        # Generate code with specified model
        blocks, raw_response = await generate_code_agent_mode(
            instruction=instruction,
            file_contents=file_contents,
            model=self._generator_model,  # NEW: передаём модель
        )
        
        logger.info(f"Code Generator: produced {len(blocks)} code block(s) for files: {[b.file_path for b in blocks]}")
        
        return blocks, raw_response



    def _extract_files_from_instruction(self, instruction: str) -> List[str]:
        """
        Extract file paths mentioned in instruction.
        
        Parses patterns like:
        - FILE: path/to/file.py
        - `path/to/file.py`
        - modify path/to/file.py
        """
        import re
        
        files: List[str] = []
        seen: set = set()
        
        patterns = [
            r'FILE:\s*[`"]?([^`"\n\s]+\.py)[`"]?',
            r'`([^`\n]+\.py)`',
            r'(?:modify|create|update|edit|change|in|to)\s+[`"]?([a-zA-Z0-9_/\\]+\.py)[`"]?',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, instruction, re.IGNORECASE):
                path = match.group(1).strip()
                if path not in seen:
                    seen.add(path)
                    files.append(path)
        
        return files


    
    # ========================================================================
    # INTERNAL: STAGING
    # ========================================================================
    
    async def _stage_code_blocks(self, code_blocks: List[ParsedCodeBlock]) -> List[Dict[str, Any]]:
        """
        Stage code blocks to VFS with atomic rollback on failure.
        
        IMPROVED: After each block is applied, validates the file structure using tree-sitter.
        If the structure is broken:
        1. Remove all tabs from the code block
        2. Re-apply the block
        3. Try to fix indentation using tree-sitter context analysis
        4. If still broken, reject the block but continue with others
        """
        errors = []
        applied_backups = []  # List of (file_path, original_change_obj, backup_content)
        
        # Lazy import tree-sitter parser
        from app.services.tree_sitter_parser import FaultTolerantParser
        try:
            ts_parser = FaultTolerantParser()
        except Exception:
            ts_parser = None
        
        for block in code_blocks:
            try:
                # 1. Backup state (store the PendingChange object or None)
                current_change = self.vfs.get_change(block.file_path)
                backup_content = self.vfs.read_file(block.file_path)
                applied_backups.append((block.file_path, current_change, backup_content))
                
                # 2. Read content (from VFS or empty if new)
                existing_content = self.vfs.read_file(block.file_path) or ""
                
                # 3. Pre-process code block: remove all tabs
                original_block_code = block.code
                if block.code and '\t' in block.code:
                    block.code = block.code.replace('\t', '    ')
                    logger.debug(f"Removed tabs from code block for {block.file_path}")
                
                # 4. Apply modification
                result = self.file_modifier.apply_code_block(existing_content, block)
                
                if result.success and result.new_content:
                    # === TREE-SITTER VALIDATION AFTER APPLY ===
                    structure_valid = True
                    repair_attempted = False
                    final_content = result.new_content
                    
                    if ts_parser and block.file_path.endswith('.py'):
                        try:
                            # Check if tree structure is broken
                            is_broken, error_details = self._check_tree_structure_broken(
                                ts_parser, result.new_content, block
                            )
                            
                            if is_broken:
                                structure_valid = False
                                repair_attempted = True
                                logger.warning(f"Tree-sitter detected structural errors in {block.file_path}: {error_details[:2]}")
                                
                                # === ATTEMPT REPAIR: tree-sitter context-aware fix ===
                                repaired_content = self._attempt_structure_repair_with_context(
                                    existing_content, block, ts_parser
                                )
                                
                                if repaired_content:
                                    # Verify repair worked
                                    is_still_broken, _ = self._check_tree_structure_broken(
                                        ts_parser, repaired_content, block
                                    )
                                    
                                    if not is_still_broken:
                                        logger.info(f"Structure repair succeeded for {block.file_path}")
                                        final_content = repaired_content
                                        structure_valid = True
                                    else:
                                        logger.warning(f"Structure repair failed for {block.file_path}")
                                else:
                                    logger.warning(f"Structure repair returned None for {block.file_path}")
                                        
                        except Exception as e:
                            logger.debug(f"Tree-sitter validation skipped: {e}")
                    
                    if structure_valid:
                        # Stage the change
                        self.vfs.stage_change(block.file_path, final_content)
                        if repair_attempted:
                            logger.info(f"Staged (after repair): {block.file_path} ({block.mode})")
                        else:
                            logger.info(f"Staged: {block.file_path} ({block.mode})")
                    else:
                        # Structure broken and repair failed - reject this block
                        from app.agents.feedback_handler import StagingErrorType
                        
                        # Restore original block code
                        block.code = original_block_code
                        
                        error_dict = block.to_dict()
                        error_dict.update({
                            "error": f"Code block breaks file structure. Tree-sitter detected critical errors that could not be repaired.",
                            "error_type": StagingErrorType.SYNTAX_VALIDATION_FAILED,
                            "code_preview": block.code[:100] if block.code else None,
                            "full_code": block.code,
                        })
                        errors.append(error_dict)
                        
                        # Restore backup for this file
                        if backup_content is not None:
                            self.vfs.stage_change(block.file_path, backup_content)
                        else:
                            self.vfs.unstage(block.file_path)
                        
                        logger.warning(f"Rejected block for {block.file_path}: structure validation failed")
                        # Continue to next block - don't stop processing
                        continue
                        
                elif result.success:
                    # Success but no new_content (edge case)
                    self.vfs.stage_change(block.file_path, result.new_content or existing_content)
                    logger.info(f"Staged: {block.file_path} ({block.mode})")
                else:
                    # Apply failed
                    # Restore original block code
                    block.code = original_block_code
                    
                    error_dict = block.to_dict()
                    error_dict.update({
                        "error": result.message,
                        "error_type": getattr(result, "error_type", None),
                        "code_preview": block.code[:100] if block.code else None,
                        "full_code": block.code,
                    })
                    
                    errors.append(error_dict)
                    
                    logger.warning(
                        f"Failed to apply block to {block.file_path}: {result.message} | "
                        f"mode={block.mode}, target_class={block.target_class}, "
                        f"target_method={block.target_method}, target_function={block.target_function}"
                    )
                    
            except Exception as e:
                error_dict = block.to_dict()
                error_dict.update({
                    "error": str(e),
                    "error_type": None,
                    "code_preview": block.code[:100] if block.code else None,
                    "full_code": block.code,
                })
                
                errors.append(error_dict)
                logger.error(f"Error staging {block.file_path}: {e}")
        
        # Note: We no longer do full rollback on errors - each block is handled independently
        # This allows successful blocks to be staged even if some fail
                    
        return errors
    
    def _attempt_structure_repair_with_context(
        self, 
        existing_content: str, 
        block: 'ParsedCodeBlock',
        ts_parser: 'FaultTolerantParser'
    ) -> Optional[str]:
        """
        Attempts to repair Python file structure using tree-sitter context analysis.
        
        Process:
        1. Remove all tabs from the code block
        2. Determine the correct indentation from the target context
        3. Re-indent the code block to match the context
        4. Apply the block and validate with tree-sitter
        5. If still broken, try autopep8 as fallback
        
        Args:
            existing_content: Original file content before the block was applied
            block: The code block being inserted
            ts_parser: Tree-sitter parser instance
            
        Returns:
            Repaired content or None if repair failed
        """
        import subprocess
        
        if not block.code or not block.code.strip():
            return None
        
        # Determine Python path
        python_path = getattr(self, '_project_python_path', None) or 'python'
        
        # === STEP 1: Remove all tabs from code block ===
        clean_code = block.code.replace('\t', '    ')
        
        # === STEP 2: Determine target indentation from context ===
        target_indent = self._determine_target_indent(existing_content, block, ts_parser)
        
        # === STEP 3: Re-indent the code block ===
        reindented_code = self._reindent_code_block(clean_code, target_indent)
        
        # === STEP 4: Create a modified block and apply ===
        from app.services.file_modifier import ParsedCodeBlock
        
        modified_block = ParsedCodeBlock(
            file_path=block.file_path,
            mode=block.mode,
            code=reindented_code,
            target_class=block.target_class,
            target_method=block.target_method,
            target_function=block.target_function,
            insert_after=block.insert_after,
            insert_before=block.insert_before,
        )
        
        result = self.file_modifier.apply_code_block(existing_content, modified_block)
        
        if result.success and result.new_content:
            # Validate with tree-sitter
            is_broken, _ = self._check_tree_structure_broken(ts_parser, result.new_content, block)
            
            if not is_broken:
                logger.debug("Structure repair via re-indentation succeeded")
                return result.new_content
        
        # === STEP 5: Fallback - try autopep8 on the whole file ===
        if result.success and result.new_content:
            try:
                indent_codes = 'E101,E111,E112,E113,E114,E115,E116,E117,E121,E122,E123,E124,E125,E126,E127,E128,E129,E131,W191'
                
                autopep8_result = subprocess.run(
                    [python_path, '-m', 'autopep8', '--select=' + indent_codes, '-'],
                    input=result.new_content,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if autopep8_result.returncode == 0 and autopep8_result.stdout:
                    # Validate the autopep8 result
                    is_broken, _ = self._check_tree_structure_broken(ts_parser, autopep8_result.stdout, block)
                    
                    if not is_broken:
                        logger.debug("Structure repair via autopep8 succeeded")
                        return autopep8_result.stdout
                        
            except subprocess.TimeoutExpired:
                logger.debug("autopep8 timed out during structure repair")
            except Exception as e:
                logger.debug(f"autopep8 failed during structure repair: {e}")
        
        return None
    
    def _check_tree_structure_broken(
        self, 
        ts_parser: 'FaultTolerantParser', 
        content: str, 
        block: 'ParsedCodeBlock'
    ) -> tuple[bool, list]:
        """
        Check if the tree structure is broken in a way that would prevent
        subsequent code blocks from being inserted.
        
        Checks:
        1. Tree-sitter reports critical errors (ERROR nodes)
        2. Target class is no longer parseable
        3. Target method/function is no longer parseable
        4. Methods inside target class have errors
        
        Returns:
            (is_broken, error_details)
        """
        error_details = []
        
        try:
            parse_result = ts_parser.parse(content)
        except Exception as e:
            return True, [f"Parse failed: {e}"]
        
        # Check 1: Critical ERROR nodes in the tree
        if parse_result.has_errors:
            for error in parse_result.errors[:5]:
                error_str = str(error)
                error_details.append(error_str)
        
        # Check 2: Target class is still parseable
        if block.target_class:
            target_cls = parse_result.get_class(block.target_class)
            if target_cls is None:
                error_details.append(f"Target class '{block.target_class}' not found or broken")
                return True, error_details
        
        # Check 3: Target method/function is still parseable
        if block.target_method:
            if block.target_class:
                target_cls = parse_result.get_class(block.target_class)
                if target_cls:
                    method = parse_result.get_method(block.target_class, block.target_method)
                    if method is None:
                        error_details.append(f"Target method '{block.target_method}' not found in class")
                        return True, error_details
            else:
                func = parse_result.get_function(block.target_method)
                if func is None:
                    error_details.append(f"Target function '{block.target_method}' not found")
                    return True, error_details
        
        elif block.target_function:
            func = parse_result.get_function(block.target_function)
            if func is None:
                error_details.append(f"Target function '{block.target_function}' not found")
                return True, error_details
        
        # Check 4: If we have critical errors, it's broken
        if error_details and parse_result.has_errors:
            return True, error_details
        
        return False, error_details
    
    def _determine_target_indent(
        self, 
        existing_content: str, 
        block: 'ParsedCodeBlock',
        ts_parser: 'FaultTolerantParser'
    ) -> int:
        """
        Determine the correct indentation level for the code block
        based on its target context.
        
        Args:
            existing_content: Original file content
            block: Code block with target information
            ts_parser: Tree-sitter parser
            
        Returns:
            Number of spaces for indentation (0, 4, 8, 12, etc.)
        """
        import re
        
        # Default: module level (0 spaces)
        target_indent = 0
        
        try:
            parse_result = ts_parser.parse(existing_content)
        except Exception:
            return target_indent
        
        # If targeting a class method
        if block.target_class and block.target_method:
            # Methods inside classes are indented 8 spaces (4 for class, 4 for method body)
            target_indent = 8
        
        # If targeting a class
        elif block.target_class:
            # Class body is indented 4 spaces
            target_indent = 4
        
        # If targeting a function
        elif block.target_method or block.target_function:
            # Function body is indented 4 spaces
            target_indent = 4
        
        # Try to detect actual indentation from existing code
        if existing_content:
            lines = existing_content.split('\n')
            
            # Look for indented lines to detect the pattern
            for line in lines:
                if line and not line[0].isspace():
                    continue
                if line.strip():
                    # Count leading spaces
                    spaces = len(line) - len(line.lstrip(' '))
                    if spaces > 0 and spaces % 4 == 0:
                        # Found a properly indented line
                        if block.target_class and block.target_method:
                            # Look for method inside class
                            if 'def ' in line:
                                target_indent = spaces + 4  # Method body
                                break
                        elif block.target_class:
                            # Look for class body
                            if not line.strip().startswith('def '):
                                target_indent = spaces
                                break
        
        logger.debug(f"Determined target indent: {target_indent} spaces for {block.target_class or block.target_method or block.target_function}")
        return target_indent
    
    def _reindent_code_block(self, code: str, target_indent: int) -> str:
        """
        Re-indent a code block to match the target indentation level.
        
        Args:
            code: Code block to re-indent
            target_indent: Target indentation in spaces
            
        Returns:
            Re-indented code
        """
        lines = code.split('\n')
        reindented_lines = []
        
        # Detect current indentation of the first non-empty line
        current_indent = 0
        for line in lines:
            if line.strip():
                current_indent = len(line) - len(line.lstrip(' '))
                break
        
        # Calculate difference
        indent_diff = target_indent - current_indent
        
        # Re-indent all lines
        for line in lines:
            if not line.strip():
                # Empty line - keep as is
                reindented_lines.append(line)
            else:
                # Non-empty line - adjust indentation
                current_spaces = len(line) - len(line.lstrip(' '))
                new_spaces = max(0, current_spaces + indent_diff)
                reindented_lines.append(' ' * new_spaces + line.lstrip(' '))
        
        return '\n'.join(reindented_lines)
    
    # ========================================================================
    # INTERNAL: VALIDATION
    # ========================================================================
    
    async def _run_validation(
        self, 
        include_tests: bool = False,
        test_timeout_sec: int = 60,
        auto_install_deps: bool = True,
    ) -> ValidationResult:
        """
        Run multi-level validation on VFS.
        
        Args:
            include_tests: If True, also run related project tests
            test_timeout_sec: Timeout for test execution
            auto_install_deps: If True, automatically install missing dependencies
            
        Returns:
            ValidationResult with all check results
        """
        from app.services.change_validator import ValidatorConfig
        
        # Build list of validation levels
        levels = [
            ValidationLevel.SYNTAX,
            ValidationLevel.IMPORTS,
        ]
        
        # NEW: Добавляем TYPES только если включено
        if self._enable_type_checking:
            levels.append(ValidationLevel.TYPES)
        
        levels.append(ValidationLevel.INTEGRATION)
        
        if include_tests:
            levels.extend([
                ValidationLevel.RUNTIME,
                ValidationLevel.TESTS,
            ])
        
        config = ValidatorConfig(
            enabled_levels=levels,
            run_related_tests=include_tests,
            test_timeout_sec=test_timeout_sec,
        )
        
        validator = ChangeValidator(vfs=self.vfs, config=config)
        
        # Pass project python path to syntax checker for proper tool resolution
        if hasattr(self, '_project_python_path') and self._project_python_path:
            validator.syntax_checker.set_project_python_path(self._project_python_path)
        
        # Run validation
        if include_tests:
            result = await validator.validate_full()
        else:
            result = await validator.validate_before_commit()
        
        return result
    
    
    async def _run_ai_validation(
        self,
        user_request: str,
        instruction: str,
        code_blocks: List[ParsedCodeBlock],
    ) -> AIValidationResult:
        """
        Run AI Validator to check if code addresses the request.
        
        Supports multiple files by combining all code blocks with clear markers.
        """
        if not code_blocks:
            # Edge case: no code blocks
            return await self.ai_validator.validate(
                user_request=user_request,
                orchestrator_instruction=instruction,
                original_content="",
                proposed_code="[No code generated]",
                file_path="",
            )
        
        # === Combine all code blocks with clear file markers ===
        combined_code_parts = []
        for block in code_blocks:
            combined_code_parts.append(
                f"### FILE: {block.file_path}\n"
                f"### MODE: {block.mode}\n"
                f"```python\n{block.code}\n```"
            )
        combined_code = "\n\n".join(combined_code_parts)
        
        # === Combine original content for all files ===
        original_parts = []
        for block in code_blocks:
            original = self.vfs.read_file_original(block.file_path)
            if original:
                original_parts.append(
                    f"### FILE: {block.file_path}\n"
                    f"```python\n{original}\n```"
                )
            else:
                original_parts.append(
                    f"### FILE: {block.file_path}\n"
                    f"[NEW FILE - no original content]"
                )
        combined_original = "\n\n".join(original_parts)
        
        # === Build file_path as summary ===
        if len(code_blocks) == 1:
            file_path_str = code_blocks[0].file_path
        else:
            file_path_str = f"{len(code_blocks)} files: " + ", ".join(
                b.file_path for b in code_blocks[:3]
            )
            if len(code_blocks) > 3:
                file_path_str += f" ... and {len(code_blocks) - 3} more"
        
        # === Run validation ===
        result = await self.ai_validator.validate(
            user_request=user_request,
            orchestrator_instruction=instruction,
            original_content=combined_original,
            proposed_code=combined_code,
            file_path=file_path_str,
        )
        
        return result    
    
    
    async def _get_orchestrator_decision_on_ai_feedback(
        self,
        ai_result: AIValidationResult,
        code_blocks: List[ParsedCodeBlock],
        working_history: List[Dict[str, str]],
    ) -> OrchestratorFeedbackDecision:
        """
        Ask Orchestrator to decide on AI Validator rejection.
        """
        # Build code preview
        code_preview_parts = []
        for b in code_blocks[:3]:  # Limit to 3 blocks
            code_preview_parts.append(f"**File:** `{b.file_path}`")
            code_preview_parts.append(f"```python\n{b.code[:400]}{'...' if len(b.code) > 400 else ''}\n```")
        code_preview = "\n".join(code_preview_parts)
        
        # Build issues list
        issues_list = "\n".join(f"- {issue}" for issue in ai_result.critical_issues)
        
        critique_prompt = f"""[AI VALIDATOR REJECTED YOUR CODE]

    The AI Validator has rejected the code you instructed to generate.

    **Verdict:** {ai_result.verdict}
    **Confidence:** {ai_result.confidence:.0%}

    **Critical Issues:**
    {issues_list}

    **Your Generated Code:**
    {code_preview}

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔴 MANDATORY RESPONSE FORMAT — FOLLOW EXACTLY
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    Your response MUST begin with this exact line:

    **My decision:** ACCEPT

    OR

    **My decision:** OVERRIDE

    Then immediately follow with:

    **Reasoning:** [1-3 sentences explaining your decision]

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    IF YOU CHOOSE **ACCEPT**:
    - You agree the validator found real issues
    - You MUST provide a revised instruction section: ## Instruction for Code Generator
    - Include complete details for Code Generator to fix the issues

    IF YOU CHOOSE **OVERRIDE**:
    - You believe the validator is WRONG
    - Explain WHY with references to specific code lines
    - Example: "Line 5 shows re.finditer is called, so 're' IS available via class import"
    - User will decide whether to proceed to tests

    ⚠️ The system REQUIRES the exact format "**My decision:** ACCEPT" or "**My decision:** OVERRIDE"
    Failure to include this marker will cause the system to default to OVERRIDE."""

        # Add to history
        decision_history = working_history.copy()
        decision_history.append({
            "role": "user",
            "content": critique_prompt,
        })
        
        # Call Orchestrator
        orchestrator_result = await self._run_orchestrator(
            user_request=self._pending_user_request,
            history=decision_history,
            orchestrator_model=self._orchestrator_model,
        )
        
        # Parse decision from response
        decision = self._parse_orchestrator_feedback_decision(orchestrator_result.raw_response)
        
        return decision    
    
    
    
    
    def _format_validation_feedback(
        self,
        validation_result: ValidationResult,
        code_blocks: List[ParsedCodeBlock],
    ) -> str:
        """
        Format technical validation errors as feedback for Orchestrator.
        """
        parts = []
        parts.append("[TECHNICAL VALIDATION FAILED - YOUR CODE HAS ERRORS]")
        parts.append("")
        parts.append(f"Errors: {validation_result.error_count} | Warnings: {validation_result.warning_count}")
        parts.append("")
        parts.append("Issues found:")
        
        for issue in validation_result.issues[:10]:  # Limit to 10
            severity = "❌ ERROR" if issue.severity == IssueSeverity.ERROR else "⚠️ WARNING"
            parts.append(f"  {severity} [{issue.level.value}] {issue.file_path}:{issue.line}")
            parts.append(f"    {issue.message}")
        
        if len(validation_result.issues) > 10:
            parts.append(f"  ... and {len(validation_result.issues) - 10} more issues")
        
        parts.append("")
        parts.append("Your generated code:")
        for block in code_blocks:
            parts.append(f"```python")
            parts.append(f"# {block.file_path}")
            parts.append(block.code[:1000])
            if len(block.code) > 1000:
                parts.append("... [truncated]")
            parts.append("```")
        
        parts.append("")
        parts.append("Please analyze these errors and provide a REVISED instruction that fixes them.")
        parts.append("Focus on the root cause, not just the symptoms.")
        
        return "\n".join(parts)    
    
    
    def _format_errors_for_orchestrator(
        self, 
        errors: List['ValidationIssue'], 
        code_blocks: List['ParsedCodeBlock']
    ) -> str:
        """
        Format errors and generated code for Orchestrator feedback.
        
        Includes:
        - File ownership markers (YOUR FILE vs existing)
        - Generated code with line numbers for analysis
        - Clear action items
        
        Args:
            errors: List of validation issues
            code_blocks: Code blocks that were generated
            
        Returns:
            Formatted feedback string for Orchestrator
        """
        from collections import defaultdict
        
        # Build set of files we created
        our_files = {b.file_path for b in code_blocks}
        
        parts = []
        parts.append("## ❌ Validation Errors Detected")
        parts.append("")
        parts.append("The generated code has errors that must be fixed.")
        parts.append("")
        
        # === OWNERSHIP CONTEXT ===
        parts.append("### 📋 Context")
        parts.append("")
        parts.append(f"**Files generated this iteration:** {len(code_blocks)}")
        for b in code_blocks:
            parts.append(f"  • `{b.file_path}`")
        parts.append("")
        parts.append("**Responsibility:**")
        parts.append("• If error is in YOUR file → fix it or DELETE it")
        parts.append("• If error shows truncation (EOF, unterminated) → SPLIT the file into smaller pieces")
        parts.append("")
        
        # === ERRORS BY FILE ===
        errors_by_file: Dict[str, List] = defaultdict(list)
        for err in errors:
            errors_by_file[err.file_path].append(err)
        
        parts.append("### 🔴 Errors Found")
        parts.append("")
        
        for file_path, file_errors in errors_by_file.items():
            # Determine ownership
            is_our_file = file_path in our_files
            if is_our_file:
                ownership_marker = "🔴 YOUR FILE — you must fix or delete"
            else:
                ownership_marker = "📁 Pre-existing file"
            
            parts.append(f"**`{file_path}`** ({ownership_marker})")
            parts.append("")
            
            for err in file_errors[:5]:
                # Format location
                loc = f"line {err.line}" if err.line else "unknown location"
                
                # Format level
                level_str = err.level.value if hasattr(err.level, 'value') else str(err.level)
                
                parts.append(f"  • **[{level_str}]** {loc}")
                parts.append(f"    {err.message}")
                
                # Add suggestion if available
                if hasattr(err, 'suggestion') and err.suggestion:
                    parts.append(f"    💡 Suggestion: {err.suggestion}")
            
            if len(file_errors) > 5:
                parts.append(f"  • ... and {len(file_errors) - 5} more errors in this file")
            
            parts.append("")
        
        # === GENERATED CODE FOR ANALYSIS ===
        if code_blocks:
            parts.append("─" * 50)
            parts.append("### 📝 Your Generated Code")
            parts.append("")
            parts.append("Review this to identify what went wrong:")
            parts.append("")
            
            for block in code_blocks:
                # Format mode
                mode_str = block.mode if isinstance(block.mode, str) else str(block.mode)
                
                parts.append(f"**`{block.file_path}`** (MODE: `{mode_str}`)")
                
                # Add targets if present
                targets = []
                if block.target_class:
                    targets.append(f"TARGET_CLASS: `{block.target_class}`")
                if block.target_method:
                    targets.append(f"TARGET_METHOD: `{block.target_method}`")
                if block.target_function:
                    targets.append(f"TARGET_FUNCTION: `{block.target_function}`")
                
                if targets:
                    parts.append("  " + ", ".join(targets))
                
                # Add code with line numbers
                code_lines = block.code.split('\n')
                max_lines_to_show = 50
                
                numbered_lines = []
                for i, line in enumerate(code_lines[:max_lines_to_show], 1):
                    numbered_lines.append(f"{i:3d} │ {line}")
                
                code_preview = '\n'.join(numbered_lines)
                
                if len(code_lines) > max_lines_to_show:
                    code_preview += f"\n    │ ... ({len(code_lines) - max_lines_to_show} more lines)"
                
                parts.append(f"```")
                parts.append(code_preview)
                parts.append(f"```")
                parts.append("")
        
        # === ACTION ITEMS ===
        parts.append("─" * 50)
        parts.append("### ✅ Required Actions")
        parts.append("")
        parts.append("1. **Locate the error** — find it in the generated code above")
        parts.append("2. **Identify root cause** — what in your instruction caused this?")
        parts.append("3. **Write corrected instruction** — fix the underlying issue")
        parts.append("")
        parts.append("If file was truncated (unterminated string, unexpected EOF):")
        parts.append("  • DELETE the broken file")
        parts.append("  • SPLIT into smaller files")
        parts.append("  • Regenerate with less content per file")
        parts.append("")

        # === NEW FILES CONTEXT ===
        error_locations = {err.file_path: [err.line] for err in errors if err.line}
        new_files_context = self._format_new_files_context(error_locations, max_tokens=50000)
        if new_files_context:
            parts.append("─" * 50)
            parts.append(new_files_context)
        
        return "\n".join(parts)


    def _format_test_errors_for_orchestrator(
        self,
        test_errors: List['ValidationIssue'],
        runtime_errors: List['ValidationIssue'],
        validation_result: 'ValidationResult',
        code_blocks: List['ParsedCodeBlock']
    ) -> str:
        """
        Format test/runtime errors for Orchestrator.
        
        UPDATED: Includes full error details and generated code with line numbers.
        """
        from collections import defaultdict
        parts = ["## ❌ Test/Runtime Validation Failed\n"]
        
        # Statistics
        parts.append(f"**Tests Run:** {validation_result.tests_run}")
        parts.append(f"**Tests Passed:** {validation_result.tests_passed}")
        parts.append(f"**Tests Failed:** {validation_result.tests_failed}")
        parts.append(f"**Runtime Files Checked:** {validation_result.runtime_files_checked}")
        parts.append(f"**Runtime Files Passed:** {validation_result.runtime_files_passed}")
        parts.append(f"**Runtime Files Failed:** {validation_result.runtime_files_failed}")
        parts.append("")
        
        # === ИСПРАВЛЕНИЕ: Извлекаем детали из runtime_test_summary ===
        runtime_details: Dict[str, str] = {}
        if validation_result.runtime_test_summary:
            for r in validation_result.runtime_test_summary.get("results", []):
                if r.get("status") in ("failed", "error", "timeout") and r.get("details"):
                    runtime_details[r.get("file_path", "")] = r.get("details", "")
        
        if test_errors:
            parts.append(f"### Test Failures ({len(test_errors)}):")
            for err in test_errors[:10]:
                loc = f" (line {err.line})" if err.line else ""
                parts.append(f"\n**File:** `{err.file_path}`{loc}")
                parts.append(f"**Error:** {err.message[:300]}")
                
                # Если message содержит traceback, он уже там
                # Иначе пытаемся найти в runtime_details
                if "Traceback" not in err.message and err.file_path in runtime_details:
                    details = runtime_details[err.file_path]
                    parts.append(f"**Traceback:**")
                    parts.append("```")
                    for line in details.split('\n')[-15:]:  # Последние 15 строк
                        parts.append(line)
                    parts.append("```")
            
            if len(test_errors) > 10:
                parts.append(f"\n... and {len(test_errors) - 10} more test errors")
            parts.append("")
        
        if runtime_errors:
            parts.append(f"### Runtime Errors ({len(runtime_errors)}):")
            for err in runtime_errors[:10]:
                loc = f" (line {err.line})" if err.line else ""
                parts.append(f"\n**File:** `{err.file_path}`{loc}")
                
                # Проверяем, содержит ли message уже traceback
                if "Traceback" in err.message or "Details:" in err.message:
                    # Message уже содержит детали — выводим как есть
                    parts.append(f"**Error with details:**")
                    parts.append("```")
                    # Ограничиваем вывод
                    msg_lines = err.message.split('\n')
                    for line in msg_lines[:20]:
                        parts.append(line)
                    if len(msg_lines) > 20:
                        parts.append(f"... ({len(msg_lines) - 20} more lines)")
                    parts.append("```")
                else:
                    # Короткое сообщение — ищем детали в runtime_details
                    parts.append(f"**Error:** {err.message}")
                    if err.file_path in runtime_details:
                        details = runtime_details[err.file_path]
                        parts.append(f"**Full traceback:**")
                        parts.append("```")
                        for line in details.split('\n')[-15:]:
                            parts.append(line)
                        parts.append("```")
            
            if len(runtime_errors) > 10:
                parts.append(f"\n... and {len(runtime_errors) - 10} more runtime errors")
            parts.append("")
        
        # Add generated code with line numbers
        if code_blocks:
            parts.append("─" * 50)
            parts.append("### Generated Code (analyze for test failures):")
            parts.append("")
            
            for block in code_blocks:
                parts.append(f"**`{block.file_path}`** (MODE: {block.mode})")
                if block.target_class:
                    parts.append(f"TARGET_CLASS: `{block.target_class}`")
                if block.target_method:
                    parts.append(f"TARGET_METHOD: `{block.target_method}`")
                
                code_lines = block.code.split('\n')[:40]
                numbered_lines = [f"{i:3d} | {line}" for i, line in enumerate(code_lines, 1)]
                code_preview = '\n'.join(numbered_lines)
                
                if len(block.code.split('\n')) > 40:
                    code_preview += "\n... (truncated)"
                
                parts.append(f"```python\n{code_preview}\n```")
                parts.append("")
        
        parts.append("─" * 50)
        parts.append("### Your Task:")
        parts.append("1. **Match errors to generated code** — which lines cause failures")
        parts.append("2. **Understand what the error means** — read the traceback carefully")
        parts.append("3. **Write corrected instruction** — fix the root cause, not symptoms")

        # === NEW FILES CONTEXT ===
        all_errors = test_errors + runtime_errors
        error_locations = defaultdict(list)
        for err in all_errors:
            if err.line:
                error_locations[err.file_path].append(err.line)
        
        new_files_context = self._format_new_files_context(dict(error_locations), max_tokens=50000)
        if new_files_context:
            parts.append("─" * 50)
            parts.append(new_files_context)
        
        return "\n".join(parts)
    
    
    def _format_ai_validator_feedback(
        self,
        ai_result: AIValidationResult,
        code_blocks: List[ParsedCodeBlock],
    ) -> str:
        """
        Format AI Validator rejection as feedback for Orchestrator.
        
        UPDATED: Includes generated code with line numbers.
        """
        parts = []
        parts.append("## ⚠️ AI VALIDATOR REJECTED CODE")
        parts.append("")
        parts.append(f"**Verdict:** {ai_result.verdict}")
        parts.append(f"**Confidence:** {ai_result.confidence:.0%}")
        parts.append("")
        parts.append("**Critical Issues:**")
        for issue in ai_result.critical_issues:
            parts.append(f"  - {issue}")
        
        # Add generated code with line numbers
        parts.append("")
        parts.append("─" * 50)
        parts.append("### Your Generated Code:")
        parts.append("")
        parts.append("Review this to verify or refute the validator's claims:")
        parts.append("")
        
        for block in code_blocks:
            parts.append(f"**`{block.file_path}`** (MODE: {block.mode})")
            if block.target_class:
                parts.append(f"TARGET_CLASS: `{block.target_class}`")
            if block.target_method:
                parts.append(f"TARGET_METHOD: `{block.target_method}`")
            
            code_lines = block.code.split('\n')[:50]
            numbered_lines = [f"{i:3d} | {line}" for i, line in enumerate(code_lines, 1)]
            code_preview = '\n'.join(numbered_lines)
            
            if len(block.code.split('\n')) > 50:
                code_preview += "\n... (truncated)"
            
            parts.append(f"```python\n{code_preview}\n```")
        
        parts.append("")
        parts.append("─" * 50)
        parts.append("### Your Decision:")
        parts.append("")
        parts.append("**If validator is CORRECT:**")
        parts.append("  - Write **My decision:** ACCEPT")
        parts.append("  - Provide revised instruction")
        parts.append("")
        parts.append("**If validator is WRONG:**")
        parts.append("  - Write **My decision:** OVERRIDE")
        parts.append("  - Reference specific lines in code as evidence")
        parts.append("  - Example: 'Validator claims X missing. Line 5 shows X exists.'")
        
        return "\n".join(parts)
    
        
    def _format_test_feedback(
        self,
        test_issues: List[Any],
        code_blocks: List[ParsedCodeBlock],
    ) -> str:
        """
        Format test failures as feedback for Orchestrator.
        """
        parts = []
        parts.append("[TESTS FAILED - YOUR CODE BREAKS EXISTING FUNCTIONALITY]")
        parts.append("")
        parts.append(f"Failed tests: {len(test_issues)}")
        parts.append("")
        parts.append("Test failures:")
        
        for issue in test_issues[:5]:  # Limit to 5
            parts.append(f"  ❌ {issue.file_path}")
            parts.append(f"    {issue.message}")
            if issue.details:
                parts.append(f"    Details: {issue.details[:200]}")
        
        if len(test_issues) > 5:
            parts.append(f"  ... and {len(test_issues) - 5} more failures")
        
        parts.append("")
        parts.append("Your generated code:")
        for block in code_blocks:
            parts.append(f"```python")
            parts.append(f"# {block.file_path}")
            parts.append(block.code[:1000])
            if len(block.code) > 1000:
                parts.append("... [truncated]")
            parts.append("```")
        
        parts.append("")
        parts.append("Please analyze the test failures and provide a REVISED instruction.")
        parts.append("Your code must pass ALL existing tests while implementing the requested changes.")
        
        return "\n".join(parts)    
    
    # ========================================================================
    # INTERNAL: PROJECT TESTS
    # ========================================================================
    
    def _parse_orchestrator_feedback_decision(self, response: str) -> OrchestratorFeedbackDecision:
        """
        Parse Orchestrator's response when handling validator feedback.
        
        Supports both English and Russian responses.
        """
        import re
        
        decision = "OVERRIDE"
        reasoning = "No explicit decision found in response — proceeding to tests"
        new_instruction = None
        new_analysis = None
        
        logger.info(f"Parsing orchestrator decision, response length: {len(response)}")
        
        # === Parse decision (English + Russian) ===
        decision_patterns = [
            (r'\*\*My decision:\*\*\s*(ACCEPT|OVERRIDE)', 'My decision'),
            (r'\*\*Мое решение:\*\*\s*(ACCEPT|OVERRIDE)', 'Мое решение'),
            (r'\*\*Моё решение:\*\*\s*(ACCEPT|OVERRIDE)', 'Моё решение'),
            (r'\*\*Decision:\*\*\s*(ACCEPT|OVERRIDE)', 'Decision'),
            (r'\*\*Решение:\*\*\s*(ACCEPT|OVERRIDE)', 'Решение'),
            (r'My decision:\s*(ACCEPT|OVERRIDE)', 'My decision (no bold)'),
            (r'Мое решение:\s*(ACCEPT|OVERRIDE)', 'Мое решение (no bold)'),
            (r'Моё решение:\s*(ACCEPT|OVERRIDE)', 'Моё решение (no bold)'),
        ]
        
        decision_found = False
        for pattern, name in decision_patterns:
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                decision = match.group(1).upper()
                decision_found = True
                logger.info(f"Found decision '{decision}' via pattern '{name}'")
                break
        
        if not decision_found:
            logger.warning(f"No decision pattern matched in response: {response[:300]}...")
        
        # === Parse reasoning (English + Russian) ===
        # ИСПРАВЛЕНО: Более жадный паттерн, захватываем до ## или конца
        reasoning_patterns = [
            r'\*\*Reasoning:\*\*\s*(.+?)(?=\n##[^#]|\n\*\*[A-ZА-Я]|\Z)',
            r'\*\*Обоснование:\*\*\s*(.+?)(?=\n##[^#]|\n\*\*[A-ZА-Я]|\Z)',
            r'Reasoning:\s*(.+?)(?=\n##[^#]|\n\*\*[A-ZА-Я]|\Z)',
            r'Обоснование:\s*(.+?)(?=\n##[^#]|\n\*\*[A-ZА-Я]|\Z)',
        ]
        
        for pattern in reasoning_patterns:
            match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
            if match:
                reasoning = match.group(1).strip()
                # Убираем лишние переносы строк
                reasoning = re.sub(r'\n{3,}', '\n\n', reasoning)
                logger.info(f"Found reasoning: {reasoning[:100]}...")
                break
        
        # === Fallback: если reasoning пуст но decision найден ===
        if decision_found and (not reasoning or reasoning == "No explicit decision found in response — proceeding to tests"):
            # Пробуем взять текст между decision и следующей секцией
            decision_pos = response.lower().find(decision.lower())
            if decision_pos != -1:
                after_decision = response[decision_pos + len(decision):]
                # Ищем первый абзац после решения
                paragraph_match = re.search(r'\n\n(.+?)(?=\n\n|\n##|\Z)', after_decision, re.DOTALL)
                if paragraph_match:
                    fallback_reasoning = paragraph_match.group(1).strip()
                    if len(fallback_reasoning) > 20:
                        reasoning = fallback_reasoning
                        logger.info(f"Using fallback reasoning: {reasoning[:100]}...")
        
        # === Check for implicit ACCEPT indicators ===
        if not decision_found:
            accept_indicators = [
                r'(?:i |я )?agree with',
                r'the validator is correct',
                r'валидатор прав',
                r'согласен с (валидатором|критикой)',
                r'need to fix',
                r'нужно исправить',
                r'should be revised',
                r'следует исправить',
                r'принимаю критику',
                r'accept.{0,20}feedback',
            ]
            
            for pattern in accept_indicators:
                if re.search(pattern, response, re.IGNORECASE):
                    # Check if there's a new instruction
                    instruction_match = re.search(
                        r'##\s*(?:Instruction|Инструкция)[^\n]*\n(.*?)(?=\n##\s*(?!#)|$)',
                        response,
                        re.DOTALL | re.IGNORECASE
                    )
                    if instruction_match and len(instruction_match.group(1).strip()) > 50:
                        decision = "ACCEPT"
                        if not reasoning or reasoning.startswith("No explicit"):
                            reasoning = f"Implicit agreement detected: '{pattern}'"
                        logger.info(f"Detected implicit ACCEPT via indicator: {pattern}")
                        break
        
        # === Parse analysis ===
        analysis_match = re.search(
            r'##\s*(?:Analysis|Анализ)\s*\n(.*?)(?=\n##\s*(?:Instruction|Инструкция)|$)',
            response,
            re.DOTALL | re.IGNORECASE
        )
        if analysis_match:
            new_analysis = analysis_match.group(1).strip()
        
        # === Parse instruction (only if ACCEPT) ===
        if decision == "ACCEPT":
            instruction_match = re.search(
                r'##\s*(?:Instruction|Инструкция)[^\n]*\n(.*?)(?=\n##\s*(?!#)|$)',
                response,
                re.DOTALL | re.IGNORECASE
            )
            if instruction_match:
                new_instruction = instruction_match.group(1).strip()
            
            # If ACCEPT but no valid instruction — change to OVERRIDE
            if not new_instruction or len(new_instruction) < 20:
                logger.warning(
                    "Orchestrator said ACCEPT but provided no valid instruction. "
                    "Changing to OVERRIDE to proceed to tests."
                )
                decision = "OVERRIDE"
                reasoning = "ACCEPT without valid instruction — proceeding to tests"
                new_instruction = None
        
        logger.info(f"Final parsed decision: {decision}, reasoning length: {len(reasoning)}")
        
        return OrchestratorFeedbackDecision(
            decision=decision,
            reasoning=reasoning,
            new_instruction=new_instruction,
            new_analysis=new_analysis,
        )       
       
    # ========================================================================
    # USER FEEDBACK HANDLING
    # ========================================================================
    
    async def handle_user_feedback(
        self,
        action: str,
        user_message: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[PipelineResult]:
        """
        Handle user feedback on AI Validator rejection.
        
        This is called from UI when user responds to validator rejection.
        
        Args:
            action: One of "accept", "override", "replace", "cancel"
            user_message: User's custom message (for "replace" action)
            history: Conversation history (needed for re-run)
            
        Returns:
            New PipelineResult if action requires re-processing, None otherwise
        """
        from app.agents.feedback_handler import UserAction
        
        if not self.feedback_loop:
            logger.warning("No active feedback loop")
            return None
        
        if action == "cancel":
            await self.discard_pending_changes()
            return PipelineResult(
                success=False,
                status=PipelineStatus.CANCELLED,
                errors=["User cancelled request"],
            )
        
        elif action == "override":
            # User rejects validator critique - proceed to tests
            logger.info("User overrode validator - proceeding to tests")
            self.feedback_loop.user_overrides_count += 1
            
            # Run validation with tests
            validation_with_tests = await self._run_validation(include_tests=True)
            
            if validation_with_tests.success:
                return PipelineResult(
                    success=True,
                    status=PipelineStatus.AWAITING_CONFIRMATION,
                    pending_changes=self._pending_changes,
                    diffs=self.vfs.get_all_diffs(),
                    validation_result=validation_with_tests.to_dict(),
                )
            else:
                # Tests or other validation failed
                test_issues = [
                    issue for issue in validation_with_tests.issues 
                    if issue.level == ValidationLevel.TESTS
                ]
                
                if test_issues:
                    return PipelineResult(
                        success=False,
                        status=PipelineStatus.FAILED,
                        validation_result=validation_with_tests.to_dict(),
                        errors=[f"Tests failed: {issue.message}" for issue in test_issues],
                    )
                else:
                    return PipelineResult(
                        success=False,
                        status=PipelineStatus.FAILED,
                        validation_result=validation_with_tests.to_dict(),
                        errors=["Validation failed"],
                    )        
        
        elif action == "replace" and user_message:
            # User provides custom critique instead of validator
            logger.info("User replacing validator critique with custom feedback")
            self.feedback_loop.add_user_feedback(user_message, replaces_validator=True)
            
            if history:
                # Re-run with user's feedback
                enhanced_history = history.copy()
                enhanced_history.append({
                    "role": "user",
                    "content": f"[USER FEEDBACK - MUST ADDRESS]\n{user_message}",
                })
                
                self.vfs.discard_all()
                
                orchestrator_result = await self._run_orchestrator(
                    user_request=self._pending_user_request,
                    history=enhanced_history,
                    orchestrator_model=self._orchestrator_model,
                )
                
                code_blocks, _ = await self._run_code_generator(
                    instruction=orchestrator_result.instruction,
                    target_file=orchestrator_result.target_file,
                    target_files=getattr(orchestrator_result, 'target_files', []),  # NEW
                )
                
                if code_blocks:
                    await self._stage_code_blocks(code_blocks)
                    
                    # Run full validation with tests
                    validation_result = await self._run_validation(include_tests=True)
                    
                    if validation_result.success:
                        self._pending_changes = await self._build_pending_changes(
                            code_blocks=code_blocks,
                            validation_passed=True,
                            ai_validation_passed=True,
                        )
                        self._pending_orchestrator_instruction = orchestrator_result.instruction
                        
                        return PipelineResult(
                            success=True,
                            status=PipelineStatus.AWAITING_CONFIRMATION,
                            instruction=orchestrator_result.instruction,
                            code_blocks=code_blocks,
                            validation_result=validation_result.to_dict(),
                            pending_changes=self._pending_changes,
                            diffs=self.vfs.get_all_diffs(),
                        )
                    else:
                        return PipelineResult(
                            success=False,
                            status=PipelineStatus.FAILED,
                            validation_result=validation_result.to_dict(),
                            errors=["Validation failed after user feedback"],
                        )
            
            return None        
        
        elif action == "accept":
            # User accepts validator critique - send to orchestrator
            logger.info("User accepted validator critique")
            
            if history:
                feedback = self.feedback_loop.get_feedback_for_orchestrator()
                enhanced_history = history.copy()
                
                if feedback.get("validator_feedback"):
                    enhanced_history.append({
                        "role": "user",
                        "content": f"[VALIDATOR FEEDBACK - ACCEPTED BY USER]\n{feedback['validator_feedback']}\n\nPlease revise.",
                    })
                
                self.vfs.discard_all()
                
                orchestrator_result = await self._run_orchestrator(
                    user_request=self._pending_user_request,
                    history=enhanced_history,
                    orchestrator_model=self._orchestrator_model,
                )
                
                self.feedback_loop.record_orchestrator_revision(
                    reason="User accepted validator critique",
                    previous_instruction=self._pending_orchestrator_instruction,
                    new_instruction=orchestrator_result.instruction,
                )
                
                code_blocks, _ = await self._run_code_generator(
                    instruction=orchestrator_result.instruction,
                    target_file=orchestrator_result.target_file,
                    target_files=getattr(orchestrator_result, 'target_files', []),  # NEW
                )
                
                if code_blocks:
                    await self._stage_code_blocks(code_blocks)
                    
                    # Run full validation with tests
                    validation_result = await self._run_validation(include_tests=True)
                    
                    if validation_result.success:
                        self._pending_changes = await self._build_pending_changes(
                            code_blocks=code_blocks,
                            validation_passed=True,
                            ai_validation_passed=True,
                        )
                        self._pending_orchestrator_instruction = orchestrator_result.instruction
                        
                        return PipelineResult(
                            success=True,
                            status=PipelineStatus.AWAITING_CONFIRMATION,
                            instruction=orchestrator_result.instruction,
                            code_blocks=code_blocks,
                            validation_result=validation_result.to_dict(),
                            pending_changes=self._pending_changes,
                            diffs=self.vfs.get_all_diffs(),
                            feedback_iterations=self.feedback_loop.orchestrator_revisions,
                        )
                    else:
                        return PipelineResult(
                            success=False,
                            status=PipelineStatus.FAILED,
                            validation_result=validation_result.to_dict(),
                            errors=["Validation failed after revision"],
                        )
            
            return None        
        
        return None
    
    
    
    # ========================================================================
    # INTERNAL: FEEDBACK LOOP
    # ========================================================================
    
    async def _handle_ai_rejection(
        self,
        ai_result: AIValidationResult,
        user_request: str,
        history: List[Dict[str, str]],
    ) -> tuple[Optional[PipelineResult], Optional[OrchestratorFeedbackDecision]]:
        """
        Handle AI Validator rejection with feedback loop.
        
        Sends feedback to Orchestrator for revision.
        Orchestrator can ACCEPT (revise) or OVERRIDE (disagree with validator).
        
        Returns:
            Tuple of (PipelineResult if new code generated, OrchestratorFeedbackDecision)
        """
        if not self.feedback_loop.can_revise_instruction():
            logger.warning("Feedback loop: max revisions reached")
            self._notify_stage("FEEDBACK", "❌ Достигнут лимит попыток исправления", None)
            return None, None
        
        # Add validator feedback to loop state
        self.feedback_loop.add_validator_feedback(
            approved=ai_result.approved,
            confidence=ai_result.confidence,
            verdict=ai_result.verdict,
            critical_issues=ai_result.critical_issues,
            model_used=ai_result.model_used,
        )
        
        # Get formatted feedback for orchestrator
        feedback = self.feedback_loop.get_feedback_for_orchestrator()
        
        # Build enhanced history with feedback
        enhanced_history = history.copy()
        
        if feedback.get("validator_feedback"):
            enhanced_history.append({
                "role": "user",
                "content": f"""[VALIDATOR FEEDBACK]

{feedback['validator_feedback']}

Please analyze this feedback and decide:
1. If you AGREE with the validator's critique → respond with **My decision:** ACCEPT and provide revised instruction
2. If you DISAGREE with the validator → respond with **My decision:** OVERRIDE and explain why the code is actually correct

Remember: You can override the validator if you believe the critique is incorrect or based on misunderstanding.""",
            })
        
        # === NOTIFY: Orchestrator analyzing feedback ===
        self._notify_stage("ORCHESTRATOR", 
            f"Оркестратор анализирует критику валидатора (итерация {self.feedback_loop.orchestrator_revisions + 1})...", 
            {"iteration": self.feedback_loop.orchestrator_revisions + 1}
        )
        
        # Re-run orchestrator with feedback
        logger.info("Feedback loop: requesting orchestrator decision on validator feedback")
        
        orchestrator_result = await self._run_orchestrator(
            user_request=user_request,
            history=enhanced_history,
            orchestrator_model=self._orchestrator_model,
        )
        
        # Parse Orchestrator's decision
        decision = self._parse_orchestrator_feedback_decision(orchestrator_result.raw_response)
        
        logger.info(f"Orchestrator decision: {decision.decision} - {decision.reasoning[:100]}...")
        
        # === NOTIFY: Orchestrator's decision ===
        self._notify_stage("ORCHESTRATOR_DECISION", 
            f"Решение Оркестратора: {decision.decision}", 
            {"decision": decision.decision, "reasoning": decision.reasoning}
        )
        
        # If OVERRIDE - Orchestrator disagrees with validator
        if decision.decision == "OVERRIDE":
            logger.info("Orchestrator overrode validator - proceeding to tests")
            self._notify_stage("ORCHESTRATOR_DECISION", 
                "Оркестратор НЕ согласен с валидатором — код считается корректным", 
                None
            )
            # Don't generate new code, return None for result but with decision
            return None, decision
        
        # If ACCEPT - generate new code based on revised instruction
        self._notify_stage("ORCHESTRATOR_DECISION", 
            "Оркестратор СОГЛАСЕН с валидатором — генерируем исправленный код", 
            None
        )
        
        # Clear VFS for new attempt
        self.vfs.discard_all()
        
        # Record revision
        self.feedback_loop.record_orchestrator_revision(
            reason=f"AI Validator rejected: {ai_result.verdict}",
            previous_instruction=self._pending_orchestrator_instruction,
            new_instruction=orchestrator_result.instruction,
        )
        
        # === NOTIFY: New instruction ===
        if orchestrator_result.instruction:
            self._notify_stage("INSTRUCTION", 
                f"Новая инструкция для Code Generator (итерация {self.feedback_loop.orchestrator_revisions})", 
                {"instruction": orchestrator_result.instruction[:500] + "..." if len(orchestrator_result.instruction) > 500 else orchestrator_result.instruction}
            )
        
        # === NOTIFY: Code generation ===
        self._notify_stage("CODE_GEN", "Генерация исправленного кода...", None)
        
        # Generate new code
        code_blocks, _ = await self._run_code_generator(
            instruction=orchestrator_result.instruction,
            target_file=orchestrator_result.target_file,
            target_files=getattr(orchestrator_result, 'target_files', []),  # NEW
        )
        
        if not code_blocks:
            self._notify_stage("CODE_GEN", "❌ Code Generator не вернул код", None)
            return None, decision
        
        self._notify_stage("CODE_GEN", 
            f"✅ Сгенерировано {len(code_blocks)} блок(ов) кода", 
            {"files": [b.file_path for b in code_blocks]}
        )
        
        # Stage and validate again
        await self._stage_code_blocks(code_blocks)
        
        # === NOTIFY: Validation ===
        self._notify_stage("VALIDATION", "Валидация исправленного кода...", None)
        
        validation_result = await self._run_validation(include_tests=False)
        
        self._notify_stage("VALIDATION", 
            "✅ Валидация пройдена" if validation_result.success else f"⚠️ Ошибок: {validation_result.error_count}", 
            {"success": validation_result.success, "error_count": validation_result.error_count}
        )
        
        # === NOTIFY: AI Validation of new code ===
        self._notify_stage("AI_VALIDATION", "AI Validator проверяет исправленный код...", None)
        
        new_ai_result = await self._run_ai_validation(
            user_request=user_request,
            instruction=orchestrator_result.instruction,
            code_blocks=code_blocks,
        )
        
        self._notify_stage("AI_VALIDATION", 
            "✅ AI Validator одобрил" if new_ai_result.approved else "⚠️ AI Validator снова отклонил", 
            {"approved": new_ai_result.approved, "confidence": new_ai_result.confidence}
        )
        
        # === NOTIFY: Tests ===
        self._notify_stage("TESTS", "Запуск тестов...", None)
        
        validation_with_tests = await self._run_validation(include_tests=True)
        
        test_issues = [i for i in validation_with_tests.issues if i.level == ValidationLevel.TESTS]
        if test_issues:
            self._notify_stage("TESTS", f"❌ {len(test_issues)} тестов провалено", None)
        else:
            self._notify_stage("TESTS", "✅ Все тесты прошли", None)
        
        # Build pending changes
        self._pending_changes = await self._build_pending_changes(
            code_blocks=code_blocks,
            validation_passed=validation_with_tests.success,
            ai_validation_passed=new_ai_result.approved,
        )
        
        self._pending_orchestrator_instruction = orchestrator_result.instruction
        
        # Determine success
        success = (
            (validation_with_tests.success or validation_with_tests.error_count == 0) and
            (new_ai_result.approved or len(test_issues) == 0)
        )
        
        return PipelineResult(
            success=success,
            status=PipelineStatus.AWAITING_CONFIRMATION if success else PipelineStatus.FAILED,
            analysis=orchestrator_result.analysis,
            instruction=orchestrator_result.instruction,
            code_blocks=code_blocks,
            validation_result=validation_with_tests.to_dict(),
            ai_validation_result=new_ai_result.to_dict(),
            pending_changes=self._pending_changes,
            diffs=self.vfs.get_all_diffs(),
            feedback_iterations=self.feedback_loop.orchestrator_revisions,
        ), decision
            
    async def _handle_validation_errors(
        self,
        validation_result: ValidationResult,
        user_request: str,
        history: List[Dict[str, str]],
    ) -> Optional[PipelineResult]:
        """
        Handle validation errors by sending them to Orchestrator for fix.
        
        Only called for BLOCKING errors (syntax).
        Non-blocking errors are passed as feedback but don't stop pipeline.
        
        Args:
            validation_result: Result with validation errors
            user_request: Original user request
            history: Conversation history
            
        Returns:
            PipelineResult with fixed code or None if fix failed
        """
        from app.agents.feedback_handler import create_validation_error_feedback
        
        if not self.feedback_loop.can_revise_instruction():
            logger.warning("Cannot fix validation errors: max revisions reached")
            return None
        
        # Format errors for Orchestrator
        feedback = create_validation_error_feedback(validation_result.to_dict())
        if not feedback:
            return None
        
        # Build enhanced history with errors
        enhanced_history = history.copy()
        enhanced_history.append({
            "role": "user",
            "content": f"""[VALIDATION ERRORS - MUST FIX]

{feedback.to_prompt_format()}

Your generated code has validation errors that MUST be fixed.
Please analyze the errors and provide a revised instruction that fixes them.

Focus on:
1. Syntax errors (missing colons, brackets, indentation)
2. Import errors (wrong module names, missing packages)
3. Type errors (if enabled)

Provide a corrected version of your instruction.""",
        })
        
        logger.info("Requesting Orchestrator to fix validation errors...")
        
        # Re-run orchestrator with errors
        orchestrator_result = await self._run_orchestrator(
            user_request=user_request,
            history=enhanced_history,
            orchestrator_model=self._orchestrator_model,
        )
        
        # Clear VFS and generate new code
        self.vfs.discard_all()
        
        self.feedback_loop.record_orchestrator_revision(
            reason=f"Fixing {validation_result.error_count} validation errors",
            previous_instruction=self._pending_orchestrator_instruction,
            new_instruction=orchestrator_result.instruction,
        )
        
        code_blocks, _ = await self._run_code_generator(
            instruction=orchestrator_result.instruction,
            target_file=orchestrator_result.target_file,
            target_files=getattr(orchestrator_result, 'target_files', []),  # NEW
        )
        
        if code_blocks:
            await self._stage_code_blocks(code_blocks)
            
            # Re-validate
            new_validation = await self._run_validation(include_tests=False)
            
            return PipelineResult(
                success=new_validation.success or new_validation.error_count == 0,
                status=PipelineStatus.VALIDATING,
                instruction=orchestrator_result.instruction,
                code_blocks=code_blocks,
                validation_result=new_validation.to_dict(),
            )
        
        return None
    
    
    
    
    async def _handle_validation_errors_with_feedback(
        self,
        validation_result: ValidationResult,
        user_request: str,
        history: List[Dict[str, str]],
    ) -> Optional[PipelineResult]:
        """
        Handle validation errors with stage notifications.
        
        Similar to _handle_validation_errors but with progress feedback.
        """
        from app.agents.feedback_handler import create_validation_error_feedback
        
        if not self.feedback_loop.can_revise_instruction():
            self._notify_stage("FEEDBACK", "❌ Достигнут лимит попыток исправления", None)
            return None
        
        feedback = create_validation_error_feedback(validation_result.to_dict())
        if not feedback:
            return None
        
        self._notify_stage("FEEDBACK", 
            f"Отправка {validation_result.error_count} ошибок Оркестратору (попытка {self.feedback_loop.orchestrator_revisions + 1}/{self.feedback_loop.max_orchestrator_revisions})", 
            None
        )
        
        enhanced_history = history.copy()
        enhanced_history.append({
            "role": "user",
            "content": f"""[VALIDATION ERRORS - MUST FIX]

{feedback.to_prompt_format()}

Your generated code has validation errors that MUST be fixed.
Please analyze the errors and provide a revised instruction.""",
        })
        
        self._notify_stage("ORCHESTRATOR", "Оркестратор анализирует ошибки...", {
            "iteration": self.feedback_loop.orchestrator_revisions + 1,
        })
        
        orchestrator_result = await self._run_orchestrator(
            user_request=user_request,
            history=enhanced_history,
            orchestrator_model=self._orchestrator_model,
        )
        
        self.vfs.discard_all()
        
        self.feedback_loop.record_orchestrator_revision(
            reason=f"Fixing {validation_result.error_count} validation errors",
            previous_instruction=self._pending_orchestrator_instruction,
            new_instruction=orchestrator_result.instruction,
        )
        
        # Notify about new instruction
        if orchestrator_result.instruction:
            self._notify_stage("INSTRUCTION", "Новая инструкция для Code Generator", {
                "instruction": orchestrator_result.instruction,
                "iteration": self.feedback_loop.orchestrator_revisions,
            })
        
        self._notify_stage("CODE_GEN", "Генерация исправленного кода...", None)
        
        code_blocks, _ = await self._run_code_generator(
            instruction=orchestrator_result.instruction,
            target_file=orchestrator_result.target_file,
            target_files=getattr(orchestrator_result, 'target_files', []),  # NEW
        )
        
        if code_blocks:
            await self._stage_code_blocks(code_blocks)
            
            self._notify_stage("VALIDATION", "Повторная валидация...", None)
            new_validation = await self._run_validation(include_tests=False)
            
            # Notify validation result
            self._notify_stage("VALIDATION", 
                "✅ Ошибки исправлены" if new_validation.success else f"⚠️ Осталось {new_validation.error_count} ошибок",
                {"success": new_validation.success}
            )
            
            return PipelineResult(
                success=new_validation.success or new_validation.error_count == 0,
                status=PipelineStatus.VALIDATING,
                instruction=orchestrator_result.instruction,
                code_blocks=code_blocks,
                validation_result=new_validation.to_dict(),
            )
        
        return None    
    # ========================================================================
    # INTERNAL: HELPERS
    # ========================================================================
    
    async def _build_pending_changes(
        self,
        code_blocks: List[ParsedCodeBlock],
        validation_passed: bool,
        ai_validation_passed: bool,
    ) -> List[PendingChange]:
        """Build list of pending changes for user review.
        
        Uses VFS staged content as the source of truth, since code blocks
        have already been applied to VFS during _stage_code_blocks.
        """
        pending = []
        
        for block in code_blocks:
            # Get the staged content from VFS (this is what will be committed)
            staged_content = self.vfs.read_file(block.file_path)
            
            # Get original content for comparison
            original_content = self.vfs.read_file_original(block.file_path) or ""
            
            if staged_content is not None:
                # VFS has staged content - use it directly
                result = ModifyResult(
                    success=True,
                    new_content=staged_content,
                    message=f"Staged content from VFS ({block.mode})",
                    changes_made=[f"Applied {block.mode} on {block.file_path}"],
                )
            else:
                # Fallback: apply code block to original content
                # This shouldn't normally happen if _stage_code_blocks succeeded
                result = self.file_modifier.apply_code_block(original_content, block)
                logger.warning(
                    f"_build_pending_changes: No staged content for {block.file_path}, "
                    f"falling back to apply_code_block (success={result.success})"
                )
            
            pending.append(PendingChange(
                file_path=block.file_path,
                code_block=block,
                modify_result=result,
                validation_passed=validation_passed,
                ai_validation_passed=ai_validation_passed,
            ))
        
        logger.info(f"_build_pending_changes: Built {len(pending)} pending changes")
        return pending
    
    
    
    def _update_status(self, status: PipelineStatus, message: str = ""):
        """Update pipeline status and notify callback"""
        self.status = status
        logger.info(f"Pipeline status: {status.value} - {message}")
        
        if self._on_status:
            self._on_status(status, message)
    
    # ========================================================================
    # PROPERTIES
    # ========================================================================
    
    @property
    def has_pending_changes(self) -> bool:
        """Check if there are pending changes awaiting confirmation"""
        return len(self._pending_changes) > 0
    
    @property
    def pending_files(self) -> List[str]:
        """Get list of files with pending changes"""
        return [c.file_path for c in self._pending_changes]
    
    
    async def _apply_pending_deletions(self) -> List[Dict[str, Any]]:
        """
        Apply pending deletions by either deleting files or commenting out code.
        
        Called after all tests pass successfully.
        
        For FILE deletions: stages file for removal
        For METHOD/FUNCTION/CLASS deletions: comments out the code
        
        Returns:
            List of dicts with deletion results
        """
        results = []
        
        for deletion in self._pending_deletions:
            try:
                # Choose deletion method based on type
                if deletion.target_type == "FILE":
                    result = await self._delete_file(deletion)
                else:
                    # METHOD, FUNCTION, CLASS — comment out
                    result = await self._comment_out_code(deletion)
                
                results.append({
                    "target": deletion.target_name,
                    "type": deletion.target_type,
                    "file": deletion.file_path,
                    "parent_class": deletion.parent_class,
                    "reason": deletion.reason,
                    "success": result.get("success", False),
                    "error": result.get("error"),
                    "note": result.get("note"),
                })
                
                if result.get("success"):
                    if deletion.target_type == "FILE":
                        logger.info(f"Staged deletion of file: {deletion.file_path}")
                    else:
                        logger.info(
                            f"Commented out {deletion.target_type} '{deletion.target_name}' "
                            f"in {deletion.file_path}"
                        )
                else:
                    logger.warning(
                        f"Failed to delete '{deletion.target_name}': {result.get('error')}"
                    )
                    
            except Exception as e:
                logger.error(f"Error applying deletion '{deletion.target_name}': {e}")
                results.append({
                    "target": deletion.target_name,
                    "type": deletion.target_type,
                    "file": deletion.file_path,
                    "parent_class": deletion.parent_class,
                    "reason": deletion.reason,
                    "success": False,
                    "error": str(e),
                })
        
        # Clear pending deletions after processing
        self._pending_deletions = []
        
        return results    
    
    
    async def _delete_file(self, deletion: PendingDeletion) -> Dict[str, Any]:
        """
        Stage a whole file for deletion.
        
        The file will be removed when changes are committed.
        
        Args:
            deletion: PendingDeletion with target_type="FILE"
            
        Returns:
            Dict with 'success' and optionally 'error' or 'note'
        """
        from app.services.virtual_fs import ChangeType
        
        file_path = deletion.file_path
        
        # Check if file exists in VFS or on disk
        exists_in_vfs = self.vfs.file_exists(file_path)
        exists_on_disk = Path(self.project_dir, file_path).exists()
        
        if not exists_in_vfs and not exists_on_disk:
            logger.info(f"File {file_path} already doesn't exist, skipping deletion")
            return {
                "success": True, 
                "note": "File already doesn't exist"
            }
        
        # Stage deletion in VFS
        try:
            self.vfs.stage_change(file_path, "", ChangeType.DELETE)
            logger.info(f"Staged file deletion: {file_path}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to stage deletion of {file_path}: {e}")
            return {
                "success": False,
                "error": str(e)
            }    
    
    async def _comment_out_code(self, deletion: PendingDeletion) -> Dict[str, Any]:
        """
        Comment out a method/function/class in a file.
        Uses VFS to stage the change.
        """
        import ast
        
        # Read from VFS (may have pending changes)
        source = self.vfs.read_file(deletion.file_path)
        if source is None:
            return {"success": False, "error": f"File not found: {deletion.file_path}"}
        
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return {"success": False, "error": f"Cannot parse file: {e}"}
        
        # Find target node
        target_node = None
        
        if deletion.target_type == "METHOD" and deletion.parent_class:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == deletion.parent_class:
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if item.name == deletion.target_name:
                                target_node = item
                                break
                    break
                    
        elif deletion.target_type == "FUNCTION":
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == deletion.target_name:
                        target_node = node
                        break
                        
        elif deletion.target_type == "CLASS":
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef) and node.name == deletion.target_name:
                    target_node = node
                    break
        
        if not target_node:
            return {
                "success": False, 
                "error": f"{deletion.target_type} '{deletion.target_name}' not found in {deletion.file_path}"
            }
        
        # Comment out the code
        lines = source.splitlines(keepends=True)
        start_line = target_node.lineno - 1  # 0-indexed
        end_line = target_node.end_lineno     # exclusive
        
        # Build commented block
        header = [
            f"# === COMMENTED OUT (obsolete) ===\n",
            f"# Reason: {deletion.reason}\n",
            f"# Target: {deletion.target_type} {deletion.target_name}\n",
            f"#\n",
        ]
        
        footer = [
            f"#\n",
            f"# === END COMMENTED OUT ===\n",
        ]
        
        commented = []
        for line in lines[start_line:end_line]:
            if line.strip():
                commented.append(f"# {line}")
            else:
                commented.append("#\n")
        
        # Reconstruct
        new_lines = lines[:start_line] + header + commented + footer + lines[end_line:]
        new_source = "".join(new_lines)
        
        # Stage to VFS
        from app.services.virtual_fs import ChangeType
        self.vfs.stage_change(deletion.file_path, new_source, ChangeType.MODIFY)
        
        return {"success": True}    
    
    
    def _format_generated_code_for_context(self, code_blocks: List[ParsedCodeBlock]) -> str:
        """
        Format generated code for Orchestrator context.
        
        This is shown to Orchestrator when there's an error so it can see 
        exactly what Code Generator produced from its instruction.
        
        Args:
            code_blocks: List of generated code blocks
            
        Returns:
            Formatted string for inclusion in feedback
        """
        if not code_blocks:
            return ""
        
        parts = [
            "## 📝 Generated Code (from your instruction):",
            "Review this to understand what Code Generator produced:\n"
        ]
        
        for i, block in enumerate(code_blocks, 1):
            mode_str = block.mode if isinstance(block.mode, str) else str(block.mode)
            parts.append(f"### Block {i}: `{block.file_path}`")
            parts.append(f"**MODE:** `{mode_str}`")
            
            if block.target_class:
                parts.append(f"**TARGET_CLASS:** `{block.target_class}`")
            if block.target_method:
                parts.append(f"**TARGET_METHOD:** `{block.target_method}`")
            if block.target_function:
                parts.append(f"**TARGET_FUNCTION:** `{block.target_function}`")
            if block.insert_after:
                parts.append(f"**INSERT_AFTER:** `{block.insert_after}`")
            if block.insert_before:
                parts.append(f"**INSERT_BEFORE:** `{block.insert_before}`")
            
            # Truncate very long code
            code_preview = block.code
            code_lines = code_preview.split('\n')
            if len(code_lines) > 60:
                code_preview = '\n'.join(code_lines[:60])
                code_preview += f"\n\n... [{len(code_lines) - 60} more lines truncated]"
            
            parts.append(f"```python\n{code_preview}\n```")
            parts.append("")
        
        return "\n".join(parts)    
    
    
    def _format_new_files_context(
        self, 
        error_locations: Optional[Dict[str, List[int]]] = None, 
        max_tokens: int = 50000
    ) -> str:
        """
        Format content of ALL staged files (new and modified) for Orchestrator feedback.
        
        Shows files that were created or modified during this session so Orchestrator
        can see exactly what Code Generator produced and fix errors.
        
        Args:
            error_locations: Dict of {file_path: [line_numbers]} for highlighting
            max_tokens: Maximum tokens to include
            
        Returns:
            Formatted string with file contents, or empty string if no staged files
        """
        # Get all staged files content with token limit
        staged_content = self.vfs.get_all_staged_content_with_token_limit(max_tokens, error_locations)
        
        if not staged_content:
            logger.debug("_format_new_files_context: No staged files to include")
            return ""

        # Categorize files as NEW or MODIFIED
        new_files = set()
        modified_files = set()
        for file_path in staged_content.keys():
            change = self.vfs.get_change(file_path)
            if change and change.original_content is None:
                new_files.add(file_path)
            else:
                modified_files.add(file_path)

        parts = [
            "## 📁 Staged Files (preserved from previous iterations):",
            "",
            f"**New files:** {len(new_files)} | **Modified files:** {len(modified_files)}",
            "",
            "These files contain your generated code. Review them to identify errors.",
            "",
        ]
        
        for file_path, content in staged_content.items():
            # Determine file status
            if file_path in new_files:
                status = "🆕 NEW"
            else:
                status = "📝 MODIFIED"
            
            parts.append(f"### `{file_path}` ({status})")
            
            # Check if content was truncated
            if "... [lines" in content or "[truncated" in content:
                parts.append("⚠️ *File truncated due to size. Showing error context only.*")
            
            # Detect language for syntax highlighting
            if file_path.endswith('.py'):
                lang = "python"
            elif file_path.endswith(('.js', '.ts')):
                lang = "javascript"
            elif file_path.endswith('.json'):
                lang = "json"
            else:
                lang = ""
            
            parts.append(f"```{lang}")
            parts.append(content)
            parts.append("```")
            parts.append("")
        
        parts.append("---")
        parts.append("")
        parts.append("**Important:** These files exist in VFS and will be validated.")
        parts.append("You must either:")
        parts.append("1. **Fix errors** in these files by providing corrected instruction")
        parts.append("2. **Delete the file** if it's not needed (use DELETE operation)")
        parts.append("")
        
        logger.info(f"_format_new_files_context: Included {len(staged_content)} staged files in feedback")
        
        return "\n".join(parts)
    
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics"""
        return {
            "status": self.status.value,
            "pending_changes": len(self._pending_changes),
            "feedback_iterations": self.feedback_loop.orchestrator_revisions if self.feedback_loop else 0,
            "ai_validator_stats": self.ai_validator.stats,
        }
        
        