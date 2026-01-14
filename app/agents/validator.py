# app/agents/validator.py
"""
AI Validator Agent for Agent Mode.

Validates that generated code actually addresses the user's request.
Uses lightweight models (Qwen/DeepSeek) for fast validation.

Key principles:
- Validator answers ONE question: "Does this code solve the problem?"
- Does NOT suggest improvements or alternatives
- Binary decision: APPROVE or REJECT
- Focuses on critical issues only (syntax errors, wrong logic)
- Ignores style, naming, edge cases (unless specifically requested)
"""

from __future__ import annotations
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from config.settings import cfg
from app.llm.api_client import call_llm
from app.llm.prompt_templates import format_ai_validator_prompt
from app.utils.token_counter import TokenCounter

if TYPE_CHECKING:
    from app.agents.feedback_handler import ValidatorFeedback

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class AIValidationRequest:
    """Request for AI validation."""
    user_request: str           # Original user's request
    orchestrator_instruction: str  # Instruction given to Code Generator
    original_content: str       # File content before changes (or empty for new file)
    proposed_code: str          # Generated code to validate
    file_path: str              # Target file path


@dataclass
class AIValidationResult:
    """Result of AI validation."""
    approved: bool              # Binary decision
    confidence: float           # 0.0 - 1.0
    core_request: str           # Extracted core request
    verdict: str                # One-sentence explanation
    critical_issues: List[str]  # Issues if rejected (max 3)
    model_used: str             # Which model was used
    tokens_used: int            # Total tokens consumed
    duration_ms: float          # Time taken
    raw_response: str           # Raw LLM response (for debugging)
    parse_error: Optional[str] = None  # If JSON parsing failed
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "confidence": self.confidence,
            "core_request": self.core_request,
            "verdict": self.verdict,
            "critical_issues": self.critical_issues,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "duration_ms": self.duration_ms,
        }


# Alias for backward compatibility with tests
ValidationResult = AIValidationResult


# ============================================================================
# VALIDATOR CLASS
# ============================================================================

