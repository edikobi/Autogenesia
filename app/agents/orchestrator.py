# app/agents/orchestrator.py
"""
Orchestrator Agent - Analyzes code and generates instructions for Code Generator.

According to the plan (Ask mode):
1. Receives selected chunks from Pre-filter + compact index
2. Analyzes code in context of user query
3. Can use tools (read_file, search_code, web_search) if needed
4. Generates detailed instruction for Code Generator
5. Does NOT generate code itself - only instructions

Tool usage limits:
- web_search: Maximum 3 calls per session (to control costs and context size)
- read_file, search_code: No strict limit (controlled by max_tool_iterations)

UPDATED: Now passes orchestrator_model to prompt formatting for adaptive blocks!
UPDATED: Added forced finalization when instruction section is missing after tool iterations.
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Set  # ← добавить Set

from config.settings import cfg
from app.llm.api_client import call_llm_with_tools, call_llm

# Prompts (centralized)
from app.llm.prompt_templates import (
    format_orchestrator_prompt_ask,
    format_orchestrator_prompt_ask_agent,
    format_orchestrator_prompt_new_project,
    format_orchestrator_prompt_new_project_agent,
    MAX_WEB_SEARCH_CALLS,
)

# Tools
from app.tools.tool_definitions import ORCHESTRATOR_TOOLS
from app.tools.tool_executor import ToolExecutor, parse_tool_call

from app.agents.pre_filter import SelectedChunk

# NEW: Import intra-session compression for DeepSeek Reasoner and Gemini 3.0 Pro
from app.history.context_manager import (
    get_compressor,
    is_context_overflow_error,
)

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

# CHANGED: Increased from 5 to 7 to allow more complex multi-file analysis
MAX_TOOL_ITERATIONS = 10  # Maximum total tool call iterations
MAX_TEST_RUNS = 5  # NEW

# NEW: Batch file reading limit for models with smaller context windows
# Applies ONLY to Claude Opus 4.5 and DeepSeek Reasoner
BATCH_FILE_TOKEN_LIMIT = 82000  # 82k tokens max per batch of file reads

# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class ToolCall:
    """Record of a tool call made by Orchestrator"""
    name: str
    arguments: Dict[str, Any]
    output: str
    success: bool
    thinking: str = ""


@dataclass
class ToolUsageStats:
    """Tracks tool usage during orchestration"""
    web_search_count: int = 0
    read_file_count: int = 0
    search_code_count: int = 0
    total_calls: int = 0
    test_run_count: int = 0  # NEW

    
    def can_use_web_search(self) -> bool:
        """Check if web_search can still be used"""
        return self.web_search_count < MAX_WEB_SEARCH_CALLS
    
    def can_run_tests(self) -> bool:  # NEW
        """Check if run_project_tests can still be used"""
        return self.test_run_count < MAX_TEST_RUNS
    
    def increment(self, tool_name: str) -> None:
        """Increment counter for a tool"""
        self.total_calls += 1
        if tool_name == "web_search":
            self.web_search_count += 1
        elif tool_name == "read_file":
            self.read_file_count += 1
        elif tool_name == "search_code":
            self.search_code_count += 1
        elif tool_name == "run_project_tests":  # NEW
            self.test_run_count += 1
    
    def get_remaining_web_searches(self) -> int:
        """Get remaining web_search calls"""
        return MAX_WEB_SEARCH_CALLS - self.web_search_count
    
    def get_remaining_test_runs(self) -> int:  # NEW
        """Get remaining test run calls"""
        return MAX_TEST_RUNS - self.test_run_count

@dataclass
class OrchestratorResult:
    """Result of orchestration"""
    analysis: str  # Analysis of the problem
    instruction: str  # Instruction for Code Generator
    tool_calls: List[ToolCall] = field(default_factory=list)
    target_file: Optional[str] = None  # File to modify (if determined)
    target_files: List[str] = field(default_factory=list)  # NEW: все файлы
    raw_response: str = ""
    tool_usage: Optional[ToolUsageStats] = None


# ============================================================================
# MAIN ORCHESTRATION FUNCTIONS
# ============================================================================

async def orchestrate(
    user_query: str,
    selected_chunks: List[SelectedChunk],
    compact_index: str,
    history: List[Dict[str, str]],
    orchestrator_model: str,
    project_dir: str,
    index: Dict[str, Any],
    project_map: str = "",
    tool_executor: Optional[Callable] = None,
) -> OrchestratorResult:
    """
    Analyzes code and generates instructions for Code Generator.
    
    Algorithm (from plan):
    1. Format selected chunks into context
    2. Build messages with system prompt, history, and user query
    3. Call Orchestrator LLM with tool support
    4. If LLM calls tools (read_file, search_code, web_search), execute them and retry
    5. Parse response into analysis + instruction sections
    
    Tool limits:
    - web_search: max 3 calls per session
    - Total iterations: max 7 (increased from 5)
    
    UPDATED: Selected chunks are now passed as a SEPARATE user message before the query.
    After the first tool call, this message is REMOVED to save tokens.
    
    Args:
        user_query: User's question
        selected_chunks: Chunks from Pre-filter
        compact_index: Compact index string
        history: Conversation history (already processed by HistoryManager)
        orchestrator_model: Model to use (from Router) - ALSO USED FOR ADAPTIVE BLOCKS!
        project_dir: Path to project root
        index: Full project index (for search_code tool)
        project_map: Project map string with file descriptions
        tool_executor: Optional custom tool executor function
        
    Returns:
        OrchestratorResult with analysis and instruction
    """
    logger.info(f"Orchestrator: analyzing with {cfg.get_model_display_name(orchestrator_model)}")
    
    # Initialize tool usage tracking
    tool_usage = ToolUsageStats()
    
    # === LOGIC FOR CONVERSATION SUMMARY ===
    conversation_summary = "No previous context (Start of conversation)."
    if history:
        conversation_summary = (
            f"⚠️ CONTINUING CONVERSATION ({len(history)} messages in history).\n"
            "Review the message history passed to you to understand previous actions.\n"
            "Do not introduce yourself again. Focus on the latest user request."
        )

    # Format chunks for context (as separate message, NOT in system prompt)
    chunks_context = _format_chunks_for_context(selected_chunks)
    
    # Build prompts using centralized templates
    # NOTE: selected_chunks is NOT used in system prompt anymore
    prompts = format_orchestrator_prompt_ask(
        user_query=user_query,
        selected_chunks="",  # DEPRECATED - chunks passed as separate message
        compact_index=compact_index,
        project_map=project_map,
        remaining_web_searches=tool_usage.get_remaining_web_searches(),
        orchestrator_model_id=orchestrator_model,
        conversation_summary=conversation_summary
    )
    
    # Build messages
    messages = [{"role": "system", "content": prompts["system"]}]
    messages.extend(history)
    
    # === NEW: Add chunks as SEPARATE user message BEFORE the query ===
    chunks_message = _format_chunks_as_initial_context_message(selected_chunks)
    if chunks_message:
        messages.append(chunks_message)
        logger.info(f"Orchestrator: Added {len(selected_chunks)} chunks as initial context message")
    
    # Add actual user query
    messages.append({"role": "user", "content": prompts["user"]})
    
    # Track if chunks have been removed (happens after first tool call)
    chunks_removed = False
    
    # Tool calls tracking
    all_tool_calls: List[ToolCall] = []
    
    # Setup tool executor
    executor_instance = ToolExecutor(project_dir=project_dir, index=index)
    if tool_executor is None:
        tool_executor = lambda name, args: executor_instance.execute(name, args)
    
    # Track response content
    content = ""
    
    # Get available tools (may be modified if web_search limit reached)
    available_tools = ORCHESTRATOR_TOOLS.copy()
    
    # Call LLM with tools
    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            # Update available tools based on usage
            available_tools = _get_available_tools(tool_usage)
            
            # Cache optimization for Claude models
            is_claude_model_check = orchestrator_model in [cfg.MODEL_OPUS_4_5, cfg.MODEL_SONNET_4_5, cfg.MODEL_SONNET_4_6, cfg.MODEL_OPUS_4_6]
            if is_claude_model_check:
                 _optimize_cache_by_size(messages, limit=3)

            response = await call_llm_with_tools(
                model=orchestrator_model,
                messages=messages,
                tools=available_tools,
                temperature=0,
                max_tokens=4000,
                tool_choice="auto",
            )
            
            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
            # [NEW] Extract reasoning_content for DeepSeek R1 support
            reasoning_content = response.get("reasoning_content")
            # [NEW] Extract thought_signature for Gemini 3.0 Pro support
            thought_signature = response.get("thought_signature")
            # [NEW] Extract reasoning_details for Gemini 3 OpenRouter compatibility
            reasoning_details = response.get("reasoning_details")
            
            # If no tool calls, we're done
            if not tool_calls:
                logger.info(f"Orchestrator: completed analysis (iteration {iteration + 1})")
                break
            
            # === NEW: Remove chunks message after FIRST tool call ===
            if not chunks_removed and chunks_message:
                messages = _remove_chunks_message_from_history(messages)
                chunks_removed = True
            
            # Execute tool calls
            logger.info(f"Orchestrator: executing {len(tool_calls)} tool call(s)")
            
            # Check if Claude model for caching
            is_claude_model = orchestrator_model in [cfg.MODEL_OPUS_4_5, cfg.MODEL_SONNET_4_5, cfg.MODEL_SONNET_4_6, cfg.MODEL_OPUS_4_6]
            
            # Capture thinking content before tool calls
            # [UPDATED] Use reasoning_content if available (for DeepSeek), otherwise fallback to content
            current_thinking = reasoning_content if reasoning_content else (content if content else "")
            
            # === NEW: Use batch limit for specific models ===
            if _should_use_batch_limit(orchestrator_model):
                logger.info(f"Orchestrator: applying batch file limit for {cfg.get_model_display_name(orchestrator_model)}")
                
                batch_tool_calls, assistant_tool_calls, tool_results = _process_file_tools_with_batch_limit(
                    tool_calls=tool_calls,
                    tool_executor=tool_executor,
                    tool_usage=tool_usage,
                    token_limit=BATCH_FILE_TOKEN_LIMIT,
                )
                
                # Assign thinking to first tool call
                if batch_tool_calls and current_thinking:
                    batch_tool_calls[0].thinking = current_thinking
                
                all_tool_calls.extend(batch_tool_calls)
                
                # Apply Claude cache optimization to results
                if is_claude_model:
                    for tr in tool_results:
                        tool_name = tr.get("name", "")
                        tool_content = tr.get("content", "")
                        is_heavy_tool = tool_name in ["read_file", "read_code_chunk"]
                        
                        if is_heavy_tool and isinstance(tool_content, str) and len(tool_content) > 100:
                            tr["content"] = [
                                {
                                    "type": "text",
                                    "text": tool_content,
                                    "cache_control": {"type": "ephemeral"}
                                }
                            ]
                            logger.debug(f"Orchestrator: Cache control enabled for {tool_name}")
            else:
                # === Original logic for other models ===
                assistant_tool_calls = []
                tool_results = []
                
                for i, tc in enumerate(tool_calls):
                    func_name, func_args, tc_id = parse_tool_call(tc)
                    
                    # Check web_search limit
                    if func_name == "web_search" and not tool_usage.can_use_web_search():
                        tool_result = _format_web_search_limit_error(tool_usage)
                        success = False
                        logger.warning(f"Orchestrator: web_search limit reached ({MAX_WEB_SEARCH_CALLS})")
                    elif func_name == "run_project_tests" and not tool_usage.can_run_tests():
                        tool_result = _format_test_run_limit_error(tool_usage)
                        success = False
                    else:
                        # Execute tool
                        tool_result = tool_executor(func_name, func_args)
                        success = not tool_result.startswith("<!-- ERROR")
                        tool_usage.increment(func_name)
                    
                    # Assign thinking only to the first tool call of the batch
                    tool_thinking = current_thinking if i == 0 else ""
                    
                    # Create ToolCall record with thinking
                    tool_call_record = ToolCall(
                        name=func_name,
                        arguments=func_args,
                        output=tool_result,
                        success=success,
                        thinking=tool_thinking
                    )
                    all_tool_calls.append(tool_call_record)
                    
                    assistant_tool_calls.append(tc)

                    # === Caching Logic ===
                    content_payload = tool_result
                    is_heavy_tool = func_name in ["read_file", "read_code_chunk"]
                    
                    if is_claude_model and is_heavy_tool and len(tool_result) > 100:
                        content_payload = [
                            {
                                "type": "text",
                                "text": tool_result,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ]
                        logger.debug(f"Orchestrator: Cache control enabled for {func_name}")

                    tool_results.append({
                        "tool_call_id": tc_id,
                        "name": func_name,
                        "content": content_payload,
                    })
            
            # Add assistant message with tool calls
            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": assistant_tool_calls,
            }

            # [NEW] CRITICAL: Pass reasoning_content back to history for DeepSeek R1
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            
            # [NEW] CRITICAL: Pass thought_signature back to history for Gemini 3.0 Pro
            if thought_signature:
                assistant_msg["thought_signature"] = thought_signature
            
            # [NEW] CRITICAL: Pass reasoning_details back to history for Gemini 3 OpenRouter
            if reasoning_details:
                assistant_msg["reasoning_details"] = reasoning_details
            
            messages.append(assistant_msg)
            
            # Add tool results
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "name": tr["name"],
                    "content": tr["content"],
                })
            
            # Update system prompt with new web_search count
            updated_prompts = format_orchestrator_prompt_ask(
                user_query=user_query,
                selected_chunks="",  # DEPRECATED
                compact_index=compact_index,
                project_map=project_map,
                remaining_web_searches=tool_usage.get_remaining_web_searches(),
                orchestrator_model_id=orchestrator_model,
                conversation_summary=conversation_summary
            )
            messages[0]["content"] = updated_prompts["system"]
            
        except Exception as e:
            logger.error(f"Orchestrator LLM error: {e}")
            return OrchestratorResult(
                analysis=f"Error during analysis: {e}",
                instruction="Unable to generate instruction due to error.",
                tool_calls=all_tool_calls,
                raw_response="",
                tool_usage=tool_usage,
            )
    
    # =========================================================================
    # NEW: FORCED FINALIZATION - If no instruction section found after all iterations
    # =========================================================================
    if not _has_instruction_section(content):
        logger.warning("Orchestrator: No instruction section found, requesting final response")
        
        # Add a user message requesting the final instruction
        messages.append({
            "role": "user",
            "content": (
                "⚠️ FINAL REQUEST: You have used all available tool calls.\n"
                "Please provide your complete response NOW with:\n\n"
                "## Analysis\n[Summary of your findings]\n\n"
                "## Instruction for Code Generator\n"
                "**Task:** [What needs to be done]\n"
                "**File:** [Which file(s) to modify]\n"
                "**Changes:** [Specific changes required]\n\n"
                "DO NOT request more tools. Write your instruction based on what you already know."
            )
        })
        
        # Final call WITHOUT tools - model MUST respond with text
        try:
            final_content = await call_llm(
                model=orchestrator_model,
                messages=messages,
                temperature=0,
                max_tokens=4000,
            )
            content = final_content
            logger.info("Orchestrator: Received forced final response")
        except Exception as e:
            logger.error(f"Orchestrator final response error: {e}")
            # Keep the original content if final call fails
    # =========================================================================
    
    parsed = _parse_orchestrator_response(content)
    
    return OrchestratorResult(
        analysis=parsed["analysis"],
        instruction=parsed["instruction"],
        tool_calls=all_tool_calls,
        target_file=parsed.get("target_file"),
        target_files=parsed.get("target_files", []),
        raw_response=content,
        tool_usage=tool_usage,
    )


def _format_test_run_limit_error(tool_usage: ToolUsageStats) -> str:
    """Format error message when test run limit is reached"""
    return f"""<!-- TEST_LIMIT_ERROR -->
