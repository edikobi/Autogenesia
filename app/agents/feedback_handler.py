# app/agents/feedback_handler.py
"""
Feedback Handler for Agent Mode.

Manages different types of feedback:
1. VALIDATOR_FEEDBACK — from AI Validator (can be analyzed and optionally rejected by Orchestrator)
2. USER_FEEDBACK — from user (MUST be acted upon, cannot be rejected without justification)
3. TEST_ERRORS — from test execution (requires root cause analysis)

This separation is CRITICAL for proper agent behavior:
- Validator feedback: Orchestrator can analyze and decide to override
- User feedback: Orchestrator MUST address, can only reject after providing justification to user
- Test errors: Orchestrator must analyze logs and continue debugging
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.runtime_tester import RuntimeTestSummary, TestResult

logger = logging.getLogger(__name__)


class FeedbackSource(Enum):
    """Source of feedback — determines how Orchestrator must handle it."""
    VALIDATOR = "validator"      # From AI Validator — can be overridden
    USER = "user"                # From user — MUST be addressed
    TEST_ERROR = "test_error"    # From test execution — requires debugging
    SYNTAX_ERROR = "syntax"      # From syntax checker — must be fixed
    TEST_RUN = "test_run"        # NEW: From run_project_tests tool
    VALIDATION_ERROR = "validation"   # NEW: From ChangeValidator (technical errors)


class StagingErrorType(Enum):
    """Classification of staging errors with AI-friendly descriptions and solutions."""
    # Target not found errors
    CLASS_NOT_FOUND = "class_not_found"
    METHOD_NOT_FOUND = "method_not_found"
    FUNCTION_NOT_FOUND = "function_not_found"
    INSERT_PATTERN_NOT_FOUND = "insert_pattern_not_found"

    # Missing required parameters
    MISSING_TARGET_CLASS = "missing_target_class"
    MISSING_TARGET_METHOD = "missing_target_method"
    MISSING_TARGET_FUNCTION = "missing_target_function"

    # Mode/parser errors
    INVALID_MODE = "invalid_mode"
    PARSER_UNAVAILABLE = "parser_unavailable"
    
    # Syntax validation errors
    SYNTAX_VALIDATION_FAILED = "syntax_validation_failed"
    
    INVALID_CODE_FORMAT = "invalid_code_format"

    # Generic fallback
    UNKNOWN = "unknown"

# советы по ошибкам стенджинга
def get_staging_error_guidance(error_type: StagingErrorType) -> dict:
    """Returns AI-friendly description and solution algorithm for each staging error type."""
    guidance_map = {
        StagingErrorType.CLASS_NOT_FOUND: {
            "description": "The class specified in TARGET_CLASS does not exist in the file.",
            "cause": "Typo in class name, wrong file, or class was renamed/removed.",
            "solution": "1. Use read_file to verify the exact class name. 2. Check for typos (case-sensitive). 3. If class doesn't exist, use ADD_CLASS mode instead of REPLACE_CLASS. 4. If class is in different file, update FILE path.",
            "mode_hint": "Consider ADD_CLASS if creating new class",
        },
        StagingErrorType.METHOD_NOT_FOUND: {
            "description": "The method specified in TARGET_METHOD does not exist in the target class.",
            "cause": "Typo in method name, method is in different class, or method doesn't exist yet.",
            "solution": "1. Verify method name spelling (case-sensitive). 2. Check if method is in the correct class. 3. If method doesn't exist, use ADD_METHOD mode instead of REPLACE_METHOD. 4. If method is a standalone function, use REPLACE_FUNCTION with TARGET_FUNCTION.",
            "mode_hint": "Use ADD_METHOD to add new method, or REPLACE_FUNCTION if it's not in a class",
        },
        StagingErrorType.FUNCTION_NOT_FOUND: {
            "description": "The function specified in TARGET_FUNCTION does not exist at module level.",
            "cause": "Typo in function name, function is inside a class (method), or function doesn't exist.",
            "solution": "1. Verify function name spelling. 2. Check if function is actually a method inside a class. 3. If it's a method, use REPLACE_METHOD with TARGET_CLASS and TARGET_METHOD. 4. If function doesn't exist, use ADD_FUNCTION mode.",
            "mode_hint": "Use REPLACE_METHOD if target is inside a class",
        },
        StagingErrorType.INSERT_PATTERN_NOT_FOUND: {
            "description": "The pattern specified in INSERT_AFTER or INSERT_BEFORE was not found in the target.",
            "cause": "Pattern text doesn't match exactly, or target code structure changed.",
            "solution": "1. Read the current file content. 2. Find the exact text you want to insert after/before. 3. Use a unique substring that exists in the file. 4. Consider using a different insertion strategy (APPEND_FILE, or specific line reference).",
            "mode_hint": "Use APPEND_FILE to add at end, or specify exact line content",
        },
        StagingErrorType.MISSING_TARGET_CLASS: {
            "description": "MODE requires TARGET_CLASS but it was not provided.",
            "cause": "Instruction specified REPLACE_METHOD or INSERT_CLASS without TARGET_CLASS.",
            "solution": "1. Add TARGET_CLASS parameter with the class name. 2. If modifying a standalone function, use REPLACE_FUNCTION instead. 3. Verify the class exists in the file.",
            "mode_hint": "Add TARGET_CLASS or switch to REPLACE_FUNCTION",
        },
        StagingErrorType.MISSING_TARGET_METHOD: {
            "description": "MODE requires TARGET_METHOD but it was not provided.",
            "cause": "Instruction specified REPLACE_METHOD without TARGET_METHOD.",
            "solution": "1. Add TARGET_METHOD parameter with the method name. 2. Verify the method exists in the target class.",
            "mode_hint": "Add TARGET_METHOD parameter",
        },
        StagingErrorType.MISSING_TARGET_FUNCTION: {
            "description": "MODE requires TARGET_FUNCTION but it was not provided.",
            "cause": "Instruction specified REPLACE_FUNCTION without TARGET_FUNCTION.",
            "solution": "1. Add TARGET_FUNCTION parameter with the function name. 2. Verify the function exists at module level.",
            "mode_hint": "Add TARGET_FUNCTION parameter",
        },
        StagingErrorType.INVALID_MODE: {
            "description": "The specified MODE is not recognized.",
            "cause": "Typo in mode name or using unsupported mode.",
            "solution": "1. Use one of valid modes: REPLACE_FILE, REPLACE_CLASS, REPLACE_METHOD, REPLACE_FUNCTION, ADD_METHOD, ADD_FUNCTION, ADD_CLASS, INSERT_IMPORT, APPEND_FILE. 2. Check spelling and case.",
            "mode_hint": "Valid modes: REPLACE_FILE, REPLACE_METHOD, ADD_METHOD, etc.",
        },
        StagingErrorType.PARSER_UNAVAILABLE: {
            "description": "The code parser is not available to analyze the file structure.",
            "cause": "Tree-sitter parser failed to initialize.",
            "solution": "1. Use REPLACE_FILE mode to replace entire file content. 2. This is a system issue, not instruction issue.",
            "mode_hint": "Use REPLACE_FILE as fallback",
        },
        StagingErrorType.SYNTAX_VALIDATION_FAILED: {
            "description": "The applied code change breaks the file's syntax structure, making classes/methods unparseable.",
            "cause": "Common causes: 1) Wrong indentation level (Python is indent-sensitive). 2) Incomplete code block (missing closing brackets, quotes, or colons). 3) Previous code block in the same file already broke syntax, causing cascading failures. 4) Code was inserted at wrong position breaking existing structure.",
            "solution": "1. CHECK INDENTATION: Ensure code uses correct indentation (4 spaces for Python). Method bodies must be indented relative to class. 2. CHECK COMPLETENESS: Verify all brackets (), [], {} are balanced. Check all strings are properly closed. 3. CHECK PREVIOUS BLOCKS: If multiple blocks target same file, earlier block may have broken syntax. Fix that block first. 4. USE read_file TOOL: Read the current file content to see exact structure before modification. 5. SIMPLIFY: If complex insertion fails, try REPLACE_METHOD or REPLACE_CLASS to replace entire unit.",
            "mode_hint": "Check indentation (4 spaces), ensure code is complete, consider REPLACE_METHOD instead of INSERT",
        },
        StagingErrorType.INVALID_CODE_FORMAT: {
            "description": "Code block for ADD_NEW_FUNCTION must start with 'def' or 'async def'.",
            "cause": "Code doesn't start with function definition or has syntax error.",
            "solution": "1. Ensure code starts with 'def function_name():' or 'async def function_name():'. 2. Check for syntax errors. 3. Provide complete function definition.",
            "mode_hint": "ADD_NEW_FUNCTION requires a complete function definition",
        },
        StagingErrorType.UNKNOWN: {
            "description": "An unexpected staging error occurred.",
            "cause": "Unknown cause.",
            "solution": "1. Read the error message carefully. 2. Verify file path exists. 3. Check that code syntax is valid. 4. Try a simpler modification mode.",
            "mode_hint": "Try REPLACE_FILE as fallback",
        },
    }
    
    return guidance_map.get(error_type, guidance_map[StagingErrorType.UNKNOWN])


class FeedbackPriority(Enum):
    """Priority level for feedback processing."""
    CRITICAL = "critical"        # Must fix immediately (syntax errors, crashes)
    HIGH = "high"                # User feedback — must address
    MEDIUM = "medium"            # Validator feedback — analyze and decide
    LOW = "low"                  # Suggestions — optional


class UserAction(Enum):
    """Actions user can take when Validator rejects code."""
    CANCEL_REQUEST = "cancel"           # Cancel the entire request
    OVERRIDE_VALIDATOR = "override"     # Reject validator's critique, proceed to tests
    REPLACE_CRITIQUE = "replace"        # Replace validator critique with user's own
    ACCEPT_CRITIQUE = "accept"          # Accept validator critique as-is


@dataclass
class FeedbackItem:
    """
    Single feedback item with source tracking.
    
    Attributes:
        source: Who provided this feedback (validator, user, test)
        priority: How urgently it must be addressed
        message: The feedback content
        context: Additional context (code snippet, file path, etc.)
        timestamp: When feedback was received
        requires_response: Whether Orchestrator must explain its decision
    """
    source: FeedbackSource
    priority: FeedbackPriority
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    requires_response: bool = False  # True for user feedback
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "priority": self.priority.value,
            "message": self.message,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "requires_response": self.requires_response,
        }


@dataclass
class ValidatorFeedback:
    """
    Structured feedback from AI Validator.
    
    Passed as SEPARATE parameter to Orchestrator.
    Orchestrator can analyze and decide to accept or override.
    """
    approved: bool
    confidence: float
    verdict: str
    critical_issues: List[str]
    model_used: str
    
    # Orchestrator's response to this feedback
    orchestrator_accepted: Optional[bool] = None
    orchestrator_reasoning: Optional[str] = None
    
    def to_prompt_format(self) -> str:
        """Format for inclusion in Orchestrator prompt as SEPARATE section."""
        parts = []
        parts.append("=" * 60)
        parts.append("⚠️ AI VALIDATOR FEEDBACK (Source: VALIDATOR)")
        parts.append("=" * 60)
        parts.append(f"Status: {'✅ APPROVED' if self.approved else '❌ REJECTED'}")
        parts.append(f"Confidence: {self.confidence:.0%}")
        parts.append(f"Verdict: {self.verdict}")
        
        if self.critical_issues:
            parts.append("\nCritical Issues:")
            for i, issue in enumerate(self.critical_issues, 1):
                parts.append(f"  {i}. {issue}")
        
        parts.append("")
        parts.append("YOUR OPTIONS:")
        parts.append("1. ACCEPT critique → Revise your instruction")
        parts.append("2. OVERRIDE critique → Provide reasoning why validator is wrong")
        parts.append("=" * 60)
        
        return "\n".join(parts)


@dataclass
class UserFeedback:
    """
    Feedback from user — MUST be addressed.
    
    Orchestrator cannot ignore this. Must either:
    1. Act on it immediately
    2. Explain why it disagrees (but still attempt the user's request)
    """
    message: str
    replaces_validator: bool = False  # True if user replaced validator's critique
    original_validator_feedback: Optional[ValidatorFeedback] = None
    
    def to_prompt_format(self) -> str:
        """Format for inclusion in Orchestrator prompt as SEPARATE section."""
        parts = []
        parts.append("=" * 60)
        parts.append("🔴 USER FEEDBACK (Source: USER — MANDATORY)")
        parts.append("=" * 60)
        parts.append(f"Message: {self.message}")
        
        if self.replaces_validator:
            parts.append("")
            parts.append("⚠️ This feedback REPLACES the AI Validator's critique.")
            parts.append("User has chosen to override the validator with their own assessment.")
        
        parts.append("")
        parts.append("REQUIRED ACTION:")
        parts.append("You MUST address this feedback. Even if you disagree,")
        parts.append("you must attempt the user's request and explain your concerns.")
        parts.append("=" * 60)
        
        return "\n".join(parts)


@dataclass
class TestErrorFeedback:
    """
    Feedback from test execution — requires debugging.
    
    Contains error logs and context for Orchestrator to analyze.
    """
    test_type: str  # "syntax", "import", "unit", "integration"
    error_message: str
    traceback: Optional[str] = None
    file_path: Optional[str] = None
    failed_code: Optional[str] = None
    
    def to_prompt_format(self) -> str:
        """Format for inclusion in Orchestrator prompt."""
        parts = []
        parts.append("=" * 60)
        parts.append(f"🔴 TEST ERROR (Type: {self.test_type.upper()})")
        parts.append("=" * 60)
        
        if self.file_path:
            parts.append(f"File: {self.file_path}")
        
        parts.append(f"\nError: {self.error_message}")
        
        if self.traceback:
            parts.append(f"\nTraceback:\n```\n{self.traceback}\n```")
        
        if self.failed_code:
            parts.append(f"\nFailed Code:\n```\n{self.failed_code}\n```")
        
        parts.append("")
        parts.append("REQUIRED ACTION:")
        parts.append("1. Analyze the error and identify ROOT CAUSE")
        parts.append("2. Check if similar issues exist elsewhere")
        parts.append("3. Generate revised instruction to fix ALL related issues")
        parts.append("=" * 60)
        
        return "\n".join(parts)

@dataclass
class StagingErrorFeedback:
    """
    Staging errors: Code is valid python, but targets (files/classes/methods) are missing.
    """
    file_path: str
    mode: str
    error: str
    error_type: StagingErrorType = StagingErrorType.UNKNOWN
    target_class: Optional[str] = None
    target_method: Optional[str] = None
    target_function: Optional[str] = None
    insert_pattern: Optional[str] = None
    
    def to_prompt_format(self) -> str:
        guidance = get_staging_error_guidance(self.error_type)
        parts = []
        
        # Header
        parts.append("❌ STAGING ERROR: " + self.error_type.value.upper().replace('_', ' '))
        parts.append("=" * 60)
        
        # File and mode info
        parts.append(f"File: {self.file_path}")
        parts.append(f"Mode: {self.mode}")
        
        # Target information
        if self.target_class:
            parts.append(f"Target Class: {self.target_class}")
        if self.target_method:
            parts.append(f"Target Method: {self.target_method}")
        if self.target_function:
            parts.append(f"Target Function: {self.target_function}")
        if self.insert_pattern:
            parts.append(f"Insert Pattern: {self.insert_pattern}")
        
        parts.append("")
        
        # What went wrong
        parts.append("WHAT WENT WRONG:")
        parts.append(f"  {guidance['description']}")
        parts.append("")
        
        # Why this happens
        parts.append("WHY THIS HAPPENS:")
        parts.append(f"  {guidance['cause']}")
        parts.append("")
        
        # How to fix
        parts.append("HOW TO FIX:")
        solution_lines = guidance['solution'].split('. ')
        for step in solution_lines:
            step = step.strip()
            if step:
                parts.append(f"  {step}.")
        parts.append("")
        
        # Mode hint
        if guidance.get('mode_hint'):
            parts.append(f"💡 HINT: {guidance['mode_hint']}")
            parts.append("")
        
        # MODE CHECK: New section for mode verification
        parts.append("MODE CHECK:")
        parts.append("Verify that the correct code insertion mode was applied.")
        parts.append("Ensure the revised instruction uses the correct mode.")
        parts.append("")
        
        # Required action
        parts.append("REQUIRED ACTION:")
        parts.append("Write a corrected instruction with fixed MODE, TARGET, or file path.")
        parts.append("This error does NOT count against your iteration limit.")
        parts.append("=" * 60)
        
        return "\n".join(parts)

# ============================================================================
# NEW: TEST RUN FEEDBACK (from run_project_tests tool)
# ============================================================================

@dataclass
class FailedTest:
    """Single failed test details."""
    type: str  # "fail" or "error"
    name: str  # "tests.test_auth.TestAuth.test_login"
    traceback: str


@dataclass
class TestRunFeedback:
    """
    NEW: Structured feedback from run_project_tests tool.
    
    This is DIFFERENT from TestErrorFeedback:
    - TestErrorFeedback: Generic errors (import, syntax, runtime)
    - TestRunFeedback: Structured unittest results with pass/fail counts
    
    Orchestrator sees this as a distinct feedback type and understands
    it came from the run_project_tests tool, not from validation errors.
    """
    test_file: str
    test_target: str  # Class/method name or "entire file"
    success: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    tests_errors: int
    execution_time_ms: float
    failed_tests: List[FailedTest]
    raw_output: str  # Truncated unittest output
    system_error: Optional[str] = None  # Timeout, import error, etc.
    
    def to_prompt_format(self) -> str:
        """
        Format for Orchestrator prompt.
        
        Uses distinct header so Orchestrator knows this is from run_project_tests.
        """
        parts = []
        parts.append("=" * 60)
        
        if self.success:
            parts.append("✅ TEST RUN RESULT (Source: run_project_tests tool)")
        else:
            parts.append("❌ TEST RUN RESULT (Source: run_project_tests tool)")
        
        parts.append("=" * 60)
        parts.append(f"Test File: {self.test_file}")
        parts.append(f"Target: {self.test_target}")
        parts.append(f"Status: {'PASSED' if self.success else 'FAILED'}")
        parts.append("")
        parts.append("Summary:")
        parts.append(f"  • Tests run: {self.tests_run}")
        parts.append(f"  • Passed: {self.tests_passed}")
        parts.append(f"  • Failed: {self.tests_failed}")
        parts.append(f"  • Errors: {self.tests_errors}")
        parts.append(f"  • Time: {self.execution_time_ms:.0f}ms")
        
        if self.system_error:
            parts.append("")
            parts.append(f"⚠️ System Error: {self.system_error}")
        
        if self.failed_tests:
            parts.append("")
            parts.append("Failed Tests:")
            for i, ft in enumerate(self.failed_tests, 1):
                parts.append(f"\n  {i}. [{ft.type.upper()}] {ft.name}")
                # Indent traceback
                tb_lines = ft.traceback.split('\n')
                for line in tb_lines[:10]:  # Limit traceback lines
                    parts.append(f"     {line}")
                if len(tb_lines) > 10:
                    parts.append(f"     ... [{len(tb_lines) - 10} more lines]")
        
        if not self.success:
            parts.append("")
            parts.append("REQUIRED ACTION:")
            parts.append("1. Analyze each failed test")
            parts.append("2. Identify the root cause in YOUR generated code")
            parts.append("3. Revise instruction to fix the issues")
            parts.append("4. Consider running tests again after fix")
        
        parts.append("=" * 60)
        
        return "\n".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": "test_run",
            "test_file": self.test_file,
            "test_target": self.test_target,
            "success": self.success,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_errors": self.tests_errors,
            "execution_time_ms": self.execution_time_ms,
            "failed_tests": [
                {"type": ft.type, "name": ft.name, "traceback": ft.traceback[:200]}
                for ft in self.failed_tests
            ],
            "system_error": self.system_error,
        }


# ============================================================================
# NEW: VALIDATION ERROR FEEDBACK (from ChangeValidator)
# ============================================================================

@dataclass
class ValidationErrorFeedback:
    """
    Feedback from technical validation (syntax, imports, types, integration).
    
    This is DIFFERENT from AI Validator:
    - AI Validator: semantic check "does code solve the problem?"
    - ValidationErrorFeedback: technical check "does code compile/import correctly?"
    
    Orchestrator receives this and decides:
    1. Fix the issues (generate new code)
    2. Explain why issues are acceptable (legacy code, type stubs missing, etc.)
    3. Request user decision
    """
    validation_level: str  # "syntax", "imports", "types", "integration", "runtime", "tests"
    error_count: int
    warning_count: int
    issues: List[Dict[str, Any]]  # List of ValidationIssue.to_dict()
    affected_files: List[str]
    is_blocking: bool = False  # True only for syntax errors
    
    def to_prompt_format(self) -> str:
        """Format for Orchestrator prompt."""
        parts = []
        parts.append("=" * 60)
        
        if self.is_blocking:
            parts.append(f"🔴 BLOCKING VALIDATION ERRORS (Level: {self.validation_level.upper()})")
        else:
            parts.append(f"⚠️ VALIDATION ISSUES (Level: {self.validation_level.upper()})")
        
        parts.append("=" * 60)
        parts.append(f"Errors: {self.error_count} | Warnings: {self.warning_count}")
        parts.append(f"Affected files: {', '.join(self.affected_files)}")
        parts.append("")
        
        # Group issues by file
        issues_by_file: Dict[str, List[Dict]] = {}
        for issue in self.issues:
            file_path = issue.get("file_path", "<unknown>")
            if file_path not in issues_by_file:
                issues_by_file[file_path] = []
            issues_by_file[file_path].append(issue)
        
        for file_path, file_issues in issues_by_file.items():
            parts.append(f"📄 {file_path}:")
            for issue in file_issues[:5]:  # Max 5 issues per file
                severity = issue.get("severity", "error")
                line = issue.get("line", "?")
                message = issue.get("message", "Unknown error")
                icon = "❌" if severity == "error" else "⚠️"
                parts.append(f"  {icon} Line {line}: {message[:100]}")
            if len(file_issues) > 5:
                parts.append(f"  ... and {len(file_issues) - 5} more issues")
            parts.append("")
        
        parts.append("YOUR OPTIONS:")
        if self.is_blocking:
            parts.append("1. MUST FIX these errors - code cannot run with syntax errors")
        else:
            parts.append("1. FIX → Revise your code to address these issues")
            parts.append("2. EXPLAIN → If issues are in existing code or acceptable, explain why")
            parts.append("3. IGNORE → Mark as known issues (for type hints, legacy code)")
        
        parts.append("=" * 60)
        
        return "\n".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": "validation_error",
            "validation_level": self.validation_level,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": self.issues,
            "affected_files": self.affected_files,
            "is_blocking": self.is_blocking,
        }


@dataclass
class RuntimeTestFeedback:
    """
    Feedback from RuntimeTester — comprehensive runtime validation results.
    
    This captures:
    - Which files were tested and how
    - Which were skipped and why
    - Detailed error information for failures
    
    Used to give Orchestrator clear picture of what was/wasn't validated.
    """
    total_files: int
    passed: int
    failed: int
    skipped: int
    timeouts: int
    errors: int
    
    # Detailed results by category
    passed_files: List[Dict[str, Any]] = field(default_factory=list)
    failed_files: List[Dict[str, Any]] = field(default_factory=list)
    skipped_files: List[Dict[str, Any]] = field(default_factory=list)
    
    # Project analysis info
    project_size: str = "unknown"  # "small" | "medium" | "large"
    detected_frameworks: List[str] = field(default_factory=list)
    total_timeout_used: int = 0
    
    def to_prompt_format(self) -> str:
        """Format for Orchestrator prompt."""
        parts = []
        parts.append("=" * 60)
        
        if self.failed == 0 and self.errors == 0 and self.timeouts == 0:
            parts.append("✅ RUNTIME VALIDATION RESULTS")
        else:
            parts.append("❌ RUNTIME VALIDATION RESULTS")
        
        parts.append("=" * 60)
        parts.append("")
        parts.append("**Summary:**")
        parts.append(f"  • Total files: {self.total_files}")
        parts.append(f"  • Passed: {self.passed}")
        parts.append(f"  • Failed: {self.failed}")
        parts.append(f"  • Skipped: {self.skipped}")
        parts.append(f"  • Timeouts: {self.timeouts}")
        parts.append(f"  • Errors: {self.errors}")
        
        if self.detected_frameworks:
            parts.append(f"  • Frameworks detected: {', '.join(self.detected_frameworks)}")
        
        parts.append(f"  • Project size: {self.project_size}")
        parts.append("")
        
        # Failed files (most important)
        if self.failed_files:
            parts.append("**❌ FAILED FILES (must fix):**")
            for f in self.failed_files[:10]:
                parts.append(f"  • `{f['file_path']}` ({f.get('app_type', 'unknown')})")
                parts.append(f"    Error: {f.get('message', 'Unknown error')}")
                if f.get('suggestion'):
                    parts.append(f"    💡 {f['suggestion']}")
            if len(self.failed_files) > 10:
                parts.append(f"  ... and {len(self.failed_files) - 10} more")
            parts.append("")
        
        # Skipped files (informational)
        if self.skipped_files:
            parts.append("**⚠️ SKIPPED FILES (not tested):**")
            for f in self.skipped_files[:5]:
                parts.append(f"  • `{f['file_path']}` ({f.get('app_type', 'unknown')})")
                parts.append(f"    Reason: {f.get('message', 'Skipped')}")
            if len(self.skipped_files) > 5:
                parts.append(f"  ... and {len(self.skipped_files) - 5} more")
            parts.append("")
        
        # Passed files (brief)
        if self.passed_files:
            parts.append(f"**✅ PASSED FILES ({len(self.passed_files)}):**")
            for f in self.passed_files[:5]:
                parts.append(f"  • `{f['file_path']}` ({f.get('app_type', 'unknown')})")
            if len(self.passed_files) > 5:
                parts.append(f"  ... and {len(self.passed_files) - 5} more")
            parts.append("")
        
        if self.failed > 0 or self.errors > 0 or self.timeouts > 0:
            parts.append("**REQUIRED ACTION:**")
            parts.append("1. Fix the failing files — they have runtime errors")
            parts.append("2. Check timeout files for infinite loops")
            parts.append("3. Skipped files were not tested — manual testing may be needed")
        else:
            parts.append("**STATUS:** All testable files passed runtime validation.")
            if self.skipped > 0:
                parts.append(f"Note: {self.skipped} file(s) were skipped (web apps, missing deps, etc.)")
        
        parts.append("=" * 60)
        
        return "\n".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": "runtime_test",
            "total_files": self.total_files,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "timeouts": self.timeouts,
            "errors": self.errors,
            "passed_files": self.passed_files,
            "failed_files": self.failed_files,
            "skipped_files": self.skipped_files,
            "project_size": self.project_size,
            "detected_frameworks": self.detected_frameworks,
        }


def create_runtime_test_feedback(summary_dict: Dict[str, Any]) -> Optional[RuntimeTestFeedback]:
    """
    Create RuntimeTestFeedback from RuntimeTestSummary.to_dict().
    
    Args:
        summary_dict: Dict from RuntimeTestSummary.to_dict()
        
    Returns:
        RuntimeTestFeedback or None if no results
    """
    if not summary_dict or summary_dict.get("total_files", 0) == 0:
        return None
    
    results = summary_dict.get("results", [])
    
    passed_files = [r for r in results if r.get("status") == "passed"]
    failed_files = [r for r in results if r.get("status") in ("failed", "error")]
    skipped_files = [r for r in results if r.get("status") == "skipped"]
    timeout_files = [r for r in results if r.get("status") == "timeout"]
    
    # Add timeout files to failed for simplicity
    failed_files.extend(timeout_files)
    
    analysis = summary_dict.get("analysis", {})
    
    return RuntimeTestFeedback(
        total_files=summary_dict.get("total_files", 0),
        passed=summary_dict.get("passed", 0),
        failed=summary_dict.get("failed", 0),
        skipped=summary_dict.get("skipped", 0),
        timeouts=summary_dict.get("timeouts", 0),
        errors=summary_dict.get("errors", 0),
        passed_files=passed_files,
        failed_files=failed_files,
        skipped_files=skipped_files,
        project_size=analysis.get("size_category", "unknown"),
        detected_frameworks=list(analysis.get("detected_frameworks", {}).keys()),
        total_timeout_used=analysis.get("total_timeout_seconds", 0),
    )


def create_validation_error_feedback(
    validation_result: Dict[str, Any],
    level: str = "all",
) -> Optional[ValidationErrorFeedback]:
    """
    Create ValidationErrorFeedback from ChangeValidator result.
    
    Args:
        validation_result: Dict from ValidationResult.to_dict()
        level: Filter by level ("syntax", "imports", "types", etc.) or "all"
    
    Returns:
        ValidationErrorFeedback or None if no issues
    """
    issues = validation_result.get("issues", [])
    
    if level != "all":
        issues = [i for i in issues if i.get("level") == level]
    
    if not issues:
        return None
    
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    affected_files = list(set(i.get("file_path", "") for i in issues if i.get("file_path")))
    
    # Syntax errors are blocking
    is_blocking = any(i.get("level") == "syntax" and i.get("severity") == "error" for i in issues)
    
    return ValidationErrorFeedback(
        validation_level=level if level != "all" else "mixed",
        error_count=len(errors),
        warning_count=len(warnings),
        issues=issues,
        affected_files=affected_files,
        is_blocking=is_blocking,
    )


def create_test_run_feedback_from_xml(xml_result: str, test_file: str) -> Optional[TestRunFeedback]:
    """
    Create TestRunFeedback from run_project_tests XML output.
    
    Args:
        xml_result: XML string from run_project_tests tool
        test_file: Original test file path
    
    Returns:
        TestRunFeedback or None if parsing fails
    """
    from app.tools.run_project_tests import parse_test_result_xml
    
    result = parse_test_result_xml(xml_result)
    if not result:
        return None
    
    # Convert to FailedTest objects
    failed_tests = [
        FailedTest(
            type=ft.get("type", "fail"),
            name=ft.get("name", "unknown"),
            traceback=ft.get("traceback", ""),
        )
        for ft in result.failed_tests
    ]
    
    # Extract test_target from XML
    import re
    target_match = re.search(r'<test_target>([^<]+)</test_target>', xml_result)
    test_target = target_match.group(1) if target_match else "unknown"
    
    return TestRunFeedback(
        test_file=test_file,
        test_target=test_target,
        success=result.success,
        tests_run=result.tests_run,
        tests_passed=result.tests_passed,
        tests_failed=result.tests_failed,
        tests_errors=result.tests_errors,
        execution_time_ms=result.execution_time_ms,
        failed_tests=failed_tests,
        raw_output=result.test_output,
        system_error=result.error_message,
    )

@dataclass 
class OrchestratorFeedbackResponse:
    """
    Orchestrator's response to feedback.
    
    Used to track how Orchestrator handled each feedback item.
    """
    feedback_source: FeedbackSource
    accepted: bool
    reasoning: str
    action_taken: str  # "revised_instruction", "override", "escalate_to_user"
    revised_instruction: Optional[str] = None


class FeedbackHandler:
    """
    Centralized handler for all feedback types.
    
    Responsibilities:
    1. Categorize incoming feedback by source
    2. Format feedback for Orchestrator prompt (as SEPARATE sections)
    3. Track Orchestrator's responses to feedback
    4. Enforce rules (user feedback cannot be ignored)
    """
    
    def __init__(self):
        self.pending_feedback: List[FeedbackItem] = []
        self.processed_feedback: List[tuple[FeedbackItem, OrchestratorFeedbackResponse]] = []
        self._validator_feedback: Optional[ValidatorFeedback] = None
        self._user_feedback: Optional[UserFeedback] = None
        self._test_errors: List[TestErrorFeedback] = []
        self._test_run_results: List[TestRunFeedback] = []  # NEW
        self._validation_errors: Optional[ValidationErrorFeedback] = None  # NEW
        self._runtime_test_feedback: Optional[RuntimeTestFeedback] = None  # NEW
        self._staging_errors: List[StagingErrorFeedback] = []

    
    def add_validator_feedback(self, feedback: ValidatorFeedback) -> None:
        """Add feedback from AI Validator."""
        self._validator_feedback = feedback
        logger.info(f"FeedbackHandler: Added validator feedback (approved={feedback.approved})")
    
    def add_user_feedback(self, message: str, replaces_validator: bool = False) -> None:
        """
        Add feedback from user.
        
        Args:
            message: User's feedback message
            replaces_validator: True if user is replacing validator's critique
        """
        self._user_feedback = UserFeedback(
            message=message,
            replaces_validator=replaces_validator,
            original_validator_feedback=self._validator_feedback if replaces_validator else None,
        )
        logger.info(f"FeedbackHandler: Added user feedback (replaces_validator={replaces_validator})")
    
    
    def add_test_error(
        self,
        test_type: str,
        error_message: str,
        traceback: Optional[str] = None,
        file_path: Optional[str] = None,
        failed_code: Optional[str] = None,
    ) -> None:
        """Add test error feedback (from validation, not from run_project_tests)."""
        self._test_errors.append(TestErrorFeedback(
            test_type=test_type,
            error_message=error_message,
            traceback=traceback,
            file_path=file_path,
            failed_code=failed_code,
        ))
        logger.info(f"FeedbackHandler: Added test error ({test_type})")
    
    def add_test_run_result(self, result: TestRunFeedback) -> None:
        """
        NEW: Add test run result from run_project_tests tool.
        
        This is tracked separately from test_errors to give Orchestrator
        clear distinction between validation errors and unittest results.
        """
        self._test_run_results.append(result)
        logger.info(
            f"FeedbackHandler: Added test run result "
            f"(file={result.test_file}, success={result.success}, "
            f"passed={result.tests_passed}/{result.tests_run})"
        )
    
    
    def add_test_run_result_from_xml(self, xml_result: str, test_file: str) -> Optional[TestRunFeedback]:
        """
        NEW: Convenience method to add test run result from XML.
        
        Args:
            xml_result: XML string from run_project_tests
            test_file: Test file path
        
        Returns:
            Created TestRunFeedback or None if parsing failed
        """
        feedback = create_test_run_feedback_from_xml(xml_result, test_file)
        if feedback:
            self.add_test_run_result(feedback)
        return feedback
    
    
    def add_staging_error(
        self,
        file_path: str,
        mode: str,
        error: str,
        error_type: StagingErrorType = StagingErrorType.UNKNOWN,
        target_class: Optional[str] = None,
        target_method: Optional[str] = None,
        target_function: Optional[str] = None,
        insert_pattern: Optional[str] = None,
    ) -> None:
        """Add feedback about staging failure."""
        # Sanitize error_type to prevent crashes on None
        if error_type is None:
            error_type = StagingErrorType.UNKNOWN
            
        self._staging_errors.append(StagingErrorFeedback(
            file_path=file_path,
            mode=mode,
            error=error,
            error_type=error_type,
            target_class=target_class,
            target_method=target_method,
            target_function=target_function,
            insert_pattern=insert_pattern,
        ))
        logger.info(f"FeedbackHandler: Added staging error for {file_path} (type={error_type.value})")
    
    
    
    
    def get_feedback_for_orchestrator(self) -> Dict[str, str]:
        """
        Get ALL feedback formatted for Orchestrator prompt.
        
        Returns dict with SEPARATE keys for each feedback type.
        """
        result = {
            "validator_feedback": "",
            "user_feedback": "",
            "test_errors": "",
            "test_run_results": "",
            "validation_errors": "",
            "runtime_test_feedback": "",  # NEW
        }
        
        if self._validator_feedback and not self._validator_feedback.approved:
            result["validator_feedback"] = self._validator_feedback.to_prompt_format()
        
        if self._user_feedback:
            result["user_feedback"] = self._user_feedback.to_prompt_format()
        
        if self._test_errors:
            error_parts = []
            for error in self._test_errors:
                error_parts.append(error.to_prompt_format())
            result["test_errors"] = "\n\n".join(error_parts)
        
        if self._test_run_results:
            run_parts = []
            for run_result in self._test_run_results:
                run_parts.append(run_result.to_prompt_format())
            result["test_run_results"] = "\n\n".join(run_parts)
        
        if self._validation_errors:
            result["validation_errors"] = self._validation_errors.to_prompt_format()
        
        # NEW: Add runtime test feedback
        if self._runtime_test_feedback:
            result["runtime_test_feedback"] = self._runtime_test_feedback.to_prompt_format()
        
        if self._staging_errors:
            parts = [e.to_prompt_format() for e in self._staging_errors]
            result["staging_errors"] = "\n\n".join(parts)
            
        return result
        
    
    
    def has_mandatory_feedback(self) -> bool:
        """Check if there's feedback that MUST be addressed (user or test errors)."""
        return self._user_feedback is not None or len(self._test_errors) > 0
    
    def has_any_feedback(self) -> bool:
        """Check if there's any pending feedback."""
        has_validator = self._validator_feedback is not None and not self._validator_feedback.approved
        return has_validator or self.has_mandatory_feedback()
    
    
    
    def clear_feedback(self) -> None:
        """Clear all pending feedback after processing."""
        self._validator_feedback = None
        self._user_feedback = None
        self._test_errors = []
        self._test_run_results = []
        self._validation_errors = None      # NEW: was missing
        self._runtime_test_feedback = None  # NEW
        self._staging_errors = []
        logger.info("FeedbackHandler: Cleared all feedback")    
    
    
    
    def record_orchestrator_response(
        self,
        source: FeedbackSource,
        accepted: bool,
        reasoning: str,
        action_taken: str,
        revised_instruction: Optional[str] = None,
    ) -> None:
        """Record how Orchestrator responded to feedback."""
        response = OrchestratorFeedbackResponse(
            feedback_source=source,
            accepted=accepted,
            reasoning=reasoning,
            action_taken=action_taken,
            revised_instruction=revised_instruction,
        )
        
        # Update validator feedback if applicable
        if source == FeedbackSource.VALIDATOR and self._validator_feedback:
            self._validator_feedback.orchestrator_accepted = accepted
            self._validator_feedback.orchestrator_reasoning = reasoning
        
        logger.info(f"FeedbackHandler: Recorded response for {source.value} (accepted={accepted})")

    def add_validation_errors(self, validation_result: Dict[str, Any]) -> None:
        """
        NEW: Add validation errors from ChangeValidator.
        
        Args:
            validation_result: Dict from ValidationResult.to_dict()
        """
        feedback = create_validation_error_feedback(validation_result)
        if feedback:
            self._validation_errors = feedback
            logger.info(
                f"FeedbackHandler: Added validation errors "
                f"(errors={feedback.error_count}, warnings={feedback.warning_count})"
            )
        else:
            self._validation_errors = None


    def add_runtime_test_feedback(self, summary_dict: Dict[str, Any]) -> None:
        """
        NEW: Add runtime test feedback from RuntimeTestSummary.
        
        Args:
            summary_dict: Dict from RuntimeTestSummary.to_dict() or ValidationResult.runtime_test_summary
        """
        feedback = create_runtime_test_feedback(summary_dict)
        if feedback:
            self._runtime_test_feedback = feedback
            logger.info(
                f"FeedbackHandler: Added runtime test feedback "
                f"(passed={feedback.passed}, failed={feedback.failed}, skipped={feedback.skipped})"
            )
        else:
            self._runtime_test_feedback = None


def handle_user_validation_action(
    action: UserAction,
    validator_feedback: ValidatorFeedback,
    user_message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process user's action on validator rejection.
    
    Args:
        action: What user chose to do
        validator_feedback: The validator's original feedback
        user_message: User's custom message (for REPLACE_CRITIQUE action)
    
    Returns:
        Dict with processed feedback ready for Orchestrator
    """
    handler = FeedbackHandler()
    
    if action == UserAction.CANCEL_REQUEST:
        return {
            "cancelled": True,
            "reason": "User cancelled the request after validator rejection",
        }
    
    elif action == UserAction.OVERRIDE_VALIDATOR:
        # User rejects validator's critique — code goes to tests
        return {
            "cancelled": False,
            "proceed_to_tests": True,
            "validator_overridden": True,
            "validator_feedback": validator_feedback.to_prompt_format(),
        }
    
    elif action == UserAction.REPLACE_CRITIQUE:
        # User provides their own critique (treated as validator feedback)
        handler.add_validator_feedback(ValidatorFeedback(
            approved=False,
            confidence=1.0,  # User is always confident
            verdict=user_message or "User provided custom critique",
            critical_issues=[user_message] if user_message else [],
            model_used="user",
        ))
        return {
            "cancelled": False,
            "proceed_to_tests": False,
            "feedback": handler.get_feedback_for_orchestrator(),
            "feedback_source": "user_as_validator",
        }
    
    elif action == UserAction.ACCEPT_CRITIQUE:
        # User accepts validator critique — send to Orchestrator unchanged
        handler.add_validator_feedback(validator_feedback)
        return {
            "cancelled": False,
            "proceed_to_tests": False,
            "feedback": handler.get_feedback_for_orchestrator(),
            "feedback_source": "validator",
        }
    
    return {"cancelled": False, "error": "Unknown action"}