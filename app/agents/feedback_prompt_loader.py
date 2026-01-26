# app/agents/feedback_prompt_loader.py
"""
Dynamic Feedback Prompt Loader for Agent Mode.

Loads the FEEDBACK HANDLING prompt block on-demand when:
1. Technical validation errors occur
2. AI Validator rejects code
3. Test failures happen

This block is loaded ONCE and stays in the prompt for the rest of the session.

Design principle: Keep base Orchestrator prompt lean, load detailed
feedback instructions only when needed.
"""

from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# FEEDBACK HANDLING PROMPT BLOCK
# ============================================================================

def _build_feedback_handling_block() -> str:
    """
    Build the complete feedback handling instruction block.
    
    This is loaded dynamically when first error/critique occurs.
    Once loaded, it stays in the prompt for the session.
    
    NO CODE EXAMPLES - only thinking framework and process.
    """
    parts = []
    
    parts.append("")
    parts.append("━" * 60)
    parts.append("🔄 FEEDBACK HANDLING PROTOCOL")
    parts.append("━" * 60)
    parts.append("")
    
    # === CORE PRINCIPLE ===
    parts.append("## CORE PRINCIPLE")
    parts.append("")
    parts.append("When you receive feedback, your previous instruction FAILED.")
    parts.append("The Code Generator produced code that has problems.")
    parts.append("Your job: analyze what went wrong and write a CORRECTED instruction.")
    parts.append("")
    parts.append("⚠️ CRITICAL: The Code Generator will NOT see:")
    parts.append("  - The error messages")
    parts.append("  - Your analysis")
    parts.append("  - The feedback you received")
    parts.append("")
    parts.append("It will ONLY see your NEW instruction. Make it complete.")
    parts.append("")
    
    # === MANDATORY CODE ANALYSIS ===
    parts.append("━" * 60)
    parts.append("📋 MANDATORY: ANALYZE GENERATED CODE")
    parts.append("━" * 60)
    parts.append("")
    parts.append("When you receive feedback with generated code, you MUST:")
    parts.append("")
    parts.append("1. **READ the generated code** — it shows what Code Generator produced")
    parts.append("2. **IDENTIFY the error location** — which file, which line, which construct")
    parts.append("3. **UNDERSTAND the mismatch** — what did you instruct vs what was generated")
    parts.append("4. **DETERMINE root cause** — was your instruction unclear? incomplete? wrong?")
    parts.append("")
    parts.append("Ask yourself:")
    parts.append("  - Did Code Generator misunderstand my instruction?")
    parts.append("  - Did I forget to specify something important?")
    parts.append("  - Did I assume context that Code Generator didn't have?")
    parts.append("  - Is the error in MY instruction or in existing code?")
    parts.append("")
    
    # === REVISION PROCESS ===
    parts.append("━" * 60)
    parts.append("🧠 REVISION PROCESS")
    parts.append("━" * 60)
    parts.append("")
    parts.append("STEP 1: LOCATE THE FAILURE")
    parts.append("  • What exactly failed? (error message, validator critique)")
    parts.append("  • Where in the generated code? (file, function, line)")
    parts.append("  • Is this in NEW code or EXISTING code?")
    parts.append("")
    parts.append("STEP 2: COMPARE INSTRUCTION vs OUTPUT")
    parts.append("  • What did you tell Code Generator to do?")
    parts.append("  • What did it actually produce?")
    parts.append("  • Where is the gap?")
    parts.append("")
    parts.append("STEP 3: IDENTIFY ROOT CAUSE")
    parts.append("  • WHY did your instruction produce wrong code?")
    parts.append("  • What information was missing?")
    parts.append("  • What did you assume incorrectly?")
    parts.append("")
    parts.append("STEP 4: DESIGN THE FIX")
    parts.append("  • What SPECIFIC changes will address the root cause?")
    parts.append("  • Will your fix introduce new problems?")
    parts.append("  • Are there RELATED issues to fix at the same time?")
    parts.append("")
    parts.append("STEP 5: WRITE CORRECTED INSTRUCTION")
    parts.append("  • Your new instruction must be COMPLETE")
    parts.append("  • Include ALL details: file paths, signatures, logic")
    parts.append("  • Code Generator starts FRESH — no memory of previous attempt")
    parts.append("")
    
