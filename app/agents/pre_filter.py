# app/agents/pre_filter.py
"""
Pre-filter Agent - Selects top N most relevant AST chunks for analysis.

UPDATED LOGIC:
- Loads ACTUAL CODE for each chunk from semantic index
- Sends code to DeepSeek for analysis (not just metadata)
- DeepSeek sees real code and selects best 5 chunks
- Applies 75k token limit (whole chunks only, no splitting)
- At least 1 chunk must reach Orchestrator

Supports both regular and compressed semantic indexes.
"""

from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional

from config.settings import cfg
from app.llm.api_client import call_llm, get_model_for_role
from app.llm.prompt_templates import PREFILTER_SYSTEM_PROMPT, PREFILTER_USER_PROMPT
from app.services.project_map_builder import get_project_map_for_prompt
from app.utils.token_counter import TokenCounter

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class SelectedChunk:
    """A single selected chunk with metadata"""
    chunk_id: str
    file_path: str
    chunk_type: str
    name: str
    relevance_score: float
    reason: str
    tokens: int
    code: str = ""
    start_line: int = 0
    end_line: int = 0


@dataclass
class PreFilterResult:
    """Result of pre-filtering operation"""
    selected_chunks: List[SelectedChunk]
    total_tokens: int
    pruned: bool
    pruned_count: int
    original_count: int


# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def pre_filter_chunks(
    user_query: str,
    index: Dict[str, Any],
    project_dir: str,
    # [NEW] Добавляем параметр для определения модели оркестратора
    orchestrator_model: str = None
) -> PreFilterResult:
    """
    Selects top N most relevant AST chunks from project index.
    
    NEW ALGORITHM:
    1. Load ALL chunks with their CODE from semantic index
    2. Build context with actual code for LLM
    3. Apply token limit BEFORE sending to LLM (max varies by model)
    4. Send chunks WITH CODE to DeepSeek
    5. DeepSeek returns N most relevant chunk_ids
    6. Return selected chunks with code for Orchestrator
    
    UPDATED: Model-specific token limits to account for tokenization differences:
    - Claude Opus 4.5: Conservative tokenization (~1.3-1.4x tiktoken)
    - DeepSeek Reasoner: Small context (64k) + conservative tokenization
    - Gemini 3.0 Pro: Huge context (1M), extended limits
    - Others: Standard limits
    
    Args:
        user_query: User's question
        index: Full or compressed project index
        project_dir: Path to project root
        orchestrator_model: Target model (affects token limits)
        
    Returns:
        PreFilterResult with selected chunks (including code)
    """
    
    # =========================================================================
    # DEBUG LOGGING — диагностика почему лимиты могут не применяться
    # =========================================================================
    logger.info(f"[PRE_FILTER] orchestrator_model received: '{orchestrator_model}'")
    logger.debug(f"[PRE_FILTER] cfg.MODEL_OPUS_4_5 = '{cfg.MODEL_OPUS_4_5}'")
    logger.debug(f"[PRE_FILTER] cfg.MODEL_DEEPSEEK_REASONER = '{cfg.MODEL_DEEPSEEK_REASONER}'")
    logger.debug(f"[PRE_FILTER] cfg.MODEL_GEMINI_3_PRO = '{cfg.MODEL_GEMINI_3_PRO}'")
    
    # =========================================================================
    # ДИНАМИЧЕСКИЕ ЛИМИТЫ В ЗАВИСИМОСТИ ОТ МОДЕЛИ ОРКЕСТРАТОРА
    # =========================================================================
    # 
    # Проблема: Токенизатор tiktoken (OpenAI cl100k_base) даёт заниженные 
    # оценки для моделей других провайдеров:
    # - Claude (Anthropic) — коэффициент ~1.3-1.4x
    # - DeepSeek — коэффициент ~1.4-1.5x
    # 
    # Кроме того, DeepSeek Reasoner имеет всего 64k контекст, из которых
    # значительная часть уходит на reasoning_content.
    # 
    # Поэтому для этих моделей мы УЖЕСТОЧАЕМ лимиты на пре-фильтре,
    # чтобы оставить место для:
    # - Системного промпта (~5-10k)
    # - Tool calls результатов (~20-40k)
    # - Ответа модели / reasoning (~10-20k)
    # =========================================================================
    
    # Модели с УЖЕСТОЧЁННЫМИ лимитами (маленький контекст или дорогие)
    STRICT_LIMIT_MODELS = {
        cfg.MODEL_OPUS_4_5,           # Дорогой + консервативная токенизация Claude
        cfg.MODEL_DEEPSEEK_REASONER,  # Всего 64k контекст!
    }
    
    # Helper function для более надёжного сравнения моделей
    def _is_strict_limit_model(model: str) -> bool:
        """Check if model requires strict token limits using fuzzy matching."""
        if not model:
            return False
        
        # Точное совпадение
        if model in STRICT_LIMIT_MODELS:
            return True
        
        # Fuzzy matching по подстрокам (на случай разных форматов ID)
        model_lower = model.lower()
        
        # Claude Opus 4.5 / Opus 4
        if "opus" in model_lower and ("4.5" in model_lower or "4-" in model_lower or "claude" in model_lower):
            return True
        
        # DeepSeek Reasoner
        if "deepseek" in model_lower and "reasoner" in model_lower:
            return True
        
        return False
    
    def _is_gemini_3_pro(model: str) -> bool:
        """Check if model is Gemini 3.0 Pro using fuzzy matching."""
        if not model:
            return False
        
        # Точное совпадение
        if model == cfg.MODEL_GEMINI_3_PRO:
            return True
        
        # Fuzzy matching
        model_lower = model.lower()
        if "gemini" in model_lower and ("3.0" in model_lower or "3-" in model_lower) and "pro" in model_lower:
            return True
        
        return False
    
    # Определяем лимиты
    if _is_gemini_3_pro(orchestrator_model):
        # =====================================================================
        # Gemini 3.0 Pro — огромное окно ~1M токенов
        # Можно позволить много контекста
        # =====================================================================
        max_chunks = 15
        max_tokens = 150000
        logger.info(
            f"[PRE_FILTER] Using EXTENDED limits for Gemini 3.0 Pro "
            f"({max_chunks} chunks, {max_tokens:,} tokens)"
        )
    
    elif _is_strict_limit_model(orchestrator_model):
        # =====================================================================
        # Claude Opus 4.5 и DeepSeek Reasoner — УЖЕСТОЧЁННЫЕ лимиты
        # 
        # Расчёт для DeepSeek Reasoner (64k контекст):
        #   - Системный промпт: ~5k
        #   - Резерв на reasoning_content: ~15-20k (может быть огромным!)
        #   - Резерв на tool_calls результаты: ~10k
        #   - Итого доступно для чанков: ~30k tiktoken ≈ 20-22k реальных
        # 
        # Расчёт для Claude Opus 4.5 (200k контекст, но дорогой):
        #   - Экономим токены из-за высокой стоимости
        #   - Консервативная токенизация: 30k tiktoken ≈ 40k реальных
        # =====================================================================
        max_chunks = 2   # Только 2 самых релевантных чанка
        max_tokens = 30000  # 30k tiktoken — безопасный лимит
        
        model_name = cfg.get_model_display_name(orchestrator_model) if orchestrator_model else "Unknown"
        logger.info(
            f"[PRE_FILTER] Using STRICT limits for {model_name} "
            f"({max_chunks} chunks, {max_tokens:,} tokens)"
        )
    
    else:
        # =====================================================================
        # Стандартные лимиты для остальных моделей:
        # - GPT-5.1 Codex Max (128k контекст)
        # - Claude Sonnet 4.5 (200k контекст)
        # - DeepSeek Chat (64k, но без reasoning overhead)
        # - и др.
        # =====================================================================
        max_chunks = cfg.PRE_FILTER_MAX_CHUNKS  # 5
        max_tokens = cfg.PRE_FILTER_MAX_TOKENS  # 75000
        logger.info(
            f"[PRE_FILTER] Using STANDARD limits "
            f"({max_chunks} chunks, {max_tokens:,} tokens)"
        )
    
    # =========================================================================
    # ОСНОВНАЯ ЛОГИКА (без изменений)
    # =========================================================================
    
    # Validate inputs
    if not user_query or not user_query.strip():
        logger.warning("Pre-filter: empty user query")
        return _empty_result()
    
    if not index:
        logger.warning("Pre-filter: empty index provided")
        return _empty_result()
    
    # Detect index format
    is_compressed = index.get("compressed", False)
    logger.info(f"Pre-filter: using {'compressed' if is_compressed else 'regular'} index")
    
    # STEP 1: Load ALL chunks with CODE
    all_chunks = _load_all_chunks_with_code(index, project_dir)
    
    if not all_chunks:
        logger.warning("Pre-filter: no chunks loaded from index")
        return _empty_result()
    
    logger.info(f"Pre-filter: loaded {len(all_chunks)} chunks with code")
    
    # STEP 2: Build chunks list WITH CODE for LLM
    # Apply token budget for LLM input
    chunks_for_llm, total_input_tokens = _build_chunks_for_llm(
        all_chunks, 
        max_input_tokens=max_tokens
    )
    
    logger.info(
        f"Pre-filter: prepared {len(chunks_for_llm)} chunks "
        f"({total_input_tokens} tokens) for LLM analysis"
    )
    
    # STEP 3: Get project map for additional context
    project_map_str = get_project_map_for_prompt(project_dir)
    
    # STEP 4: Call LLM with actual code
    selected_ids = await _call_prefilter_llm(
        user_query=user_query,
        chunks_with_code=chunks_for_llm,
        project_map_str=project_map_str,
        max_chunks=max_chunks,
    )
    
    # STEP 5: If LLM selection failed, use fallback
    if not selected_ids:
        logger.warning("Pre-filter: LLM selection failed, using fallback")
        selected_ids = _fallback_selection(chunks_for_llm, max_chunks)
    
    # STEP 6: Get selected chunks with code
    selected_chunks = _get_selected_chunks(
        selected_ids=selected_ids,
        all_chunks=chunks_for_llm,
    )
    
    # Calculate totals
    total_tokens = sum(chunk.tokens for chunk in selected_chunks)
    original_count = len(selected_chunks)
    
    logger.info(f"Pre-filter: selected {len(selected_chunks)} chunks, {total_tokens} tokens")
    
    # STEP 7: Apply token limit for OUTPUT (prune if needed)
    selected_chunks, pruned, pruned_count = _apply_token_limit(
        chunks=selected_chunks,
        max_tokens=max_tokens,
    )
    
    # STEP 8: Auto-include README.md if exists
    readme_path = Path(project_dir) / "README.md"
    # Проверяем, нет ли его уже в списке (чтобы не дублировать)
    readme_already_selected = any(c.file_path == "README.md" for c in selected_chunks)
    
    if readme_path.exists() and not readme_already_selected:
        readme_content = _read_file_safe(readme_path)
        if readme_content:
            token_counter = TokenCounter()
            readme_tokens = token_counter.count(readme_content)
            
            # Для strict моделей — ограничиваем README до 5k токенов
            if _is_strict_limit_model(orchestrator_model) and readme_tokens > 5000:
                # Обрезаем README для экономии токенов
                readme_lines = readme_content.splitlines()
                truncated_lines = []
                current_tokens = 0
                for line in readme_lines:
                    line_tokens = token_counter.count(line)
                    if current_tokens + line_tokens > 5000:
                        truncated_lines.append("\n... [README truncated due to token limit] ...")
                        break
                    truncated_lines.append(line)
                    current_tokens += line_tokens
                readme_content = "\n".join(truncated_lines)
                readme_tokens = token_counter.count(readme_content)
                logger.info(f"Pre-filter: README.md truncated to {readme_tokens} tokens for strict model")
            
            readme_chunk = SelectedChunk(
                chunk_id="README.md",
                file_path="README.md",
                chunk_type="documentation",
                name="Project Documentation",
                relevance_score=1.01,  # Ставим выше 1.0, чтобы он был самым первым
                reason="Project README (Auto-included)",
                tokens=readme_tokens,
                code=readme_content,
                start_line=1,
                end_line=len(readme_content.splitlines())
            )
            # Вставляем в начало списка
            selected_chunks.insert(0, readme_chunk)
            logger.info(f"Pre-filter: Auto-included README.md ({readme_tokens} tokens)")
    
    return PreFilterResult(
        selected_chunks=selected_chunks,
        total_tokens=sum(c.tokens for c in selected_chunks),
        pruned=pruned,
        pruned_count=pruned_count,
        original_count=original_count,
    )
    
    
