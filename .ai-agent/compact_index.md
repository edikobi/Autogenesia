# Project Map (104 files, 688,906 tokens)
# Root: C:\Users\Admin\AI_Assistant_Pro

## (root)/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `check_models.py` (156 tok): Utility module with minimal significant logic.
- `debug_cert.py` (451 tok): Utility module with minimal significant logic.
- `main.py` (61829 tok): CLI application framework with custom navigation exceptions and structured logging, integrating state management and user input handling for menu-driven workflows.
- `mainsertif.py` (2232 tok): Unified diagnostic script for testing connectivity and performance of multiple AI model APIs (OpenAI-compatible, GigaChat, OpenRouter, DeepSeek) with automated certificate management and error logging.
- `maintestchunk .py` (4249 tok): Full system test for code chunking and project analysis that integrates scanning, tokenization, and rich visualization tools for detailed reporting.
- `temp_test.py` (12 tok): Unit test script for verifying basic console output functionality, integrates with pytest for automated testing.
- `test.py` (2468 tok): Asynchronous test suite for OpenRouter AI model availability and performance, integrating httpx for HTTP requests and asyncio for concurrency and delay management.
- `test_all_chunkers.py` (1372 tok): Unit test suite for validating language-specific code chunkers (Python, Go, SQL) using SmartChunker classes and Rich library for structured console output.
- `test_cache_logic.py` (375 tok): Unit test suite validating response caching behavior for AI tool interactions, specifically verifying cache_control integration for Claude models processing resource-intensive operations.
- `test_caching_real.py` (701 tok): Unit test for caching behavior in LLM tool calls, integrating with orchestration and tool-calling frameworks to validate cache control interception and cleanup.
- `test_deepseek_direct.py` (165 tok): Unit test for DeepSeek API integration, validating configuration and response parsing for the 'pre_filter' role.
- `test_index_builder.py` (5828 tok): Unit test suite for validating the project index builder's core functions, including directory selection, project creation, structure verification, and JSON metadata generation.
- `test_prefilter_minimal.py` (429 tok): Unit test for a minimal LLM pre-filter workflow, validating model configuration and JSON response generation via call_llm with performance timing.

## app/
- `__init__.py` (0 tok): Utility module with minimal significant logic.

## app\advice/
- `__init__.py` (213 tok): Utility module with minimal significant logic.
- `advice_loader.py` (4216 tok): Advice data management system for loading, caching, and querying structured advice from JSON files, providing formatted catalogs for prompt generation and on-demand content retrieval.

## app\agents/
- `__init__.py` (166 tok): Utility module with minimal significant logic.
- `agent_pipeline.py` (37526 tok): Multi-stage AI code generation pipeline with orchestrator-driven feedback loops, integrating virtual filesystem staging and multi-level validation (syntax, AI, tests).
- `code_generator.py` (13760 tok): Code generation and validation engine for ASK mode, integrating structured code blocks, automated error correction, and retry logic with frontend display support.
- `feedback_handler.py` (9584 tok): Feedback orchestration system for processing and prioritizing diverse feedback types (user, validator, test, staging) with structured metadata and integration into decision-making workflows.
- `feedback_loop.py` (4905 tok): State management and orchestration for iterative AI validation cycles, integrating validation attempts, feedback handling, orchestrator revisions, and test run tracking.
- `feedback_prompt_loader.py` (3131 tok): Singleton-managed lazy loader for structured feedback handling prompts, enabling thread-safe retrieval and session reset in code generation systems.
- `orchestrator.py` (16013 tok): Core orchestration engine for managing multi-step LLM workflows with tool execution, file processing, and usage tracking, integrating with LLM models, search tools, and token-limited batch operations.
- `pre_filter.py` (7312 tok): Pre-filtering module for selecting and prioritizing relevant code chunks from a semantic index based on a user query and token budget, integrating with LLM ranking and file extraction utilities.
- `router.py` (6524 tok): AI model router for classifying coding tasks by complexity and risk, then routing them to appropriate orchestrator models with fallback and logging.
- `validator.py` (3581 tok): AI validation system for code generation that processes validation requests through AI models with structured feedback, integrating with token counting, logging, and LLM orchestration.

