# app/agents/router.py
"""
Router Agent - Classifies task complexity and selects appropriate Orchestrator model.

THREE-LEVEL ROUTING SYSTEM:
- 🟢 Simple → GPT-5.1 Codex Max (standard coding tasks)
- 🟡 Medium → Claude Sonnet 4.5 (multi-component, business logic)
- 🔴 Complex → Claude Opus 4.5 (security, concurrency, architecture)

According to the plan:
- If ROUTER_ENABLED = True: Uses Gemini 2.0 Flash to classify complexity
- If ROUTER_ENABLED = False: Returns fixed model from config

Error handling:
- Empty LLM response → fallback to simple model
- JSON parse error → fallback to simple model
- Any exception → fallback to simple model with error logging
"""

from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

from config.settings import cfg
from app.llm.api_client import call_llm, is_router_enabled

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class RouteResult:
    """Result of routing decision"""
    orchestrator_model: str
    complexity_level: str  # "simple", "medium", "complex" (NEW!)
    reasoning: str
    confidence: float
    risk_level: str  # "low", "medium", "high", "critical"
    router_used: bool  # True if automatic router was used


# ============================================================================
# ROUTER PROMPTS (THREE-LEVEL SYSTEM)
# ============================================================================

def _build_router_system_prompt() -> str:
    """Build Router system prompt with three-level complexity classification"""
    prompt_parts = []
    
    prompt_parts.append("You are Router - a task complexity classifier for an AI coding assistant.")
    prompt_parts.append("")
    prompt_parts.append("Your job: Analyze the user's coding request and classify it into one of THREE complexity levels.")
    prompt_parts.append("")
    prompt_parts.append("=" * 60)
    prompt_parts.append("COMPLEXITY CLASSIFICATION ALGORITHM")
    prompt_parts.append("=" * 60)
    prompt_parts.append("")
    prompt_parts.append("STEP 1: Determine the CATEGORY of the task")
    prompt_parts.append("├─ Creating code from scratch? → Category 1")
    prompt_parts.append("├─ Adding to existing project? → Category 2")
    prompt_parts.append("└─ Fixing a bug? → Category 3")
    prompt_parts.append("")
    prompt_parts.append("STEP 2: Evaluate complexity criteria for the identified category")
    prompt_parts.append("")
    prompt_parts.append("-" * 40)
    prompt_parts.append("CATEGORY 1: CREATING CODE FROM SCRATCH")
    prompt_parts.append("-" * 40)
    prompt_parts.append("")
    prompt_parts.append("🟢 SIMPLE (gpt-5.1-codex-max)")
    prompt_parts.append("")
    prompt_parts.append("A task is SIMPLE when the user's request describes something that:")
    prompt_parts.append("")
    prompt_parts.append("• Has a clear, singular purpose that can be stated in one sentence")
    prompt_parts.append("• Could be explained to a junior developer without needing to discuss")
    prompt_parts.append("  how it fits into the broader system")
    prompt_parts.append("• Has predictable inputs and outputs that the user specified or implied")
    prompt_parts.append("• Resembles tasks that developers do routinely (like writing a validator,")
    prompt_parts.append("  parser, or basic CRUD operation)")
    prompt_parts.append("")
    prompt_parts.append("The request should feel COMPLETE and SELF-CONTAINED:")
    prompt_parts.append("• You don't need to ask 'but how should this interact with other parts?'")
    prompt_parts.append("• You don't need to wonder 'which architectural pattern fits here?'")
    prompt_parts.append("• You don't need to consider 'what security implications does this have?'")
    prompt_parts.append("")
    prompt_parts.append("SIMPLE requests typically:")
    prompt_parts.append("• Name a specific thing to create (not a concept or system)")
    prompt_parts.append("• Provide enough detail that there's little room for interpretation")
    prompt_parts.append("• Don't mention connecting to other services or coordinating between parts")
    prompt_parts.append("• Don't involve decisions about 'how to design' or 'which approach to use'")
    prompt_parts.append("")
    prompt_parts.append("If the request makes you think about:")
    prompt_parts.append("• 'This needs multiple pieces working together' → NOT simple")
    prompt_parts.append("• 'There are several valid ways to approach this' → NOT simple")
    prompt_parts.append("• 'This could break other things if done wrong' → NOT simple")
    prompt_parts.append("• 'The user hasn't specified exactly what they want' → NOT simple")
    prompt_parts.append("")
    prompt_parts.append("Examples of SIMPLE requests:")
    prompt_parts.append('• "Create a function to validate email format"')
    prompt_parts.append('• "Write a CSV parser that returns a list of dictionaries"')
    prompt_parts.append('• "Add a decorator to log function execution time"')
    prompt_parts.append('• "Create utility function to convert Unix timestamp to ISO date"')
    prompt_parts.append("")
    prompt_parts.append("Examples that seem simple but are NOT:")
    prompt_parts.append('• "Add error handling" (vague - which errors? what strategy?)')
    prompt_parts.append('• "Create user authentication" (multiple components needed)')
    prompt_parts.append('• "Make the code more robust" (undefined goal)')
    prompt_parts.append('• "Add caching" (decision needed - where? what strategy?)')
    prompt_parts.append("")
    prompt_parts.append("")
    prompt_parts.append("🟡 MEDIUM (sonnet-4.5) - 2+ of these are true:")
    prompt_parts.append("  • The user's request explicitly describes creating multiple interconnected components that must work together as a unit.")
    prompt_parts.append("  • The request defines or implies a need to implement domain-specific business rules or a defined workflow.")
    prompt_parts.append("  • The request mentions the need for integrated error handling, retries, or fallback mechanisms.")
    prompt_parts.append("  • The user asks to evaluate or choose between different implementation approaches for a non-trivial task.")
    prompt_parts.append("")
    prompt_parts.append("🔴 COMPLEX (opus-4.5) - ANY of these is true:")
    prompt_parts.append("  • The user's request explicitly involves security, authentication, authorization, data encryption, or data protection.")
    prompt_parts.append("  • The request discusses or requires making architectural decisions (e.g., patterns, scalability, concurrency models).")
    prompt_parts.append("  • The user mentions compliance requirements (GDPR, PCI DSS, HIPAA) or handling sensitive regulated data.")
    prompt_parts.append("  • The request is to design a system with multiple interacting services or components.")
    prompt_parts.append("-" * 40)
    prompt_parts.append("CATEGORY 2: ADDING TO EXISTING PROJECT")
    prompt_parts.append("-" * 40)
    prompt_parts.append("")
    prompt_parts.append("🟢 SIMPLE (gpt-5.1-codex-max)")
    prompt_parts.append("")
    prompt_parts.append("A task is SIMPLE when adding to existing code IF:")
    prompt_parts.append("")
    prompt_parts.append("THE CHANGE IS ADDITIVE, NOT TRANSFORMATIVE:")
    prompt_parts.append("• Adding something NEW alongside existing code (not replacing it)")
    prompt_parts.append("• The new code follows an EXISTING pattern you can point to")
    prompt_parts.append("• Think: 'copying and adapting' rather than 'designing and integrating'")
    prompt_parts.append("")
    prompt_parts.append("THE CHANGE IS LOCALIZED:")
    prompt_parts.append("• Modifications stay within one clearly defined boundary (one file, one class)")
    prompt_parts.append("• You can confidently say 'this won't affect anything else'")
    prompt_parts.append("• No need to trace dependencies or wonder about ripple effects")
    prompt_parts.append("")
    prompt_parts.append("THE EXISTING CODE PROVIDES A CLEAR TEMPLATE:")
    prompt_parts.append("• Request explicitly references what to copy: 'like UserController', 'same as in EmailValidator'")
    prompt_parts.append("• Or the task is SO standard that similar code certainly exists (add logging, add docstring)")
    prompt_parts.append("• The pattern is obvious and repeatable")
    prompt_parts.append("")
    prompt_parts.append("NO ARCHITECTURAL UNDERSTANDING NEEDED:")
    prompt_parts.append("• You don't need to understand WHY the code is structured this way")
    prompt_parts.append("• You don't need to consider 'will this break something subtle?'")
    prompt_parts.append("• The change is so isolated that architectural context is irrelevant")
    prompt_parts.append("")
    prompt_parts.append("Ask yourself these questions:")
    prompt_parts.append("• Can this be done by copying existing code and changing names/values?")
    prompt_parts.append("  → YES = simple, NO = escalate")
    prompt_parts.append("• Could this change have side effects elsewhere in the system?")
    prompt_parts.append("  → NO = simple, MAYBE/YES = escalate")
    prompt_parts.append("• Does the request say what to do, or ask you to figure it out?")
    prompt_parts.append("  → WHAT = simple, FIGURE OUT = escalate")
    prompt_parts.append("")
    prompt_parts.append("Examples of SIMPLE additions:")
    prompt_parts.append('• "Add create_product() endpoint following UserController pattern"')
    prompt_parts.append('• "Add email validation to signup form like in login form"')
    prompt_parts.append('• "Add logging to database queries using existing logger"')
    prompt_parts.append('• "Add docstrings to PublicAPI class methods"')
    prompt_parts.append("")
    prompt_parts.append("Examples that seem simple but are NOT:")
    prompt_parts.append('• "Add caching to user queries" (WHERE? Strategy? Invalidation? Side effects?)')
    prompt_parts.append('• "Make the API more RESTful" (vague transformation, not addition)')
    prompt_parts.append('• "Add feature X" (no template mentioned, unclear scope)')
    prompt_parts.append('• "Refactor authentication to use JWT" (transformative, affects system)')
    prompt_parts.append('• "Fix the slow queries" (diagnosis needed, not clear what to add)')
    prompt_parts.append("")
    prompt_parts.append("🟡 MEDIUM (sonnet-4.5) - 2+ of these are true:")
    prompt_parts.append("  • The user's request explicitly mentions modifying or extending 2-4 named or distinct parts of the system (e.g., services, models, APIs).")
    prompt_parts.append("  • The request states a requirement for backward compatibility or integration with existing external systems.")
    prompt_parts.append("  • The user asks to add a new feature that requires understanding existing business logic to integrate correctly.")
    prompt_parts.append("  • The request mentions the need to handle errors or edge cases arising from the addition.")
    prompt_parts.append("")
    prompt_parts.append("🔴 COMPLEX (opus-4.5) - ANY of these is true:")
    prompt_parts.append("  • The user's request explicitly states the change affects the core architecture or requires refactoring existing structure.")
    prompt_parts.append("  • The request involves working with legacy code, undocumented systems, or performing a data migration.")
    prompt_parts.append("  • The user mentions the task is security-critical or involves permission/access changes.")
    prompt_parts.append("  • The request indicates the change could have system-wide side effects or impact many components.")
    prompt_parts.append("")
    prompt_parts.append("-" * 40)
    prompt_parts.append("CATEGORY 3: FIXING BUGS")
    prompt_parts.append("-" * 40)
    prompt_parts.append("")
    prompt_parts.append("⚠️ IMPORTANT CONTEXT: In ASK mode, the model CANNOT run code or tests.")
    prompt_parts.append("This fundamentally limits debugging capability — the model must fix bugs")
    prompt_parts.append("through pure code reasoning, without the ability to verify the fix.")
    prompt_parts.append("")
    prompt_parts.append("🟢 SIMPLE (gpt-5.1-codex-max)")
    prompt_parts.append("")
    prompt_parts.append("A bug is SIMPLE to fix when:")
    prompt_parts.append("")
    prompt_parts.append("THE BUG LOCATION IS EXACTLY KNOWN:")
    prompt_parts.append("• Request provides precise location: specific file, function, or line number")
    prompt_parts.append("• A stack trace or error message points directly to the problem")
    prompt_parts.append("• No investigation or diagnosis is needed — you already know WHERE the bug is")
    prompt_parts.append("")
    prompt_parts.append("THE BUG CAUSE IS OBVIOUS:")
    prompt_parts.append("• The error is self-evident when you look at the code (typo, wrong operator, missing check)")
    prompt_parts.append("• The expected behavior vs actual behavior is crystal clear")
    prompt_parts.append("• Think: 'any developer would spot this immediately'")
    prompt_parts.append("")
    prompt_parts.append("THE BUG IS ISOLATED:")
    prompt_parts.append("• The problem exists within a single, self-contained piece of code")
    prompt_parts.append("• Fixing it here won't require changes elsewhere")
    prompt_parts.append("• The bug isn't a symptom of a deeper architectural issue")
    prompt_parts.append("")
    prompt_parts.append("THE BUG REPRODUCES CONSISTENTLY:")
    prompt_parts.append("• 'Always fails when X happens' (not 'sometimes' or 'occasionally')")
    prompt_parts.append("• The conditions that trigger it are clear and deterministic")
    prompt_parts.append("• No timing, concurrency, or environment-dependent factors")
    prompt_parts.append("")
    prompt_parts.append("Ask yourself:")
    prompt_parts.append("• If I look at the code at the mentioned location, will the bug be obvious?")
    prompt_parts.append("  → YES = simple, NO = escalate")
    prompt_parts.append("• Could this bug be a symptom of something wrong elsewhere?")
    prompt_parts.append("  → NO = simple, MAYBE/YES = escalate")
    prompt_parts.append("• Does 'fixing' this require understanding why the system works this way?")
    prompt_parts.append("  → NO = simple, YES = escalate")
    prompt_parts.append("• Is any diagnosis or investigation mentioned in the request?")
    prompt_parts.append("  → NO = simple, YES = escalate")
    prompt_parts.append("")
    prompt_parts.append("Examples of SIMPLE bugs:")
    prompt_parts.append('• "Fix syntax error in line 42: using = instead of == in if statement"')
    prompt_parts.append('• "Function returns None, should return empty list (line 15 in utils.py)"')
    prompt_parts.append('• "Typo in variable name: user_naem should be user_name (auth.py:89)"')
    prompt_parts.append('• "Missing null check causes crash at line 123: if user is None"')
    prompt_parts.append("")
    prompt_parts.append("Examples that seem simple but are NOT:")
    prompt_parts.append('• "Fix the session timeout issue" (WHERE is the bug? Needs diagnosis)')
    prompt_parts.append('• "API sometimes returns wrong data" (intermittent = needs investigation)')
    prompt_parts.append('• "Application crashes under load" (concurrency issue, non-obvious cause)')
    prompt_parts.append('• "Database queries are slow" (performance = needs profiling and analysis)')
    prompt_parts.append('• "Fix the authentication bug" (vague location and cause)')
    prompt_parts.append('• "User logout doesn\'t work properly" (what does "properly" mean? Symptoms unclear)')
    prompt_parts.append("")
    prompt_parts.append("🟡 MEDIUM (sonnet-4.5) - 2+ of these are true:")
    prompt_parts.append("  • The user describes the bug as intermittent ('sometimes', 'occasionally', 'under certain conditions').")
    prompt_parts.append("  • The bug report lacks a clear diagnosis, stack trace, or the user asks to find the root cause.")
    prompt_parts.append("  • The user states the issue is related to performance, efficiency, or resource usage.")
    prompt_parts.append("  • The bug symptoms are described in a way that suggests they could manifest in multiple parts of the system.")
    prompt_parts.append("")
    prompt_parts.append("🔴 COMPLEX (opus-4.5) - ANY of these is true:")
    prompt_parts.append("  • The user explicitly mentions a security vulnerability (e.g., injection, XSS, auth bypass, data leak).")
    prompt_parts.append("  • The bug description involves concurrency, race conditions, deadlocks, or memory management.")
    prompt_parts.append("  • The user describes a 'Heisenbug' or a problem that only occurs in production/live environments.")
    prompt_parts.append("  • The bug report suggests the issue stems from the interaction between unrelated or distributed components.")
    prompt_parts.append("=" * 60)
    prompt_parts.append("DECISION RULES")
    prompt_parts.append("=" * 60)
    prompt_parts.append("")
    prompt_parts.append("PRIORITY ORDER (check in this sequence):")
    prompt_parts.append("1. If ANY complex criteria match → opus-4.5")
    prompt_parts.append("2. If 2+ medium criteria match → sonnet-4.5")
    prompt_parts.append("3. If all simple criteria match → gpt-5.1-codex-max")
    prompt_parts.append("4. If uncertain → escalate (prefer sonnet-4.5 over simple)")
    prompt_parts.append("")
    prompt_parts.append("CRITICAL MARKERS (always → opus-4.5):")
    prompt_parts.append("• Security, authentication, encryption, vulnerabilities")
    prompt_parts.append("• Concurrency, threading, race conditions, deadlocks")
    prompt_parts.append("• Heisenbug, non-reproducible, production-only issues")
    prompt_parts.append("• Legacy system without documentation")
    prompt_parts.append("• System-wide architectural changes")
    prompt_parts.append("")
    prompt_parts.append("=" * 60)
    prompt_parts.append("OUTPUT FORMAT")
    prompt_parts.append("=" * 60)
    prompt_parts.append("")
    prompt_parts.append("Output ONLY valid JSON (no markdown, no explanation):")
    prompt_parts.append("{")
    prompt_parts.append('  "orchestrator": "gpt-5.1-codex-max" | "sonnet-4.5" | "opus-4.5",')
    prompt_parts.append('  "complexity_level": "simple" | "medium" | "complex",')
    prompt_parts.append('  "category": "creation" | "addition" | "bugfix",')
    prompt_parts.append('  "reasoning": "Brief explanation of which criteria matched",')
    prompt_parts.append('  "confidence": 0.0-1.0,')
    prompt_parts.append('  "risk_level": "low" | "medium" | "high" | "critical"')
    prompt_parts.append("}")
    prompt_parts.append("")
    prompt_parts.append("CONFIDENCE GUIDE:")
    prompt_parts.append("• 0.8-1.0: Clear complexity markers present")
    prompt_parts.append("• 0.5-0.7: Borderline case, some ambiguity")
    prompt_parts.append("• 0.0-0.4: Insufficient information, defaulting to safer option")
    
    return "\n".join(prompt_parts)