# ============================================================================
# CHUNK LOADING (NEW - loads code from index/files)
# ============================================================================

def _load_all_chunks_with_code(
    index: Dict[str, Any],
    project_dir: str,
) -> List[SelectedChunk]:
    """
    Load ALL chunks with their actual code.
    
    Reads code from:
    1. Semantic index (if code is stored there)
    2. Original files (using line numbers from index)
    """
    chunks = []
    token_counter = TokenCounter()
    
    is_compressed = index.get("compressed", False)
    
    if is_compressed:
        chunks = _load_from_compressed_index(index, project_dir, token_counter)
    else:
        chunks = _load_from_regular_index(index, project_dir, token_counter)
    
    return chunks


def _load_from_regular_index(
    index: Dict[str, Any],
    project_dir: str,
    token_counter: TokenCounter,
) -> List[SelectedChunk]:
    """Load chunks from regular (non-compressed) semantic index"""
    chunks = []
    
    for file_path, file_data in index.get("files", {}).items():
        full_path = Path(project_dir) / file_path
        
        # Read file content once
        file_content = _read_file_safe(full_path)
        if file_content is None:
            continue
        
        file_lines = file_content.splitlines()
        
        # Load classes
        for cls in file_data.get("classes", []):
            chunk = _extract_chunk_from_lines(
                file_path=file_path,
                name=cls.get("name", ""),
                chunk_type="class",
                lines_str=cls.get("lines", ""),
                file_lines=file_lines,
                token_counter=token_counter,
                description=cls.get("description", ""),
            )
            if chunk:
                chunks.append(chunk)
        
        # Load functions
        for func in file_data.get("functions", []):
            chunk = _extract_chunk_from_lines(
                file_path=file_path,
                name=func.get("name", ""),
                chunk_type="function",
                lines_str=func.get("lines", ""),
                file_lines=file_lines,
                token_counter=token_counter,
                description=func.get("description", ""),
            )
            if chunk:
                chunks.append(chunk)
    
    return chunks