## app\builders/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `semantic_index_builder.py` (19793 tok): Semantic indexing pipeline for source code analysis, integrating LLM-based metadata extraction with structured data models for dependency tracking and performance monitoring.

## app\core/
- `__init__.py` (0 tok): Utility module with minimal significant logic.

## app\history/
- `__init__.py` (193 tok): Utility module with minimal significant logic.
- `compressor.py` (2757 tok): LLM-based message history compression module with dynamic token thresholding, code block preservation, and irrelevant context pruning for conversational AI systems.
- `context_manager.py` (5589 tok): Context compression manager for LLM sessions, handling proactive and reactive compression strategies with model-specific caching and error detection.
- `manager.py` (5356 tok): History management service for dialog systems, handling storage, compression, display, and tracing via integration with HistoryStorage, OrchestratorTraceStorage, and compression modules.
- `orchestrator_trace.py` (1525 tok): Centralized SQLite-based trace management for orchestrator workflow steps, enabling detailed bilingual logging and session-integrated serialization.
- `storage.py` (5674 tok): SQLite-based storage manager for conversation threads, messages, and agent file changes, integrating JSON metadata serialization and persistent data handling.

## app\llm/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `api_client.py` (7439 tok): Universal LLM client with multi-provider support, automatic routing, and standardized request/response handling for external AI service integration.
- `prompt_templates.py` (64131 tok): Centralized prompt template generator for multi-agent AI workflows, dynamically adapting instructions based on model cognitive type classification and operational mode (ASK/NEW PROJECT/AGENT).

## app\services/
- `__init__.py` (40 tok): Utility module with minimal significant logic.
- `ai_client.py` (1355 tok): Unified LLM API client with OpenAI-compatible interface for configurable model selection and structured response handling across multiple providers.
- `backup_manager.py` (4391 tok): Manages file backup sessions with metadata serialization, integrating filesystem operations and JSON storage for session persistence and cleanup.
- `change_validator.py` (13441 tok): Multi-level code change validator integrated with VirtualFileSystem, supporting syntax, import, type, integration, runtime, and test validation with configurable rules and detailed issue reporting.
- `file_io_tools.py` (2827 tok): File I/O toolkit with token-aware chunking and XML-wrapping for AI document processing, integrating read/write operations with automatic type detection and syntax validation.
- `file_modifier.py` (15103 tok): File modification engine for structured code changes using Tree-sitter parsing, supporting insert/replace/append operations with instruction-based targeting and result tracking.
- `go_chunker.py` (2757 tok): Go source code parser for hierarchical semantic segmentation, outputs structured chunks and aggregated context for indexing or AI model consumption.
- `index_manager.py` (6500 tok): Core index management system for AI-powered code analysis projects, providing full/incremental indexing with semantic compression and statistical monitoring.
- `index_reader.py` (9575 tok): Semantic index query engine for Python codebases with configurable detail levels, fuzzy search, and CLI integration for structured metadata retrieval.
- `index_updater.py` (2492 tok): Semantic index updater for codebases using Qwen via OpenRouter API, processes changed files into XML chunks and updates a JSON-based semantic index with structured analysis.
- `json_chunker.py` (2107 tok): Token-aware JSON file chunker for LLM context management, integrating adaptive splitting strategies with distributed processing metadata.
- `project_map_builder.py` (6437 tok): Project map builder for structured codebase analysis with AI-generated file descriptions, integrating semantic indexing and incremental updates for efficient change tracking.
- `project_scanner.py` (3416 tok): Recursive directory scanner that builds a JSON project map with file hashes and token counts, supporting incremental sync and language-specific chunking.
- `python_chunker.py` (5328 tok): AST-based Python source code analyzer that generates both flat chunk lists and hierarchical tree structures for modular code segmentation, supporting contextual assembly and tree navigation for AI processing.
- `runtime_tester.py` (25018 tok): Runtime testing framework with dynamic timeout calculation and framework-specific execution strategies, integrating project analysis, framework detection, and result summarization for multi-application testing.
- `sql_chunker.py` (2257 tok): SQL statement chunker with regex-based classification for DDL/DML operations, integrates token counting and line tracking for structured file segmentation.
- `syntax_checker.py` (5104 tok): Syntax validation and auto-correction engine for Python and JSON code, integrating with autopep8 and Black for formatting.
- `tree_sitter_parser.py` (4331 tok): Fault-tolerant Python AST parser using Tree-sitter that extracts classes, functions, and imports into structured data classes for code analysis and transformation.
- `virtual_fs.py` (13685 tok): Virtual file system manager for staging, analyzing, and committing file changes with dependency tracking and integrated backup/history for rollback support.

