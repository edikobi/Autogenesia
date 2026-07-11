"""
Core TesterAgent class — isolated LLM context, tool loop, report generation and translation.

The TesterAgent verifies that AI-generated code correctly implements the user's requirements.
It has read-only access to project files and can write test files to an isolated temp directory.
"""
import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
        user_additional_input: str = "",
        project_python_path: Optional[str] = None,
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
            user_additional_input: Additional testing instructions from user.
            project_python_path: Path to project's Python interpreter.
        """
        self._project_dir = project_dir
        self._vfs = vfs
        self._project_index = project_index
        self._user_request = user_request
        self._orchestrator_plan = orchestrator_plan
        self._model = tester_model
        self._user_additional_input = user_additional_input
        self._project_python_path = project_python_path
        self._test_temp_dir = tempfile.mkdtemp(prefix="tester_")
        self._tool_executor = TesterToolExecutor(
            project_dir, project_index, vfs, self._test_temp_dir, project_python_path
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
        finally:
            shutil.rmtree(self._test_temp_dir, ignore_errors=True)

    def _build_tester_prompt(self) -> Dict[str, str]:
        """
        Build system and user prompts for the TesterAgent.

        Returns:
            Dict with 'system' and 'user' keys containing prompt text.
        """
        system_prompt = (
            "You are a testing agent. Your goal: verify that the AI-generated code works correctly "
            "and matches the user's request.\n\n"

            "**Core rule**: A test must check the OUTPUT — not just that the code runs without crashing.\n"
            "Write test files, run them, and assert expected values.\n\n"

            "**Test requirements**:\n"
            "- Test the normal case (happy path).\n"
            "- Test at least one edge case (empty input, boundary).\n"
            "- If the user gave examples, test those exactly.\n"
            "- If the code modifies files or state, verify the change.\n"
            "- If exceptions are expected, test that they are raised.\n\n"

            "**Before final verdict**:\n"
            "- Make sure all your tests PASS with the correct results.\n"
            "- If a test fails, first check if the test itself is correct.\n\n"

            "**Tools**: You have read-only access to project files. You can write tests to a temp dir, "
            "compile/run code, check environment, run ruff, and see git diff.\n\n"

            "**Report format** (copy this exactly, filling in the brackets):\n"
            "# Testing Report\n"
            "## Requirements Compliance\n"
            "[Check each user requirement against the code]\n\n"
            "## Tests Run\n"
            "[For each test: what you tested, input, expected, actual, PASS/FAIL]\n\n"
            "## Issues Found\n"
            "[List with severity: 🔴 Critical / 🟡 Warning / 🔵 Info]\n\n"
            "## Conclusion\n"
            "**Verdict: PASS / PASS WITH WARNINGS / FAIL**\n"
            "[Summary]\n\n"

            "**Final note**: Write this report as your last message. Do not call tools after it."
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