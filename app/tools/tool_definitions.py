# app/tools/tool_definitions.py
"""
Tool definitions for LLM function calling.

Defines tools in OpenAI-compatible format for use with various LLM providers.
Each tool has:
- name: Unique identifier
- description: What the tool does (for LLM to understand)
- parameters: JSON Schema of accepted parameters
"""
from pathlib import Path
from typing import List, Dict, Any


# ============================================================================
# READ_CODE_CHUNK_TOOL
# ============================================================================

READ_CODE_CHUNK_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_code_chunk",
        "description": (
            "Read ONLY a specific class or function from a Python file. "
            "Saves tokens compared to reading the whole file. "
            "Returns the code of the requested object with minimal context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the Python file (e.g., 'app/main.py')"
                },
                "chunk_name": {
                    "type": "string",
                    "description": "Name of the class or function to read (e.g., 'User', 'process_data')"
                }
            },
            "required": ["file_path", "chunk_name"]
        }
    }
}


# ============================================================================
# READ FILE TOOL
# ============================================================================

READ_FILE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a file from the project with full content wrapped in XML format. "
            "Returns file content with metadata (path, type, tokens, line numbers). "
            "Use when you need to see the complete content of a file that wasn't "
            "included in the pre-filtered chunks, or when you need more context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Path to file relative to project root. "
                        "Example: 'src/auth.py' or 'app/services/database.py'"
                    )
                },
                "include_line_numbers": {
                    "type": "boolean",
                    "description": (
                        "Whether to include line numbers in output. "
                        "Default: true. Useful for referencing specific lines."
                    ),
                    "default": True
                }
            },
            "required": ["file_path"]
        }
    }
}

# ============================================================================
# SEARCH CODE TOOL
# ============================================================================

SEARCH_CODE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_code",
        "description": (
            "Search for the definition of a class, function, or method by name in the project's semantic index. "
            "Returns the file path and line numbers where the element is defined. "
            "Use when you need to quickly locate where a specific component is defined in the codebase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Name of the class, function, or method to search for. "
                        "Example: 'UserAuthenticator', 'process_data', 'login'"
                    )
                },
                "search_type": {
                    "type": "string",
                    "enum": ["all", "class", "function", "method"],
                    "description": (
                        "Type of element to search for. "
                        "'all' searches for everything. Default: 'all'"
                    ),
                    "default": "all"
                }
            },
            "required": ["query"]
        }
    }
}


# ============================================================================
# GREP/FULLTEXT SEARCH TOOL
# ============================================================================

GREP_SEARCH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "grep_search",
        "description": (
            "Full-text search across all project files (like grep). "
            "Searches for text patterns in file contents, not just definitions. "
            "Use when you need to find: "
            "- Specific error messages or log entries "
            "- Function/method calls (not just definitions) "
            "- Configuration values, constants, strings "
            "- TODO/FIXME comments "
            "- Import statements or usage of specific libraries "
            "Returns matches with file paths, line numbers, and context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Text pattern to search for. Can be plain text or regex. "
                        "For regex, use Python regex syntax. Example: 'def create_.*' or 'TODO:'"
                    )
                },
                "use_regex": {
                    "type": "boolean",
                    "description": "Whether pattern is a regular expression. Default: false",
                    "default": False
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive search. Default: false",
                    "default": False
                },
                "file_pattern": {
                    "type": "string",
                    "description": (
                        "Filter files by pattern (glob). "
                        "Examples: '*.py', '*.json', 'app/**/*.py', 'tests/*'"
                    )
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Subdirectory to search in (relative to project root). "
                        "Example: 'app/', 'tests/unit/'"
                    )
                },
                "max_files": {
                    "type": "integer",
                    "description": (
                        "Maximum number of files to search (for performance). "
                        "Default: 100, Max: 500"
                    ),
                    "default": 100,
                    "maximum": 500
                },
                "max_matches_per_file": {
                    "type": "integer",
                    "description": (
                        "Maximum matches to return per file. Default: 20"
                    ),
                    "default": 20,
                    "maximum": 100
                },
                "max_total_matches": {
                    "type": "integer",
                    "description": (
                        "Total matches to return across all files. Default: 50"
                    ),
                    "default": 50,
                    "maximum": 200
                },
                "context_lines": {
                    "type": "integer",
                    "description": (
                        "Number of context lines to show around match. Default: 2"
                    ),
                    "default": 2,
                    "minimum": 0,
                    "maximum": 10
                }
            },
            "required": ["pattern"]
        }
    }
}