## app\tools/
- `__init__.py` (209 tok): Utility module with minimal significant logic.
- `dependency_manager.py` (6988 tok): Dependency management system for Python AI agents, handling package installation, import mapping, and PyPI integration with virtual environment support and XML/JSON reporting.
- `file_relations.py` (2925 tok): File relationship analyzer for Python projects that extracts imports, importers, tests, and sibling files, supporting virtual file systems and outputting structured XML.
- `general_web_search.py` (3414 tok): Asynchronous web search tool using DuckDuckGo with structured result parsing, ranking, and token-limited result selection, providing both async and synchronous interfaces for integration.
- `grep_search.py` (2525 tok): Grep-like search tool for project directories and virtual file systems, returning XML-formatted results with regex support, file filtering, and context-aware match formatting.
- `read_file.py` (1701 tok): File reading and analysis utility with XML-wrapped output, integrating token counting, file type detection, and security checks for structured content delivery.
- `run_project_tests.py` (9124 tok): Isolated unit test runner with virtual filesystem integration, supporting chunked execution and XML result generation via subprocess.
- `search_code.py` (3196 tok): Semantic code search tool for project indexing with unified regular/compressed index support, returning structured XML results with metadata and error handling.
- `tool_definitions.py` (4087 tok): Centralized registry for tool discovery and retrieval, enabling dynamic integration of code navigation, file operations, dependency management, and external search capabilities.
- `tool_executor.py` (3335 tok): Tool execution orchestrator for file operations, code search, web queries, and package management, integrating with semantic indexing and virtual file systems.
- `web_search.py` (4291 tok): Web search module using DuckDuckGo for asynchronous querying, parallel page fetching, and semantic relevance ranking, returning structured results with token management.

## app\utils/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `compact_index.py` (1637 tok): Utility module for generating compact project overviews and chunk metadata from semantic indexes, supporting navigation and pre-filtering in the Orchestrator system.
- `file_parser.py` (1543 tok): Unified file parsing and conversion to text across multiple formats (txt, pdf, docx, xlsx, csv) with token limit handling, producing a standardized ParsedFile output structure for downstream processing.
- `file_types.py` (517 tok): File type classifier using extension mapping for code, text, data, SQL, and config files, with chunking and encoding support methods for system-wide file handling and processing.
- `pipeline_trace_logger.py` (3200 tok): Real-time pipeline execution logger that serializes tool call, iteration, and request lifecycle traces to JSON files with integrated logging and automatic cleanup.
- `token_counter.py` (191 tok): Token counting utility for text strings using tiktoken encodings, supporting batch processing and defaulting to cl100k_base encoding.
- `translator.py` (3334 tok): Russian translation service for code and technical text using Gemini 2.0 Flash, with caching, async/sync wrappers, and pre-translation filters for Russian content and code blocks.
- `validation_logger.py` (2255 tok): Session-specific logging utility for validation pipelines, integrating with Python's logging system and file management for structured error tracking and traceback capture.
- `xml_parser.py` (3770 tok): XML parsing and validation system for extracting and processing AI-generated code blocks from XML responses, integrating syntax validation, language detection, and structured metadata handling.
- `xml_wrapper.py` (7448 tok): XML serialization framework for code/text preservation with CDATA encapsulation, supporting hierarchical metadata embedding and context-aware chunk wrapping for AI analysis workflows.

