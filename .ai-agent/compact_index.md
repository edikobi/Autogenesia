# Project Map (92 files, 746,850 tokens)
# Root: C:\Users\Admin\AI_Assistant_Pro

## (root)/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `check_models.py` (156 tok): Utility module with minimal significant logic.
- `debug_cert.py` (451 tok): Utility module with minimal significant logic.
- `main.py` (79156 tok): Centralized application entry point for multi-mode AI orchestration with custom exception handling, logging, and state management.
- `mainsertif.py` (2232 tok): Unified diagnostic script for testing connectivity and performance of multiple AI model APIs (OpenAI-compatible, GigaChat, OpenRouter, DeepSeek) with automated certificate management and error logging.
- `maintestchunk .py` (4249 tok): Full system test for code chunking and project analysis that integrates scanning, tokenization, and rich visualization tools for detailed reporting.
- `temp_test.py` (12 tok): Unit test script for verifying basic console output functionality, integrates with pytest for automated testing.
- `test_staging_bug.py` (6008 tok): Test suite for staging bug detection, validating code state transitions and mutation analysis through mocked AI validation and virtual file system integration.

## app/
- `__init__.py` (0 tok): Utility module with minimal significant logic.

## app\advice/
- `__init__.py` (213 tok): Utility module with minimal significant logic.
- `advice_loader.py` (4216 tok): Advice data management system for loading, caching, and querying structured advice from JSON files, providing formatted catalogs for prompt generation and on-demand content retrieval.

## app\agents/
- `__init__.py` (166 tok): Utility module with minimal significant logic.
- `agent_pipeline.py` (60102 tok): Central coordinator for Agent Mode managing code generation lifecycle with multi-level validation (pyright/ruff), user confirmation workflows, and integration with VirtualFileSystem, BackupManager, and AIValidator services.
- `code_generator.py` (13845 tok): Code generation and validation service for ASK mode, integrating regex-based autofixing, truncation detection, and structured output formatting for frontend display.
- `feedback_handler.py` (12936 tok): Centralized feedback management system for code validation and error handling, integrating with orchestrator workflows to route, prioritize, and serialize feedback from validators, tests, staging, and user actions.
- `feedback_loop.py` (5560 tok): Manages the state and history of iterative AI output refinement through validator, user, and test feedback loops, integrating with retry, revision, and audit logging systems.
- `feedback_prompt_loader.py` (3728 tok): Singleton-based feedback prompt loader with thread-safe lazy initialization, providing session-scoped access to a dynamically generated feedback handling protocol for code correction guidance.
- `orchestrator.py` (16462 tok): Orchestrates LLM-driven code analysis and general chat interactions with tool execution, usage limits, and error recovery, integrating with project indices, file processing, and multiple LLM backends.
- `pre_filter.py` (12868 tok): Pre-filter agent for code analysis pipelines, supporting normal and advanced modes with tool execution, chunk selection, and structured result generation.
- `router.py` (6562 tok): AI-driven task routing system that classifies coding queries by complexity and selects appropriate orchestrator models, integrating with Gemini 2.0 Flash and configuration-based model mapping for dynamic workflow selection.
- `tester.py` (3509 tok): TesterAgent executes AI-generated code in an isolated environment with assertion-based tests and LLM-driven tool calls, producing structured markdown reports via TesterReport for orchestrator integration and user-facing error reporting.
- `validator.py` (6340 tok): Validates AI-generated code changes using a tiered LLM-based validation system with fallback mechanisms, integrating with logging, feedback handlers, and API token counters for robust error recovery and structured result serialization.

## app\builders/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `semantic_index_builder.py` (21602 tok): Builds and manages semantic code indexes by parsing file structures, tracking dependencies, and integrating with LLMs for content analysis.

## app\core/
- `__init__.py` (0 tok): Utility module with minimal significant logic.

## app\history/
- `__init__.py` (193 tok): Utility module with minimal significant logic.
- `compressor.py` (3057 tok): Compresses conversation history using LLM-based summarization and regex-based pruning to manage token budgets, integrating with token counters and model-specific thresholds for efficient context management.
- `context_manager.py` (5627 tok): Manages LLM context compression with proactive/reactive strategies, caching compressors per model and detecting overflow errors across providers.
- `manager.py` (5617 tok): History management service for user dialog operations, integrating storage and compression modules for data persistence and optimization.
- `orchestrator_trace.py` (1525 tok): Centralized SQLite-based trace management for orchestrator workflow steps, enabling detailed bilingual logging and session-integrated serialization.
- `storage.py` (6146 tok): SQLite ORM layer for conversation threads, messages, and agent file changes, providing structured storage and retrieval with JSON metadata support.

## app\llm/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `api_client.py` (7995 tok): Universal LLM client with multi-provider routing, retry logic, rate limiting, and standardized request/response handling, integrating with asyncio, OpenAI SDK, and centralized configuration for error-resilient API interactions.
- `prompt_templates.py` (90875 tok): Prompt template factory for multi-model cognitive routing, generating model-specific adaptive blocks for ASK and new project modes with fallback handling.