<test_result>
  <status>LIMIT_REACHED</status>
  <system_error><![CDATA[
Test run limit reached. You have used all {MAX_TEST_RUNS} allowed test runs for this session.
Test runs used: {tool_usage.test_run_count}/{MAX_TEST_RUNS}

Please complete your analysis using the test results you already have.
Focus on fixing the identified issues before requesting more tests.
  ]]></system_error>
</test_result>"""


def _process_file_tools_with_batch_limit(
    tool_calls: List[Dict[str, Any]],
    tool_executor: Callable,
    tool_usage: ToolUsageStats,
    token_limit: int = BATCH_FILE_TOKEN_LIMIT,
) -> tuple[List[ToolCall], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Process tool calls with batch token limit for file-reading tools.
    
    APPLIES ONLY TO: Claude Opus 4.5 and DeepSeek Reasoner
    
    When multiple read_file/read_code_chunk calls in a single batch would exceed
    the token limit, this function:
    1. Executes file reads until the limit is reached
    2. Returns a "file too large, call separately" message for remaining files
    3. Preserves non-file tools (web_search, search_code, etc.)
    
    Args:
        tool_calls: List of tool calls from LLM response
        tool_executor: Function to execute tools
        tool_usage: Tool usage statistics tracker
        token_limit: Maximum tokens for file content in this batch
        
    Returns:
        Tuple of:
        - all_tool_calls: List of ToolCall records
        - assistant_tool_calls: Tool calls for assistant message
        - tool_results: Results for tool messages
    """
    from app.utils.token_counter import TokenCounter
    
    counter = TokenCounter()
    all_tool_calls: List[ToolCall] = []
    assistant_tool_calls: List[Dict[str, Any]] = []
    tool_results: List[Dict[str, Any]] = []
    
    # Track tokens used by file reads in this batch
    batch_file_tokens = 0
    
    # File-reading tool names
    FILE_TOOLS = {"read_file", "read_code_chunk"}
    
    # First pass: identify file tools and their order
    file_tool_indices = []
    other_tool_indices = []
    
    for i, tc in enumerate(tool_calls):
        func_info = tc.get("function", {})
        func_name = func_info.get("name", "")
        if func_name in FILE_TOOLS:
            file_tool_indices.append(i)
        else:
            other_tool_indices.append(i)
    
    # Process non-file tools first (they don't count against file limit)
    for i in other_tool_indices:
        tc = tool_calls[i]
        func_name, func_args, tc_id = parse_tool_call(tc)
        
        # Check web_search limit
        if func_name == "web_search" and not tool_usage.can_use_web_search():
            tool_result = _format_web_search_limit_error(tool_usage)
            success = False
        elif func_name == "run_project_tests" and not tool_usage.can_run_tests():
            tool_result = _format_test_run_limit_error(tool_usage)
            success = False
        else:
            tool_result = tool_executor(func_name, func_args)
            success = not tool_result.startswith("<!-- ERROR")
            tool_usage.increment(func_name)
        
        all_tool_calls.append(ToolCall(
            name=func_name,
            arguments=func_args,
            output=tool_result,
            success=success,
            thinking=""
        ))
        assistant_tool_calls.append(tc)
        tool_results.append({
            "tool_call_id": tc_id,
            "name": func_name,
            "content": tool_result,
        })
    
    # Process file tools with token tracking
    files_skipped = []
    
    for i in file_tool_indices:
        tc = tool_calls[i]
        func_name, func_args, tc_id = parse_tool_call(tc)
        
        file_path = func_args.get("file_path", "unknown")
        
        # Check if we've exceeded the batch limit
        if batch_file_tokens >= token_limit:
            # Skip this file - return message to call separately
            tool_result = _format_batch_limit_skip_message(file_path, batch_file_tokens, token_limit)
            success = True  # Not an error, just a limit
            files_skipped.append(file_path)
            
            logger.warning(
                f"Batch file limit: skipping {file_path} "
                f"(batch at {batch_file_tokens}/{token_limit} tokens)"
            )
        else:
            # Execute the file read
            tool_result = tool_executor(func_name, func_args)
            success = not tool_result.startswith("<!-- ERROR")
            tool_usage.increment(func_name)
            
            # Count tokens in the result
            result_tokens = counter.count(tool_result)
            batch_file_tokens += result_tokens
            
            logger.debug(
                f"Batch file read: {file_path} = {result_tokens} tokens "
                f"(batch total: {batch_file_tokens})"
            )
            
            # Check if THIS file pushed us over the limit for future files
            if batch_file_tokens > token_limit:
                logger.info(
                    f"Batch file limit reached after {file_path}: "
                    f"{batch_file_tokens} > {token_limit}"
                )
        
        all_tool_calls.append(ToolCall(
            name=func_name,
            arguments=func_args,
            output=tool_result,
            success=success,
            thinking=""
        ))
        assistant_tool_calls.append(tc)
        tool_results.append({
            "tool_call_id": tc_id,
            "name": func_name,
            "content": tool_result,
        })
    
    if files_skipped:
        logger.info(
            f"Batch limit: {len(files_skipped)} file(s) skipped, "
            f"batch used {batch_file_tokens} tokens"
        )
    
    return all_tool_calls, assistant_tool_calls, tool_results