class AIValidator:
    """
    AI Validator for Agent Mode.
    
    Validates that generated code addresses the user's request.
    Selects model based on context size:
    - < 10k tokens: Qwen (fast, cheap)
    - >= 10k tokens: DeepSeek (handles large contexts)
    """
    
    def __init__(self):
        self.token_counter = TokenCounter()
        self._total_validations = 0
        self._total_approvals = 0
        self._total_tokens = 0
    
    async def validate(
        self,
        request: Optional[AIValidationRequest] = None,
        *,
        # Alternative: pass arguments directly
        user_request: Optional[str] = None,
        orchestrator_instruction: Optional[str] = None,
        original_content: Optional[str] = None,
        proposed_code: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> AIValidationResult:
        """
        Validate generated code against user request.
        
        Can be called two ways:
        1. With AIValidationRequest object:
           await validator.validate(request)
           
        2. With keyword arguments:
           await validator.validate(
               user_request="...",
               orchestrator_instruction="...",
               original_content="...",
               proposed_code="...",
               file_path="..."
           )
        
        Args:
            request: AIValidationRequest with all context (option 1)
            user_request: Original user's request (option 2)
            orchestrator_instruction: Instruction for Code Generator (option 2)
            original_content: Original file content (option 2)
            proposed_code: Generated code to validate (option 2)
            file_path: Target file path (option 2)
            
        Returns:
            AIValidationResult with decision and metadata
        """
        start_time = time.time()
        
        # Handle both calling conventions
        if request is None:
            # Build request from keyword arguments
            if user_request is None or proposed_code is None:
                raise ValueError(
                    "Either 'request' or both 'user_request' and 'proposed_code' must be provided"
                )
            request = AIValidationRequest(
                user_request=user_request,
                orchestrator_instruction=orchestrator_instruction or "",
                original_content=original_content or "",
                proposed_code=proposed_code,
                file_path=file_path or "",
            )
        
        # Calculate context size for model selection
        context_size = self._calculate_context_size(request)
        model = cfg.get_ai_validator_model(context_size)
        
        logger.info(
            f"AIValidator: Starting validation (context={context_size} tokens, "
            f"model={cfg.get_model_display_name(model)})"
        )
        
        # Build prompts
        prompts = format_ai_validator_prompt(
            user_request=request.user_request,
            orchestrator_instruction=request.orchestrator_instruction,
            original_content=request.original_content,
            proposed_code=request.proposed_code,
            file_path=request.file_path,
        )
        
        messages = [
            {"role": "system", "content": prompts["system"]},
            {"role": "user", "content": prompts["user"]},
        ]
        
        try:
            # Call LLM
            response = await call_llm(
                model=model,
                messages=messages,
                temperature=0,  # Deterministic for consistency
                max_tokens=2500,  # Short response expected
            )
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Parse response
            result = self._parse_response(
                response=response,
                model=model,
                duration_ms=duration_ms,
            )
            
            # Update stats
            self._total_validations += 1
            if result.approved:
                self._total_approvals += 1
            self._total_tokens += result.tokens_used
            
            logger.info(
                f"AIValidator: {'✅ APPROVED' if result.approved else '❌ REJECTED'} "
                f"(confidence={result.confidence:.2f}, time={duration_ms:.0f}ms)"
            )
            
            return result
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"AIValidator error: {e}")
            
            # Return conservative rejection on error
            return AIValidationResult(
                approved=False,
                confidence=0.0,
                core_request="[Error during validation]",
                verdict=f"Validation failed: {str(e)[:100]}",
                critical_issues=["Validation system error - please retry"],
                model_used=model,
                tokens_used=0,
                duration_ms=duration_ms,
                raw_response="",
                parse_error=str(e),
            )
    
    def _calculate_context_size(self, request: AIValidationRequest) -> int:
        """Calculate total context size in tokens."""
        total = 0
        total += self.token_counter.count(request.user_request)
        total += self.token_counter.count(request.orchestrator_instruction)
        total += self.token_counter.count(request.original_content)
        total += self.token_counter.count(request.proposed_code)
        return total
    
    def _parse_response(
        self,
        response: str,
        model: str,
        duration_ms: float,
    ) -> AIValidationResult:
        """
        Parse LLM response into structured result.
        
        Handles:
        - Clean JSON
        - JSON wrapped in markdown code blocks
        - Malformed JSON (attempts recovery)
        """
        raw_response = response
        tokens_used = self.token_counter.count(response)
        
        # Try to extract JSON
        json_str = self._extract_json(response)
        
        if not json_str:
            logger.warning("AIValidator: No JSON found in response, attempting recovery")
            return self._create_fallback_result(
                raw_response=raw_response,
                model=model,
                duration_ms=duration_ms,
                tokens_used=tokens_used,
                parse_error="No JSON found in response",
            )
        
        try:
            data = json.loads(json_str)
            
            return AIValidationResult(
                approved=bool(data.get("approved", False)),
                confidence=float(data.get("confidence", 0.5)),
                core_request=str(data.get("core_request", "Unknown")),
                verdict=str(data.get("verdict", "No verdict provided")),
                critical_issues=list(data.get("critical_issues", [])),
                model_used=model,
                tokens_used=tokens_used,
                duration_ms=duration_ms,
                raw_response=raw_response,
            )
            
        except json.JSONDecodeError as e:
            logger.warning(f"AIValidator: JSON parse error: {e}")
            return self._create_fallback_result(
                raw_response=raw_response,
                model=model,
                duration_ms=duration_ms,
                tokens_used=tokens_used,
                parse_error=f"JSON parse error: {e}",
            )
    
    def _extract_json(self, text: str) -> Optional[str]:
        """
        Extract JSON from text, handling various formats.
        
        DeepSeek V3 может возвращать:
        1. Чистый JSON
        2. JSON в markdown блоке
        3. JSON с текстом до/после
        4. Несколько JSON блоков (берём первый с "approved")
        """
        if not text:
            return None
        
        text = text.strip()
        
        # Case 1: Already clean JSON (starts with { ends with })
        if text.startswith("{") and text.endswith("}"):
            # Проверяем что это валидный JSON с нужными полями
            if '"approved"' in text:
                return text
        
        # Case 2: JSON в markdown блоке ```json ... ```
        # Используем более надёжный паттерн
        json_block_matches = re.findall(
            r'```(?:json)?\s*\n(\{[\s\S]*?\})\s*\n```',
            text
        )
        for match in json_block_matches:
            if '"approved"' in match:
                return match.strip()
        
        # Case 3: JSON блок без markdown, но с текстом вокруг
        # Ищем структуру {...} содержащую "approved"
        # Используем более точный паттерн с учётом вложенности
        brace_depth = 0
        start_idx = None
        
        for i, char in enumerate(text):
            if char == '{':
                if brace_depth == 0:
                    start_idx = i
                brace_depth += 1
            elif char == '}':
                brace_depth -= 1
                if brace_depth == 0 and start_idx is not None:
                    potential_json = text[start_idx:i + 1]
                    if '"approved"' in potential_json:
                        # Пробуем распарсить
                        try:
                            json.loads(potential_json)
                            return potential_json
                        except json.JSONDecodeError:
                            continue  # Попробуем следующий блок
                    start_idx = None
        
        # Case 4: Fallback — простой regex для JSON-подобной структуры
        simple_match = re.search(
            r'\{\s*"approved"\s*:\s*(?:true|false)[\s\S]*?\}',
            text,
            re.IGNORECASE
        )
        if simple_match:
            potential = simple_match.group(0)
            # Пытаемся найти полный JSON начиная с этой позиции
            start = simple_match.start()
            brace_count = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return text[start:i + 1]
        
        return None    
    
    def _create_fallback_result(
        self,
        raw_response: str,
        model: str,
        duration_ms: float,
        tokens_used: int,
        parse_error: str,
    ) -> AIValidationResult:
        """
        Create a fallback result when JSON parsing fails.
        
        ВАЖНО: При сбое парсинга возвращаем approved=True!
        
        Логика:
        - Ложное отклонение (false reject) → бесконечный цикл исправлений
        - Ложное одобрение (false approve) → тесты поймают реальные баги
        
        Тесты — более надёжный способ найти баги, чем сломанный парсинг.
        """
        logger.warning(
            f"AIValidator: Falling back to APPROVE due to parse error: {parse_error}. "
            f"Raw response preview: {raw_response[:200]}..."
        )
        
        return AIValidationResult(
            approved=True,  # ← КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: True вместо False
            confidence=0.3,  # Низкая уверенность показывает что это fallback
            core_request="[Validator response could not be parsed]",
            verdict="Validator response parsing failed — proceeding to tests for verification",
            critical_issues=[],  # Пустой, т.к. approved=True
            model_used=model,
            tokens_used=tokens_used,
            duration_ms=duration_ms,
            raw_response=raw_response,
            parse_error=parse_error,
        )

    
    def create_structured_feedback(self, result: AIValidationResult) -> ValidatorFeedback:
        """
        Create structured feedback object from validation result.
        
        This object is passed as SEPARATE parameter to Orchestrator.
        Uses ValidatorFeedback from feedback_handler.py for consistency.
        """
        # Import is already at top via TYPE_CHECKING
        return ValidatorFeedback(
            approved=result.approved,
            confidence=result.confidence,
            verdict=result.verdict,
            critical_issues=result.critical_issues,
            model_used=result.model_used,
        )

    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        approval_rate = 0.0
        if self._total_validations > 0:
            approval_rate = self._total_approvals / self._total_validations
        
        return {
            "total_validations": self._total_validations,
            "total_approvals": self._total_approvals,
            "approval_rate": approval_rate,
            "total_tokens": self._total_tokens,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

# Global validator instance
_validator: Optional[AIValidator] = None


def get_validator() -> AIValidator:
    """Get or create global AIValidator instance."""
    global _validator
    if _validator is None:
        _validator = AIValidator()
    return _validator


async def validate_code_change(
    user_request: str,
    orchestrator_instruction: str,
    original_content: str,
    proposed_code: str,
    file_path: str = "",
) -> tuple[AIValidationResult, ValidatorFeedback]:
    """
    Convenience function to validate a code change.
    
    Args:
        user_request: Original user's request
        orchestrator_instruction: Instruction for Code Generator
        original_content: Original file content (empty string for new file)
        proposed_code: Generated code to validate
        file_path: Target file path (optional, for logging)
        
    Returns:
        Tuple of (AIValidationResult, ValidatorFeedback)
    """
    # Import here to avoid circular dependency
    from app.agents.feedback_handler import ValidatorFeedback
    
    validator = get_validator()
    
    result = await validator.validate(
        user_request=user_request,
        orchestrator_instruction=orchestrator_instruction,
        original_content=original_content,
        proposed_code=proposed_code,
        file_path=file_path,
    )
    
    # Create structured feedback using feedback_handler's class
    feedback = ValidatorFeedback(
        approved=result.approved,
        confidence=result.confidence,
        verdict=result.verdict,
        critical_issues=result.critical_issues,
        model_used=result.model_used,
    )
    
    return result, feedback