## app\services/
- `__init__.py` (40 tok): Utility module with minimal significant logic.
- `ast_grep_applier.py` (2251 tok): AST-based code patching system using ast-grep for multi-language search-and-replace, integrating with pipeline feedback and error classification.
- `backup_manager.py` (4391 tok): Manages file backup sessions with metadata serialization, integrating filesystem operations and JSON storage for session persistence and cleanup.
- `change_validator.py` (21505 tok): Multi-level code change validation system using AST parsing and language-specific checks, integrated with VirtualFileSystem for staged file awareness across Python, JS/TS, Go, and Java.
- `code_commenter.py` (1865 tok): Comments out code elements (functions, methods, classes) in multi-language files using AST or Tree-sitter parsing with fallback support.
- `file_io_tools.py` (2827 tok): File I/O toolkit with token-aware chunking and XML-wrapping for AI document processing, integrating read/write operations with automatic type detection and syntax validation.
- `file_modifier.py` (65482 tok): FileModifier applies structured code modifications (insert, replace, patch) to Python and multi-language files using Tree-sitter parsing for structural awareness and automatic indentation normalization, integrated with a VirtualFileSystem for staging and syntax validation.
- `go_adapter.py` (8170 tok): Go language adapter for linting, formatting, and compilation with full dependency management, integrating with VFS for staged file handling and external Go tools.
- `go_chunker.py` (2757 tok): Hierarchical Go source code parser and chunker for structured code analysis, with contextual snippet assembly for AI processing and downstream integration.
- `index_manager.py` (6500 tok): Core index management system for AI-powered code analysis projects, providing full/incremental indexing with semantic compression and statistical monitoring.
- `index_reader.py` (9575 tok): Semantic index query engine for Python codebases with configurable detail levels, fuzzy search, and CLI integration for structured metadata retrieval.
- `index_updater.py` (2134 tok): Updates a project's semantic index by detecting changed files, chunking them, and generating semantic entries via Qwen, integrating with ProjectScanner, FileReaderTool, and a provider-aware LLM routing system.
- `java_adapter.py` (6413 tok): Java code analysis and compilation adapter integrating Checkstyle, google-java-format, and Maven for multi-file projects with virtual file system prioritization.
- `js_ts_adapter.py` (6686 tok): JavaScript/TypeScript code quality and compilation pipeline integrating ESLint, Prettier, and tsc with a Virtual File System for staged changes and dependency-aware processing.
- `json_chunker.py` (2107 tok): Intelligent JSON file chunking with token-based splitting strategies, integrates with TokenCounter for estimation and outputs structured JSONChunk objects with metadata.
- `language_adapter.py` (3647 tok): Language adapter framework for multi-language code analysis and formatting, integrating with system tools and VFS for robust linting, compilation, and error reporting workflows.
- `project_map_builder.py` (6473 tok): Builds and maintains a project map with AI-generated file descriptions, supporting full builds and incremental syncs, and integrates with semantic indexing, file type detection, and LLM-based description generation via an async API client.
- `project_scanner.py` (3416 tok): Recursive directory scanner that builds a JSON project map with file hashes and token counts, supporting incremental sync and language-specific chunking.
- `python_chunker.py` (5328 tok): AST-based Python source code analyzer that generates both flat chunk lists and hierarchical tree structures for modular code segmentation, supporting contextual assembly and tree navigation for AI processing.
- `runtime_tester.py` (38240 tok): Unified runtime tester that detects application type via AST-based framework analysis and applies appropriate testing strategies with fallback validation, integrating FrameworkDetector, TimeoutCalculator, and RuntimeTestSummary for comprehensive project analysis and test result aggregation.
- `sql_chunker.py` (2257 tok): SQL script parser and chunker for token-based processing, with AI prompt generation and table-level grouping for database analysis.
- `syntax_checker.py` (16348 tok): Multi-language syntax validation and auto-fix service using AST for Python and Tree-sitter for Java/JS/TS/Go, with a try-revert loop over external formatters and internal indentation strategies.
- `syntax_fixer_agent.py` (6697 tok): AI-powered syntax correction agent that uses LLM calls with retry logic and Tree-sitter context extraction to fix code syntax errors across multiple programming languages.
- `tree_sitter_parser.py` (11346 tok): Multi-language fault-tolerant code parser using Tree-sitter to extract structured code elements (classes, functions, imports) with error recovery, integrated with language-specific parsers for JavaScript, TypeScript, Go, Java, and Python.
- `virtual_fs.py` (14343 tok): Virtual file system manager for staging and committing file changes with dependency analysis, integrating with backup systems and asynchronous file operations.

