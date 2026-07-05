"""
Tool executor for TesterAgent — read-only VFS access, blocked install_dependency,
implements 6 unique tester tools via subprocess/adapters.

Delegates all read-only orchestrator tools to the base ToolExecutor.
"""
import difflib
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.tools.tool_executor import ToolExecutor
from app.services.virtual_fs import VirtualFileSystem
from app.services.java_adapter import JavaAdapter
from app.services.go_adapter import GoAdapter
from app.services.js_ts_adapter import JsTsAdapter

logger = logging.getLogger(__name__)


class TesterToolExecutor:
    """
    Tool executor for TesterAgent with read-only VFS access.

    Delegates read-only orchestrator tools to the base ToolExecutor.
    Implements 6 unique tester tools: run_ruff, check_environment,
    compile_code, run_code, git_diff_vfs_disk, write_test_file.
    Blocks install_dependency with an error message.
    """

    def __init__(
        self,
        project_dir: str,
        index: Dict[str, Any],
        virtual_fs: VirtualFileSystem,
        test_temp_dir: str,
        project_python_path: Optional[str] = None,
    ):
        """
        Initialize TesterToolExecutor.

        Args:
            project_dir: Path to project root.
            index: Project semantic index.
            virtual_fs: VirtualFileSystem instance for read access.
            test_temp_dir: Isolated temporary directory for test files.
            project_python_path: Path to project's Python interpreter.
        """
        self._project_dir = project_dir
        self._index = index
        self._vfs = virtual_fs
        self._test_temp_dir = test_temp_dir
        self._base_executor = ToolExecutor(project_dir, index, virtual_fs)
        self._project_python_path = project_python_path or getattr(
            virtual_fs, 'get_project_python', lambda: 'python'
        )()
        self._java_adapter = JavaAdapter(Path(project_dir))
        self._go_adapter = GoAdapter(Path(project_dir))
        self._js_ts_adapter = JsTsAdapter(Path(project_dir))

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Dispatch tool execution to the appropriate handler.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments dictionary.

        Returns:
            Tool execution result as a string.
        """
        if tool_name == "install_dependency":
            return self._format_error(
                "Tool 'install_dependency' is not available for Tester agent. "
                "The Tester cannot install packages."
            )
        elif tool_name == "run_ruff":
            return self._execute_run_ruff(arguments)
        elif tool_name == "check_environment":
            return self._execute_check_environment(arguments)
        elif tool_name == "compile_code":
            return self._execute_compile_code(arguments)
        elif tool_name == "run_code":
            return self._execute_run_code(arguments)
        elif tool_name == "git_diff_vfs_disk":
            return self._execute_git_diff(arguments)
        elif tool_name == "write_test_file":
            return self._execute_write_test_file(arguments)
        else:
            return self._base_executor.execute(tool_name, arguments)

    def _execute_run_ruff(self, arguments: Dict) -> str:
        """
        Run ruff linter on a VFS file.

        Args:
            arguments: Dict with file_path, optional select_rules, ignore_rules, fix, config_override.

        Returns:
            XML string with stdout, stderr, exit_code.
        """
        file_path = arguments.get("file_path")
        if not file_path:
            return self._format_error("Missing required parameter: file_path")

        content = self._vfs.read_file(file_path)
        if content is None:
            return self._format_error(f"File not found in VFS: {file_path}")

        # Write content to temp file with original extension
        ext = os.path.splitext(file_path)[1] or ".py"
        fd, tmp_file = tempfile.mkstemp(suffix=ext, dir=self._test_temp_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception:
            os.close(fd)
            raise

        # Build ruff command
        cmd = [self._project_python_path, "-m", "ruff", "check", "--output-format", "json"]

        select_rules = arguments.get("select_rules")
        if select_rules:
            cmd.extend(["--select", ",".join(select_rules)])

        ignore_rules = arguments.get("ignore_rules")
        if ignore_rules:
            cmd.extend(["--ignore", ",".join(ignore_rules)])

        if arguments.get("fix", False):
            cmd.append("--fix")

        config_override = arguments.get("config_override")
        if config_override:
            ruff_toml_path = os.path.join(self._test_temp_dir, "ruff.toml")
            Path(ruff_toml_path).write_text(config_override, encoding='utf-8')
            cmd.extend(["--config", ruff_toml_path])

        cmd.append(tmp_file)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self._test_temp_dir,
                encoding='utf-8',
                errors='replace',
            )
            return (
                f"<ruff_result>\n"
                f"<stdout>{result.stdout}</stdout>\n"
                f"<stderr>{result.stderr}</stderr>\n"
                f"<exit_code>{result.returncode}</exit_code>\n"
                f"</ruff_result>"
            )
        except FileNotFoundError:
            return self._format_error("ruff not found. Ensure ruff is installed in the project environment.")
        except subprocess.TimeoutExpired:
            return self._format_error("ruff timed out after 60s")
        except Exception as e:
            logger.error(f"Error running ruff: {e}", exc_info=True)
            return self._format_error(f"ruff execution error: {e}")

    def _execute_check_environment(self, arguments: Dict) -> str:
        """
        Collect and return environment information.

        Args:
            arguments: Unused.

        Returns:
            XML string with environment details.
        """
        info_parts = []
        info_parts.append(f"<os_system>{platform.system()}</os_system>")
        info_parts.append(f"<os_release>{platform.release()}</os_release>")
        info_parts.append(f"<os_machine>{platform.machine()}</os_machine>")
        info_parts.append(f"<python_version>{platform.python_version()}</python_version>")
        info_parts.append(f"<python_path>{self._project_python_path}</python_path>")

        # Check tool versions
        tool_checks = [
            ("java", ["java", "-version"], True),   # java outputs to stderr
            ("go", ["go", "--version"], False),
            ("node", ["node", "--version"], False),
            ("tsc", ["tsc", "--version"], False),
        ]

        for tool_name, cmd, use_stderr in tool_checks:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=10, text=True,
                    encoding='utf-8', errors='replace',
                )
                version_output = result.stderr.strip() if use_stderr else result.stdout.strip()
                info_parts.append(f"<{tool_name}_version>{version_output or 'unknown'}</{tool_name}_version>")
            except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
                info_parts.append(f"<{tool_name}_version>not found</{tool_name}_version>")

        # System resources
        try:
            import psutil
            cpu_count = psutil.cpu_count()
            ram_total = psutil.virtual_memory().total
            disk_free = psutil.disk_usage(self._project_dir).free
            info_parts.append(f"<cpu_count>{cpu_count}</cpu_count>")
            info_parts.append(f"<ram_total_bytes>{ram_total}</ram_total_bytes>")
            info_parts.append(f"<disk_free_bytes>{disk_free}</disk_free_bytes>")
        except ImportError:
            cpu_count = os.cpu_count()
            info_parts.append(f"<cpu_count>{cpu_count}</cpu_count>")
            info_parts.append("<ram_total_bytes>psutil not installed</ram_total_bytes>")
            try:
                disk_free = shutil.disk_usage(self._project_dir).free
                info_parts.append(f"<disk_free_bytes>{disk_free}</disk_free_bytes>")
            except Exception:
                info_parts.append("<disk_free_bytes>unknown</disk_free_bytes>")
        except Exception:
            info_parts.append("<cpu_count>unknown</cpu_count>")
            info_parts.append("<ram_total_bytes>unknown</ram_total_bytes>")
            info_parts.append("<disk_free_bytes>unknown</disk_free_bytes>")

        return f"<environment>\n" + "\n".join(info_parts) + "\n</environment>"

    def _execute_compile_code(self, arguments: Dict) -> str:
        """
        Compile a file from VFS for syntax/type checking without executing.

        Args:
            arguments: Dict with file_path and language.

        Returns:
            XML string with success, stdout, stderr, exit_code.
        """
        file_path = arguments.get("file_path")
        language = arguments.get("language")

        if not file_path:
            return self._format_error("Missing required parameter: file_path")
        if not language:
            return self._format_error("Missing required parameter: language")

        content = self._vfs.read_file(file_path)
        if content is None:
            return self._format_error(f"File not found in VFS: {file_path}")

        if language == "python":
            ext = ".py"
            fd, tmp_file = tempfile.mkstemp(suffix=ext, dir=self._test_temp_dir)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(content)
            except Exception:
                os.close(fd)
                raise

            try:
                result = subprocess.run(
                    [self._project_python_path, "-m", "py_compile", tmp_file],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding='utf-8',
                    errors='replace',
                )
                success = result.returncode == 0
                return (
                    f"<compile_result>\n"
                    f"<success>{str(success).lower()}</success>\n"
                    f"<stdout>{result.stdout}</stdout>\n"
                    f"<stderr>{result.stderr}</stderr>\n"
                    f"<exit_code>{result.returncode}</exit_code>\n"
                    f"</compile_result>"
                )
            except subprocess.TimeoutExpired:
                return self._format_error("Python compilation timed out after 30s")
            except Exception as e:
                return self._format_error(f"Python compilation error: {e}")

        elif language == "java":
            try:
                result = self._java_adapter.compile_check(
                    content, file_path, project_root=Path(self._project_dir)
                )
                return (
                    f"<compile_result>\n"
                    f"<success>{str(result.get('success', False)).lower()}</success>\n"
                    f"<stdout>{result.get('stdout', '')}</stdout>\n"
                    f"<stderr>{result.get('stderr', '')}</stderr>\n"
                    f"<exit_code>{result.get('exit_code', -1)}</exit_code>\n"
                    f"</compile_result>"
                )
            except Exception as e:
                return self._format_error(f"Java compilation error: {e}")

        elif language == "go":
            try:
                result = self._go_adapter.compile_check(
                    content, file_path, project_root=Path(self._project_dir)
                )
                return (
                    f"<compile_result>\n"
                    f"<success>{str(result.get('success', False)).lower()}</success>\n"
                    f"<stdout>{result.get('stdout', '')}</stdout>\n"
                    f"<stderr>{result.get('stderr', '')}</stderr>\n"
                    f"<exit_code>{result.get('exit_code', -1)}</exit_code>\n"
                    f"</compile_result>"
                )
            except Exception as e:
                return self._format_error(f"Go compilation error: {e}")

        elif language in ("javascript", "typescript"):
            try:
                result = self._js_ts_adapter.compile_check(
                    content, file_path, project_root=Path(self._project_dir)
                )
                return (
                    f"<compile_result>\n"
                    f"<success>{str(result.get('success', False)).lower()}</success>\n"
                    f"<stdout>{result.get('stdout', '')}</stdout>\n"
                    f"<stderr>{result.get('stderr', '')}</stderr>\n"
                    f"<exit_code>{result.get('exit_code', -1)}</exit_code>\n"
                    f"</compile_result>"
                )
            except Exception as e:
                return self._format_error(f"{language} compilation error: {e}")

        else:
            return self._format_error(f"Unsupported language: {language}")

    def _execute_run_code(self, arguments: Dict) -> str:
        """
        Execute a file from VFS and return output.

        Args:
            arguments: Dict with file_path, language, optional timeout_sec, args.

        Returns:
            XML string with stdout, stderr, exit_code, duration_sec.
        """
        file_path = arguments.get("file_path")
        language = arguments.get("language")
        timeout_sec = min(arguments.get("timeout_sec", 30), 120)
        args = arguments.get("args", [])

        if not file_path:
            return self._format_error("Missing required parameter: file_path")
        if not language:
            return self._format_error("Missing required parameter: language")

        content = self._vfs.read_file(file_path)
        if content is None:
            return self._format_error(f"File not found in VFS: {file_path}")

        # Extension map
        ext_map = {
            "python": ".py",
            "java": ".java",
            "go": ".go",
            "javascript": ".js",
            "typescript": ".ts",
        }
        ext = ext_map.get(language)
        if not ext:
            return self._format_error(f"Unsupported language: {language}")

        # Write content to temp file
        fd, tmp_file = tempfile.mkstemp(suffix=ext, dir=self._test_temp_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception:
            os.close(fd)
            raise

        # Build command by language
        if language == "python":
            cmd = [self._project_python_path, tmp_file, *args]
        elif language == "java":
            # Compile first
            class_name = Path(tmp_file).stem
            try:
                compile_result = subprocess.run(
                    ["javac", tmp_file],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding='utf-8',
                    errors='replace',
                    cwd=self._test_temp_dir,
                )
                if compile_result.returncode != 0:
                    return (
                        f"<run_result>\n"
                        f"<stdout>{compile_result.stdout}</stdout>\n"
                        f"<stderr>{compile_result.stderr}</stderr>\n"
                        f"<exit_code>{compile_result.returncode}</exit_code>\n"
                        f"<duration_sec>0.00</duration_sec>\n"
                        f"</run_result>"
                    )
            except Exception as e:
                return self._format_error(f"Java compilation failed: {e}")
            cmd = ["java", "-cp", self._test_temp_dir, class_name, *args]
        elif language == "go":
            cmd = ["go", "run", tmp_file, *args]
        elif language == "javascript":
            cmd = ["node", tmp_file, *args]
        elif language == "typescript":
            cmd = ["npx", "ts-node", tmp_file, *args]
        else:
            return self._format_error(f"Unsupported language: {language}")

        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                encoding='utf-8',
                errors='replace',
                cwd=self._test_temp_dir,
            )
            duration = round(time.time() - t0, 2)
            return (
                f"<run_result>\n"
                f"<stdout>{result.stdout}</stdout>\n"
                f"<stderr>{result.stderr}</stderr>\n"
                f"<exit_code>{result.returncode}</exit_code>\n"
                f"<duration_sec>{duration}</duration_sec>\n"
                f"</run_result>"
            )
        except subprocess.TimeoutExpired:
            duration = round(time.time() - t0, 2)
            return (
                f"<run_result>\n"
                f"<timeout>true</timeout>\n"
                f"<duration_sec>{timeout_sec}</duration_sec>\n"
                f"</run_result>"
            )
        except Exception as e:
            duration = round(time.time() - t0, 2)
            return self._format_error(f"Code execution error: {e}")

    def _execute_git_diff(self, arguments: Dict) -> str:
        """
        Show unified diff between VFS staged content and on-disk content.

        Args:
            arguments: Dict with optional file_path filter.

        Returns:
            XML string with diffs for all staged files.
        """
        file_path_filter = arguments.get("file_path")
        staged_files = self._vfs.get_staged_files()

        if file_path_filter:
            # Normalize slashes for comparison
            normalized_filter = file_path_filter.replace("\\", "/")
            staged_files = [
                f for f in staged_files
                if f.replace("\\", "/") == normalized_filter
            ]

        if not staged_files:
            return "<diffs>\n<message>No staged files found.</message>\n</diffs>"

        diff_parts = []
        for path in staged_files:
            staged = self._vfs.read_file(path)
            original = self._vfs.read_file_original(path)

            if original is None:
                diff_text = f"[NEW FILE] {path}\n{staged or ''}"
            else:
                original_lines = original.splitlines(keepends=True)
                staged_lines = (staged or "").splitlines(keepends=True)
                diff_lines = list(difflib.unified_diff(
                    original_lines,
                    staged_lines,
                    fromfile=f"disk/{path}",
                    tofile=f"vfs/{path}",
                    lineterm="",
                ))
                diff_text = "".join(diff_lines) if diff_lines else "(no changes)"

            diff_parts.append(f"<file_diff path=\"{path}\">\n{diff_text}\n</file_diff>")

        return "<diffs>\n" + "\n".join(diff_parts) + "\n</diffs>"

    def _execute_write_test_file(self, arguments: Dict) -> str:
        """
        Write a test file to the agent's isolated temp directory.

        Args:
            arguments: Dict with file_name (basename only) and content.

        Returns:
            XML string with path and success message.
        """
        file_name = arguments.get("file_name")
        content = arguments.get("content")

        if not file_name:
            return self._format_error("Missing required parameter: file_name")
        if content is None:
            return self._format_error("Missing required parameter: content")

        # Sanitize: only basename, no paths
        safe_name = os.path.basename(file_name)
        if not safe_name:
            return self._format_error("Invalid file name")

        full_path = os.path.join(self._test_temp_dir, safe_name)

        try:
            Path(full_path).write_text(content, encoding='utf-8')
            return (
                f"<write_result>\n"
                f"<path>{full_path}</path>\n"
                f"<message>Test file written successfully</message>\n"
                f"</write_result>"
            )
        except (IOError, OSError) as e:
            return self._format_error(f"Failed to write test file: {e}")

    def _format_error(self, message: str) -> str:
        """
        Format an error message as an XML comment.

        Args:
            message: Error message text.

        Returns:
            Formatted error string.
        """
        return f"<!-- ERROR: {message} -->"