def _build_router_user_prompt() -> str:
    """Build Router user prompt template"""
    prompt_parts = []
    
    prompt_parts.append("User request: {user_query}")
    prompt_parts.append("")
    prompt_parts.append("Project context (if available):")
    prompt_parts.append("{project_context}")
    prompt_parts.append("")
    prompt_parts.append("Classify this task complexity using the three-level system.")
    
    return "\n".join(prompt_parts)


ROUTER_SYSTEM_PROMPT = _build_router_system_prompt()
ROUTER_USER_PROMPT = _build_router_user_prompt()


# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def route_request(
    user_query: str,
    project_context: str = "",
) -> RouteResult:
    """
    Determines task complexity and selects appropriate Orchestrator model.
    
    THREE-LEVEL ROUTING:
    1. If ROUTER_ENABLED = False → return fixed_model from config
    2. If ROUTER_ENABLED = True → call Router LLM (Gemini 2.0 Flash)
       to classify:
       - simple → GPT-5.1 Codex Max
       - medium → Claude Sonnet 4.5
       - complex → Claude Opus 4.5
    
    Args:
        user_query: User's question/request
        project_context: Optional project context for better classification
        
    Returns:
        RouteResult with selected model and metadata
        
    Note:
        This function never raises exceptions - it always returns a valid RouteResult,
        falling back to simple model on any error.
    """
    # Validate input
    if not user_query or not user_query.strip():
        logger.warning("Router: empty user query, defaulting to simple model")
        return _create_fallback_result("Empty user query", "simple")
    
    # Check if router is enabled
    if not is_router_enabled():
        return _handle_router_disabled()
    
    # Router enabled - call LLM to classify
    logger.info("Router: classifying task complexity (3-level system)...")
    
    try:
        response = await _call_router_llm(user_query, project_context)
        
        if not response or not response.strip():
            logger.warning("Router: empty response from LLM")
            return _create_fallback_result("Empty LLM response", "simple")
        
        # Parse JSON response
        result = _parse_router_response(response)
        
        # Map to actual model names from config (THREE-LEVEL)
        complexity = result.get("complexity_level", "simple")
        orchestrator_model = _get_model_for_complexity(complexity)
        
        logger.info(
            f"Router decision: {complexity} → {cfg.get_model_display_name(orchestrator_model)} "
            f"(confidence: {result['confidence']:.2f}, risk: {result['risk_level']})"
        )
        
        return RouteResult(
            orchestrator_model=orchestrator_model,
            complexity_level=complexity,
            reasoning=result.get("reasoning", ""),
            confidence=result.get("confidence", 0.8),
            risk_level=result.get("risk_level", "medium"),
            router_used=True,
        )
        
    except Exception as e:
        logger.error(f"Router error: {e}", exc_info=True)
        return _create_fallback_result(f"Router error: {type(e).__name__}", "simple")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _get_model_for_complexity(complexity: str) -> str:
    """Map complexity level to model from config"""
    config = cfg.get_orchestrator_model_config()
    models = config.get("orchestrator_models", {})
    
    if complexity == "complex":
        return models.get("complex", cfg.ORCHESTRATOR_COMPLEX_MODEL)
    elif complexity == "medium":
        return models.get("medium", cfg.ORCHESTRATOR_MEDIUM_MODEL)
    else:
        return models.get("simple", cfg.ORCHESTRATOR_SIMPLE_MODEL)


