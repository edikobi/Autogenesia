"""
ast-grep based code applier.
Uses SEARCH/REPLACE CHANGE BLOCK contract to modify source code.
"""
from __future__ import annotations
import re
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict
from ast_grep_py import SgRoot

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES: set[str] = {"python", "javascript", "typescript", "go", "java", "jsx", "tsx"}
SUPPORTED_CODE_EXTENSIONS: set[str] = {'.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.java'}

EXT_TO_LANG: Dict[str, str] = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'tsx',
    '.go': 'go',
    '.java': 'java'
}

@dataclass
class AstGrepPatch:
    """Dataclass representing a SEARCH/REPLACE patch for ast-grep."""
    file_path: str
    search_pattern: str = ""
    replace_code: str = ""
    language: str = "python"
    is_new_file: bool = False
    mode: str = "AST_REPLACE"
    
    # Compatibility fields for pipeline getattr
    target_class: Optional[str] = None
    target_method: Optional[str] = None
    target_function: Optional[str] = None
    insert_after: Optional[str] = None
    insert_before: Optional[str] = None
    replace_pattern: Optional[str] = None

    @property
    def code(self) -> str:
        """Alias for replace_code used by pipeline."""
        return self.replace_code

    @code.setter
    def code(self, value: str):
        """Setter for code alias."""
        self.replace_code = value

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "file_path": self.file_path,
            "search_pattern": self.search_pattern,
            "replace_code": self.replace_code,
            "language": self.language,
            "is_new_file": self.is_new_file,
            "mode": self.mode
        }

@dataclass
class ApplyResult:
    """Result of applying an AstGrepPatch."""
    success: bool
    new_content: Optional[str] = None
    message: str = ""
    changes_made: List[str] = field(default_factory=list)
    error_type: Optional[Any] = None
    broken_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "message": self.message,
            "changes_made": self.changes_made
        }

def classify_staging_error(error_message: str, mode: Optional[str] = None) -> "StagingErrorType":
    """
    Classify staging errors including ast-grep specific ones and legacy phrasings.
    
    Broadens classification so legacy-phrased messages never wrongly map to UNKNOWN.
    Keeps ast-grep-specific branches first (highest priority).
    """
    try:
        from app.agents.feedback_handler import StagingErrorType
    except ImportError:
        # Fallback if the lazy import fails, though in practice it succeeds.
        return None

    msg = error_message.lower()
    
    # --- ast-grep specific (Highest Priority) ---
    if "pattern_not_found" in msg or ("pattern" in msg and "not found" in msg):
        return StagingErrorType.PATTERN_NOT_FOUND
    if "ambiguous_pattern" in msg or ("ambiguous" in msg and ("match" in msg or "found" in msg or "locations" in msg)):
        return StagingErrorType.AMBIGUOUS_PATTERN
    if any(x in msg for x in ["ast parse error", "ast parsing failed", "parse error", "parsing failed"]):
        return StagingErrorType.AST_PARSE_ERROR
        
    # --- legacy missing-target params ---
    if ("missing" in msg or "required" in msg) and ("target_class" in msg or "target class" in msg):
        return StagingErrorType.MISSING_TARGET_CLASS
    if ("missing" in msg or "required" in msg) and ("target_method" in msg or "target method" in msg):
        return StagingErrorType.MISSING_TARGET_METHOD
    if ("missing" in msg or "required" in msg) and ("target_function" in msg or "target function" in msg):
        return StagingErrorType.MISSING_TARGET_FUNCTION

    # --- legacy not-found ---
    if "class" in msg and "not found" in msg:
        return StagingErrorType.CLASS_NOT_FOUND
    if "method" in msg and "not found" in msg:
        return StagingErrorType.METHOD_NOT_FOUND
    if "function" in msg and "not found" in msg:
        return StagingErrorType.FUNCTION_NOT_FOUND
    if "insert" in msg and "not found" in msg:
        return StagingErrorType.INSERT_PATTERN_NOT_FOUND
    if "replace" in msg and "pattern" in msg and "not found" in msg:
        return StagingErrorType.REPLACE_PATTERN_NOT_FOUND

    # --- legacy ambiguity / integrity / syntax ---
    if "ambiguous" in msg and ("replace" in msg or "pattern" in msg):
        return StagingErrorType.AMBIGUOUS_REPLACE_PATTERN
    if "integrity" in msg:
        return StagingErrorType.INTEGRITY_FAILURE
    if "syntax" in msg and ("fail" in msg or "invalid" in msg or "broken" in msg):
        return StagingErrorType.SYNTAX_VALIDATION_FAILED
    if "invalid mode" in msg or "unknown mode" in msg:
        return StagingErrorType.INVALID_MODE
    if "parser" in msg and "unavailable" in msg:
        return StagingErrorType.PARSER_UNAVAILABLE
        
    return StagingErrorType.UNKNOWN


