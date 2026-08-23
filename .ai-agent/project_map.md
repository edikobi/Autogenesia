# Project Map

**Root:** `C:\Users\Admin\AI_Assistant_Pro (te.)`
**Files:** 228
**Total tokens:** 1296965
**Updated:** 2026-07-12T00:09:47.814412Z

## Files

| Path | Type | Tokens | Description |
|------|------|--------|-------------|
| `__init__.py` | python | 0 | Utility module with minimal significant logic. |
| `app\__init__.py` | python | 0 | Code file (no description available) |
| `app\advice\__init__.py` | python | 213 | Code file (no description available) |
| `app\advice\advice_catalog.json` | json | 2239 | This JSON file serves as a structured catalog of methodological thinking frameworks for the AI Orchestrator Agent. It organizes advice into three categories (General, Existing Project, New Project) with specific applicability to different task modes like "ask" or "new_project". Each advice entry includes an ID, name, description, and "when_to_use" guidance, providing systematic approaches for scenarios such as bug hunting, refactoring, security audits, and feature integration. This catalog is loaded by the `advice_loader.py` module to supply context-aware guidance during AI-driven code generation and analysis workflows. |
| `app\advice\advice_content.json` | json | 16352 | This JSON file serves as a structured knowledge base containing expert advice for AI agents working on code generation and analysis tasks. It provides 19 detailed methodologies (ADV-G01 through ADV-E07) covering critical software engineering workflows like bug hunting, feature integration, safe refactoring, security auditing, dependency analysis, error recovery, and GUI development. Each advice entry includes a core principle, a multi-phase thinking framework, and anti-patterns to avoid, designed to guide the AI's reasoning process during complex coding operations. This content is loaded by the `advice_loader.py` module to provide contextual guidance and improve the quality of AI-driven code generation and problem-solving within the agent pipeline. |
| `app\advice\advice_loader.py` | python | 4216 | Code file (no description available) |
| `app\agents\__init__.py` | python | 166 | Code file (no description available) |
| `app\agents\agent_pipeline.py` | python | 60102 | Code file (no description available) |
| `app\agents\code_generator.py` | python | 13845 | Code file (no description available) |
| `app\agents\feedback_handler.py` | python | 12936 | Code file (no description available) |
| `app\agents\feedback_loop.py` | python | 5560 | Code file (no description available) |
| `app\agents\feedback_prompt_loader.py` | python | 3728 | Code file (no description available) |
| `app\agents\orchestrator.py` | python | 16462 | Code file (no description available) |
| `app\agents\pre_filter.py` | python | 12868 | Code file (no description available) |
| `app\agents\router.py` | python | 6562 | Code file (no description available) |
| `app\agents\tester.py` | python | 3509 | Code file (no description available) |
| `app\agents\validator.py` | python | 6340 | Code file (no description available) |
| `app\builders\__init__.py` | python | 0 | Code file (no description available) |
| `app\builders\semantic_index_builder.py` | python | 21602 | Code file (no description available) |
| `app\core\__init__.py` | python | 0 | Code file (no description available) |
| `app\history\__init__.py` | python | 193 | Code file (no description available) |
| `app\history\compressor.py` | python | 3057 | Code file (no description available) |
| `app\history\context_manager.py` | python | 5627 | Code file (no description available) |
| `app\history\manager.py` | python | 5617 | Code file (no description available) |
| `app\history\orchestrator_trace.py` | python | 1525 | Code file (no description available) |
| `app\history\storage.py` | python | 6146 | Code file (no description available) |
| `app\llm\__init__.py` | python | 0 | Code file (no description available) |
| `app\llm\api_client.py` | python | 7995 | Code file (no description available) |
| `app\llm\prompt_templates.py` | python | 90875 | Code file (no description available) |
| `app\services\__init__.py` | python | 40 | Code file (no description available) |
| `app\services\ast_grep_applier.py` | python | 2251 | Code file (no description available) |
| `app\services\backup_manager.py` | python | 4391 | Code file (no description available) |
| `app\services\change_validator.py` | python | 21505 | Code file (no description available) |
| `app\services\code_commenter.py` | python | 1865 | Code file (no description available) |
| `app\services\file_io_tools.py` | python | 2827 | Code file (no description available) |
| `app\services\file_modifier.py` | python | 65482 | Code file (no description available) |
| `app\services\go_adapter.py` | python | 8170 | Code file (no description available) |
| `app\services\go_chunker.py` | python | 2757 | Code file (no description available) |
| `app\services\index_manager.py` | python | 6500 | Code file (no description available) |
| `app\services\index_reader.py` | python | 9575 | Code file (no description available) |
| `app\services\index_updater.py` | python | 2134 | Code file (no description available) |
| `app\services\java_adapter.py` | python | 6413 | Code file (no description available) |
| `app\services\js_ts_adapter.py` | python | 6686 | Code file (no description available) |
| `app\services\json_chunker.py` | python | 2107 | Code file (no description available) |
| `app\services\language_adapter.py` | python | 3647 | Code file (no description available) |
| `app\services\project_map_builder.py` | python | 6473 | Code file (no description available) |
| `app\services\project_scanner.py` | python | 3416 | Code file (no description available) |
| `app\services\python_chunker.py` | python | 5328 | Code file (no description available) |
| `app\services\runtime_tester.py` | python | 38240 | Code file (no description available) |
| `app\services\sql_chunker.py` | python | 2257 | Code file (no description available) |
| `app\services\syntax_checker.py` | python | 16348 | Code file (no description available) |
| `app\services\syntax_fixer_agent.py` | python | 6697 | Code file (no description available) |
| `app\services\tree_sitter_parser.py` | python | 11346 | Code file (no description available) |
| `app\services\virtual_fs.py` | python | 14343 | Code file (no description available) |
| `app\tools\__init__.py` | python | 209 | Code file (no description available) |
| `app\tools\dependency_manager.py` | python | 23117 | Code file (no description available) |
| `app\tools\file_relations.py` | python | 5604 | Code file (no description available) |
| `app\tools\general_web_search.py` | python | 3414 | Code file (no description available) |
| `app\tools\grep_search.py` | python | 4576 | Code file (no description available) |
| `app\tools\list_files.py` | python | 1415 | Code file (no description available) |
| `app\tools\read_file.py` | python | 2440 | Code file (no description available) |
| `app\tools\read_line_context.py` | python | 1214 | Code file (no description available) |
| `app\tools\search_code.py` | python | 3196 | Code file (no description available) |
| `app\tools\tester_tool_definitions.py` | python | 1841 | Code file (no description available) |
| `app\tools\tester_tool_executor.py` | python | 4314 | Code file (no description available) |
| `app\tools\tool_definitions.py` | python | 6614 | Code file (no description available) |
| `app\tools\tool_executor.py` | python | 3745 | Code file (no description available) |
| `app\tools\web_search.py` | python | 4291 | Code file (no description available) |
| `app\tools\web_tools.py` | python | 3569 | Code file (no description available) |
| `app\utils\__init__.py` | python | 0 | Code file (no description available) |
| `app\utils\compact_index.py` | python | 1637 | Code file (no description available) |
| `app\utils\file_parser.py` | python | 1543 | Code file (no description available) |
| `app\utils\file_types.py` | python | 562 | Code file (no description available) |
| `app\utils\pipeline_trace_logger.py` | python | 4070 | Code file (no description available) |
| `app\utils\tester_translator.py` | python | 670 | Code file (no description available) |
| `app\utils\token_counter.py` | python | 191 | Code file (no description available) |
| `app\utils\translator.py` | python | 3425 | Code file (no description available) |
| `app\utils\validation_logger.py` | python | 2255 | Code file (no description available) |
| `app\utils\xml_parser.py` | python | 4039 | Code file (no description available) |
| `app\utils\xml_wrapper.py` | python | 7448 | Code file (no description available) |
| `certs\russian_trusted_root_ca_pem.crt` | other | 1424 | - |
| `change_detection_test.log` | other | 949 | - |
| `check_models.py` | python | 156 | Utility module with minimal significant logic. |
| `chunks_index.json` | json | 103446 | File exceeds 30000 tokens |
| `config\__init__.py` | python | 0 | Code file (no description available) |
| `config\framework_registry.json` | json | 1876 | This file is a comprehensive registry of Python frameworks and libraries categorized by their primary use cases (GUI, TUI, database, network, web, etc.). It serves as a reference catalog for the AI assistant project, likely used by the code generation or analysis components to understand available technologies and their typical applications. The structured JSON format with descriptive labels enables the system to map import statements to framework types, supporting tasks like dependency analysis, project scaffolding, or technology recommendations. |
| `config\intermediate_agent_models.py` | python | 3212 | Code file (no description available) |
| `config\provider_keys.json` | json | 382 | This is a JSON configuration file that stores API credentials and routing settings for multiple AI model providers used in the project. It contains API keys and base URLs for services like OpenRouter, DeepSeek, and OpenCode, along with provider priority configuration through `provider_entry_order` and `disabled_providers` arrays. The file also specifies a `selected_agent_provider` (currently OpenCode) and a `reasoning_effort` setting, which are used by the central configuration module (`config/settings.py`) to manage provider selection and API routing across the application's multi-provider AI orchestration system. |
| `config\provider_models.py` | python | 3494 | Code file (no description available) |
| `config\settings.py` | python | 11478 | Code file (no description available) |
| `debug_cert.py` | python | 451 | Utility module with minimal significant logic. |
| `examples\test_index\nested_example.py` | python | 276 | Code file (no description available) |
| `examples\test_index\simple_module.py` | python | 570 | Code file (no description available) |
| `examples\test_index\utils.py` | python | 215 | Code file (no description available) |
| `implementation_plan.md` | md | 621 | This file is an implementation plan for version 18.9 of the project, specifically addressing a bug in error classification within the validation process. It details a problem where syntax errors are incorrectly reported as "Targeting-First Error" due to the order of checks in the agent pipeline, causing misrouted repair attempts. The plan proposes modifying the `_check_tree_structure_broken` method in `agent_pipeline.py` to prioritize syntax checking before target method validation, ensuring proper error categorization and handling. It also outlines a verification strategy using existing test suites to confirm the fix and maintain system integrity. |
| `logs\generator_mode_tests\mode_results_20260123_233440.json` | json | 10081 | This JSON file is a test results log from a generator mode evaluation suite that benchmarks multiple AI models on code generation tasks. It contains detailed performance metrics for five models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5, Gemini 3.0 Flash, GPT-5.1-Codex-Mini) across 11 different code modification modes like REPLACE_FILE, ADD_METHOD, and INSERT_IMPORT. The log includes comprehensive statistics (pass/fail counts, error types, durations) and individual test case results with generated code previews, providing comparative analysis data for the project's code generation agent system. |
| `logs\generator_mode_tests\mode_results_20260124_001242.json` | json | 9031 | This JSON file contains test results from evaluating multiple AI models on code generation tasks. It records performance metrics for five models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5, Gemini 3.0 Flash, GPT-5.1-Codex-Mini) across 11 test cases covering different modification modes like REPLACE_FILE, ADD_METHOD, and REPLACE_FUNCTION. The file provides detailed summaries including pass/fail counts, error types (staging, syntax, validation), execution durations, and code previews for each test run, serving as a comprehensive benchmark for comparing model capabilities in the project's code generation system. |
| `logs\generator_mode_tests\mode_results_20260124_003549.json` | json | 9107 | This JSON file contains test results from evaluating multiple AI models on code generation tasks. It records performance metrics for five models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5, Gemini 3.0 Flash, GPT-5.1-Codex-Mini) across 11 test cases covering different modification modes like REPLACE_FILE, ADD_METHOD, and PATCH_METHOD. The results include detailed per-test outcomes with statuses (passed/failed/errors), execution times, error messages, and code previews, providing comparative analysis data for the project's code generation capabilities. |
| `logs\generator_mode_tests\mode_results_20260124_005746.json` | json | 9085 | This JSON file contains test results from evaluating multiple AI models on code generation tasks. It records performance metrics for five models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5, Gemini 3.0 Flash, GPT-5.1-Codex-Mini) across 11 test cases covering different modification modes like REPLACE_FILE, ADD_METHOD, and PATCH_METHOD. The file includes detailed summaries of pass/fail rates, error types (staging, syntax, validation), execution durations, and code previews for each test run, serving as a comprehensive benchmark for comparing model capabilities in the project's code generation system. |
| `logs\generator_mode_tests\mode_results_20260124_011846.json` | json | 9090 | This JSON file stores test results from a comparative evaluation of multiple AI models (DeepSeek Chat, GLM 4.7, Claude Haiku 4.5, Gemini 3.0 Flash, GPT-5.1-Codex-Mini) on code generation tasks. It records performance metrics across 11 test cases covering different modification modes (REPLACE_FILE, REPLACE_METHOD, ADD_METHOD, etc.), including pass/fail counts, error types (syntax, staging, validation), execution durations, and generated code previews. The file serves as a structured log for benchmarking the code generation capabilities of various AI models within the project's testing framework, providing detailed comparative data for analysis and model selection decisions. |
| `logs\generator_mode_tests\mode_test_20260123_230853.log` | other | 510 | - |
| `logs\generator_mode_tests\mode_test_20260123_231847.log` | other | 418 | - |
| `logs\generator_mode_tests\mode_test_20260123_231933.log` | other | 103 | - |
| `logs\generator_mode_tests\mode_test_20260123_232602.log` | other | 2361 | - |
| `logs\generator_mode_tests\mode_test_20260123_233440.log` | other | 4024 | - |
| `logs\generator_mode_tests\mode_test_20260123_235930.log` | other | 250 | - |
| `logs\generator_mode_tests\mode_test_20260124_000550.log` | other | 93 | - |
| `logs\generator_mode_tests\mode_test_20260124_001242.log` | other | 3497 | - |
| `logs\generator_mode_tests\mode_test_20260124_003549.log` | other | 3519 | - |
| `logs\generator_mode_tests\mode_test_20260124_005125.log` | other | 415 | - |
| `logs\generator_mode_tests\mode_test_20260124_005746.log` | other | 3536 | - |
| `logs\generator_mode_tests\mode_test_20260124_011846.log` | other | 3483 | - |
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
| `logs\index_test_20260116_124702.log` | other | 945 | - |
| `logs\index_test_20260116_124740.log` | other | 945 | - |
| `logs\index_test_20260116_124817.log` | other | 945 | - |
| `logs\index_test_20260116_124932.log` | other | 945 | - |
| `logs\test_run_20251210_200903.log` | other | 98 | - |
| `main.py` | python | 79156 | Centralized application entry point for multi-mode AI orchestration with custom exception handling, logging, and state management. |
| `mainsertif.py` | python | 2232 | Unified diagnostic script for testing connectivity and performance of multiple AI model APIs (OpenAI-compatible, GigaChat, OpenRouter, DeepSeek) with automated certificate management and error logging. |
| `maintestchunk .py` | python | 4249 | Full system test for code chunking and project analysis that integrates scanning, tokenization, and rich visualization tools for detailed reporting. |
| `plan.md` | md | 3631 | This is a detailed implementation plan for refactoring the user feedback handling system in an AI-powered code generation pipeline. It addresses a critical bug where user feedback cycles would prematurely fail due to accumulated iteration limits, and where the `handle_user_feedback` method would discard previously generated code by clearing the Virtual File System (VFS). The plan proposes adding a `reset_iteration_limits()` method to `FeedbackLoopState`, modifying `run_feedback_cycle` to reset limits at the start of each cycle, and refactoring `handle_user_feedback` to delegate to `run_feedback_cycle` for "accept" and "replace" actions, preserving VFS state and enabling full validation loops. It also specifies which files to modify (`feedback_loop.py`, `agent_pipeline.py`) and confirms that `main.py` requires no changes, ensuring continuous, unlimited user-driven iterations with proper code preservation and validation. |
| `pyrightconfig.json` | json | 995 | Pyright configuration file for static type checking in the AI Assistant Pro project. It specifies that Pyright should analyze the `app` and `tests` directories while excluding common directories like `node_modules`, `__pycache__`, and virtual environments. The configuration sets type checking to "standard" mode with extensive diagnostic severity overrides, treating most type-related issues as warnings (e.g., missing imports, optional type issues, unused code) while keeping syntax errors as errors and disabling certain checks like `reportSelfClsParameterName` and `reportAssertAlwaysTrue`. This ensures consistent type checking across the project's codebase. |
| `requirements.txt` | txt | 188 | This is the project's dependency specification file (requirements.txt) that lists all Python packages required to run the AI Assistant Pro application. It includes core dependencies for HTTP communication (httpx), AI/LLM integration (openai), code parsing and analysis (tree-sitter with language-specific parsers for Python, JavaScript, TypeScript, Go, and Java), code formatting and linting (autopep8, black, isort, yapf, mypy, pyright, ruff), file processing (pypdf, python-docx, openpyxl, pandas), web scraping (beautifulsoup4), and utility libraries (aiofiles, watchdog, markdown, nest_asyncio). The file ensures all necessary packages are installed for the project's multi-language code analysis, AI agent functionality, and file I/O operations. |
| `session_20260709_010815_session-.log` | other | 10656 | - |
| `staging_error_20260711_181337_124335.json` | json | 91989 | File exceeds 30000 tokens |
| `staging_error_20260711_181337_127335.json` | json | 9351 | This is a diagnostic error log file that records a failed AI code modification attempt on `app/llm/api_client.py`. It captures a specific `AI_CASCADE_FAILED` error where both primary and fallback AI models failed structural validation during a `REPLACE_IN_METHOD` operation targeting the `_make_request` method of the `LLMClient` class. The file contains the complete backup of the original file content (the full `api_client.py` source code), the attempted replacement pattern (`try:`), and the specific validation error (`unindent does not match any outer indentation level` at line 526). This log serves as a debugging artifact for the project's AI-driven code modification pipeline, preserving the exact state and error context for post-mortem analysis and potential manual recovery. |
| `staging_error_20260711_181805_653318.json` | json | 9491 | This is a JSON error log file generated by the AI Assistant Pro's code modification pipeline when an AI-driven code change fails structural validation. It records a failed attempt to modify `app/llm/api_client.py` (specifically the `_make_request` method of `LLMClient`) where both AI models (Model A and Model B) produced invalid syntax at line 537. The file captures the full error context including the target file, the attempted replacement pattern, the backup content of the original file, and the specific validation error ("invalid syntax") that prevented the change from being applied. This log is used for debugging AI code generation failures and tracking the system's error recovery behavior. |
| `temp_test.py` | python | 12 | Unit test script for verifying basic console output functionality, integrates with pytest for automated testing. |
| `test_staging_bug.py` | python | 6008 | Test suite for staging bug detection, validating code state transitions and mutation analysis through mocked AI validation and virtual file system integration. |
| `trace_20260711_172549_20260711.json` | json | 11537 | This is a detailed trace log from the AI Assistant Pro orchestrator, documenting a single failed iteration of an automated code fix attempt. It captures a user request about provider migration issues, the orchestrator's instruction to fix a SyntaxError in main.py and remove a duplicate check in api_client.py, and the full set of generated code patches across multiple files (config/settings.py, config/provider_models.py, config/intermediate_agent_models.py, app/llm/api_client.py, main.py, and config/provider_keys.json). The trace records that while technical validation passed and the AI validator approved the changes, runtime testing failed because the SyntaxError in main.py was not properly resolved, and it logs 10 staging errors where AI cascade validation failed on structural checks. This file serves as a debugging and audit record for understanding why an automated fix attempt failed, showing the exact code changes proposed, validation results, and the specific errors encountered. |
| `План внедрения тестировщика.md` | md | 9077 | This is a detailed implementation plan for integrating a new **TesterAgent** into the project's AI pipeline. It specifies the creation of four new files (tool definitions, executor, translator, and the agent itself) and modifications to three existing files (`tool_definitions.py`, `agent_pipeline.py`, `main.py`). The plan defines the agent's architecture as an isolated LLM with read-only access to the Virtual File System (VFS), a set of unique testing tools (Ruff, compilation, code execution, git diff), and a strict workflow where it is invoked after all other validations pass. It also outlines the report generation and translation process, and how the tester's feedback can be injected into the orchestrator's feedback cycle for code revisions. |
| `План по исправлению проблем с отступами.md` | md | 5842 | This is a diagnostic and remediation plan document (in Russian) that identifies and prescribes fixes for six specific indentation bugs in `app/services/file_modifier.py`. It provides a detailed analysis of each error, including the root cause, affected code lines, and exact replacement code for four critical changes in methods like `_strip_code_block_to_zero_base`, `_find_container_indent_at_insertion`, and `_patch_method`. The document serves as a technical specification for developers to correct indentation handling in the file modification system, ensuring proper tab expansion, block header detection, and zero-base stripping for code blocks. |
| `План по рефакторингу нормализации.md` | md | 1030 | This is a technical analysis document outlining a refactoring plan for the code normalization system in the project. It identifies six specific defects in the indentation normalization pipeline, including issues with zero-base stripping, Tree-sitter parsing with incorrect column offsets, missing dedentation before parsing, incomplete idempotency handling for block headers, tab character vulnerabilities, and incomplete fallback coverage for clause keywords. The document serves as a bug report and refactoring specification for the file_modifier.py component, providing detailed root cause analysis and proposed fixes for each identified issue. |
| `План по улучшении миграции на OpenAI SDK.md` | md | 3591 | This is a detailed implementation plan for improving the OpenAI SDK migration and fixing multi-provider issues in an AI coding assistant project. It identifies four specific problems: stripped model names not being propagated to API calls, potential regression for OpenRouter/RouterAI providers, hardcoded model values overriding provider-aware selection, and the need for separate orchestrator and generator model lists. The document provides concrete code changes for six files (settings.py, api_client.py, validator.py, syntax_fixer_agent.py, provider_models.py, main.py) with exact line numbers, helper function designs, and execution order. It serves as a technical specification for developers to implement targeted fixes that ensure correct model prefix handling, preserve existing provider behavior, and properly separate model roles without reducing functionality. |
| `Прошлый запрос по провайдерам.md` | md | 8630 | This is a detailed implementation plan document in Russian that diagnoses and proposes fixes for multiple interconnected bugs in an AI coding assistant project. It identifies four root problem areas: provider configuration errors (400 errors, NoneType issues), unprotected intermediate agents from reasoning_effort parameters, broken user provider selection with multiple API keys, and missing provider-to-model visibility in the UI. The document provides specific code changes across six files (settings.py, provider_models.py, intermediate_agent_models.py, api_client.py, main.py, provider_keys.json) with corrected logic for API key resolution, model ID updates for DeepSeek V4, proper handling of OpenAI SDK contracts, and UI improvements for provider selection and display. It serves as a comprehensive technical specification for a developer to systematically fix provider management, agent routing, and user interface issues in the project. |
| `блок 12.md` | md | 73 | This file contains a structured code modification instruction targeting the `main.py` file, specifying a `REPLACE_IN_METHOD` operation to update a user choice prompt. It defines a specific code snippet that replaces an existing prompt with a new `prompt_with_navigation` call offering four numbered options (1-4) with a default of `None`. This appears to be a patch or change request for the core application controller, likely part of a staged modification or AI-generated code update within the project's agent pipeline. |
| `внедрение новых языков программирования.md` | md | 6055 | This file is a detailed implementation plan for adding multi-language support (JavaScript/TypeScript, Go, Java) to the project's AI-assisted code validation system. It outlines the architectural approach, specifying new abstract and concrete language adapter classes, integration points with the existing ChangeValidator and DependencyManager, and required external tool installations. The plan focuses on enabling syntax linting, automatic error fixing, formatting, and basic runtime testing for these languages, mirroring the existing Python support. It serves as a comprehensive technical specification for developers to implement the feature. |
| `современная структура.md` | md | 2828 | This file is a project structure overview document that provides a high-level map of the AI_Assistant_Pro codebase. It lists and briefly describes the purpose of key directories and files, highlighting their roles in the AI-powered code generation and analysis system. The document serves as a quick reference for developers, noting which components are functional, temporarily broken, or deprecated, and clarifies the relationships between modules like agents, services, and tools. It helps orient new contributors to the project's architecture and operational status. |