def _handle_router_disabled() -> RouteResult:
    """Handle case when router is disabled in config"""
    config = cfg.get_orchestrator_model_config()
    fixed_model = config["fixed_model"]
    
    logger.info(f"Router disabled, using fixed model: {cfg.get_model_display_name(fixed_model)}")
    
    return RouteResult(
        orchestrator_model=fixed_model,
        complexity_level="fixed",
        reasoning="Router disabled, using fixed model from config",
        confidence=1.0,
        risk_level="medium",
        router_used=False,
    )


def _create_fallback_result(reason: str, complexity: str = "simple") -> RouteResult:
    """Create fallback result with specified complexity level"""
    model = _get_model_for_complexity(complexity)
    
    return RouteResult(
        orchestrator_model=model,
        complexity_level=complexity,
        reasoning=f"Fallback: {reason}",
        confidence=0.5,
        risk_level="medium",
        router_used=True,
    )


async def _call_router_llm(user_query: str, project_context: str) -> str:
    """Call Router LLM with proper message formatting"""
    
    # Sanitize project context (limit size to prevent token overflow)
    safe_context = project_context[:2000] if project_context else "No project context available"
    
    user_prompt = ROUTER_USER_PROMPT.format(
        user_query=user_query,
        project_context=safe_context,
    )
    
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    
    response = await call_llm(
        model=cfg.ROUTER_MODEL,
        messages=messages,
        temperature=0,
        max_tokens=400,
    )
    
    return response


