"""
Tool definitions for TesterAgent — unique testing tools + re-exported orchestrator read-only tools.

Defines 6 unique Tester tools in OpenAI-compatible function-calling JSON Schema format,
then exports TESTER_TOOLS as the combined list of orchestrator tools (minus install_dependency)
and the 6 new tester-specific tools.
"""
from typing import Any, Dict, List

from app.tools.tool_definitions import (
    LIST_FILES_TOOL,
    READ_CODE_CHUNK_TOOL,
    READ_FILE_TOOL,
    SEARCH_CODE_TOOL,
    GREP_SEARCH_TOOL,
    FILE_RELATIONS_TOOL,
    WEB_SEARCH_TOOL,
    GET_ADVICE_TOOL,
    LIST_INSTALLED_PACKAGES_TOOL,
    SEARCH_PYPI_TOOL,
    FETCH_WEBPAGE_TOOL,
    ANALYZE_WEBPAGE_TOOL,
    CHECK_SECURITY_TOOL,
    EXTRACT_MEDIA_TOOL,
    READ_LINE_CONTEXT_TOOL,
)


# ============================================================================
# RUFF_TOOL — Run ruff linter on a VFS file
# ============================================================================

RUFF_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_ruff",
        "description": (
            "Run the ruff linter on a file from the Virtual File System. "
            "Returns linting results in JSON format. "
            "Use this to check code quality, style violations, and potential bugs. "
            "Supports custom rule selection, ignore rules, auto-fix mode, and TOML config override."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file in VFS to lint (e.g., 'app/services/auth.py')."
                },
                "select_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of ruff rule codes to select (e.g., ['E', 'F', 'W'])."
                },
                "ignore_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of ruff rule codes to ignore (e.g., ['E501', 'W293'])."
                },
                "fix": {
                    "type": "boolean",
                    "description": "Whether to apply auto-fixes. Default: false.",
                    "default": False
                },
                "config_override": {
                    "type": "string",
                    "description": "Optional TOML content for [tool.ruff] section to override ruff configuration."
                }
            },
            "required": ["file_path"]
        }
    }
}


# ============================================================================
# CHECK_ENVIRONMENT_TOOL — Return system environment info
# ============================================================================

CHECK_ENVIRONMENT_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_environment",
        "description": (
            "Returns versions of Python/Java/Go/Node/tsc, OS name/release/machine, "
            "and system resources (CPU count, RAM, disk usage). "
            "Use this to understand the execution environment before running code or tests."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}


# ============================================================================
# COMPILE_CODE_TOOL — Compile a file for syntax/type checking
# ============================================================================

COMPILE_CODE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "compile_code",
        "description": (
            "Compile a file from VFS for syntax and type checking without executing it. "
            "Supports Python (py_compile), Java (javac), Go (go build), JavaScript/TypeScript (tsc). "
            "Returns compilation output with success/failure status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file in VFS to compile."
                },
                "language": {
                    "type": "string",
                    "enum": ["python", "java", "go", "javascript", "typescript"],
                    "description": "Programming language of the file."
                }
            },
            "required": ["file_path", "language"]
        }
    }
}


# ============================================================================
# RUN_CODE_TOOL — Execute a file from VFS
# ============================================================================

RUN_CODE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": (
            "Execute a file from VFS and return its output (stdout, stderr, exit code, duration). "
            "Supports Python, Java, Go, JavaScript, and TypeScript. "
            "The file is written to an isolated temp directory before execution. "
            "Timeout is configurable (default 30s, max 120s)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file in VFS to execute."
                },
                "language": {
                    "type": "string",
                    "enum": ["python", "java", "go", "javascript", "typescript"],
                    "description": "Programming language of the file."
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Execution timeout in seconds. Default: 30, Max: 120.",
                    "default": 30,
                    "maximum": 120
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional command-line arguments to pass to the executed file."
                }
            },
            "required": ["file_path", "language"]
        }
    }
}


# ============================================================================
# GIT_DIFF_VFS_DISK_TOOL — Show diff between VFS staged and on-disk content
# ============================================================================

GIT_DIFF_VFS_DISK_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "git_diff_vfs_disk",
        "description": (
            "Show unified diff between VFS staged content and on-disk content for all staged files "
            "or a specific file. This reveals exactly what changes the AI has made to the project. "
            "Use this as the FIRST step to understand what was modified before testing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Optional: filter to a specific file path. If omitted, shows diffs for all staged files."
                }
            },
            "required": []
        }
    }
}


# ============================================================================
# WRITE_TEST_FILE_TOOL — Write a test file to isolated temp directory
# ============================================================================

WRITE_TEST_FILE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "write_test_file",
        "description": (
            "Write a test file to the agent's isolated temporary directory. "
            "This DOES NOT modify project files — the test file exists only in the temp directory. "
            "Use this to create test scripts that you can then execute with run_code. "
            "The file_name must be a basename only (no directory paths)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "Basename of the test file (e.g., 'test_auth.py'). No paths allowed."
                },
                "content": {
                    "type": "string",
                    "description": "Full content of the test file."
                }
            },
            "required": ["file_name", "content"]
        }
    }
}


# ============================================================================
# COMBINED TESTER TOOLS LIST
# ============================================================================

TESTER_TOOLS: List[Dict[str, Any]] = [
    # Orchestrator read-only tools (15 tools, no install_dependency)
    LIST_FILES_TOOL,
    READ_CODE_CHUNK_TOOL,
    READ_FILE_TOOL,
    SEARCH_CODE_TOOL,
    GREP_SEARCH_TOOL,
    FILE_RELATIONS_TOOL,
    WEB_SEARCH_TOOL,
    GET_ADVICE_TOOL,
    LIST_INSTALLED_PACKAGES_TOOL,
    SEARCH_PYPI_TOOL,
    FETCH_WEBPAGE_TOOL,
    ANALYZE_WEBPAGE_TOOL,
    CHECK_SECURITY_TOOL,
    EXTRACT_MEDIA_TOOL,
    READ_LINE_CONTEXT_TOOL,
    # Tester-specific tools (6 tools)
    RUFF_TOOL,
    CHECK_ENVIRONMENT_TOOL,
    COMPILE_CODE_TOOL,
    RUN_CODE_TOOL,
    GIT_DIFF_VFS_DISK_TOOL,
    WRITE_TEST_FILE_TOOL,
]