def _load_from_compressed_index(
    index: Dict[str, Any],
    project_dir: str,
    token_counter: TokenCounter,
) -> List[SelectedChunk]:
    """Load chunks from compressed semantic index"""
    chunks = []
    
    # Cache for file contents
    file_cache: Dict[str, List[str]] = {}
    
    def get_file_lines(file_path: str) -> Optional[List[str]]:
        if file_path not in file_cache:
            full_path = Path(project_dir) / file_path
            content = _read_file_safe(full_path)
            if content is None:
                file_cache[file_path] = None
            else:
                file_cache[file_path] = content.splitlines()
        return file_cache[file_path]
    
    # Load classes
    for cls in index.get("classes", []):
        file_path = cls.get("file", "")
        file_lines = get_file_lines(file_path)
        if file_lines is None:
            continue
        
        chunk = _extract_chunk_from_lines(
            file_path=file_path,
            name=cls.get("name", ""),
            chunk_type="class",
            lines_str=cls.get("lines", ""),
            file_lines=file_lines,
            token_counter=token_counter,
            description=cls.get("description", ""),
        )
        if chunk:
            chunks.append(chunk)
    
    # Load functions
    for func in index.get("functions", []):
        file_path = func.get("file", "")
        file_lines = get_file_lines(file_path)
        if file_lines is None:
            continue
        
        chunk = _extract_chunk_from_lines(
            file_path=file_path,
            name=func.get("name", ""),
            chunk_type="function",
            lines_str=func.get("lines", ""),
            file_lines=file_lines,
            token_counter=token_counter,
            description=func.get("description", ""),
        )
        if chunk:
            chunks.append(chunk)
    
    return chunks


