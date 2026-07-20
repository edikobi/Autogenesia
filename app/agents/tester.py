"""
Core TesterAgent class — isolated LLM context, tool loop, report generation and translation.

The TesterAgent verifies that AI-generated code correctly implements the user's requirements.
It has read-only access to project files and can write test files to an isolated temp directory.
"""
import asyncio
import logging
import shutil
import tempfile
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from pathlib import Path
import os

from app.llm.api_client import call_llm_with_tools
from app.tools.tool_executor import parse_tool_call
from app.tools.tester_tool_executor import TesterToolExecutor
from app.tools.tester_tool_definitions import TESTER_TOOLS
from app.utils.tester_translator import translate_tester_report
from app.services.virtual_fs import VirtualFileSystem

logger = logging.getLogger(__name__)


@dataclass
class TesterReport:
    """Result of a TesterAgent run."""
    original_report: str          # English markdown report for Orchestrator
    translated_report: str        # Russian markdown report for user display
    success: bool                 # Whether tester completed without fatal error
    error: Optional[str] = None   # Error message if success=False


class TesterAgent:
    """
    Independent Testing Agent that verifies AI-generated code.

    Has read-only access to project files via VFS.
    Can write test files to an isolated temp directory.
    Produces a structured markdown report with verdict.
    """

    def __init__(
        self,
        project_dir: str,
        vfs: VirtualFileSystem,
        project_index: Dict[str, Any],
        user_request: str,
        orchestrator_plan: str = "",
        tester_model: Optional[str] = None,
        tester_provider: Optional[str] = None,
        user_additional_input: str = "",
        project_python_path: Optional[str] = None,
        on_tool_call: Optional[Callable[[str, Dict[str, Any], str, bool], None]] = None,
    ):
        """
        Initialize TesterAgent.

        Args:
            project_dir: Path to project root.
            vfs: VirtualFileSystem instance for read access.
            project_index: Project semantic index.
            user_request: Original user request text.
            orchestrator_plan: Plan from Pre-filter/Orchestrator.
            tester_model: LLM model to use for testing.
            tester_provider: Per-role provider to route tester LLM calls through
                (overrides cfg.get_selected_agent_provider() when a model is
                explicitly selected by the user).
            user_additional_input: Additional testing instructions from user.
            project_python_path: Path to project's Python interpreter.
            on_tool_call: Optional callback for real-time tool call streaming.
        """
        self._project_dir = project_dir
        self._vfs = vfs
        self._project_index = project_index
        self._user_request = user_request
        self._orchestrator_plan = orchestrator_plan
        self._model = tester_model

        # Fallback: if no model specified, select from orchestrator models (provider-aware)
        if self._model is None:
            from config.intermediate_agent_models import get_orchestrator_model_for_agent
            from config.settings import cfg
            self._model, _, self._model_provider = get_orchestrator_model_for_agent(
                cfg.get_available_providers(),
                preferred_provider=cfg.get_selected_agent_provider(),
            )
        else:
            # Per-role provider chosen by the user together with the model.
            self._model_provider = tester_provider
        self._user_additional_input = user_additional_input
        self._project_python_path = project_python_path
        self._on_tool_call = on_tool_call
        self._messages: List[Dict] = []
        self._cleaned_up: bool = False
        self._test_temp_dir = tempfile.mkdtemp(prefix="tester_")