def _format_batch_limit_skip_message(file_path: str, current_tokens: int, limit: int) -> str:
    """
    Format message when a file is skipped due to batch token limit.
    
    This message instructs the model to request the file in a subsequent call.
    """
    return f"""<!-- BATCH_LIMIT_REACHED -->
<batch_limit>
  <file_path>{file_path}</file_path>
  <status>SKIPPED</status>
  <reason>Batch file token limit reached</reason>
  <current_batch_tokens>{current_tokens}</current_batch_tokens>
  <limit>{limit}</limit>
  <instruction>
    This file was not read because the current batch of file reads has reached 
    the {limit:,} token limit ({current_tokens:,} tokens already used).
    
    To read this file, call read_file() or read_code_chunk() again in your 
    NEXT response. The batch limit resets with each new tool call round.
    
    Tip: If you need multiple large files, request them one at a time across 
    multiple tool call rounds.
  </instruction>
</batch_limit>"""


def _should_use_batch_limit(model: str) -> bool:
    """
    Check if model should use batch file token limit.
    
    Currently applies to:
    - Claude Opus 4.5 (smaller effective context due to cost/latency)
    - DeepSeek Reasoner (64k context window)
    """
    LIMITED_CONTEXT_MODELS = {
        cfg.MODEL_OPUS_4_5,
        cfg.MODEL_DEEPSEEK_REASONER,
        cfg.MODEL_QWEN3_MAX_THINKING,
    }
    return model in LIMITED_CONTEXT_MODELS