def _extract_chunk_from_lines(
    file_path: str,
    name: str,
    chunk_type: str,
    lines_str: str,
    file_lines: List[str],
    token_counter: TokenCounter,
    description: str = "",
) -> Optional[SelectedChunk]:
    """Extract chunk code using line numbers"""
    if not lines_str or "-" not in lines_str:
        return None
    
    try:
        start_line, end_line = map(int, lines_str.split("-"))
    except ValueError:
        return None
    
    # Extract code (1-indexed to 0-indexed)
    code_lines = file_lines[start_line - 1 : end_line]
    code = "\n".join(code_lines)
    
    if not code.strip():
        return None
    
    tokens = token_counter.count(code)
    
    return SelectedChunk(
        chunk_id=f"{file_path}:{name}",
        file_path=file_path,
        chunk_type=chunk_type,
        name=name,
        relevance_score=0.0,  # Will be set by LLM
        reason=description,  # Use description as initial reason
        tokens=tokens,
        code=code,
        start_line=start_line,
        end_line=end_line,
    )


def _read_file_safe(path: Path) -> Optional[str]:
    """Safely read file with encoding fallback"""
    if not path.exists():
        return None
    
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except Exception:
            return None
    except Exception:
        return None


# ============================================================================
# BUILD CHUNKS FOR LLM (with token budget)
# ============================================================================

def _build_chunks_for_llm(
    all_chunks: List[SelectedChunk],
    max_input_tokens: int,
) -> tuple[List[SelectedChunk], int]:
    """
    Build list of chunks to send to LLM.
    
    Applies token budget - includes as many chunks as possible
    while staying under max_input_tokens limit.
    
    Returns: (chunks_list, total_tokens)
    """
    # Reserve tokens for system prompt, user query, etc.
    RESERVED_TOKENS = 5000
    available_tokens = max_input_tokens - RESERVED_TOKENS
    
    selected = []
    total_tokens = 0
    
    # Sort by tokens (smaller first) to include more chunks
    sorted_chunks = sorted(all_chunks, key=lambda c: c.tokens)
    
    for chunk in sorted_chunks:
        # Check if adding this chunk exceeds budget
        chunk_overhead = 100  # XML tags, metadata
        chunk_total = chunk.tokens + chunk_overhead
        
        if total_tokens + chunk_total <= available_tokens:
            selected.append(chunk)
            total_tokens += chunk_total
        else:
            # If we have at least some chunks, stop adding
            if selected:
                logger.info(
                    f"Pre-filter: token budget reached, "
                    f"including {len(selected)}/{len(all_chunks)} chunks"
                )
                break
    
    # If no chunks fit, take at least the smallest one
    if not selected and all_chunks:
        smallest = min(all_chunks, key=lambda c: c.tokens)
        selected.append(smallest)
        total_tokens = smallest.tokens
        logger.warning(
            f"Pre-filter: forced inclusion of smallest chunk "
            f"({smallest.tokens} tokens)"
        )
    
    return selected, total_tokens


# ============================================================================
# LLM INTERACTION (UPDATED - sends code)
# ============================================================================

async def _call_prefilter_llm(
    user_query: str,
    chunks_with_code: List[SelectedChunk],
    project_map_str: str,
    max_chunks: int,
) -> List[Dict]:
    """
    Call Pre-filter LLM with actual code.
    
    NEW: Sends full code of each chunk, not just metadata.
    """
    # Build chunks list WITH CODE
    chunks_list_str = _format_chunks_with_code(chunks_with_code)
    
    # Build messages
    messages = [
        {
            "role": "system",
            "content": _get_prefilter_system_prompt(max_chunks)
        },
        {
            "role": "user",
            "content": _get_prefilter_user_prompt(
                user_query=user_query,
                project_map=project_map_str,
                chunks_list=chunks_list_str,
                max_chunks=max_chunks,
            )
        },
    ]
    
    model = get_model_for_role("pre_filter")
    
    try:
        response = await call_llm(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=2000,
        )
        
        if not response or not response.strip():
            logger.warning("Pre-filter: empty LLM response")
            return []
        
        return _parse_prefilter_response(response, max_chunks)
        
    except Exception as e:
        logger.error(f"Pre-filter LLM error: {e}", exc_info=True)
        return []