# Create workspace subdirectory and materialize VFS
        self._test_workspace_dir = os.path.join(self._test_temp_dir, "workspace")
        self._vfs.materialize_to_directory(self._test_workspace_dir)
        
        # Симлинкаем node_modules из реального проекта, чтобы ts-node и node могли резолвить библиотеки
        node_modules_src = Path(self._project_dir) / 'node_modules'
        node_modules_dst = Path(self._test_workspace_dir) / 'node_modules'
        if node_modules_src.exists() and not node_modules_dst.exists():
            try:
                if sys.platform == 'win32':
                    import ctypes
                    ctypes.windll.kernel32.CreateSymbolicLinkW(
                        str(node_modules_dst), str(node_modules_src), 1
                    )
                else:
                    os.symlink(node_modules_src, node_modules_dst)
            except Exception as e:
                logger.warning(f"Could not symlink node_modules for tester: {e}")
                
        self._tool_executor = TesterToolExecutor(                    
                    project_dir, project_index, vfs, self._test_workspace_dir, project_python_path
                )
        self._max_iterations = 50

    async def run(self) -> TesterReport:
        """
        Run the testing agent and produce a TesterReport.

        Returns:
            TesterReport with original and translated reports.
        """
        try:
            prompts = self._build_tester_prompt()
            messages = [
                {"role": "system", "content": prompts["system"]},
                {"role": "user", "content": prompts["user"]},
            ]
            report_md = await self._run_tester_loop(messages)
            self._messages = messages
            translated = await translate_tester_report(report_md)
            return TesterReport(
                original_report=report_md,
                translated_report=translated,
                success=True,
            )
        except Exception as e:
            logger.error(f"TesterAgent run failed: {e}", exc_info=True)
            return TesterReport(
                original_report="",
                translated_report="",
                success=False,
                error=str(e),
            )

    def _build_tester_prompt(self) -> Dict[str, str]:
        """
        Build system and user prompts for the TesterAgent.

        Returns:
            Dict with 'system' and 'user' keys containing prompt text.
        """
        system_prompt = (
            "You are an independent Testing Agent. Your mission is to verify that AI-generated code "
            "correctly implements the user's requirements and works as expected. You have read-only "
            "access to the project files and can write test files to an isolated temp directory.\n\n"

            "**CRITICAL — WHAT 'TESTING' MEANS**:\n"
            "A test is NOT just \"the code runs without crashing\". A test is a set of assertions that "
            "verify the code produces the CORRECT OUTPUT for given inputs. A test that only checks "
            "exit code is INSUFFICIENT and will be considered a FAILED TEST.\n\n"

            "**MANDATORY — TEST REQUIREMENTS**:\n"
            "1. For every changed function/method, write at least one test that:\n"
            "   - Calls the function with known inputs.\n"
            "   - Compares the actual output with the EXPECTED output (assertion).\n"
            "   - Covers both the normal case ('happy path') and at least one edge case (e.g., empty input, boundary value).\n"
            "2. If the user request contains specific examples (e.g., \"should return 42 when given 7\"), your test MUST verify that exact case.\n"
            "3. If the code is supposed to modify a file or database, verify the change actually happened (e.g., read the file back).\n"
            "4. If the code raises exceptions for invalid inputs, write a test that confirms the exception is raised.\n"
            "5. Your test file must be self-contained: include all necessary imports and setup.\n\n"

            "**BEFORE REPORTING SUCCESS**, you MUST ensure:\n"
            "- All your tests PASS with the expected results (not just run without errors).\n"
            "- The output values match the specification from the user request.\n"
            "- No edge case was left untested.\n\n"

            "**IF A TEST FAILS**:\n"
            "- First verify your test itself is correct (no syntax errors, correct imports, correct expected values).\n"
            "- If your test is correct, report the failure with the actual vs expected output.\n"
            "- If the code fails on edge cases, note that as a WARNING even if happy path passes.\n\n"

            "**RESTRICTIONS**: You MUST NOT modify project files. You MUST NOT call `install_dependency`. "
            "You have read-only access: `read_file`, `read_code_chunk`, `search_code`, `grep_search`, "
            "`list_files`, `show_file_relations`, `read_line_context`, `list_installed_packages`, "
            "`search_pypi`, `web_search`, `fetch_webpage`, `analyze_webpage`, `check_security`, "
            "`extract_media`, `get_advice`.\n\n"

            "**PERMISSIONS**: You CAN write test files using `write_test_file` — these go to your "
            "isolated temp directory and do NOT affect the project. You CAN compile and run code "
            "using `compile_code` and `run_code`. You CAN check code quality with `run_ruff`. "
            "You CAN check the environment with `check_environment`. You CAN see git diff between "
            "VFS and disk with `git_diff_vfs_disk`.\n\n"

            "**MANDATORY REPORT FORMAT**: When testing is complete, write your FINAL MESSAGE as a "
            "markdown report with these exact sections:\n\n"
            "# Testing Report\n"
            "## Requirements Compliance\n"
            "[Point-by-point analysis of how well the solution matches the user's request]\n\n"
            "## Test Cases Run\n"
            "[For each test you wrote: describe the input, expected output, actual output, and PASS/FAIL]\n\n"
            "## Errors Found\n"
            "[List each error with severity: 🔴 Critical / 🟡 Warning / 🔵 Info. "
            "If none: \"No errors found.\"]\n\n"
            "## What Was Tested\n"
            "[Files examined, tools used, test files written and their results]\n\n"
            "## Findings\n"
            "[Detailed results: compilation output, execution output, ruff results, test outputs]\n\n"
            "## Conclusion\n"
            "**Verdict: PASS / PASS WITH WARNINGS / FAIL**\n"
            "[Brief summary of the overall assessment]\n\n"

            "**IMPORTANT**: Write the report as your FINAL message with NO tool calls after it. "
            "Do not add more tool calls after writing the report. Your verdict MUST be based on "
            "the actual test results, not on the absence of compilation errors."
        )

        user_parts = [f"## User Request\n{self._user_request}"]

        if self._orchestrator_plan:
            user_parts.append(f"\n\n## Orchestrator Plan (from Pre-filter)\n{self._orchestrator_plan}")

        if self._user_additional_input:
            user_parts.append(f"\n\n## Additional Testing Instructions\n{self._user_additional_input}")

        user_parts.append(
            "\n\nStart by using git_diff_vfs_disk to see all staged changes, then read the "
            "relevant files and test thoroughly. When done, write your final markdown report."
        )

        user_prompt = "".join(user_parts)

        return {"system": system_prompt, "user": user_prompt}

    async def _run_tester_loop(self, messages: List[Dict]) -> str:
        """
        Run the LLM tool-calling loop until the agent produces a final report.

        Args:
            messages: Initial message list with system and user prompts.

        Returns:
            Final markdown report text from the agent.
        """
        final_content = ""

        for iteration in range(self._max_iterations):
            try:
                response = await call_llm_with_tools(
                    model=self._model,
                    messages=messages,
                    tools=TESTER_TOOLS,
                    temperature=0,
                    max_tokens=20000,
                    tool_choice="auto",
                    preferred_provider=self._model_provider,
                )
            except Exception as e:
                logger.error(f"TesterAgent LLM call error on iteration {iteration}: {e}", exc_info=True)
                break

            content = response.get("content", "") or ""
            tool_calls = response.get("tool_calls") or []
            final_content = content

            if not tool_calls:
                break

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                try:
                    name, args, tc_id = parse_tool_call(tc)
                except Exception as e:
                    logger.warning(f"Failed to parse tool call: {e}")
                    name, args, tc_id = "unknown", {}, f"error_{iteration}"

                try:
                    result = self._tool_executor.execute(name, args)
                except Exception as e:
                    result = f"Tool error: {e}"
                    logger.warning(f"Tool execution error for {name}: {e}")

    # Notify callback about tool call
                    if self._on_tool_call is not None:
                        try:
                            success = not result.startswith("<!-- ERROR")
                            self._on_tool_call(name, args, result[:500], success)
                        except Exception as cb_err:
                            logger.warning(f"Tool call callback error for {name}: {cb_err}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": result,
                })

        if not final_content or not final_content.strip():
            return (
                "# Testing Report\n\n"
                "## Conclusion\n\n"
                "**Verdict: FAIL**\n\n"
                "Tester agent failed to produce a report."
            )

        return final_content
    async def ask_followup(self, user_question: str) -> dict:
        """Handle a follow-up question from the user after the initial test report.

        Args:
            user_question: The user's follow-up question text.

        Returns:
            Dict with keys: response, is_new_report, new_report.
        """
        if not self._messages:
            return {"response": "", "is_new_report": False, "new_report": None}

        followup_context = (
            "The user is asking a FOLLOW-UP question after you already produced a testing report. "
            "Rules:\n"
            "1. If the question is a clarification about your existing report — answer concisely in plain text. "
            "   Do NOT regenerate the full report.\n"
            "2. If the user explicitly asks to re-test or test something new — run tools and produce a NEW full report "
            "   in the mandatory format.\n"
            "3. Do NOT output your chain-of-thought reasoning or '<think>' tags. Output only the final answer.\n"
            "4. You MUST end with a text response, not a tool call.\n"
        )
        self._messages.append({"role": "system", "content": followup_context})
        self._messages.append({"role": "user", "content": user_question})


        try:
            response = await call_llm_with_tools(
                model=self._model,
                messages=self._messages,
                tools=TESTER_TOOLS,
                temperature=0,
                max_tokens=20000,
                tool_choice="auto",
                preferred_provider=self._model_provider,
            )
        except Exception as e:
            logger.error(f"ask_followup LLM call error: {e}", exc_info=True)
            self._messages.pop()
            return {"response": "", "is_new_report": False, "new_report": None}

        content = response.get("content", "") or ""
        tool_calls = response.get("tool_calls") or []

        # Tool execution loop (up to 3 iterations)
        for _iteration in range(6):
            if not tool_calls:
                break

            self._messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                try:
                    name, args, tc_id = parse_tool_call(tc)
                except Exception as e:
                    logger.warning(f"Failed to parse tool call in followup: {e}")
                    name, args, tc_id = "unknown", {}, f"followup_error"

                try:
                    result = self._tool_executor.execute(name, args)
                except Exception as e:
                    result = f"Tool error: {e}"
                    logger.warning(f"Tool execution error in followup for {name}: {e}")

                if self._on_tool_call is not None:
                    try:
                        success = not result.startswith("<!-- ERROR")
                        self._on_tool_call(name, args, result[:500], success)
                    except Exception as cb_err:
                        logger.warning(f"Tool call callback error in followup for {name}: {cb_err}")

                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": result,
                })

            try:
                response = await call_llm_with_tools(
                    model=self._model,
                    messages=self._messages,
                    tools=TESTER_TOOLS,
                    temperature=0,
                    max_tokens=20000,
                    tool_choice="auto",
                    preferred_provider=self._model_provider,
                )
                content = response.get("content", "") or ""
                tool_calls = response.get("tool_calls") or []
            except Exception as e:
                logger.error(f"ask_followup LLM call error in tool loop: {e}", exc_info=True)
                break

        # Если после цикла модель всё ещё хочет вызвать тулы или отдала пустой контент
        if tool_calls or not content.strip():
            try:
                response = await call_llm_with_tools(
                    model=self._model,
                    messages=self._messages,
                    tools=TESTER_TOOLS,
                    temperature=0,
                    max_tokens=20000,
                    tool_choice="none",  # Запрещаем вызов инструментов, заставляем выдать текст
                    preferred_provider=self._model_provider,
                )
                content = response.get("content", "") or ""
            except Exception as e:
                logger.error(f"ask_followup final LLM call error: {e}", exc_info=True)

        # Очистка от возможных "мыслей" (CoT), если провайдер склеил их с ответом
        import re
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        if content:
            self._messages.append({"role": "assistant", "content": content})

        is_new_report = self._looks_like_report(content)

        return {
            "response": content,
            "is_new_report": is_new_report,
            "new_report": content if is_new_report else None,
        }

    def _looks_like_report(self, text: str) -> bool:
        """Check if the text looks like a testing report.

        Checks for at least 2 report markers (case-insensitive).

        Args:
            text: Text to check.

        Returns:
            True if the text appears to be a testing report.
        """
        if not text or not text.strip():
            return False

        markers = [
            "# testing report",
            "## conclusion",
            "**verdict:",
            "## requirements compliance",
            "## errors found",
        ]

        text_lower = text.lower()
        count = sum(1 for marker in markers if marker in text_lower)
        return count >= 2

    def cleanup(self) -> None:
        """Clean up temporary resources. Idempotent — safe to call multiple times."""
        if not self._cleaned_up:
            shutil.rmtree(self._test_temp_dir, ignore_errors=True)
            self._cleaned_up = True