# ============================================================================
# WEB SEARCH TOOL
# ============================================================================

WEB_SEARCH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the internet for information using DuckDuckGo. "
            "Returns the 10 most relevant page contents (limited to 25,000 tokens total). "
            "Use when you need external documentation, examples, or solutions "
            "that aren't available in the project codebase. "
            "Good for: library documentation, error messages, best practices, "
            "Stack Overflow solutions, API references."
            "⚠️ LIMIT: Maximum 3 calls per session. Use wisely!"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Be specific for better results. "
                        "Example: 'Python asyncio gather exception handling' or "
                        "'FastAPI dependency injection best practices'"
                    )
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. Default: 10. Max: 10. "
                        "Results are ranked by relevance to the query."
                    ),
                    "default": 10,
                    "maximum": 10
                },
                "region": {
                    "type": "string",
                    "description": (
                        "Region for search results. Default: 'wt-wt' (no region). "
                        "Options: 'us-en', 'uk-en', 'ru-ru', etc."
                    ),
                    "default": "wt-wt"
                }
            },
            "required": ["query"]
        }
    }
}


GENERAL_WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "general_web_search",
        "description": "Поиск в интернете для общих вопросов, новостей, фактов и юридической информации. "
                       "Используй этот инструмент, когда нужно найти актуальную информацию, которой нет в твоих знаниях. "
                       "Поддерживает фильтрацию по времени. Не используй для поиска кода (для этого есть websearch).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос. Для юридических вопросов используй точные термины. "
                                   "Пример: 'Статья 158 УК РФ изменения 2024' или 'Курс доллара ЦБ РФ сегодня'"
                },
                "time_limit": {
                    "type": "string",
                    "enum": ["d", "w", "m", "y"],
                    "description": "Фильтр по времени: 'd' (день), 'w' (неделя), 'm' (месяц), 'y' (год). "
                                   "Используй 'w' или 'm' для новостей и недавних законов."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Количество результатов (по умолчанию 5, максимум 10).",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    }
}


# ============================================================================
# FILE RELATIONS TOOL (Shows file dependencies and context)
# ============================================================================
FILE_RELATIONS_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "show_file_relations",
        "description": (
            "Analyzes and displays all structural relationships of the requested file within the project: "
            "- IMPORTS: Which files this file depends on (outgoing dependencies). "
            "- IMPORTED BY: Which files depend on this file (incoming usage). "
            "- TESTS: Associated test files. "
            "- SIBLINGS: Other files in the same directory. "
            "Operates with awareness of staged changes in VFS. "
            "Use this tool to quickly understand the impact radius and context of a file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Path to the target file to analyze. "
                        "Example: 'app/services/virtual_fs.py'"
                    )
                },
                "include_tests": {
                    "type": "boolean",
                    "description": "Whether to include associated test files in results.",
                    "default": True
                },
                "include_siblings": {
                    "type": "boolean",
                    "description": "Whether to include files from the same directory.",
                    "default": True
                },
                "max_relations": {
                    "type": "integer",
                    "description": "Maximum number of relations to return per category (0 = all).",
                    "default": 20
                }
            },
            "required": ["file_path"]
        }
    }
}

# ============================================================================
# GET_ADVICE TOOL (Expert Knowledge Base)
# ============================================================================
GET_ADVICE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_advice",
        "description": (
            "Unlocks specialized expert knowledge required for complex tasks. "
            "Even highly capable agents rely on these frameworks to align with project-specific architectural standards and best practices. "
            "Calling this tool is a strategic move: it equips you with the precise context and hidden requirements needed to solve the problem correctly on the first try. "
            "Treat this as accessing the project's 'Brain'—essential for any task involving architecture, debugging, or critical logic."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "advice_ids": {
                    "type": "string",
                    "description": (
                        "The ID of the specialized knowledge module to load. "
                        "Refer to the Catalog to select the methodology matching your current challenge."
                    )
                }
            },
            "required": ["advice_ids"]
        }
    }
}

# ============================================================================
# RUN PROJECT TESTS TOOL
# ============================================================================