# === RESOLUTION CONTINUITY ===
    parts.append("━" * 60)
    parts.append("⏩ RESOLUTION CONTINUITY")
    parts.append("━" * 60)
    parts.append("")
    parts.append("If errors persist or change, the resolution process must continue.")
    parts.append("You are expected to generate a new instruction to bridge the remaining gap.")
    parts.append("Treat the current state as a new problem to be solved.")
    parts.append("Actively modify the code until it reaches a fully functional state.")
    parts.append("")
    
    # === FEEDBACK TYPES ===
    parts.append("━" * 60)
    parts.append("📋 FEEDBACK TYPES AND AUTHORITY")
    parts.append("━" * 60)
    parts.append("")
    
    # Technical Errors
    parts.append("┌" + "─" * 58 + "┐")
    parts.append("│ TECHNICAL ERRORS (Syntax, Import, Runtime, Test failures) │")
    parts.append("├" + "─" * 58 + "┤")
    parts.append("│ Authority: ABSOLUTE — these are objective failures         │")
    parts.append("│                                                            │")
    parts.append("│ • The code objectively does not work                       │")
    parts.append("│ • You cannot disagree with technical errors                │")
    parts.append("│ • You MUST fix them in your new instruction                │")
    parts.append("│                                                            │")
    parts.append("│ Approach:                                                  │")
    parts.append("│ 1. Read the error message carefully                        │")
    parts.append("│ 2. Find the error in the GENERATED CODE shown to you       │")
    parts.append("│ 3. Identify what in YOUR INSTRUCTION caused this           │")
    parts.append("│ 4. Write corrected instruction                             │")
    parts.append("└" + "─" * 58 + "┘")
    parts.append("")
    

    # Staging Errors
    parts.append("┌" + "─" * 58 + "┐")
    parts.append("│ STAGING ERRORS (Target Not Found)                        │")
    parts.append("├" + "─" * 58 + "┤")
    parts.append("│ Authority: ABSOLUTE — The file modification failed.      │")
    parts.append("│                                                          │")
    parts.append("│ DIAGNOSIS:                                               │")
    parts.append("│ The instruction tried to attach code to a location that  │")
    parts.append("│ doesn't exist in the current file version.               │")
    parts.append("│                                                          │")
    parts.append("│ SOLUTION PATH:                                           │")
    parts.append("│ 1. Re-read the file content provided in context.         │")
    parts.append("│ 2. Check if the Class/Method name has a typo or prefix.  │")
    parts.append("│ 3. Check nesting: Is the method inside a class?          │")
    parts.append("│                                                          │")
    parts.append("│ STRATEGIC SHIFT:                                         │")
    parts.append("│ If the target is truly missing, do not keep trying to     │")
    parts.append("│ replace it. Instead, change your strategy to CREATE it   │")
    parts.append("│ (use INSERT or APPEND modes) to add the missing logic.   │")
    parts.append("└" + "─" * 58 + "┘")
    parts.append("")
    
    
    # User Feedback
    parts.append("┌" + "─" * 58 + "┐")
    parts.append("│ USER FEEDBACK (from human user)                            │")
    parts.append("├" + "─" * 58 + "┤")
    parts.append("│ Authority: MANDATORY — user requirements take priority     │")
    parts.append("│                                                            │")
    parts.append("│ • User is telling you the code does not meet their needs   │")
    parts.append("│ • You cannot ignore or override user feedback              │")
    parts.append("│ • Even if you disagree, attempt what user asks             │")
    parts.append("│ • You may express concerns, but STILL provide instruction  │")
    parts.append("└" + "─" * 58 + "┘")
    parts.append("")
    
    # Validator Feedback
    parts.append("┌" + "─" * 58 + "┐")
    parts.append("│ AI VALIDATOR FEEDBACK (semantic critique)                  │")
    parts.append("├" + "─" * 58 + "┤")
    parts.append("│ Authority: ADVISORY — you must evaluate before acting      │")
    parts.append("│                                                            │")
    parts.append("│ The validator can be WRONG. Apply this test:               │")
    parts.append("│                                                            │")
    parts.append("│ 'If I ignore this critique, will the code FAIL TO WORK    │")
    parts.append("│  or FAIL TO DO what the user asked?'                       │")
    parts.append("│                                                            │")
    parts.append("│ • If YES → critique is valid → ACCEPT and write new instr. │")
    parts.append("│ • If NO  → critique is invalid → OVERRIDE (no new instr.)  │")
    parts.append("│                                                            │")
    parts.append("│ IMPORTANT: If validator claims something is missing/wrong, │")
    parts.append("│ CHECK THE GENERATED CODE before deciding.                  │")
    parts.append("└" + "─" * 58 + "┘")
    parts.append("")
    
    # Override requirements
    parts.append("OVERRIDE requires EVIDENCE:")
    parts.append("  • State what validator claimed")
    parts.append("  • Show WHERE in generated code the claim is wrong")
    parts.append("  • Reference specific lines/constructs as proof")
    parts.append("")
    
    # === OUTPUT FORMAT ===
    parts.append("━" * 60)
    parts.append("📤 REQUIRED OUTPUT FORMAT")
    parts.append("━" * 60)
    parts.append("")
    parts.append("Think of this format as a strict API contract: clean structure guarantees your logic is applied correctly.")
    parts.append("Maintain these headers precisely to help the system process your expert decision.")    
    parts.append("Your response MUST contain these sections:")
    parts.append("")
    parts.append("## Error Analysis")
    parts.append("")
    parts.append("**Generated code review:**")
    parts.append("[What you see in the generated code that relates to the error]")
    parts.append("")
    parts.append("**Root cause:**")
    parts.append("[What in YOUR INSTRUCTION caused this — be specific]")
    parts.append("")
    parts.append("**For validator feedback:**")
    parts.append("  **My decision:** ACCEPT or OVERRIDE")
    parts.append("  **Evidence:** [Reference to specific code if OVERRIDE]")
    parts.append("")
    parts.append("## Instruction for Code Generator")
    parts.append("")
    parts.append("[Your COMPLETE revised instruction using standard format]")
    parts.append("[Include: SCOPE, Task, FILE blocks, ACTION blocks]")
    parts.append("[All details needed for Code Generator to produce correct code]")
    parts.append("")
    parts.append("EXCEPTION: If you OVERRIDE validator feedback with evidence,")
    parts.append("you may skip the instruction section. Code proceeds to testing.")
    parts.append("")
    
    # === EFFICIENCY ===
    parts.append("━" * 60)
    parts.append("⚡ EFFICIENCY")
    parts.append("━" * 60)
    parts.append("")
    parts.append("You have limited revision cycles. Make each one count:")
    parts.append("")
    parts.append("• READ the generated code BEFORE writing new instruction")
    parts.append("• Fix ROOT CAUSE, not just symptom")
    parts.append("• Fix ALL related issues in one revision")
    parts.append("• One thorough revision beats multiple quick guesses")
    parts.append("")
    
    return "\n".join(parts)