async def orchestrate_new_project(
    user_query: str,
    history: List[Dict[str, str]],
    orchestrator_model: str,
    project_dir: str = "",
) -> OrchestratorResult:
    """
    Orchestration for new projects (no existing code/index).
    
    Simplified flow without Pre-filter or code-related tools.
    Only web_search is available (with limits).
    
    UPDATED: Now passes orchestrator_model to prompt formatting for adaptive blocks!
    UPDATED: Added forced finalization when instruction section is missing.
    """
    logger.info(f"Orchestrator (new project): using {cfg.get_model_display_name(orchestrator_model)}")
    
    # Initialize tool usage tracking
    tool_usage = ToolUsageStats()
    
    # Build prompts using centralized templates
    prompts = format_orchestrator_prompt_new_project(
        user_query=user_query,
        remaining_web_searches=tool_usage.get_remaining_web_searches(),
        orchestrator_model_id=orchestrator_model,
    )
    
    messages = [{"role": "system", "content": prompts["system"]}]
    messages.extend(history)
    messages.append({"role": "user", "content": prompts["user"]})
    
    # Excluding only code-reading tools that don't make sense for empty project
    new_project_tools = [
        tool for tool in ORCHESTRATOR_TOOLS 
        if tool["function"]["name"] in [
            "web_search",
            "install_dependency",
            "search_pypi",
            "list_installed_packages",
            "get_advice",
        ]
    ]
        
    all_tool_calls: List[ToolCall] = []
    content = ""
    
    # Tool executor for web_search only
    executor_instance = ToolExecutor(project_dir=project_dir or ".", index={})
    
    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            # Check if web_search is still available
            if not tool_usage.can_use_web_search():
                available_tools = []  # No tools available
            else:
                available_tools = new_project_tools
            
            if available_tools:
                response = await call_llm_with_tools(
                    model=orchestrator_model,
                    messages=messages,
                    tools=available_tools,
                    temperature=0,
                    max_tokens=20000,
                    tool_choice="auto",
                )
            else:
                # No tools available, just call without tools
                response_content = await call_llm(
                    model=orchestrator_model,
                    messages=messages,
                    temperature=0,
                    max_tokens=20000,
                )
                response = {"content": response_content, "tool_calls": []}
            
            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
            
            if not tool_calls:
                break
            
            # Process tool calls
            assistant_tool_calls = []
            tool_results = []
            
            # Capture thinking content before tool calls
            current_thinking = content if content else ""
            
            for i, tc in enumerate(tool_calls):
                func_name, func_args, tc_id = parse_tool_call(tc)
                
                if func_name == "web_search" and not tool_usage.can_use_web_search():
                    tool_result = _format_web_search_limit_error(tool_usage)
                    success = False
                else:
                    tool_result = executor_instance.execute(func_name, func_args)
                    success = not tool_result.startswith("<!-- ERROR")
                    tool_usage.increment(func_name)
                
                # Assign thinking to first tool call in batch
                tool_thinking = current_thinking if i == 0 else ""
                
                all_tool_calls.append(ToolCall(
                    name=func_name,
                    arguments=func_args,
                    output=tool_result,
                    success=success,
                    thinking=tool_thinking
                ))
                
                assistant_tool_calls.append(tc)
                tool_results.append({
                    "tool_call_id": tc_id,
                    "name": func_name,
                    "content": tool_result,
                })
            
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": assistant_tool_calls,
            })
            
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "name": tr["name"],
                    "content": tr["content"],
                })
                
        except Exception as e:
            logger.error(f"Orchestrator error (new project): {e}")
            return OrchestratorResult(
                analysis=f"Error: {e}",
                instruction="",
                tool_calls=all_tool_calls,
                raw_response="",
                tool_usage=tool_usage,
            )
    
    # =========================================================================
    # NEW: FORCED FINALIZATION - If no instruction section found after all iterations
    # =========================================================================
    if not _has_instruction_section(content):
        logger.warning("Orchestrator (new project): No instruction section found, requesting final response")
        
        messages.append({
            "role": "user",
            "content": (
                "⚠️ FINAL REQUEST: You have used all available tool calls.\n"
                "Please provide your complete response NOW with:\n\n"
                "## Analysis\n[Your architectural decisions]\n\n"
                "## Setup Instructions (for User)\n[Folder structure, dependencies, setup steps]\n\n"
                "## Instruction for Code Generator\n"
                "**Task:** [Project summary]\n"
                "**Files to create:** [List of files in order]\n"
                "**Implementation details:** [Details for each file]\n\n"
                "DO NOT request more tools. Write your instruction based on what you already know."
            )
        })
        
        try:
            final_content = await call_llm(
                model=orchestrator_model,
                messages=messages,
                temperature=0,
                max_tokens=20000,
            )
            content = final_content
            logger.info("Orchestrator (new project): Received forced final response")
        except Exception as e:
            logger.error(f"Orchestrator (new project) final response error: {e}")
    # =========================================================================
    
    parsed = _parse_orchestrator_response(content)
    
    return OrchestratorResult(
        analysis=parsed["analysis"],
        instruction=parsed["instruction"],
        tool_calls=all_tool_calls,
        raw_response=content,
        tool_usage=tool_usage,
    )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _has_instruction_section(content: str) -> bool:
    """
    Check if response contains instruction section.
    
    NEW: Added to detect when Orchestrator failed to provide instruction.
    
    Args:
        content: Response content from Orchestrator
        
    Returns:
        True if instruction section is found, False otherwise
    """
    if not content:
        return False
    
    patterns = [
        r'##\s*Instruction',
        r'##\s*Инструкция',
        r'\*\*Task:\*\*',
        r'\*\*Задача:\*\*',
        r'\*\*File:\*\*',
        r'\*\*Файл:\*\*',
    ]
    return any(re.search(p, content, re.IGNORECASE) for p in patterns)


def _get_available_tools(tool_usage: ToolUsageStats) -> List[Dict[str, Any]]:
    """
    Get list of available tools based on current usage.
    
    Removes tools that have reached their limits.
    """
    available = []
    
    for tool in ORCHESTRATOR_TOOLS:
        tool_name = tool["function"]["name"]
        
        # Check web_search limit
        if tool_name == "web_search" and not tool_usage.can_use_web_search():
            continue
        
        # Check test run limit (NEW)
        if tool_name == "run_project_tests" and not tool_usage.can_run_tests():
            continue
        
        available.append(tool)
    
    return available


def _format_web_search_limit_error(tool_usage: ToolUsageStats) -> str:
    """Format error message when web_search limit is reached"""
    return f"""<!-- ERROR -->
<error>
  <message>web_search limit reached</message>
  <details>
    You have used all {MAX_WEB_SEARCH_CALLS} allowed web_search calls for this session.
    Web searches used: {tool_usage.web_search_count}/{MAX_WEB_SEARCH_CALLS}
    
    Please complete your analysis using the information you already have.
    You can still use read_file() and search_code() tools.
  </details>
</error>"""


def _format_chunks_for_context(chunks: List[SelectedChunk]) -> str:
    """Format selected chunks as context for Orchestrator"""
    if not chunks:
        return "[No code chunks provided]"
    
    prompt_parts = []
    
    for i, chunk in enumerate(chunks, 1):
        prompt_parts.append(f'### Chunk {i}: {chunk.name} ({chunk.chunk_type})')
        prompt_parts.append(f'**File:** `{chunk.file_path}` (lines {chunk.start_line}-{chunk.end_line})')
        prompt_parts.append(f'**Relevance:** {chunk.relevance_score:.2f} - {chunk.reason}')
        prompt_parts.append('')
        prompt_parts.append('```python')
        prompt_parts.append(chunk.code)
        prompt_parts.append('```')
        prompt_parts.append('')
    
    return '\n'.join(prompt_parts)

def _format_chunks_as_initial_context_message(chunks: List[SelectedChunk]) -> Optional[Dict[str, str]]:
    """
    Format selected chunks as a separate user message for initial context.
    
    This message is added BEFORE the user query and will be REMOVED after 
    the first tool call to save tokens.
    
    Returns:
        User message dict with chunks, or None if no chunks
    """
    if not chunks:
        return None
    
    parts = []
    parts.append("=" * 60)
    parts.append("📦 PRE-FILTERED CODE CHUNKS (for initial context)")
    parts.append("=" * 60)
    parts.append("")
    parts.append("The following code chunks were pre-selected as most relevant to your query.")
    parts.append("Use them for initial analysis. You can request additional files with read_file() if needed.")
    parts.append("")
    
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"### Chunk {i}: {chunk.name} ({chunk.chunk_type})")
        parts.append(f"**File:** `{chunk.file_path}` (lines {chunk.start_line}-{chunk.end_line})")
        parts.append(f"**Relevance:** {chunk.relevance_score:.2f} - {chunk.reason}")
        parts.append("")
        parts.append("```python")
        parts.append(chunk.code)
        parts.append("```")
        parts.append("")
    
    parts.append("=" * 60)
    parts.append("END OF PRE-FILTERED CHUNKS")
    parts.append("=" * 60)
    
    return {
        "role": "user",
        "content": "\n".join(parts)
    }