RUN_PROJECT_TESTS_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_project_tests",
        "description": (
            "Run unittest tests on staged code changes (VirtualFileSystem) for analysis. "
            "Tests run in isolated environment, NOT on real files. "
            "Use to verify code changes before committing. "
            "Can run entire test file or specific test class/method. "
            "⚠️ LIMIT: Maximum 5 calls per session. Use strategically! "
            "⚠️ TIMEOUT: 30 seconds max per call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": (
                        "Path to test file relative to project root. "
                        "Example: 'tests/test_auth.py' or 'tests/unit/test_user.py'"
                    )
                },
                "chunk_name": {
                    "type": "string",
                    "description": (
                        "Optional. Run only specific test class or method. "
                        "Examples: 'TestUserAuth' (class), 'TestUserAuth.test_login' (method). "
                        "If omitted, runs all tests in the file."
                    )
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": (
                        "Maximum execution time in seconds. Default: 30. Max: 60. "
                        "Use shorter timeout for quick sanity checks."
                    ),
                    "default": 30,
                    "maximum": 60
                }
            },
            "required": ["test_path"]
        }
    }
}


# ============================================================================
# DEPENDENCY MANAGEMENT TOOLS
# ============================================================================

LIST_INSTALLED_PACKAGES_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_installed_packages",
        "description": (
            "List all Python packages installed in the project environment. "
            "Use BEFORE generating code to check what libraries are available. "
            "Returns package names and versions. "
            "Helps avoid import errors by knowing what's already installed."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

INSTALL_DEPENDENCY_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "install_dependency",
        "description": (
            "Install a Python package into the project environment. "
            "Use when validation fails due to missing imports. "
            "Automatically maps import names to pip packages: "
            "docx→python-docx, cv2→opencv-python, PIL→Pillow, etc. "
            "⚠️ Use only for legitimate dependencies needed by the code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "import_name": {
                    "type": "string",
                    "description": (
                        "The import name that failed (e.g., 'docx', 'cv2', 'PIL'). "
                        "Will be automatically converted to pip package name."
                    )
                },
                "version": {
                    "type": "string",
                    "description": "Specific version to install (optional). Example: '2.0.1'"
                }
            },
            "required": ["import_name"]
        }
    }
}

SEARCH_PYPI_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_pypi",
        "description": (
            "Search for a package on PyPI to find the correct package name. "
            "Use when you're unsure of the exact pip package name. "
            "Returns package info including install command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Package name to search for on PyPI"
                }
            },
            "required": ["query"]
        }
    }
}

# ============================================================================
# FETCH WEBPAGE TOOL (raw HTML)
# ============================================================================
FETCH_WEBPAGE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "fetch_webpage",
        "description": (
            "Fetch the raw HTML content of a webpage. Returns the full HTML wrapped in CDATA. "
            "Use when you need the complete source code of a page for detailed parsing or analysis. "
            "⚠️ LIMIT: Maximum 3 calls per session. For structured analysis, consider analyze_webpage instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL of the webpage (e.g., 'https://example.com/page')."
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum number of characters to return. Default: 100000, Max: 500000.",
                    "default": 100000,
                    "maximum": 500000
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds. Default: 10, Max: 30.",
                    "default": 10,
                    "maximum": 30
                }
            },
            "required": ["url"]
        }
    }
}

# ============================================================================
# WEBPAGE ANALYSIS TOOL
# ============================================================================
ANALYZE_WEBPAGE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "analyze_webpage",
        "description": (
            "Perform structural analysis of a webpage: extract title, meta tags, links, forms, media (images, video, audio), "
            "and detect technologies (if possible). Returns structured XML without the full HTML. "
            "Use when you need to understand the content and structure of a page without loading the entire HTML."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL of the webpage to analyze."
                },
                "extract_links": {
                    "type": "boolean",
                    "description": "Extract hyperlinks from the page. Default: true.",
                    "default": True
                },
                "extract_metadata": {
                    "type": "boolean",
                    "description": "Extract title and meta tags. Default: true.",
                    "default": True
                },
                "extract_forms": {
                    "type": "boolean",
                    "description": "Extract forms and their inputs. Default: true.",
                    "default": True
                },
                "extract_media": {
                    "type": "boolean",
                    "description": "Extract image, video, and audio sources. Default: true.",
                    "default": True
                },
                "extract_technologies": {
                    "type": "boolean",
                    "description": "Attempt to detect technologies (e.g., frameworks, CMS). Default: false (experimental).",
                    "default": False
                },
                "max_links": {
                    "type": "integer",
                    "description": "Maximum number of links to return. Default: 100, Max: 500.",
                    "default": 100,
                    "maximum": 500
                }
            },
            "required": ["url"]
        }
    }
}