# ============================================================================
# LOADER STATE MANAGEMENT
# ============================================================================

class FeedbackPromptLoader:
    """
    Manages dynamic loading of feedback handling prompt.
    
    The feedback block is loaded ONCE when first needed,
    then stays in the prompt for the rest of the session.
    
    Thread-safe for read operations after initialization.
    """
    
    def __init__(self):
        self._feedback_block: Optional[str] = None
        self._loaded: bool = False
    
    def get_feedback_block(self, force_load: bool = False) -> str:
        """
        Get the feedback handling block.
        
        Args:
            force_load: If True, load even if not triggered by error
            
        Returns:
            Feedback handling prompt block, or empty string if not loaded
        """
        if force_load and not self._loaded:
            self._load_block()
        
        return self._feedback_block or ""
    
    def trigger_load(self) -> str:
        """
        Trigger loading of feedback block (called on first error).
        
        Returns:
            The loaded feedback block
        """
        if not self._loaded:
            self._load_block()
            logger.info("FeedbackPromptLoader: Loaded feedback handling block")
        
        return self._feedback_block or ""
    
    def _load_block(self) -> None:
        """Internal: Load the feedback block."""
        self._feedback_block = _build_feedback_handling_block()
        self._loaded = True
    
    def is_loaded(self) -> bool:
        """Check if feedback block has been loaded."""
        return self._loaded
    
    def reset(self) -> None:
        """Reset loader state (for new session)."""
        self._feedback_block = None
        self._loaded = False


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_FEEDBACK_LOADER: Optional[FeedbackPromptLoader] = None


def get_feedback_loader() -> FeedbackPromptLoader:
    """Get global feedback prompt loader instance."""
    global _FEEDBACK_LOADER
    if _FEEDBACK_LOADER is None:
        _FEEDBACK_LOADER = FeedbackPromptLoader()
    return _FEEDBACK_LOADER


def reset_feedback_loader() -> None:
    """Reset feedback loader for new session."""
    global _FEEDBACK_LOADER
    if _FEEDBACK_LOADER is not None:
        _FEEDBACK_LOADER.reset()


def get_feedback_block_if_needed(has_errors: bool = False) -> str:
    """
    Get feedback block if errors present.
    
    Convenience function for prompt formatting.
    
    Args:
        has_errors: True if there are validation errors or feedback
        
    Returns:
        Feedback block if needed, empty string otherwise
    """
    loader = get_feedback_loader()
    
    if has_errors:
        return loader.trigger_load()
    
    return loader.get_feedback_block()