def _parse_router_response(response: str) -> Dict[str, Any]:
    """
    Parse Router LLM response (expects JSON).
    
    Expected format:
    {
        "orchestrator": "gpt-5.1-codex-max" | "sonnet-4.5" | "opus-4.5",
        "complexity_level": "simple" | "medium" | "complex",
        "category": "creation" | "addition" | "bugfix",
        "reasoning": "...",
        "confidence": 0.0-1.0,
        "risk_level": "low" | "medium" | "high" | "critical"
    }
    """
    json_str = _extract_json_from_response(response)
    
    if not json_str:
        logger.warning(f"Could not extract JSON from router response: {response[:200]}...")
        return _default_router_result()
    
    try:
        result = json.loads(json_str)
        return _normalize_router_result(result)
        
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error in router response: {e}")
        logger.debug(f"Attempted to parse: {json_str[:300]}")
        return _default_router_result()


def _extract_json_from_response(response: str) -> Optional[str]:
    """
    Extract JSON object from LLM response.
    
    Tries multiple strategies:
    1. JSON in markdown code block
    2. Brace matching for raw JSON
    3. Simple regex fallback
    """
    # Strategy 1: JSON in markdown code block
    code_block_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response)
    if code_block_match:
        return code_block_match.group(1).strip()
    
    # Strategy 2: Brace matching
    start_idx = response.find('{')
    if start_idx != -1:
        brace_count = 0
        for i, char in enumerate(response[start_idx:], start=start_idx):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    json_str = response[start_idx:i + 1]
                    # Validate it looks like our expected JSON
                    if '"orchestrator"' in json_str:
                        return json_str.strip()
                    break
    
    # Strategy 3: Simple pattern (last resort)
    if '"orchestrator"' in response:
        simple_match = re.search(
            r'\{[^{}]*"orchestrator"\s*:\s*"[^"]*"[^{}]*\}',
            response
        )
        if simple_match:
            return simple_match.group(0)
    
    return None