# ============================================================================
# SECURITY CHECK TOOL
# ============================================================================
CHECK_SECURITY_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_security",
        "description": (
            "Evaluate basic security posture of a website: HTTPS enforcement, security headers (HSTS, CSP, X-Frame-Options, etc.), "
            "cookie security flags, and SSL/TLS certificate validity. Returns a report with recommendations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the website to check (e.g., 'https://example.com')."
                },
                "check_certificate": {
                    "type": "boolean",
                    "description": "Check SSL/TLS certificate details. Default: true.",
                    "default": True
                },
                "follow_redirects": {
                    "type": "boolean",
                    "description": "Follow redirects to final URL. Default: true.",
                    "default": True
                }
            },
            "required": ["url"]
        }
    }
}

# ============================================================================
# EXTRACT MEDIA TOOL
# ============================================================================
EXTRACT_MEDIA_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "extract_media",
        "description": (
            "Extract URLs of media resources (images, videos, audio) from a webpage. "
            "Returns the URLs along with their type and optionally file size (if available from headers). "
            "Does NOT download the actual media – only provides metadata. "
            "Use when you need to know what media exists on a page before generating download code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the webpage to scan for media."
                },
                "media_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["image", "video", "audio"]},
                    "description": "List of media types to extract. Default: all.",
                    "default": ["image", "video", "audio"]
                },
                "max_urls": {
                    "type": "integer",
                    "description": "Maximum number of URLs to return per type. Default: 50, Max: 200.",
                    "default": 50,
                    "maximum": 200
                },
                "check_size": {
                    "type": "boolean",
                    "description": "Attempt to fetch Content-Length for each resource (performs a HEAD request). May increase execution time. Default: false.",
                    "default": False
                }
            },
            "required": ["url"]
        }
    }
}
# ============================================================================
# COMBINED TOOLS LIST
# ============================================================================

ORCHESTRATOR_TOOLS: List[Dict[str, Any]] = [
    READ_CODE_CHUNK_TOOL,
    READ_FILE_TOOL,
    SEARCH_CODE_TOOL,
    GREP_SEARCH_TOOL,
    FILE_RELATIONS_TOOL,
    WEB_SEARCH_TOOL,
    GET_ADVICE_TOOL,
    RUN_PROJECT_TESTS_TOOL,
    # NEW: Dependency management
    LIST_INSTALLED_PACKAGES_TOOL,
    INSTALL_DEPENDENCY_TOOL,
    SEARCH_PYPI_TOOL,
    FETCH_WEBPAGE_TOOL,      # <-- добавить
    ANALYZE_WEBPAGE_TOOL,    # <-- добавить
    CHECK_SECURITY_TOOL,     # <-- добавить
    EXTRACT_MEDIA_TOOL,      # <-- добавить
]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_tool_by_name(name: str) -> Dict[str, Any]:
    """Get tool definition by name."""
    tools_map = {
        "read_code_chunk": READ_CODE_CHUNK_TOOL,
        "read_file": READ_FILE_TOOL,
        "search_code": SEARCH_CODE_TOOL,
        "grep_search": GREP_SEARCH_TOOL,
        "show_file_relations": FILE_RELATIONS_TOOL,
        "web_search": WEB_SEARCH_TOOL,
        "general_web_search": GENERAL_WEB_SEARCH_TOOL,
        "get_advice": GET_ADVICE_TOOL,
        "run_project_tests": RUN_PROJECT_TESTS_TOOL,
        "list_installed_packages": LIST_INSTALLED_PACKAGES_TOOL,
        "install_dependency": INSTALL_DEPENDENCY_TOOL,
        "search_pypi": SEARCH_PYPI_TOOL,
        "fetch_webpage": FETCH_WEBPAGE_TOOL,        # <-- добавить
        "analyze_webpage": ANALYZE_WEBPAGE_TOOL,    # <-- добавить
        "check_security": CHECK_SECURITY_TOOL,      # <-- добавить
        "extract_media": EXTRACT_MEDIA_TOOL,        # <-- добавить
    }
    if name not in tools_map:
        raise ValueError(f"Unknown tool: {name}")
    return tools_map[name]



def get_tool_names() -> List[str]:
    """Get list of all available tool names."""
    return [
        "read_code_chunk",
        "read_file",
        "search_code",
        "grep_search",  # ДОБАВЛЕНО
        "show_file_relations",
        "web_search",
        "general_web_search",
        "get_advice",
        "run_project_tests",
        "list_installed_packages",
        "install_dependency",
        "search_pypi",
        "fetch_webpage",        # <-- добавить
        "analyze_webpage",       # <-- добавить
        "check_security",        # <-- добавить
        "extract_media",         # <-- добавить
    ]