def _format_chunks_with_code(chunks: List[SelectedChunk]) -> str:
    """
    Format chunks WITH their actual code for LLM.
    
    Format:
    === CHUNK 1 ===
    ID: path/to/file.py:ClassName
    Type: class
    File: path/to/file.py
    Lines: 10-50
    Tokens: 200
    
    CODE:
    ```python
    class ClassName:
        def method(self):
            ...
    ```
    
    === CHUNK 2 ===
    ...
    """
    parts = []
    
    for i, chunk in enumerate(chunks, 1):
        part = f"""=== CHUNK {i} ===
ID: {chunk.chunk_id}
Type: {chunk.chunk_type}
File: {chunk.file_path}
Lines: {chunk.start_line}-{chunk.end_line}
Tokens: {chunk.tokens}

CODE:
{chunk.code}

"""
        parts.append(part)
    
    return "\n".join(parts)


def _get_prefilter_system_prompt(max_chunks: int) -> str:
    """System prompt for Pre-filter LLM"""
    return f"""You are a code analysis expert. Your task is to select the {max_chunks} most relevant code chunks for answering a user's question.

You will receive:
1. A user query about the codebase
2. A project map (file descriptions)
3. A list of code chunks WITH THEIR ACTUAL CODE

IMPORTANT: You can see the ACTUAL CODE of each chunk. Analyze the code carefully to determine relevance.

Your response must be a JSON object with this structure:
{{
    "selected_chunks": [
        {{
            "chunk_id": "path/to/file.py:ClassName",
            "relevance_score": 0.95,
            "reason": "Contains the main login logic that user asked about"
        }},
        ...
    ]
}}

Selection criteria:
1. Direct relevance to user's question (most important)
2. Contains code that answers or relates to the query
3. Dependencies that are needed to understand the main code
4. Utility functions used by relevant code

Return EXACTLY {max_chunks} chunks (or fewer if there aren't enough relevant ones).
Order by relevance_score (highest first).
Scores should be between 0.0 and 1.0.
IMPORTANT: Write the "reason" field in Russian language."""

def _get_prefilter_user_prompt(
    user_query: str,
    project_map: str,
    chunks_list: str,
    max_chunks: int,
) -> str:
    """User prompt for Pre-filter LLM"""
    return f"""USER QUERY:
{user_query}

PROJECT MAP:
{project_map if project_map else "[No project map available]"}

CODE CHUNKS TO ANALYZE:
{chunks_list}

Select the {max_chunks} most relevant chunks and return as JSON.
Remember: You can see the actual code - use it to make informed decisions!"""


# ============================================================================
# RESPONSE PARSING (unchanged)
# ============================================================================

def _parse_prefilter_response(response: str, max_chunks: int) -> List[Dict]:
    """Parse Pre-filter LLM response."""
    # Try to extract JSON
    json_str = _extract_json_from_response(response)
    
    if not json_str:
        logger.warning(f"Pre-filter: could not extract JSON from response")
        return _try_regex_extraction(response, max_chunks)
    
    try:
        json_str = _clean_json_string(json_str)
        result = json.loads(json_str)
        chunks = result.get("selected_chunks", [])
        
        if not chunks:
            logger.warning("Pre-filter: no 'selected_chunks' in parsed JSON")
            return _try_regex_extraction(response, max_chunks)
        
        return _normalize_chunks(chunks, max_chunks)
        
    except json.JSONDecodeError as e:
        logger.warning(f"Pre-filter JSON decode error: {e}")
        return _try_regex_extraction(response, max_chunks)


def _extract_json_from_response(response: str) -> Optional[str]:
    """Extract JSON object from LLM response"""
    # Strategy 1: JSON in markdown code block
    code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
    if code_block_match:
        content = code_block_match.group(1).strip()
        if content.startswith('{'):
            return content
    
    # Strategy 2: Brace matching
    start_idx = response.find('{')
    if start_idx != -1:
        json_str = _extract_by_brace_matching(response, start_idx)
        if json_str and '"selected_chunks"' in json_str:
            return json_str
    
    return None


def _extract_by_brace_matching(text: str, start_idx: int) -> Optional[str]:
    """Extract JSON by counting matching braces"""
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text[start_idx:], start=start_idx):
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\' and in_string:
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                return text[start_idx:i + 1]
    
    return None