def _normalize_router_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and validate router result fields (THREE-LEVEL)"""
    
    # Normalize orchestrator → complexity_level mapping
    orchestrator = str(result.get("orchestrator", "gpt-5.1-codex-max")).lower()
    
    # Determine complexity from orchestrator string
    if any(x in orchestrator for x in ("opus", "complex")):
        result["complexity_level"] = "complex"
        result["orchestrator"] = "opus-4.5"
    elif any(x in orchestrator for x in ("sonnet", "medium")):
        result["complexity_level"] = "medium"
        result["orchestrator"] = "sonnet-4.5"
    else:
        result["complexity_level"] = "simple"
        result["orchestrator"] = "gpt-5.1-codex-max"
    
    # Also check explicit complexity_level field
    explicit_complexity = str(result.get("complexity_level", "")).lower()
    if explicit_complexity in ("complex", "high"):
        result["complexity_level"] = "complex"
        result["orchestrator"] = "opus-4.5"
    elif explicit_complexity == "medium":
        result["complexity_level"] = "medium"
        result["orchestrator"] = "sonnet-4.5"
    elif explicit_complexity in ("simple", "low"):
        result["complexity_level"] = "simple"
        result["orchestrator"] = "gpt-5.1-codex-max"
    
    # Ensure reasoning is string
    result["reasoning"] = str(result.get("reasoning", ""))[:500]
    
    # Normalize confidence (0.0 to 1.0)
    try:
        confidence = float(result.get("confidence", 0.8))
        result["confidence"] = max(0.0, min(1.0, confidence))
    except (ValueError, TypeError):
        result["confidence"] = 0.8
    
    # Normalize risk level
    risk = str(result.get("risk_level", "medium")).lower()
    if risk not in ("low", "medium", "high", "critical"):
        result["risk_level"] = "medium"
    else:
        result["risk_level"] = risk
    
    return result


def _default_router_result() -> Dict[str, Any]:
    """Return default router result for fallback cases"""
    return {
        "orchestrator": "gpt-5.1-codex-max",
        "complexity_level": "simple",
        "reasoning": "Could not parse response, defaulting to simple model",
        "confidence": 0.5,
        "risk_level": "medium",
    }