## config/
- `__init__.py` (0 tok): Utility module with minimal significant logic.
- `settings.py` (6598 tok): Central configuration management for AI agent settings and provider routing, with integrated console logging for configuration summary and API key status.

## examples\test_index/
- `nested_example.py` (276 tok): A nested class hierarchy for organizational encapsulation and database connection management, integrating SQL query execution with automatic connection handling.
- `simple_module.py` (570 tok): A Python module providing user and admin entity classes with authentication and persistence, alongside utility functions for statistical computation and asynchronous data fetching.
- `utils.py` (215 tok): Utility module providing a thread-safe singleton for configuration, string formatting for names, and email validation via regex.

## scripts/
- `test_advice_system — копия.py` (5612 tok): Unit test suite for validating the file structure, data integrity, and consistency of an advice system module, integrating with JSON data files and a custom test runner for execution and reporting.
- `test_advice_system.py` (6150 tok): Unit test suite for an advice system, validating JSON file integrity, lazy loading, content retrieval, catalog generation, mode filtering, singleton behavior, and LLM prompt integration.
- `test_agent_foundation.py` (17710 tok): Unit test suite for a Virtual File System (VFS) component, integrating test fixtures, runners, and statistics for staging and reading operations.
- `test_agent_stage2.py` (17273 tok): Unit test suite for VirtualFileSystem operations, integrating command-line configuration, isolated test fixtures, and result reporting.
- `test_agent_stage3.py` (8181 tok): Unit test suite for validating code generation agent components, including file modification, code block parsing, and import verification.
- `test_agents.py` (16428 tok): Unit testing framework for agent-based systems with color-coded console output and structured result reporting.
- `test_context_compression.py` (6064 tok): Unit test suite for proactive and non-proactive LLM context compression, integrating with token counting, logging, and real API calls to validate compression behavior and message integrity.
- `test_deepseek_wrapper.py` (1417 tok): Unit test for XML-to-AI processing via DeepSeek/OpenRouter, integrating XMLWrapper and AIService with logging and file output.
- `test_file_modifier_indentation.py` (22879 tok): Unit test suite for validating indentation normalization in a FileModifier class, integrating with custom logging, test fixtures, and command-line argument parsing.
- `test_general_chat.py` (1872 tok): Unit test suite for a general chat orchestrator, validating dynamic model selection, operational modes, file attachments, and tool execution through integration with configuration, parsing, and orchestration modules.
- `test_general_chat_with_history.py` (8280 tok): Unit test suite for a chat system's history management and interactive viewing features, integrating HistoryManager and ChatViewer for data retrieval, pagination, search, export, and display.
- `test_generator_models.py` (9621 tok): Unit test suite for evaluating code generation models across ASK and AGENT modes, integrating structured test cases, performance logging, and result reporting.
- `test_history_manager.py` (5294 tok): Unit test suite for HistoryManager functionality, integrating with logging, SQLite databases, TokenCounter, and LLM API clients for comprehensive validation.
- `test_indent_normalization.py` (14743 tok): Unit test suite for validating indentation normalization and code structure preservation during Python code modifications, integrating with custom utilities and the unittest framework.
- `test_index_manager.py` (8339 tok): Unit test suite for an index manager, integrating color-coded logging, progress tracking, and dependency validation for system health checks.
- `test_orchestrator_raw.py` (6154 tok): Test orchestration utility for validating and formatting raw LLM responses with structured console output and regex-based section extraction.
- `test_run_project_tests.py` (11022 tok): Unit test suite for validating the test execution framework, including result parsing, VirtualFileSystem integration, and targeted test runs.
- `test_runtime_tester_integration.py` (8636 tok): Integration test suite for validating runtime components across multiple scenarios, executed asynchronously with comprehensive system integration.
- `test_semantic_index.py` (10118 tok): Unit test suite for semantic indexing components, validating import dependencies, API connectivity, and logging utilities with color-coded terminal output.
- `test_validation_pipeline.py` (3945 tok): Unit test suite for a Python validation pipeline, verifying subprocess encoding fixes, import error handling, Cyrillic character support, and mypy integration.