## app\tools/
- `__init__.py` (209 tok): Utility module with minimal significant logic.
- `dependency_manager.py` (23117 tok): Multi-language dependency management system for AI agents that auto-detects project languages, creates/manages virtual environments, and installs missing packages via subprocess calls with VFS-aware configuration.
- `file_relations.py` (5604 tok): Multi-language file dependency analyzer that extracts imports, reverse dependencies, test files, and sibling relationships using a virtual filesystem for accurate project-wide navigation.
- `general_web_search.py` (3414 tok): Asynchronous web search tool using DuckDuckGo with structured result parsing, ranking, and token-limited result selection, providing both async and synchronous interfaces for integration.
- `grep_search.py` (4576 tok): Grep-like search tool for codebases with regex support, multiline matching, and context-aware results, integrating with virtual file systems and file type detection for efficient project-wide text searches.
- `list_files.py` (1415 tok): Utility for listing files with recursive scanning and VFS integration, returning XML metadata while filtering ignored patterns via fnmatch.
- `read_file.py` (2440 tok): File reading and processing utility with XML-wrapped output, supporting token limits, line numbering, and chunk extraction via language-aware parsing.
- `read_line_context.py` (1214 tok): Utility for reading file lines with surrounding context, supporting XML-safe output, line numbering, and virtual file system integration.
- `search_code.py` (3196 tok): Semantic code search tool for project indexing with unified regular/compressed index support, returning structured XML results with metadata and error handling.
- `tester_tool_definitions.py` (1841 tok): Utility module with minimal significant logic.
- `tester_tool_executor.py` (4314 tok): Executes tester-specific tools for code analysis and environment checks with read-only VFS access, integrating with language-specific adapters for Java, Go, and JavaScript/TypeScript while blocking dependency installation.
- `tool_definitions.py` (6614 tok): Tool registry providing name-based lookup and enumeration of code analysis, file operations, web search, and dependency management tools, integrated with RUFF linting and web scraping utilities.
- `tool_executor.py` (3745 tok): ToolExecutor executes project tools (file I/O, code search, web queries, dependency management) with virtual file system support, integrating external APIs and custom tool registration.
- `web_search.py` (4291 tok): Web search module using DuckDuckGo for asynchronous querying, parallel page fetching, and semantic relevance ranking, returning structured results with token management.
- `web_tools.py` (3569 tok): Web scraping and analysis toolkit providing URL validation, HTML fetching, webpage structure analysis, security assessment, and media extraction with XML-formatted outputs.

## app\utils/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `compact_index.py` (1637 tok): Utility module for generating compact project overviews and chunk metadata from semantic indexes, supporting navigation and pre-filtering in the Orchestrator system.
- `file_parser.py` (1543 tok): Unified file parsing and conversion to text across multiple formats (txt, pdf, docx, xlsx, csv) with token limit handling, producing a standardized ParsedFile output structure for downstream processing.
- `file_types.py` (562 tok): File type classifier using extension-to-category mapping with pathlib integration for extension extraction, supporting chunking and text-based file queries.
- `pipeline_trace_logger.py` (4070 tok): Logs real-time pipeline execution traces with detailed iteration-level tracking, integrating JSON serialization, file system persistence, and automatic error reporting.
- `tester_translator.py` (670 tok): Asynchronous translation of tester reports from English to Russian using Gemini Flash Lite, with fallback to original text on failure, integrated with LLM API and configuration-driven model selection.
- `token_counter.py` (191 tok): Token counting utility for text strings using tiktoken encodings, supporting batch processing and defaulting to cl100k_base encoding.
- `translator.py` (3425 tok): Multilingual translation service for Russian text processing with caching, async support, and context-aware translation for technical content, code, and AI agent outputs.
- `validation_logger.py` (2255 tok): Session-specific logging utility for validation pipelines, integrating with Python's logging system and file management for structured error tracking and traceback capture.
- `xml_parser.py` (4039 tok): XML parsing utility for extracting and validating AI-generated code blocks from XML/CDATA/Markdown responses, integrating syntax validation and language detection for structured output.
- `xml_wrapper.py` (7448 tok): XML serialization framework for code/text preservation with CDATA encapsulation, supporting hierarchical metadata embedding and context-aware chunk wrapping for AI analysis workflows.

## config/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `intermediate_agent_models.py` (3212 tok): Model selection logic for intermediate, orchestrator, and generator agents with provider priority fallback and reasoning effort handling, integrated with config.provider_models.
- `provider_models.py` (3494 tok): Provides role-based model filtering for providers, returning tuples of model metadata while enforcing role classification with a default fallback to orchestrator for unassigned models.
- `settings.py` (11478 tok): Central configuration module for managing AI provider settings, API keys, and routing logic across OpenRouter, DeepSeek, and Google Gemini, with environment variable integration and orchestrator workflow support.

## examples\test_index/
- `nested_example.py` (276 tok): A nested class hierarchy for organizational encapsulation and database connection management, integrating SQL query execution with automatic connection handling.
- `simple_module.py` (570 tok): A Python module providing user and admin entity classes with authentication and persistence, alongside utility functions for statistical computation and asynchronous data fetching.
- `utils.py` (215 tok): Utility module providing a thread-safe singleton for configuration, string formatting for names, and email validation via regex.