def _remove_chunks_message_from_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove the pre-filtered chunks message from message history.
    
    Called after first tool call to save tokens.
    The chunks message is identified by the marker "PRE-FILTERED CODE CHUNKS".
    
    Args:
        messages: Current message list
        
    Returns:
        Message list with chunks message removed
    """
    CHUNKS_MARKER = "PRE-FILTERED CODE CHUNKS"
    
    filtered = []
    removed = False
    
    for msg in messages:
        content = msg.get("content", "")
        
        # Check if this is the chunks message
        if msg.get("role") == "user" and isinstance(content, str) and CHUNKS_MARKER in content:
            logger.info("Orchestrator: Removing pre-filtered chunks message from context (token optimization)")
            removed = True
            continue  # Skip this message
        
        filtered.append(msg)
    
    if removed:
        logger.debug(f"Orchestrator: Context reduced from {len(messages)} to {len(filtered)} messages")
    
    return filtered


def _parse_orchestrator_response(response: str) -> Dict[str, str]:
    """
    Parse Orchestrator response into analysis and instruction sections.
    
    IMPROVED: More robust parsing with multiple fallback patterns.
    """
    from typing import Set
    
    result = {
        "analysis": "",
        "instruction": "",
        "target_file": None,
        "target_files": [],
    }
    
    if not response or not response.strip():
        return result
    
    # === Analysis ===
    analysis_patterns = [
        r'##\s*(?:Analysis|Анализ)\s*\n(.*?)(?=\n##\s*(?:Instruction|Инструкция|Setup|Implementation|Changes)|$)',
        r'##\s*(?:Analysis|Анализ)\s*\n(.*?)(?=\n##\s*|$)',
    ]
    
    for pattern in analysis_patterns:
        analysis_match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if analysis_match:
            result["analysis"] = analysis_match.group(1).strip()
            break
    
    # === Instruction (multiple patterns with priority) ===
    instruction_patterns = [
        # Pattern 1: Standard "## Instruction for Code Generator"
        (r'##\s*Instruction\s+for\s+Code\s+Generator\s*\n(.*?)(?=\n##\s*(?!#)|$)', 'standard_en'),
        
        # Pattern 2: Russian "## Инструкция для Code Generator"
        (r'##\s*Инструкция\s+для\s+Code\s+Generator\s*\n(.*?)(?=\n##\s*(?!#)|$)', 'standard_ru'),
        
        # Pattern 3: Short "## Instruction" or "## Инструкция"
        (r'##\s*(?:Instruction|Инструкция)[:\s]*\n(.*?)(?=\n##\s*(?!#)|$)', 'short'),
        
        # Pattern 4: "## Code Changes" or "## Implementation"
        (r'##\s*(?:Code\s+Changes|Implementation|Изменения\s+кода)\s*\n(.*?)(?=\n##\s*(?!#)|$)', 'code_changes'),
        
        # Pattern 5: "## Changes Required" or "## Необходимые изменения"
        (r'##\s*(?:Changes?\s+Required|Необходимые\s+изменения)\s*\n(.*?)(?=\n##\s*(?!#)|$)', 'changes_required'),
        
        # Pattern 6: From **SCOPE:** to end (fallback for structured instructions)
        (r'(\*\*SCOPE:\*\*.*?)(?=\n##\s*(?!#)|$)', 'scope_block'),
        
        # Pattern 7: From **Task:** to end
        (r'(\*\*Task:\*\*.*?)(?=\n##\s*(?!#)|$)', 'task_block'),
        
        # Pattern 8: CODE_BLOCK markers
        (r'(###\s*CODE_BLOCK\s*\d*.*?)(?=\n##\s*(?!#)|$)', 'code_block'),
    ]
    
    for pattern, pattern_name in instruction_patterns:
        match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if match:
            instruction_text = match.group(1).strip()
            # Validate instruction has meaningful content
            if len(instruction_text) > 30 and _looks_like_instruction(instruction_text):
                result["instruction"] = instruction_text
                logger.debug(f"Instruction found via pattern: {pattern_name}")
                break
    
    # === Fallback: Extract anything that looks like an instruction ===
    if not result["instruction"]:
        # Try to find FILE: and MODE: directives anywhere
        file_mode_pattern = r'((?:FILE:|###\s*FILE:)[^\n]+\n(?:MODE:|###\s*MODE:)[^\n]+\n.*?)(?=\n##\s*(?!#)|$)'
        file_mode_match = re.search(file_mode_pattern, response, re.DOTALL | re.IGNORECASE)
        if file_mode_match:
            result["instruction"] = file_mode_match.group(1).strip()
            logger.debug("Instruction found via FILE/MODE fallback")
    
    # === Ultimate fallback: If response has code blocks with Python, extract them ===
    if not result["instruction"]:
        code_blocks = re.findall(r'```python\s*\n(.+?)```', response, re.DOTALL)
        if code_blocks:
            # Check if any code block has FILE: marker
            for block in code_blocks:
                if 'FILE:' in block or '# FILE:' in block:
                    # Extract the whole code block section
                    block_pattern = r'(```python\s*\n.+?```)'
                    all_blocks = re.findall(block_pattern, response, re.DOTALL)
                    if all_blocks:
                        result["instruction"] = '\n\n'.join(all_blocks)
                        logger.debug("Instruction found via code block fallback")
                        break
    
    # === Extract target files ===
    file_patterns = [
        r'###\s*FILE:\s*`([^`]+)`',
        r'\*\*File:\*\*\s*`([^`]+)`',
        r'\*\*Файл:\*\*\s*`([^`]+)`',
        r'FILE:\s*`?([^\s`\n]+\.py)`?',
        r'#\s*FILE:\s*`?([^\s`\n]+\.py)`?',
        r'modify\s+`([^`]+\.py)`',
        r'create\s+`([^`]+\.py)`',
        r'in\s+`([^`]+\.py)`',
    ]
    
    found_files: Set[str] = set()
    search_text = result["instruction"] or response
    
    for pattern in file_patterns:
        for match in re.finditer(pattern, search_text, re.IGNORECASE):
            file_path = match.group(1).strip()
            # Basic validation
            if file_path.endswith('.py') and not file_path.startswith('http'):
                # Normalize path
                file_path = file_path.replace('\\', '/')
                found_files.add(file_path)
    
    result["target_files"] = sorted(found_files)
    
    # target_file = first one (for backward compatibility)
    if result["target_files"]:
        result["target_file"] = result["target_files"][0]
    
    # === Logging for debugging ===
    if result["instruction"]:
        logger.info(f"Parsed instruction: {len(result['instruction'])} chars, files: {result['target_files']}")
    else:
        logger.warning(f"No instruction parsed from response ({len(response)} chars)")
        # Log first 500 chars for debugging
        logger.debug(f"Response preview: {response[:500]}...")
    
    return result



def _looks_like_instruction(text: str) -> bool:
    """
    Check if text looks like a valid instruction (not just analysis).
    
    IMPROVED: More comprehensive check for actionable content.
    
    Returns True if text contains actionable markers.
    """
    if not text or len(text) < 30:
        return False
    
    # Markers that indicate actionable instruction
    action_markers = [
        # English
        "**Task:**",
        "**File:**",
        "**Changes:**",
        "**SCOPE:**",
        "**Location:**",
        "**Signature:**",
        "**Logic:**",
        "**Implementation:**",
        "**Method:**",
        "**Function:**",
        "**Class:**",
        "**Where in method:**",
        "**Preserve:**",
        
        # Section markers
        "### FILE:",
        "### CODE_BLOCK",
        "FILE:",
        "MODE:",
        "TARGET_CLASS:",
        "TARGET_METHOD:",
        "TARGET_FUNCTION:",
        
        # Action types
        "MODIFY_METHOD",
        "MODIFY_CLASS",
        "MODIFY_FUNCTION",
        "ADD_METHOD",
        "ADD_CLASS",
        "ADD_FUNCTION",
        "CREATE_FILE",
        "REPLACE_FILE",
        "REPLACE_METHOD",
        "REPLACE_CLASS",
        "REPLACE_FUNCTION",
        "INSERT_",
        "APPEND_",
        "PATCH_",
        "DELETE",
        
        # Russian
        "**Задача:**",
        "**Файл:**",
        "**Изменения:**",
        "**Метод:**",
        "**Функция:**",
        "**Класс:**",
        
        # Code block indicators
        "```python",
        "def ",
        "class ",
        "async def ",
        
        # List markers with actions
        "- Add ",
        "- Create ",
        "- Modify ",
        "- Update ",
        "- Remove ",
        "- Delete ",
        "- Replace ",
        "- Добавить ",
        "- Создать ",
        "- Изменить ",
        
        # Numbered steps
        "1.",
        "1)",
        "Step 1",
        "Шаг 1",
    ]
    
    text_lower = text.lower()
    
    # Check for any action marker
    for marker in action_markers:
        if marker in text or marker.lower() in text_lower:
            return True
    
    # Check for code content (def, class, import)
    code_indicators = [
        r'\bdef\s+\w+\s*\(',
        r'\bclass\s+\w+',
        r'\bimport\s+\w+',
        r'\bfrom\s+\w+\s+import',
    ]
    
    for pattern in code_indicators:
        if re.search(pattern, text):
            return True
    
    return False


def _validate_instruction_format(instruction: str) -> tuple[bool, List[str]]:
    """
    Validate that instruction follows expected format.
    Returns (is_valid, list_of_issues).
    """
    issues = []
    
    # Check for required elements
    if not re.search(r'\*\*(?:SCOPE|Task):\*\*', instruction):
        issues.append("Missing **SCOPE:** or **Task:** header")
    
    if not re.search(r'\*\*(?:File|Файл):\*\*|###\s*FILE:', instruction):
        issues.append("Missing file specification")
    
    # Check for action blocks (for SCOPE B+)
    has_actions = any([
        "MODIFY_METHOD" in instruction,
        "ADD_METHOD" in instruction,
        "ADD_CLASS" in instruction,
        "ADD_FUNCTION" in instruction,
        "CREATE_FILE" in instruction,
    ])
    
    # If has Changes section but no action blocks - warn
    if "**Changes:**" in instruction and not has_actions:
        issues.append("Has **Changes:** but no ACTION blocks (MODIFY_METHOD, etc.)")
    
    return len(issues) == 0, issues

def _optimize_cache_by_size(messages: List[Dict[str, Any]], limit: int = 3):
    """
    Экономичная оптимизация кэша:
    1. Находит все блоки с cache_control.
    2. Сортирует их по размеру контента (len(text)).
    3. Оставляет кэш только у топ-N самых больших блоков.
    4. У остальных (мелких) снимает флаг cache_control, чтобы не занимать слоты зря.
    """
    # Список кандидатов: храним (размер, ссылка_на_блок)
    candidates = []
    
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control", {}).get("type") == "ephemeral":
                    text_len = len(block.get("text", ""))
                    candidates.append({
                        "size": text_len,
                        "block": block
                    })
    
    total_candidates = len(candidates)
    
    # Если блоков меньше лимита, ничего делать не надо - кэшируем всё
    if total_candidates <= limit:
        return

    # Сортируем по размеру (от больших к меньшим)
    candidates.sort(key=lambda x: x["size"], reverse=True)
    
    # Топ-3 "жирных" оставляем, остальных разжалуем
    losers = candidates[limit:]
    
    logger.info(f"Orchestrator: Cache optimization. Keeping {limit} largest blocks. Dropping {len(losers)} smaller ones.")
    
    for item in losers:
        # Удаляем флаг кэширования у мелких блоков
        del item["block"]["cache_control"]


# ============================================================================
# GENERAL CHAT MODE (для универсальных вопросов, не связанных с кодовой базой)
# ============================================================================

@dataclass
class UserFile:
    """Представляет загруженный пользователем файл"""
    filename: str
    content: str
    tokens: int
    file_type: str  # 'pdf', 'docx', 'txt', 'xlsx', etc.


@dataclass
class GeneralChatResult:
    """Результат работы General Chat Orchestrator"""
    response: str  # Финальный ответ пользователю
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_response: str = ""
    tool_usage: Optional[ToolUsageStats] = None


class GeneralChatOrchestrator:
    """
    Оркестратор для режима General Chat (без кодовой базы).
    Работает с файлами пользователя и веб-поиском.
    """
    
    def __init__(self, model: str = None, is_legal_mode: bool = False):
        """
        Args:
            model: Модель для использования (по умолчанию из cfg.GENERAL_CHAT_MODEL)
            is_legal_mode: Включить юридический режим (специальный промпт)
        """
        self.model = model or cfg.GENERAL_CHAT_MODEL
        self.is_legal_mode = is_legal_mode
        logger.info(f"GeneralChatOrchestrator initialized: model={cfg.get_model_display_name(self.model)}, legal_mode={is_legal_mode}")

    async def orchestrate_general(
        self,
        user_query: str,
        user_files: List[UserFile],
        history: List[Dict[str, str]],
    ) -> GeneralChatResult:
        """
        Основная функция оркестрации для General Chat.
        
        Args:
            user_query: Запрос пользователя
            user_files: Список загруженных файлов
            history: История чата (уже обработанная HistoryManager)
        
        Returns:
            GeneralChatResult с ответом и метаданными
        """
        logger.info(f"General Chat: processing query (files={len(user_files)}, legal_mode={self.is_legal_mode})")
        
        # Инициализация трекера инструментов
        tool_usage = ToolUsageStats()
        
        # Формируем промпты
        from app.llm.prompt_templates import format_orchestrator_prompt_general
        
        prompts = format_orchestrator_prompt_general(
            user_query=user_query,
            user_files=[{"filename": f.filename, "content": f.content} for f in user_files],
            is_legal_mode=self.is_legal_mode,
            remaining_web_searches=tool_usage.get_remaining_web_searches()
        )
        
        # Строим сообщения
        messages = [{"role": "system", "content": prompts["system"]}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompts["user"]})
        
        # Трекинг вызовов инструментов
        all_tool_calls: List[ToolCall] = []
        content = ""
        
        # Доступные инструменты для General Chat (только general_web_search)
        available_tools = self._get_general_tools(tool_usage)
        
        # ЛОКАЛЬНЫЕ НАСТРОЙКИ ЛИМИТОВ ДЛЯ GENERAL CHAT
        GENERAL_CHAT_MAX_ITERATIONS = 25  # Увеличено с дефолтных 5-7 до 25
        MAX_GENERAL_SEARCHES = 15         # Увеличено с дефолтных 3 до 15

        # Основной цикл с инструментами
        for iteration in range(GENERAL_CHAT_MAX_ITERATIONS):
            try:
                # Обновляем список инструментов (если лимит поиска исчерпан)
                available_tools = self._get_general_tools(tool_usage)
                
                # Оптимизация кэша для Claude (если используется)
                is_claude = self.model in [cfg.MODEL_OPUS_4_5, cfg.MODEL_SONNET_4_5, cfg.MODEL_SONNET_4_6, cfg.MODEL_OPUS_4_6]
                if is_claude:
                    _optimize_cache_by_size(messages, limit=3)
                
                # Вызов LLM с инструментами
                response = await call_llm_with_tools(
                    model=self.model,
                    messages=messages,
                    tools=available_tools,
                    temperature=None,  # Чуть выше для креативности в General Chat
                    max_tokens=7000,
                    tool_choice="auto",
                )
                
                content = response.get("content", "")
                tool_calls = response.get("tool_calls", [])
                
                # Если нет вызовов инструментов — готово
                if not tool_calls:
                    logger.info(f"General Chat: completed (iteration {iteration + 1})")
                    break
                
                # Выполняем вызовы инструментов
                logger.info(f"General Chat: executing {len(tool_calls)} tool call(s)")
                assistant_tool_calls = []
                tool_results = []
                
                # Захватываем мысли модели (текст до вызова инструмента)
                current_thinking = content if content else ""
                
                for tc in tool_calls:
                    func_name, func_args, tc_id = parse_tool_call(tc)
                    
                    # Проверка лимита для general_web_search с учетом увеличенного лимита
                    # Если используется стандартный метод tool_usage.can_use_web_search(), он проверит на 3.
                    # Поэтому проверяем вручную на наш новый лимит MAX_GENERAL_SEARCHES
                    search_limit_reached = False
                    if func_name == "general_web_search":
                        if tool_usage.web_search_count >= MAX_GENERAL_SEARCHES:
                            search_limit_reached = True

                    if search_limit_reached:
                        tool_result = _format_web_search_limit_error(tool_usage)
                        success = False
                        logger.warning(f"General Chat: search limit reached ({MAX_GENERAL_SEARCHES})")
                    else:
                        # Выполняем инструмент
                        # ПРИМЕЧАНИЕ: Чтобы увеличить кол-во результатов за раз (до 20), 
                        # нужно убедиться, что _execute_general_tool это поддерживает.
                        # Можно передать max_results прямо в аргументах, если модель сама не догадалась.
                        if func_name == "general_web_search" and "max_results" not in func_args:
                             func_args["max_results"] = 10 # Помогаем модели брать больше

                        tool_result = await self._execute_general_tool(func_name, func_args)
                        success = not tool_result.startswith("<!--ERROR-->")
                        
                        # Обновляем статистику (general_web_search = web_search в статистике)
                        if func_name == "general_web_search":
                            tool_usage.increment("web_search")
                    
                    # Записываем вызов с мыслями
                    tool_call_record = ToolCall(
                        name=func_name,
                        arguments=func_args,
                        output=tool_result,
                        success=success,
                        thinking=current_thinking
                    )
                    all_tool_calls.append(tool_call_record)
                    
                    # Очищаем current_thinking после первого использования
                    if current_thinking:
                        current_thinking = ""
                    
                    # Собираем для следующей итерации
                    assistant_tool_calls.append(tc)
                    
                    # Формируем результат (с кэшированием для Claude)
                    # [FIX]: Исправлена ошибка 400 - теперь отправляем СПИСОК (list), а не словарь
                    content_payload = tool_result
                    if is_claude and len(tool_result) > 100:
                        content_payload = [
                            {
                                "type": "text",
                                "text": tool_result,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ]
                    
                    tool_results.append({
                        "tool_call_id": tc_id,
                        "name": func_name,
                        "content": content_payload,
                    })
                
                # Добавляем сообщения в историю
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": assistant_tool_calls,
                })
                
                for tr in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "name": tr["name"],
                        "content": tr["content"],
                    })
                
                # Обновляем системный промпт с новым лимитом поиска
                # Важно: передаем (MAX - текущее), чтобы промпт показывал верный остаток
                remaining_searches = max(0, MAX_GENERAL_SEARCHES - tool_usage.web_search_count)
                
                prompts = format_orchestrator_prompt_general(
                    user_query=user_query,
                    user_files=[{"filename": f.filename, "content": f.content} for f in user_files],
                    is_legal_mode=self.is_legal_mode,
                    remaining_web_searches=remaining_searches
                )
                messages[0]["content"] = prompts["system"]
                
            except Exception as e:
                logger.error(f"General Chat LLM error: {e}")
                return GeneralChatResult(
                    response=f"Произошла ошибка при обработке запроса: {e}",
                    raw_response=str(e),
                    tool_calls=all_tool_calls,
                    tool_usage=tool_usage,
                )
        
        # Возвращаем финальный результат
        return GeneralChatResult(
            response=content,
            tool_calls=all_tool_calls,
            raw_response=content,
            tool_usage=tool_usage,
        )
    
    def _get_general_tools(self, tool_usage: ToolUsageStats) -> List[Dict[str, Any]]:
        """Возвращает доступные инструменты для General Chat"""
        from app.tools.tool_definitions import GENERAL_WEB_SEARCH_TOOL
        
        tools = []
        
        # Добавляем general_web_search, только если лимит не исчерпан
        if tool_usage.can_use_web_search():
            tools.append(GENERAL_WEB_SEARCH_TOOL)
        
        return tools
    
    async def _execute_general_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
            """Выполняет инструмент для General Chat"""
            if tool_name == "general_web_search":
                from app.tools.general_web_search import (
                    async_general_web_search, 
                    format_results_xml, 
                    format_error, 
                    format_no_results
                )
                
                query = args.get("query", "")
                if not query:
                    return format_error("Query is required")
                
                max_results = min(args.get("max_results", 5), 10)
                time_limit = args.get("time_limit", "w")
                region = args.get("region", "ru-ru")
                
                try:
                    # Вызываем асинхронную функцию напрямую (мы уже в async контексте)
                    result = await async_general_web_search(query, max_results, time_limit, region)
                    
                    if not result.success:
                        return format_error(result.error or "Search failed")
                    
                    if not result.pages:
                        return format_no_results(query)
                    
                    return format_results_xml(result)
                except Exception as e:
                    return format_error(f"Search failed: {e}")
            else:
                return f"<!--ERROR-->Unknown tool: {tool_name}"


# ============================================================================
# AGENT MODE ORCHESTRATION (with context compression)
# ============================================================================

async def orchestrate_agent(
    user_query: str,
    selected_chunks: List[SelectedChunk],
    compact_index: str,
    history: List[Dict[str, str]],
    orchestrator_model: str,
    project_dir: str,
    index: Dict[str, Any],
    project_map: str = "",
    tool_executor: Optional[Callable] = None,
    is_new_project: bool = False,
) -> OrchestratorResult:
    """
    Agent Mode orchestration with automatic context compression.
    
    This is a wrapper around orchestrate() that adds:
    1. Proactive compression for DeepSeek Reasoner (100k → 30k) and Gemini 3.0 Pro (600k → 150k)
    2. Reactive compression on context overflow errors for ALL models (→ 30k)
    3. Claude cache optimization (preserved from base orchestrate)
    4. Batch file token limit for Opus 4.5 and DeepSeek Reasoner
    5. NEW: Chunks passed as separate message, removed after first tool call
    6. NEW: is_new_project parameter to use correct prompt formatter based on project type
    
    Use this function for Agent Mode. For Ask Mode, use orchestrate() directly.
    
    Args:
        user_query: User's question
        selected_chunks: Chunks from Pre-filter
        compact_index: Compact index string
        history: Conversation history (already processed by HistoryManager)
        orchestrator_model: Model to use (from Router) - ALSO USED FOR ADAPTIVE BLOCKS!
        project_dir: Path to project root
        index: Full project index (for search_code tool)
        project_map: Project map string with file descriptions
        tool_executor: Optional custom tool executor function
        is_new_project: Whether this is a new project (no existing code)
        
    Returns:
        OrchestratorResult with analysis, instruction, and tool calls
    """
    logger.info(f"Agent Mode: starting with {cfg.get_model_display_name(orchestrator_model)}")
    
    compressor = get_compressor(orchestrator_model)
    tool_usage = ToolUsageStats()
    
    # === Build initial context ===
    conversation_summary = "No previous context (Start of conversation)."
    if history:
        conversation_summary = (
            f"⚠️ CONTINUING CONVERSATION ({len(history)} messages in history).\n"
            "Review the message history passed to you to understand previous actions.\n"
            "Do not introduce yourself again. Focus on the latest user request."
        )

    chunks_context = _format_chunks_for_context(selected_chunks)
    
    # NOTE: selected_chunks NOT used in system prompt anymore
    # Choose prompt based on project type
    if is_new_project:
        prompts = format_orchestrator_prompt_new_project_agent(
            user_query=user_query,
            remaining_web_searches=tool_usage.get_remaining_web_searches(),
            orchestrator_model_id=orchestrator_model,
            conversation_summary=conversation_summary,
        )
    else:
        prompts = format_orchestrator_prompt_ask_agent(
            user_query=user_query,
            selected_chunks="",  # DEPRECATED - chunks passed as separate message
            compact_index=compact_index,
            project_map=project_map,
            remaining_web_searches=tool_usage.get_remaining_web_searches(),
            orchestrator_model_id=orchestrator_model,
            conversation_summary=conversation_summary
        )
    
    messages = [{"role": "system", "content": prompts["system"]}]
    messages.extend(history)
    
    # === NEW: Add chunks as SEPARATE user message BEFORE the query ===
    chunks_message = _format_chunks_as_initial_context_message(selected_chunks)
    if chunks_message:
        messages.append(chunks_message)
        logger.info(f"Agent Mode: Added {len(selected_chunks)} chunks as initial context message")
    
    # Add actual user query
    messages.append({"role": "user", "content": prompts["user"]})
    
    # Track if chunks have been removed
    chunks_removed = False
    
    all_tool_calls: List[ToolCall] = []
    
    # Setup tool executor
    executor_instance = ToolExecutor(project_dir=project_dir, index=index)
    if tool_executor is None:
        tool_executor = lambda name, args: executor_instance.execute(name, args)
    
    content = ""
    available_tools = ORCHESTRATOR_TOOLS.copy()
    
    # === Main agent loop ===
    
    AGENT_MAX_ITERATIONS = 100
    iteration = 0
    
    while iteration < AGENT_MAX_ITERATIONS:
        iteration += 1
        
        try:
            available_tools = _get_available_tools(tool_usage)
            
            # Claude cache optimization
            is_claude_model = orchestrator_model in [cfg.MODEL_OPUS_4_5, cfg.MODEL_SONNET_4_5, cfg.MODEL_SONNET_4_6, cfg.MODEL_OPUS_4_6]
            if is_claude_model:
                _optimize_cache_by_size(messages, limit=3)
            
            # === AGENT MODE: Check and compress context ===
            messages, compression_result = await compressor.check_and_compress(messages)
            if compression_result:
                logger.info(
                    f"Context compressed: {compression_result.original_tokens} → "
                    f"{compression_result.compressed_tokens} tokens"
                )
            
            # === LLM call with error handling ===
            try:
                response = await call_llm_with_tools(
                    model=orchestrator_model,
                    messages=messages,
                    tools=available_tools,
                    temperature=0,
                    max_tokens=20000,
                    tool_choice="auto",
                )
            except Exception as e:
                # Check for context overflow (reactive compression for all models)
                if is_context_overflow_error(e):
                    logger.warning(f"Context overflow for {orchestrator_model}, applying emergency compression...")
                    messages, emergency_result = await compressor.emergency_compress(messages)
                    logger.info(f"Emergency: {emergency_result.original_tokens} → {emergency_result.compressed_tokens} tokens")
                    
                    # Retry after compression
                    response = await call_llm_with_tools(
                        model=orchestrator_model,
                        messages=messages,
                        tools=available_tools,
                        temperature=0,
                        max_tokens=20000,
                        tool_choice="auto",
                    )
                else:
                    raise
            
            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
            reasoning_content = response.get("reasoning_content")
            thought_signature = response.get("thought_signature")
            reasoning_details = response.get("reasoning_details")
            
            if not tool_calls:
                logger.info(f"Agent Mode: completed (iteration {iteration})")
                break
            
            # === NEW: Remove chunks message after FIRST tool call ===
            if not chunks_removed and chunks_message:
                messages = _remove_chunks_message_from_history(messages)
                chunks_removed = True
            
            # === Execute tool calls ===
            logger.info(f"Agent Mode: executing {len(tool_calls)} tool call(s)")
            
            current_thinking = reasoning_content if reasoning_content else (content if content else "")
            
            # === NEW: Use batch limit for specific models ===
            if _should_use_batch_limit(orchestrator_model):
                logger.info(f"Agent Mode: applying batch file limit for {cfg.get_model_display_name(orchestrator_model)}")
                
                batch_tool_calls, assistant_tool_calls, tool_results = _process_file_tools_with_batch_limit(
                    tool_calls=tool_calls,
                    tool_executor=tool_executor,
                    tool_usage=tool_usage,
                    token_limit=BATCH_FILE_TOKEN_LIMIT,
                )
                
                # Assign thinking to first tool call
                if batch_tool_calls and current_thinking:
                    batch_tool_calls[0].thinking = current_thinking
                
                all_tool_calls.extend(batch_tool_calls)
                
                # Apply Claude cache optimization to results
                if is_claude_model:
                    for tr in tool_results:
                        tool_name = tr.get("name", "")
                        tool_content = tr.get("content", "")
                        is_heavy_tool = tool_name in ["read_file", "read_code_chunk"]
                        
                        if is_heavy_tool and isinstance(tool_content, str) and len(tool_content) > 100:
                            tr["content"] = [
                                {
                                    "type": "text",
                                    "text": tool_content,
                                    "cache_control": {"type": "ephemeral"}
                                }
                            ]
                            logger.debug(f"Agent Mode: Cache control enabled for {tool_name}")
            else:
                # === Original logic for other models ===
                assistant_tool_calls = []
                tool_results = []
                
                for i, tc in enumerate(tool_calls):
                    func_name, func_args, tc_id = parse_tool_call(tc)
                    
                    if func_name == "web_search" and not tool_usage.can_use_web_search():
                        tool_result = _format_web_search_limit_error(tool_usage)
                        success = False
                        logger.warning(f"Agent Mode: web_search limit reached ({MAX_WEB_SEARCH_CALLS})")
                    elif func_name == "run_project_tests" and not tool_usage.can_run_tests():
                        tool_result = _format_test_run_limit_error(tool_usage)
                        success = False
                    else:
                        tool_result = tool_executor(func_name, func_args)
                        success = not tool_result.startswith("<!-- ERROR")
                        tool_usage.increment(func_name)
                    
                    tool_thinking = current_thinking if i == 0 else ""
                    
                    all_tool_calls.append(ToolCall(
                        name=func_name,
                        arguments=func_args,
                        output=tool_result,
                        success=success,
                        thinking=tool_thinking
                    ))
                    
                    assistant_tool_calls.append(tc)
                    
                    # Claude cache for heavy tools
                    content_payload = tool_result
                    is_heavy_tool = func_name in ["read_file", "read_code_chunk"]
                    
                    if is_claude_model and is_heavy_tool and len(tool_result) > 100:
                        content_payload = [
                            {
                                "type": "text",
                                "text": tool_result,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ]
                        logger.debug(f"Agent Mode: Cache enabled for {func_name}")
                    
                    tool_results.append({
                        "tool_call_id": tc_id,
                        "name": func_name,
                        "content": content_payload,
                    })
            
            # Build assistant message
            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": assistant_tool_calls,
            }
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            if thought_signature:
                assistant_msg["thought_signature"] = thought_signature
            if reasoning_details:
                assistant_msg["reasoning_details"] = reasoning_details
            
            messages.append(assistant_msg)
            
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "name": tr["name"],
                    "content": tr["content"],
                })
            
            # Update system prompt with remaining searches
            if is_new_project:
                updated_prompts = format_orchestrator_prompt_new_project_agent(
                    user_query=user_query,
                    remaining_web_searches=tool_usage.get_remaining_web_searches(),
                    orchestrator_model_id=orchestrator_model,
                    conversation_summary=conversation_summary,
                )
            else:
                updated_prompts = format_orchestrator_prompt_ask_agent(
                    user_query=user_query,
                    selected_chunks="",  # DEPRECATED
                    compact_index=compact_index,
                    project_map=project_map,
                    remaining_web_searches=tool_usage.get_remaining_web_searches(),
                    orchestrator_model_id=orchestrator_model,
                    conversation_summary=conversation_summary
                )
            messages[0]["content"] = updated_prompts["system"]
            
        except Exception as e:
            logger.error(f"Agent Mode error: {e}")
            return OrchestratorResult(
                analysis=f"Error during analysis: {e}",
                instruction="Unable to generate instruction due to error.",
                tool_calls=all_tool_calls,
                raw_response="",
                tool_usage=tool_usage,
            )
    
    else:
        # while loop completed without break (hit iteration limit)
        logger.warning(f"Agent Mode: reached max iterations ({AGENT_MAX_ITERATIONS})")
    
    # === Forced finalization if no instruction ===
    if not _has_instruction_section(content):
        logger.warning("Agent Mode: No instruction section, requesting final response")
        
        messages.append({
            "role": "user",
            "content": (
                "⚠️ FINAL REQUEST: You have used all available tool calls.\n"
                "Please provide your complete response NOW with:\n\n"
                "## Analysis\n[Summary of your findings]\n\n"
                "## Instruction for Code Generator\n"
                "**Task:** [What needs to be done]\n"
                "**File:** [Which file(s) to modify]\n"
                "**Changes:** [Specific changes required]\n\n"
                "DO NOT request more tools. Write your instruction based on what you already know."
            )
        })
        
        try:
            # Compress before final call if needed
            messages, _ = await compressor.check_and_compress(messages)
            
            final_content = await call_llm(
                model=orchestrator_model,
                messages=messages,
                temperature=0,
                max_tokens=20000,
            )
            content = final_content
            logger.info("Agent Mode: Received final response")
        except Exception as e:
            if is_context_overflow_error(e):
                messages, _ = await compressor.emergency_compress(messages)
                final_content = await call_llm(
                    model=orchestrator_model,
                    messages=messages,
                    temperature=0,
                    max_tokens=20000,
                )
                content = final_content
            else:
                logger.error(f"Agent Mode final response error: {e}")
    
    parsed = _parse_orchestrator_response(content)
    
    return OrchestratorResult(
        analysis=parsed["analysis"],
        instruction=parsed["instruction"],
        tool_calls=all_tool_calls,
        target_file=parsed.get("target_file"),
        target_files=parsed.get("target_files", []),
        raw_response=content,
        tool_usage=tool_usage,
    )
