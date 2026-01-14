# app/agents/feedback_loop.py
"""
Feedback Loop State Management for Agent Mode.

Manages the state of validation cycles between:
- AI Validator (approves/rejects code)
- Orchestrator (revises instructions based on feedback)
- Code Generator (generates code from instructions)

This module is SEPARATE from Orchestrator to maintain clean architecture.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime


from app.agents.feedback_handler import (
    FeedbackSource,
    FeedbackHandler,
    ValidatorFeedback,
    UserFeedback,
    TestErrorFeedback,
    TestRunFeedback,  # NEW
    create_test_run_feedback_from_xml,  # NEW
)


logger = logging.getLogger(__name__)


class FeedbackAction(Enum):
    """Actions that can be taken in response to validator feedback."""
    ACCEPT_CRITIQUE = "accept_critique"      # Orchestrator agrees, will revise
    OVERRIDE_CRITIQUE = "override_critique"  # Orchestrator disagrees, proceed anyway
    ESCALATE_TO_USER = "escalate_to_user"    # Need user input
    MAX_RETRIES_REACHED = "max_retries"      # Exhausted retry limit


@dataclass
class ValidationAttempt:
    """Record of a single validation attempt."""
    attempt_number: int
    timestamp: datetime
    approved: bool
    confidence: float
    verdict: str
    critical_issues: List[str]
    model_used: str
    tokens_used: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt": self.attempt_number,
            "timestamp": self.timestamp.isoformat(),
            "approved": self.approved,
            "confidence": self.confidence,
            "verdict": self.verdict,
            "issues": self.critical_issues,
            "model": self.model_used,
            "tokens": self.tokens_used,
        }


@dataclass
class OrchestratorRevision:
    """Record of an orchestrator instruction revision."""
    revision_number: int
    timestamp: datetime
    reason: str  # Why revision was made
    previous_instruction_summary: str
    new_instruction_summary: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision_number,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "previous_summary": self.previous_instruction_summary,
            "new_summary": self.new_instruction_summary,
        }

@dataclass
class FeedbackRecord:
    """
    Record of feedback received and how it was handled.
    
    Tracks:
    - Source of feedback (validator, user, test)
    - Original message
    - Orchestrator's decision (accept/reject)
    - Reasoning for decision
    """
    source: FeedbackSource
    message: str
    timestamp: datetime
    orchestrator_accepted: bool
    orchestrator_reasoning: str
    resulted_in_revision: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "message": self.message[:200] + "..." if len(self.message) > 200 else self.message,
            "timestamp": self.timestamp.isoformat(),
            "orchestrator_accepted": self.orchestrator_accepted,
            "orchestrator_reasoning": self.orchestrator_reasoning,
            "resulted_in_revision": self.resulted_in_revision,
        }


# ============================================================================
# NEW: TEST RUN RECORD
# ============================================================================

@dataclass
class TestRunRecord:
    """
    NEW: Record of a test run from run_project_tests tool.
    
    Separate from ValidationAttempt to clearly distinguish:
    - ValidationAttempt: AI Validator results
    - TestRunRecord: unittest execution results
    """
    run_number: int
    timestamp: datetime
    test_file: str
    test_target: str
    success: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    tests_errors: int
    execution_time_ms: float
    failed_test_names: List[str]
    system_error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run": self.run_number,
            "timestamp": self.timestamp.isoformat(),
            "test_file": self.test_file,
            "test_target": self.test_target,
            "success": self.success,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_errors": self.tests_errors,
            "execution_time_ms": self.execution_time_ms,
            "failed_tests": self.failed_test_names,
            "system_error": self.system_error,
        }


@dataclass
class FeedbackLoopState:
    """
    Complete state of the feedback loop for a single user request.
    
    UPDATED: Now tracks feedback by source (validator vs user vs test).
    """
    # === Identifiers ===
    session_id: str
    user_request: str
    started_at: datetime = field(default_factory=datetime.now)
    
    # === Current State ===
    current_instruction: str = ""
    current_code: str = ""
    current_file_path: str = ""
    
    # === Counters ===
    validator_attempts: int = 0
    orchestrator_revisions: int = 0
    test_runs: int = 0  # NEW
    
    # === Limits (from config) ===
    max_validator_retries: int = 3
    max_orchestrator_revisions: int = 3
    max_test_runs: int = 5  # NEW
    
    # === History ===
    validation_history: List[ValidationAttempt] = field(default_factory=list)
    revision_history: List[OrchestratorRevision] = field(default_factory=list)
    test_run_history: List[TestRunRecord] = field(default_factory=list)  # NEW
    
    # === NEW: Feedback tracking by source ===
    feedback_history: List[FeedbackRecord] = field(default_factory=list)
    feedback_handler: FeedbackHandler = field(default_factory=FeedbackHandler)
    
    # === Final State ===
    final_action: Optional[FeedbackAction] = None
    completed_at: Optional[datetime] = None
    
    # === Metrics ===
    total_tokens_used: int = 0
    total_duration_ms: float = 0
    
    # === NEW: Track user overrides ===
    user_overrides_count: int = 0
    validator_overrides_count: int = 0  # When Orchestrator overrides validator
    
    def can_retry_validation(self) -> bool:
        """Check if we can make another validation attempt."""
        return self.validator_attempts < self.max_validator_retries
    
    def can_revise_instruction(self) -> bool:
        """Check if orchestrator can revise instruction."""
        return self.orchestrator_revisions < self.max_orchestrator_revisions
    
    def can_run_tests(self) -> bool:
        """NEW: Check if we can run more tests."""
        return self.test_runs < self.max_test_runs
    
    def get_remaining_test_runs(self) -> int:
        """Get number of remaining test runs allowed."""
        return max(0, self.max_test_runs - self.test_runs)    
    
    def add_validator_feedback(
        self,
        approved: bool,
        confidence: float,
        verdict: str,
        critical_issues: List[str],
        model_used: str,
    ) -> ValidatorFeedback:
        """
        NEW: Add validator feedback as separate parameter.
        
        This feedback is passed to Orchestrator separately from user feedback.
        """
        feedback = ValidatorFeedback(
            approved=approved,
            confidence=confidence,
            verdict=verdict,
            critical_issues=critical_issues,
            model_used=model_used,
        )
        self.feedback_handler.add_validator_feedback(feedback)
        
        # Also record validation attempt for backwards compatibility
        self.record_validation_attempt(
            approved=approved,
            confidence=confidence,
            verdict=verdict,
            critical_issues=critical_issues,
            model_used=model_used,
            tokens_used=0,  # Will be updated separately
        )
        
        return feedback
    
    def add_user_feedback(self, message: str, replaces_validator: bool = False) -> None:
        """
        NEW: Add user feedback — MUST be addressed by Orchestrator.
        
        Args:
            message: User's feedback
            replaces_validator: True if user is overriding validator's critique
        """
        self.feedback_handler.add_user_feedback(message, replaces_validator)
        
        if replaces_validator:
            self.user_overrides_count += 1
        
        logger.info(f"FeedbackLoop: Added user feedback (replaces_validator={replaces_validator})")
    
    def add_test_error(
        self,
        test_type: str,
        error_message: str,
        traceback: Optional[str] = None,
        file_path: Optional[str] = None,
        failed_code: Optional[str] = None,
    ) -> None:
        """
        NEW: Add test error for Orchestrator to debug.
        """
        self.feedback_handler.add_test_error(
            test_type=test_type,
            error_message=error_message,
            traceback=traceback,
            file_path=file_path,
            failed_code=failed_code,
        )
        logger.info(f"FeedbackLoop: Added test error ({test_type})")
    
    
    # ... конец метода add_test_error ...
    
    def add_test_failures(self, test_result: Dict[str, Any]) -> None:
        """
        Add test/runtime failures for Orchestrator to analyze.
        
        Called when validation with tests fails. Adds structured errors
        to the feedback handler so Orchestrator can debug them.
        
        Args:
            test_result: Dict containing:
                - test_errors: List of test failure dicts
                - runtime_errors: List of runtime error dicts
                - tests_run: Number of tests executed
                - tests_passed: Number passed
                - tests_failed: Number failed
        """
        test_errors = test_result.get("test_errors", [])
        runtime_errors = test_result.get("runtime_errors", [])
        
        # Добавляем ошибки тестов
        for err in test_errors:
            self.feedback_handler.add_test_error(
                test_type="test_failure",
                error_message=err.get("message", "Test failed"),
                traceback=err.get("traceback"),
                file_path=err.get("file_path"),
                failed_code=err.get("code"),
            )
        
        # Добавляем runtime ошибки
        for err in runtime_errors:
            self.feedback_handler.add_test_error(
                test_type="runtime",
                error_message=err.get("message", "Runtime error"),
                traceback=err.get("traceback"),
                file_path=err.get("file_path"),
                failed_code=err.get("code"),
            )
        
        logger.info(
            f"FeedbackLoop: Added test failures - "
            f"{len(test_errors)} test errors, {len(runtime_errors)} runtime errors, "
            f"tests: {test_result.get('tests_passed', 0)}/{test_result.get('tests_run', 0)} passed"
        )    
    
    
    # ========================================================================
    # NEW: TEST RUN METHODS
    # ========================================================================
    
    def record_test_run(self, result: TestRunFeedback) -> TestRunRecord:
        """
        NEW: Record a test run from run_project_tests tool.
        
        This creates both:
        1. TestRunRecord (for history tracking)
        2. Adds to FeedbackHandler (for Orchestrator prompt)
        
        Args:
            result: TestRunFeedback from the tool
        
        Returns:
            TestRunRecord for the history
        """
        self.test_runs += 1
        
        record = TestRunRecord(
            run_number=self.test_runs,
            timestamp=datetime.now(),
            test_file=result.test_file,
            test_target=result.test_target,
            success=result.success,
            tests_run=result.tests_run,
            tests_passed=result.tests_passed,
            tests_failed=result.tests_failed,
            tests_errors=result.tests_errors,
            execution_time_ms=result.execution_time_ms,
            failed_test_names=[ft.name for ft in result.failed_tests],
            system_error=result.system_error,
        )
        self.test_run_history.append(record)
        
        # Also add to feedback handler so Orchestrator sees it
        self.feedback_handler.add_test_run_result(result)
        
        logger.info(
            f"FeedbackLoop: Test run #{self.test_runs} - "
            f"{'PASSED' if result.success else 'FAILED'} "
            f"({result.tests_passed}/{result.tests_run} passed)"
        )
        
        return record
    
    def record_test_run_from_xml(self, xml_result: str, test_file: str) -> Optional[TestRunRecord]:
        """
        NEW: Convenience method to record test run from XML output.
        
        Args:
            xml_result: XML string from run_project_tests tool
            test_file: Path to test file
        
        Returns:
            TestRunRecord or None if parsing failed
        """
        feedback = create_test_run_feedback_from_xml(xml_result, test_file)
        if feedback:
            return self.record_test_run(feedback)
        return None
    
    def get_last_test_run(self) -> Optional[TestRunRecord]:
        """NEW: Get the most recent test run."""
        return self.test_run_history[-1] if self.test_run_history else None
    
    def has_passing_tests(self) -> bool:
        """NEW: Check if the most recent test run passed."""
        last_run = self.get_last_test_run()
        return last_run is not None and last_run.success
    
    def get_test_run_summary(self) -> Dict[str, Any]:
        """NEW: Get summary of all test runs."""
        if not self.test_run_history:
            return {"total_runs": 0, "passing_runs": 0, "failing_runs": 0}
        
        passing = sum(1 for r in self.test_run_history if r.success)
        return {
            "total_runs": len(self.test_run_history),
            "passing_runs": passing,
            "failing_runs": len(self.test_run_history) - passing,
            "remaining_quota": self.get_remaining_test_runs(),
            "last_result": "PASSED" if self.test_run_history[-1].success else "FAILED",
        }
    
    # ========================================================================
    # EXISTING METHODS (unchanged)
    # ========================================================================
    
    
    
    def get_feedback_for_orchestrator(self) -> Dict[str, str]:
        """
        NEW: Get all feedback formatted as SEPARATE parameters.
        
        Returns dict with keys:
        - validator_feedback: AI Validator's critique (can be overridden)
        - user_feedback: User's critique (MUST be addressed)
        - test_errors: Test execution errors (MUST be debugged)
        """
        return self.feedback_handler.get_feedback_for_orchestrator()
    
    def record_orchestrator_feedback_response(
        self,
        source: FeedbackSource,
        accepted: bool,
        reasoning: str,
        resulted_in_revision: bool,
    ) -> None:
        """
        NEW: Record how Orchestrator responded to feedback.
        """
        # Get the message from the appropriate source
        message = ""
        if source == FeedbackSource.VALIDATOR:
            if self.feedback_handler._validator_feedback:
                message = self.feedback_handler._validator_feedback.verdict
        elif source == FeedbackSource.USER:
            if self.feedback_handler._user_feedback:
                message = self.feedback_handler._user_feedback.message
        elif source == FeedbackSource.TEST_RUN:  # NEW
            last_run = self.feedback_handler.get_last_test_run()
            if last_run:
                message = f"Test run: {last_run.tests_passed}/{last_run.tests_run} passed"
        
        record = FeedbackRecord(
            source=source,
            message=message,
            timestamp=datetime.now(),
            orchestrator_accepted=accepted,
            orchestrator_reasoning=reasoning,
            resulted_in_revision=resulted_in_revision,
        )
        self.feedback_history.append(record)
        
        # Track overrides
        if source == FeedbackSource.VALIDATOR and not accepted:
            self.validator_overrides_count += 1
        
        logger.info(
            f"FeedbackLoop: Recorded orchestrator response to {source.value} "
            f"(accepted={accepted}, revision={resulted_in_revision})"
        )
    
    def record_validation_attempt(
        self,
        approved: bool,
        confidence: float,
        verdict: str,
        critical_issues: List[str],
        model_used: str,
        tokens_used: int,
    ) -> ValidationAttempt:
        """Record a new validation attempt."""
        self.validator_attempts += 1
        self.total_tokens_used += tokens_used
        
        attempt = ValidationAttempt(
            attempt_number=self.validator_attempts,
            timestamp=datetime.now(),
            approved=approved,
            confidence=confidence,
            verdict=verdict,
            critical_issues=critical_issues,
            model_used=model_used,
            tokens_used=tokens_used,
        )
        self.validation_history.append(attempt)
        
        logger.info(
            f"FeedbackLoop: Validation attempt {self.validator_attempts} - "
            f"{'APPROVED' if approved else 'REJECTED'} (confidence: {confidence:.2f})"
        )
        
        return attempt
    
    def record_orchestrator_revision(
        self,
        reason: str,
        previous_instruction: str,
        new_instruction: str,
    ) -> OrchestratorRevision:
        """Record an orchestrator instruction revision."""
        self.orchestrator_revisions += 1
        
        revision = OrchestratorRevision(
            revision_number=self.orchestrator_revisions,
            timestamp=datetime.now(),
            reason=reason,
            previous_instruction_summary=previous_instruction[:200] + "..." if len(previous_instruction) > 200 else previous_instruction,
            new_instruction_summary=new_instruction[:200] + "..." if len(new_instruction) > 200 else new_instruction,
        )
        self.revision_history.append(revision)
        
        logger.info(
            f"FeedbackLoop: Orchestrator revision {self.orchestrator_revisions} - {reason[:50]}"
        )
        
        return revision
    
    def complete(self, action: FeedbackAction) -> None:
        """Mark the feedback loop as complete."""
        self.final_action = action
        self.completed_at = datetime.now()
        self.total_duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000
        
        # Clear pending feedback
        self.feedback_handler.clear_feedback()
        
        logger.info(
            f"FeedbackLoop: Completed with action={action.value}, "
            f"attempts={self.validator_attempts}, revisions={self.orchestrator_revisions}, "
            f"duration={self.total_duration_ms:.0f}ms"
        )
    
    def get_last_validation(self) -> Optional[ValidationAttempt]:
        """Get the most recent validation attempt."""
        return self.validation_history[-1] if self.validation_history else None
    
    def get_last_issues(self) -> List[str]:
        """Get critical issues from last validation (for orchestrator feedback)."""
        last = self.get_last_validation()
        return last.critical_issues if last else []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization/logging."""
        return {
            "session_id": self.session_id,
            "user_request": self.user_request[:100] + "..." if len(self.user_request) > 100 else self.user_request,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "validator_attempts": self.validator_attempts,
            "orchestrator_revisions": self.orchestrator_revisions,
            "test_runs": self.test_runs,  # NEW
            "user_overrides_count": self.user_overrides_count,
            "validator_overrides_count": self.validator_overrides_count,
            "final_action": self.final_action.value if self.final_action else None,
            "total_tokens_used": self.total_tokens_used,
            "total_duration_ms": self.total_duration_ms,
            "validation_history": [v.to_dict() for v in self.validation_history],
            "revision_history": [r.to_dict() for r in self.revision_history],
            "test_run_history": [t.to_dict() for t in self.test_run_history],  # NEW
            "feedback_history": [f.to_dict() for f in self.feedback_history],
        }
    
    def get_summary(self) -> str:
        """Get human-readable summary of the feedback loop state."""
        status = "IN PROGRESS"
        if self.final_action:
            status = self.final_action.value.upper()
        
        last_validation = self.get_last_validation()
        last_val_status = "N/A"
        if last_validation:
            last_val_status = "✅ APPROVED" if last_validation.approved else "❌ REJECTED"
        
        last_test = self.get_last_test_run()
        last_test_status = "N/A"
        if last_test:
            last_test_status = "✅ PASSED" if last_test.success else f"❌ FAILED ({last_test.tests_failed} failures)"
        
        return (
            f"FeedbackLoop[{self.session_id[:8]}]: {status}\n"
            f"  Validator: {self.validator_attempts}/{self.max_validator_retries} attempts, last={last_val_status}\n"
            f"  Orchestrator: {self.orchestrator_revisions}/{self.max_orchestrator_revisions} revisions\n"
            f"  Test Runs: {self.test_runs}/{self.max_test_runs}, last={last_test_status}\n"  # NEW
            f"  User overrides: {self.user_overrides_count}\n"
            f"  Validator overrides: {self.validator_overrides_count}\n"
            f"  Tokens: {self.total_tokens_used}"
        )
        
    def add_validation_errors(self, validation_result: Dict[str, Any]) -> None:
        """
        NEW: Add validation errors from ChangeValidator.
        
        These are passed to Orchestrator for decision:
        - Fix the code
        - Explain why acceptable
        - Request user input
        """
        self.feedback_handler.add_validation_errors(validation_result)
        logger.info("FeedbackLoop: Added validation errors to feedback")
    
    def has_blocking_validation_errors(self) -> bool:
        """Check if there are blocking validation errors (syntax)."""
        if self.feedback_handler._validation_errors:
            return self.feedback_handler._validation_errors.is_blocking
        return False


def create_feedback_loop(
    session_id: str,
    user_request: str,
    max_validator_retries: int = 3,
    max_orchestrator_revisions: int = 3,
    max_test_runs: int = 5,  # NEW
) -> FeedbackLoopState:
    """
    Factory function to create a new FeedbackLoopState.
    
    Args:
        session_id: Unique identifier for this session
        user_request: Original user request
        max_validator_retries: Maximum AI Validator attempts
        max_orchestrator_revisions: Maximum Orchestrator revisions
        
    Returns:
        Initialized FeedbackLoopState
    """
    return FeedbackLoopState(
        session_id=session_id,
        user_request=user_request,
        max_validator_retries=max_validator_retries,
        max_orchestrator_revisions=max_orchestrator_revisions,
        max_test_runs=max_test_runs,  # NEW
    )
