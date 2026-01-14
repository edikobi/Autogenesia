# Project Map

**Root:** `C:\Users\Admin\AI_Assistant_Pro`
**Files:** 206
**Total tokens:** 994362
**Updated:** 2026-01-12T12:47:18.611774Z

## Files

| Path | Type | Tokens | Description |
|------|------|--------|-------------|
| `__init__.py` | python | 0 | Utility module with minimal significant logic. |
| `app\__init__.py` | python | 0 | Code file (no description available) |
| `app\advice\__init__.py` | python | 213 | Code file (no description available) |
| `app\advice\advice_catalog.json` | json | 2060 | This JSON file serves as a structured catalog of methodological thinking frameworks for the AI Orchestrator's Agent Mode. It organizes advice into three categories (General Methodologies, Existing Project Patterns, New Project Architecture) with specific applicability to different operational modes (ASK or NEW_PROJECT). Each advice entry includes an ID, name, description, and clear "when_to_use" guidance, providing systematic approaches for tasks like bug hunting, feature integration, refactoring, security audits, and architectural design. The catalog is loaded by the `advice_loader.py` module to supply context-aware prompts and structured guidance during AI-driven code generation and analysis workflows. |
| `app\advice\advice_content.json` | json | 14724 | This file is a structured knowledge base containing expert advice for AI-assisted code development, organized into numbered guidelines with detailed frameworks. It provides systematic thinking processes for various development scenarios like bug hunting, feature integration, refactoring, security auditing, and performance optimization. The advice is designed to guide AI agents (particularly the orchestrator and generator) through complex coding tasks by offering step-by-step methodologies, tool usage priorities, and anti-pattern warnings. It serves as a reference library that the advice_loader.py module loads and caches to inject structured guidance into AI prompts during code generation and validation workflows. |
| `app\advice\advice_loader.py` | python | 4216 | Code file (no description available) |
| `app\agents\__init__.py` | python | 166 | Code file (no description available) |
| `app\agents\agent_pipeline.py` | python | 37526 | Code file (no description available) |
| `app\agents\code_generator.py` | python | 13760 | Code file (no description available) |
| `app\agents\feedback_handler.py` | python | 9584 | Code file (no description available) |
| `app\agents\feedback_loop.py` | python | 4905 | Code file (no description available) |
| `app\agents\feedback_prompt_loader.py` | python | 3131 | Code file (no description available) |
| `app\agents\orchestrator.py` | python | 16013 | Code file (no description available) |
| `app\agents\pre_filter.py` | python | 7312 | Code file (no description available) |
| `app\agents\router.py` | python | 6524 | Code file (no description available) |
| `app\agents\validator.py` | python | 3581 | Code file (no description available) |
| `app\builders\__init__.py` | python | 0 | Code file (no description available) |
| `app\builders\semantic_index_builder.py` | python | 19793 | Code file (no description available) |
| `app\core\__init__.py` | python | 0 | Code file (no description available) |
| `app\history\__init__.py` | python | 193 | Code file (no description available) |
| `app\history\compressor.py` | python | 2757 | Code file (no description available) |
| `app\history\context_manager.py` | python | 5589 | Code file (no description available) |
| `app\history\manager.py` | python | 5356 | Code file (no description available) |
| `app\history\orchestrator_trace.py` | python | 1525 | Code file (no description available) |
| `app\history\storage.py` | python | 5674 | Code file (no description available) |
| `app\llm\__init__.py` | python | 0 | Code file (no description available) |
| `app\llm\api_client.py` | python | 7439 | Code file (no description available) |
| `app\llm\prompt_templates.py` | python | 64131 | Code file (no description available) |
| `app\services\__init__.py` | python | 40 | Code file (no description available) |
| `app\services\ai_client.py` | python | 1355 | Code file (no description available) |
| `app\services\backup_manager.py` | python | 4391 | Code file (no description available) |
| `app\services\change_validator.py` | python | 13441 | Code file (no description available) |
| `app\services\file_io_tools.py` | python | 2827 | Code file (no description available) |
| `app\services\file_modifier.py` | python | 15103 | Code file (no description available) |
| `app\services\go_chunker.py` | python | 2757 | Code file (no description available) |
| `app\services\index_manager.py` | python | 6500 | Code file (no description available) |
| `app\services\index_reader.py` | python | 9575 | Code file (no description available) |
| `app\services\index_updater.py` | python | 2492 | Code file (no description available) |
| `app\services\json_chunker.py` | python | 2107 | Code file (no description available) |
| `app\services\project_map_builder.py` | python | 6437 | Code file (no description available) |
| `app\services\project_scanner.py` | python | 3416 | Code file (no description available) |
| `app\services\python_chunker.py` | python | 5328 | Code file (no description available) |
| `app\services\runtime_tester.py` | python | 25018 | Code file (no description available) |
| `app\services\sql_chunker.py` | python | 2257 | Code file (no description available) |
| `app\services\syntax_checker.py` | python | 5104 | Code file (no description available) |
| `app\services\tree_sitter_parser.py` | python | 4331 | Code file (no description available) |
| `app\services\virtual_fs.py` | python | 13685 | Code file (no description available) |
| `app\tools\__init__.py` | python | 209 | Code file (no description available) |
| `app\tools\dependency_manager.py` | python | 6988 | Code file (no description available) |
| `app\tools\file_relations.py` | python | 2925 | Code file (no description available) |
| `app\tools\general_web_search.py` | python | 3414 | Code file (no description available) |
| `app\tools\grep_search.py` | python | 2525 | Code file (no description available) |
| `app\tools\read_file.py` | python | 1701 | Code file (no description available) |
| `app\tools\run_project_tests.py` | python | 9124 | Code file (no description available) |
| `app\tools\search_code.py` | python | 3196 | Code file (no description available) |
| `app\tools\tool_definitions.py` | python | 4087 | Code file (no description available) |
| `app\tools\tool_executor.py` | python | 3335 | Code file (no description available) |
| `app\tools\web_search.py` | python | 4291 | Code file (no description available) |
| `app\utils\__init__.py` | python | 0 | Code file (no description available) |
| `app\utils\compact_index.py` | python | 1637 | Code file (no description available) |
| `app\utils\file_parser.py` | python | 1543 | Code file (no description available) |
| `app\utils\file_types.py` | python | 517 | Code file (no description available) |
| `app\utils\pipeline_trace_logger.py` | python | 3200 | Code file (no description available) |
| `app\utils\token_counter.py` | python | 191 | Code file (no description available) |
| `app\utils\translator.py` | python | 3334 | Code file (no description available) |
| `app\utils\validation_logger.py` | python | 2255 | Code file (no description available) |
| `app\utils\xml_parser.py` | python | 3770 | Code file (no description available) |
| `app\utils\xml_wrapper.py` | python | 7448 | Code file (no description available) |
| `certs\russian_trusted_root_ca_pem.crt` | other | 1424 | - |
| `change_detection_test.log` | other | 949 | - |
| `check_models.py` | python | 156 | Utility module with minimal significant logic. |
| `chunks_index.json` | json | 103446 | File exceeds 30000 tokens |
| `config\__init__.py` | python | 0 | Code file (no description available) |
| `config\framework_registry.json` | json | 1847 | This file is a comprehensive registry of Python frameworks and libraries organized by category, serving as a reference for the AI assistant's code generation and analysis capabilities. It maps import names to human-readable descriptions across categories including GUI, TUI, database, network, web, daemon, CLI, testing, data science, and miscellaneous utilities. This registry helps the system identify and properly handle different types of dependencies when analyzing codebases or generating new code with appropriate framework integrations. |
| `config\settings.py` | python | 6598 | Code file (no description available) |
| `debug_cert.py` | python | 451 | Utility module with minimal significant logic. |
| `examples\test_index\nested_example.py` | python | 276 | Code file (no description available) |
| `examples\test_index\simple_module.py` | python | 570 | Code file (no description available) |
| `examples\test_index\utils.py` | python | 215 | Code file (no description available) |
| `logs\generator_tests\generator_results_20260103_204321.json` | json | 1462 | This JSON file stores the results of a code generation model comparison test, capturing performance metrics for three AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5) across four test cases in ASK and AGENT modes. It records detailed outcomes including success/failure status, execution duration, code output statistics, missing elements, and specific errors for each model-test combination. The file serves as a structured test report that helps evaluate model effectiveness for code generation tasks within the project's AI pipeline testing framework. |
| `logs\generator_tests\generator_results_20260103_212553.json` | json | 1257 | This JSON file stores the results of a code generator performance test comparing three AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5) across four test cases in ASK and AGENT modes. It records detailed metrics for each model-test combination including success status, execution duration, code blocks generated, lines of code produced, and any errors. The file serves as a structured performance log for evaluating AI model effectiveness in code generation tasks, with summary statistics that enable comparative analysis of model speed and output characteristics. |
| `logs\generator_tests\generator_results_20260103_214141.json` | json | 2029 | This file is a test results log from a code generator performance evaluation. It records the outcomes of testing three AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5) across seven different code generation scenarios in both ASK and AGENT modes. The log captures detailed metrics including success status, execution duration, code block counts, and lines generated for each test case, with a summary section comparing overall performance. This data supports model selection decisions in the AI-assisted development pipeline by providing empirical evidence of each model's speed and reliability for different coding tasks. |
| `logs\generator_tests\generator_results_20260103_221039.json` | json | 2028 | This JSON file is a test results log from a code generator model evaluation suite. It records performance metrics for three AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5) across seven test cases covering both ASK and AGENT modes of code generation. The file contains detailed timing data, success status, code output statistics, and a comparative summary showing Claude Haiku 4.5 as the fastest performer while all models passed all tests. This log appears to be output from the `test_generator_models.py` script, providing empirical data for model selection in the AI code generation pipeline. |
| `logs\generator_tests\generator_results_20260103_223137.json` | json | 2028 | This JSON file is a test results log from a code generator performance evaluation. It records the outcomes of testing three AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5) across seven different code generation scenarios in both ASK and AGENT modes. The file contains detailed metrics for each test case including success status, execution duration, code blocks generated, lines of code produced, and any errors encountered. The summary section provides aggregated performance statistics, showing that all models passed all tests with Claude Haiku 4.5 being the fastest on average while generating the most total lines of code. |
| `logs\generator_tests\generator_results_20260103_231832.json` | json | 2683 | This JSON file is a test results log from a code generator performance evaluation. It records the outcomes of testing four AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5, Gemini 3.0 Flash) across seven different code generation scenarios in both ASK and AGENT modes. The file contains detailed metrics for each test case including success status, execution duration, code blocks generated, lines of code produced, and any errors. The summary section aggregates performance statistics, showing that all models passed all tests with Claude Haiku and Gemini being significantly faster on average. |
| `logs\generator_tests\generator_test_20260103_203837.log` | other | 1730 | - |
| `logs\generator_tests\generator_test_20260103_204321.log` | other | 4326 | - |
| `logs\generator_tests\generator_test_20260103_212553.log` | other | 5843 | - |
| `logs\generator_tests\generator_test_20260103_214141.log` | other | 10546 | - |
| `logs\generator_tests\generator_test_20260103_221039.log` | other | 10480 | - |
| `logs\generator_tests\generator_test_20260103_223137.log` | other | 22478 | - |
| `logs\generator_tests\generator_test_20260103_231832.log` | other | 28868 | - |
| `logs\index_test_20251212_114857.log` | other | 1849 | - |
| `logs\index_test_20251212_124603.log` | other | 1100 | - |
| `logs\index_test_20251212_140817.log` | other | 1999 | - |
| `logs\index_test_20251212_143026.log` | other | 941 | - |
| `logs\index_test_20251212_153744.log` | other | 941 | - |
| `logs\index_test_20251212_153905.log` | other | 941 | - |
| `logs\index_test_20251212_154743.log` | other | 941 | - |
| `logs\index_test_20251212_154851.log` | other | 941 | - |
| `logs\index_test_20251213_105050.log` | other | 3777 | - |
| `logs\index_test_20251213_111317.log` | other | 1080 | - |
| `logs\index_test_20251213_111747.log` | other | 1083 | - |
| `logs\index_test_20251214_041600.log` | other | 972 | - |
| `logs\index_test_20251214_044257.log` | other | 1121 | - |
| `logs\index_test_20251214_051127.log` | other | 1006 | - |
| `logs\index_test_20251214_051616.log` | other | 941 | - |
| `logs\index_test_20251214_192419.log` | other | 972 | - |
| `logs\index_test_20251214_194439.log` | other | 972 | - |
| `logs\index_test_20251214_195533.log` | other | 941 | - |
| `logs\index_test_20251214_221352.log` | other | 975 | - |
| `logs\index_test_20251214_225829.log` | other | 941 | - |
| `logs\index_test_20251214_231011.log` | other | 972 | - |
| `logs\index_test_20251214_234411.log` | other | 941 | - |
| `logs\index_test_20251214_235612.log` | other | 941 | - |
| `logs\index_test_20251215_000952.log` | other | 941 | - |
| `logs\index_test_20251215_002411.log` | other | 972 | - |
| `logs\index_test_20251215_003335.log` | other | 941 | - |
| `logs\index_test_20251215_004003.log` | other | 941 | - |
| `logs\index_test_20251215_005536.log` | other | 972 | - |
| `logs\index_test_20251215_010232.log` | other | 941 | - |
| `logs\index_test_20251215_012318.log` | other | 941 | - |
| `logs\index_test_20251215_013458.log` | other | 941 | - |
| `logs\index_test_20251215_015054.log` | other | 941 | - |
| `logs\index_test_20251215_020428.log` | other | 941 | - |
| `logs\index_test_20251215_021231.log` | other | 941 | - |
| `logs\index_test_20251215_023506.log` | other | 941 | - |
| `logs\index_test_20251215_024557.log` | other | 941 | - |
| `logs\index_test_20251215_025623.log` | other | 941 | - |
| `logs\index_test_20251215_031200.log` | other | 941 | - |
| `logs\index_test_20251215_183321.log` | other | 1083 | - |
| `logs\index_test_20251215_200134.log` | other | 926 | - |
| `logs\index_test_20251215_211630.log` | other | 965 | - |
| `logs\index_test_20251215_220201.log` | other | 941 | - |
| `logs\index_test_20251217_140011.log` | other | 995 | - |
| `logs\index_test_20251217_142258.log` | other | 972 | - |
| `logs\index_test_20251217_160134.log` | other | 941 | - |
| `logs\index_test_20251217_174940.log` | other | 941 | - |
| `logs\index_test_20251217_224730.log` | other | 964 | - |
| `logs\index_test_20251219_000906.log` | other | 972 | - |
| `logs\index_test_20251220_060403.log` | other | 941 | - |
| `logs\index_test_20251220_070437.log` | other | 972 | - |
| `logs\index_test_20251221_011404.log` | other | 941 | - |
| `logs\index_test_20251221_024001.log` | other | 941 | - |
| `logs\index_test_20251221_050938.log` | other | 941 | - |
| `logs\index_test_20251222_152745.log` | other | 1061 | - |
| `logs\index_test_20251228_055331.log` | other | 941 | - |
| `logs\index_test_20251228_072618.log` | other | 1032 | - |
| `logs\index_test_20251228_080028.log` | other | 941 | - |
| `logs\index_test_20251228_090804.log` | other | 972 | - |
| `logs\index_test_20251228_183505.log` | other | 972 | - |
| `logs\index_test_20251229_001858.log` | other | 972 | - |
| `logs\index_test_20251229_022617.log` | other | 995 | - |
| `logs\index_test_20251230_141330.log` | other | 972 | - |
| `logs\index_test_20251230_142508.log` | other | 941 | - |
| `logs\index_test_20251230_152739.log` | other | 955 | - |
| `logs\index_test_20251231_032236.log` | other | 941 | - |
| `logs\index_test_20251231_221207.log` | other | 972 | - |
| `logs\index_test_20260101_024257.log` | other | 972 | - |
| `logs\index_test_20260101_043803.log` | other | 972 | - |
| `logs\index_test_20260101_060003.log` | other | 941 | - |
| `logs\index_test_20260101_073013.log` | other | 941 | - |
| `logs\index_test_20260102_210614.log` | other | 941 | - |
| `logs\index_test_20260103_060329.log` | other | 1075 | - |
| `logs\index_test_20260103_060629.log` | other | 945 | - |
| `logs\index_test_20260103_060828.log` | other | 1265 | - |
| `logs\index_test_20260103_080718.log` | other | 941 | - |
| `logs\index_test_20260103_194131.log` | other | 941 | - |
| `logs\index_test_20260103_230723.log` | other | 1121 | - |
| `logs\test_run_20251210_200903.log` | other | 98 | - |
| `main.py` | python | 61829 | CLI application framework with custom navigation exceptions and structured logging, integrating state management and user input handling for menu-driven workflows. |
| `mainsertif.py` | python | 2232 | Unified diagnostic script for testing connectivity and performance of multiple AI model APIs (OpenAI-compatible, GigaChat, OpenRouter, DeepSeek) with automated certificate management and error logging. |
| `maintestchunk .py` | python | 4249 | Full system test for code chunking and project analysis that integrates scanning, tokenization, and rich visualization tools for detailed reporting. |
| `requirements.txt` | txt | 267 | This file is the project's Python dependency manifest (`requirements.txt`) that specifies all third-party packages needed for the AI assistant system. It configures the runtime environment with libraries for AI model integration (OpenAI, GigaChat), token counting (tiktoken), configuration management (pydantic, python-dotenv), console output formatting (rich), HTTP clients (httpx, aiohttp), file processing (pypdf, python-docx, pandas), code analysis (tree-sitter, mypy, autopep8), and asynchronous operations. The dependencies enable core functionalities like multi-provider LLM communication, structured data validation, semantic code parsing, document handling, and rich user interfaces across the application modules. |
| `scripts\test_advice_system — копия.py` | python | 5612 | Code file (no description available) |
| `scripts\test_advice_system.py` | python | 6150 | Code file (no description available) |
| `scripts\test_agent_foundation.py` | python | 17710 | Code file (no description available) |
| `scripts\test_agent_stage2.py` | python | 17273 | Code file (no description available) |
| `scripts\test_agent_stage3.py` | python | 8181 | Code file (no description available) |
| `scripts\test_agents.py` | python | 16428 | Code file (no description available) |
| `scripts\test_context_compression.py` | python | 6064 | Code file (no description available) |
| `scripts\test_deepseek_wrapper.py` | python | 1417 | Code file (no description available) |
| `scripts\test_file_modifier_indentation.py` | python | 22879 | Code file (no description available) |
| `scripts\test_general_chat.py` | python | 1872 | Code file (no description available) |
| `scripts\test_general_chat_with_history.py` | python | 8280 | Code file (no description available) |
| `scripts\test_generator_models.py` | python | 9621 | Code file (no description available) |
| `scripts\test_history_manager.py` | python | 5294 | Code file (no description available) |
| `scripts\test_indent_normalization.py` | python | 14743 | Code file (no description available) |
| `scripts\test_index_manager.py` | python | 8339 | Code file (no description available) |
| `scripts\test_orchestrator_raw.py` | python | 6154 | Code file (no description available) |
| `scripts\test_run_project_tests.py` | python | 11022 | Code file (no description available) |
| `scripts\test_runtime_tester_integration.py` | python | 8636 | Code file (no description available) |
| `scripts\test_semantic_index.py` | python | 10118 | Code file (no description available) |
| `scripts\test_validation_pipeline.py` | python | 3945 | Code file (no description available) |
| `scripts\Проанализируй файл answer_generator.py.md` | md | 108 | This is a task specification file (in Russian) that provides instructions for analyzing the `answer_generator.py` file and related files in a different project directory. It requests an implementation of asynchronous text filtering for articles while preserving the existing filtering logic, asking for both code and explanations. The file serves as a development directive or analysis request rather than containing functional code within the current project. |
| `temp_test.py` | python | 12 | Unit test script for verifying basic console output functionality, integrates with pytest for automated testing. |
| `test.py` | python | 2468 | Asynchronous test suite for OpenRouter AI model availability and performance, integrating httpx for HTTP requests and asyncio for concurrency and delay management. |
| `test_all_chunkers.py` | python | 1372 | Unit test suite for validating language-specific code chunkers (Python, Go, SQL) using SmartChunker classes and Rich library for structured console output. |
| `test_cache_logic.py` | python | 375 | Unit test suite validating response caching behavior for AI tool interactions, specifically verifying cache_control integration for Claude models processing resource-intensive operations. |
| `test_caching_real.py` | python | 701 | Unit test for caching behavior in LLM tool calls, integrating with orchestration and tool-calling frameworks to validate cache control interception and cleanup. |
| `test_deepseek_direct.py` | python | 165 | Unit test for DeepSeek API integration, validating configuration and response parsing for the 'pre_filter' role. |
| `test_index_builder.py` | python | 5828 | Unit test suite for validating the project index builder's core functions, including directory selection, project creation, structure verification, and JSON metadata generation. |
| `test_prefilter_minimal.py` | python | 429 | Unit test for a minimal LLM pre-filter workflow, validating model configuration and JSON response generation via call_llm with performance timing. |
| `token_stats.json` | json | 177 | This JSON file stores aggregated token statistics for the entire project, providing a quantitative overview of codebase composition. It tracks total files and tokens broken down by language (Python vs. non-Python) and by specific file types (code, text, data). The statistics help monitor project size and distribution, likely used for context management, token budgeting, or performance analysis within the AI-assisted development system. |
| `validation_test.log` | other | 2147 | - |
| `современная структура.md` | md | 2828 | This file is a project structure overview document that provides a high-level map of the AI_Assistant_Pro codebase. It lists and briefly describes the purpose of key directories and files, highlighting their roles in the AI-powered code generation and analysis system. The document serves as a quick reference for developers, noting which components are functional, temporarily broken, or deprecated, and clarifies the relationships between modules like agents, services, and tools. It helps orient new contributors to the project's architecture and operational status. |