class _AmbiguousMatch(Exception):
    """Internal exception for multiple matches."""
    pass

class AstGrepApplier:
    """Applies SEARCH/REPLACE patches using ast-grep."""
    SUPPORTED_CODE_EXTENSIONS = SUPPORTED_CODE_EXTENSIONS

    def __init__(self, **kwargs):
        """Initialize applier."""
        pass

    def apply_patch(self, patch: AstGrepPatch, existing_content: str) -> ApplyResult:
        """Apply an AstGrepPatch to existing content."""
        try:
            if patch.is_new_file or not patch.search_pattern.strip():
                return ApplyResult(success=True, new_content=patch.replace_code, message="NEW_FILE")

            root = SgRoot(existing_content, patch.language)
            node = root.root()
            
            try:
                target = self._find_target(node, patch.search_pattern, patch.language)
            except _AmbiguousMatch:
                return ApplyResult(success=False, message="AMBIGUOUS_PATTERN: pattern matched multiple locations")

            if target is None:
                first_line = next((l for l in patch.search_pattern.splitlines() if l.strip()), "empty pattern")
                return ApplyResult(success=False, message=f"PATTERN_NOT_FOUND: pattern not found: {first_line}")

            if not patch.replace_code.strip():
                edit = target.replace("")
                new_content = node.commit_edits([edit])
                return ApplyResult(success=True, new_content=new_content, message="OK")

            resolved = self._resolve_metavars(target, patch.replace_code)
            edit = target.replace(resolved)
            new_content = node.commit_edits([edit])
            
            return ApplyResult(success=True, new_content=new_content, message="OK")

        except Exception as e:
            logger.error(f"AstGrepApplier error: {e}", exc_info=True)
            return ApplyResult(success=False, message=f"AST parse error: {e}")

    def _find_target(self, node: Any, search_pattern: str, language: str) -> Any:
        """Find target node using multiple strategies."""
        # 1. Primary - literal find
        matches = node.find_all(pattern=search_pattern)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise _AmbiguousMatch()

        # 2. Strategy A - pattern + metavar filter
        # Detect $NAME in first line
        first_line = next((l for l in search_pattern.splitlines() if l.strip()), "")
        name_match = re.search(r'\$([A-Z_][A-Z0-9_]*)', first_line)
        if name_match:
            var_name = name_match.group(1)
            # Try to find literal identifier next to it in pattern
            # This is a heuristic for "find by pattern then filter by capture"
            pass 

        # 3. Strategy B - kind + name check
        kind_map = {
            "python": {"def": "function_definition", "class": "class_definition"},
            "javascript": {"function": "function_declaration", "class": "class_declaration"},
            "typescript": {"function": "function_declaration", "class": "class_declaration"},
            # [TSX FIX] TSX uses same AST node kinds as TypeScript
            "tsx": {"function": "function_declaration", "class": "class_declaration"},
        }
                
        words = first_line.split()
        if words and language in kind_map:
            kw = words[0]
            if kw in kind_map[language]:
                kind = kind_map[language][kw]
                if len(words) > 1:
                    target_id = words[1].split('(')[0].split(':')[0]
                    all_kinds = node.find_all(kind=kind)
                    for m in all_kinds:
                        # Check name field or text
                        m_name = m.field("name")
                        if (m_name and m_name.text() == target_id) or (target_id in m.text()):
                            return m

        return None

    def _resolve_metavars(self, node: Any, replace_code: str) -> str:
        """Resolve meta-variables in replacement code."""
        def sub(match):
            token = match.group(0)
            name = match.group(1)
            if token.startswith('$$$'):
                parts = node.get_multiple_matches(name)
                return "\n".join(p.text() for p in parts) if parts else token
            else:
                m = node.get_match(name)
                return m.text() if m else token

        return re.sub(r'\${1,3}([A-Z_][A-Z0-9_]*)', sub, replace_code)

    def _classify_error(self, error_msg: str) -> Any:
        """Delegate to module level classifier."""
        return classify_staging_error(error_msg)