def _clean_json_string(json_str: str) -> str:
    """Clean common JSON issues"""
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
    json_str = json_str.lstrip('\ufeff\u200b')
    return json_str.strip()


def _normalize_chunks(chunks: List[Dict], max_chunks: int) -> List[Dict]:
    """Normalize chunk data structure"""
    normalized = []
    
    for chunk in chunks[:max_chunks * 2]:
        chunk_id = (
            chunk.get("chunk_id") or
            chunk.get("id") or
            chunk.get("chunk")
        )
        
        if not chunk_id or ":" not in str(chunk_id):
            continue
        
        try:
            score = float(chunk.get("relevance_score", 0.5))
            score = max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            score = 0.5
        
        reason = str(chunk.get("reason", ""))[:200]
        
        normalized.append({
            "chunk_id": str(chunk_id),
            "relevance_score": score,
            "reason": reason,
        })
        
        if len(normalized) >= max_chunks:
            break
    
    normalized.sort(key=lambda x: x["relevance_score"], reverse=True)
    return normalized


def _try_regex_extraction(response: str, max_chunks: int) -> List[Dict]:
    """Fallback regex extraction"""
    pattern = r'"chunk_id"\s*:\s*"([^"]+)"[^}]*?"relevance_score"\s*:\s*([\d.]+)[^}]*?"reason"\s*:\s*"([^"]*)"'
    matches = re.findall(pattern, response, re.DOTALL)
    
    if matches:
        return [
            {
                "chunk_id": m[0],
                "relevance_score": float(m[1]),
                "reason": m[2],
            }
            for m in matches[:max_chunks]
        ]
    
    return []


# ============================================================================
# SELECTION HELPERS
# ============================================================================

def _get_selected_chunks(
    selected_ids: List[Dict],
    all_chunks: List[SelectedChunk],
) -> List[SelectedChunk]:
    """Get selected chunks by IDs, preserving LLM scores"""
    # Build lookup map
    chunks_map = {c.chunk_id: c for c in all_chunks}
    
    result = []
    for selection in selected_ids:
        chunk_id = selection.get("chunk_id", "")
        chunk = chunks_map.get(chunk_id)
        
        if chunk:
            # Update with LLM's assessment
            chunk.relevance_score = selection.get("relevance_score", 0.5)
            chunk.reason = selection.get("reason", chunk.reason)
            result.append(chunk)
    
    # Sort by relevance
    result.sort(key=lambda c: c.relevance_score, reverse=True)
    
    return result


def _apply_token_limit(
    chunks: List[SelectedChunk],
    max_tokens: int,
) -> tuple[List[SelectedChunk], bool, int]:
    """Apply token limit by pruning lowest relevance chunks (whole chunks only)"""
    total_tokens = sum(chunk.tokens for chunk in chunks)
    pruned = False
    pruned_count = 0
    
    # Remove lowest relevance chunks until under limit
    while total_tokens > max_tokens and len(chunks) > 1:
        removed = chunks.pop()  # Remove lowest relevance
        total_tokens -= removed.tokens
        pruned_count += 1
        pruned = True
        
        logger.info(
            f"Pre-filter: pruned chunk {removed.chunk_id} "
            f"({removed.tokens} tokens), total now: {total_tokens}"
        )
    
    if pruned:
        logger.warning(
            f"Pre-filter: pruned {pruned_count} chunks due to token limit. "
            f"Remaining: {len(chunks)}"
        )
    
    return chunks, pruned, pruned_count


def _fallback_selection(
    chunks: List[SelectedChunk],
    max_chunks: int,
) -> List[Dict]:
    """Fallback: select first N chunks if LLM fails"""
    return [
        {
            "chunk_id": c.chunk_id,
            "relevance_score": 0.5,
            "reason": "Fallback selection",
        }
        for c in chunks[:max_chunks]
    ]


def _empty_result() -> PreFilterResult:
    """Return empty result"""
    return PreFilterResult(
        selected_chunks=[],
        total_tokens=0,
        pruned=False,
        pruned_count=0,
        original_count=0,
    )
