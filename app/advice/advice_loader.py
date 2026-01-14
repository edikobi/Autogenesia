# app/advice/advice_loader.py
"""
Advice Loader - Manages loading and retrieval of methodological advices.

The advice system provides thinking frameworks for the Orchestrator.
Advices are NOT instructions "what to do" but frameworks "how to think".

Architecture:
- advice_catalog.json: Contains IDs, names, and descriptions (loaded into prompt)
- advice_content.json: Contains full advice texts (loaded on demand via tool)

IMPORTANT: This module is designed to avoid circular imports.
It does NOT import from prompt_templates or orchestrator.
"""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class AdviceSummary:
    """Summary of an advice for the catalog (shown in prompt)"""
    id: str
    name: str
    category: str
    description: str
    when_to_use: str


@dataclass
class AdviceCategory:
    """Category metadata"""
    name: str
    description: str
    applies_to: List[str]  # ["ask", "new_project"]


# ============================================================================
# ADVICE SYSTEM
# ============================================================================

class AdviceSystem:
    """
    Manages advice loading and retrieval.
    
    Initialization loads the catalog into memory.
    Full advice content is loaded on demand via get_advice().
    
    Thread-safe for read operations after initialization.
    """
    
    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize the advice system.
        
        Args:
            base_path: Path to advice directory. If None, uses default.
        """
        if base_path is None:
            # Default to app/advice/ directory
            base_path = Path(__file__).parent
        else:
            base_path = Path(base_path)
        
        self.base_path = base_path
        self.catalog: Dict[str, AdviceSummary] = {}
        self.categories: Dict[str, AdviceCategory] = {}
        self.content_cache: Dict[str, str] = {}
        self._content_data: Optional[Dict[str, Any]] = None
        self._initialized = False
        
        # Search indices
        self.name_to_id: Dict[str, str] = {}  # normalized name -> ID
        self.search_index: List[Tuple[str, str, str]] = []  # (id, normalized_name, description)
        
        self._load_catalog()
    
    def _load_catalog(self) -> None:
        """Load advice catalog from JSON file"""
        catalog_path = self.base_path / "advice_catalog.json"
        
        if not catalog_path.exists():
            logger.warning(f"Advice catalog not found at {catalog_path}")
            self._initialized = True
            return
        
        try:
            with open(catalog_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Load categories
            for cat_id, cat_data in data.get('categories', {}).items():
                self.categories[cat_id] = AdviceCategory(
                    name=cat_data['name'],
                    description=cat_data['description'],
                    applies_to=cat_data.get('applies_to', ['ask', 'new_project'])
                )
            
            # Load advice summaries and build search indices
            for advice_data in data.get('advices', []):
                advice = AdviceSummary(
                    id=advice_data['id'],
                    name=advice_data['name'],
                    category=advice_data['category'],
                    description=advice_data['description'],
                    when_to_use=advice_data.get('when_to_use', '')
                )
                self.catalog[advice.id] = advice
                
                # Build search indices
                normalized_name = self._normalize_text(advice.name)
                self.name_to_id[normalized_name] = advice.id
                
                normalized_desc = self._normalize_text(advice.description)
                self.search_index.append((
                    advice.id,
                    normalized_name,
                    normalized_desc
                ))
            
            self._initialized = True
            logger.info(f"AdviceSystem: Loaded {len(self.catalog)} advices in {len(self.categories)} categories")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse advice catalog JSON: {e}")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to load advice catalog: {e}")
            self._initialized = True
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for search (lowercase, remove special chars)"""
        # Convert to lowercase
        text = text.lower()
        # Remove special characters, keep only alphanumeric and spaces
        text = re.sub(r'[^\w\s-]', '', text)
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def _load_content(self) -> None:
        """Lazy load advice content from JSON file"""
        if self._content_data is not None:
            return
        
        content_path = self.base_path / "advice_content.json"
        
        if not content_path.exists():
            logger.warning(f"Advice content file not found at {content_path}")
            self._content_data = {}
            return
        
        try:
            with open(content_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._content_data = data.get('advices', {})
            logger.debug(f"AdviceSystem: Loaded content for {len(self._content_data)} advices")
        except Exception as e:
            logger.error(f"Failed to load advice content: {e}")
            self._content_data = {}
    
    def find_advice(self, query: str) -> List[Tuple[str, str, float]]:
        """
        Search for advices by ID, name, or description.
        
        Args:
            query: Search query (can be ID, name, or keywords)
            
        Returns:
            List of (advice_id, advice_name, match_score) sorted by relevance
        """
        if not query or not query.strip():
            return []
        
        query = self._normalize_text(query)
        query_words = query.split()
        results = []
        
        for advice_id, name, desc in self.search_index:
            score = 0.0
            
            # Exact ID match (highest priority)
            if advice_id.lower() == query.upper():  # IDs are uppercase
                score = 10.0
            # Partial ID match
            elif advice_id.lower().startswith(query):
                score = 8.0
            # Contains ID
            elif query in advice_id.lower():
                score = 6.0
            
            # Exact name match
            elif name == query:
                score = max(score, 9.0)
            # Contains entire name
            elif query in name:
                score = max(score, 7.0)
            # Contains words from name
            else:
                name_words = name.split()
                matched_words = sum(1 for q_word in query_words if any(q_word in n_word or n_word in q_word for n_word in name_words))
                if matched_words > 0:
                    score = max(score, 5.0 + matched_words * 0.5)
            
            # Search in description
            if score < 5.0:  # Only search in description if no good match yet
                desc_words = desc.split()
                matched_desc_words = sum(1 for q_word in query_words if any(q_word in d_word or d_word in q_word for d_word in desc_words))
                if matched_desc_words > 0:
                    score = max(score, 3.0 + matched_desc_words * 0.3)
            
            # Search in when_to_use
            if advice_id in self.catalog and self.catalog[advice_id].when_to_use:
                when_to_use_norm = self._normalize_text(self.catalog[advice_id].when_to_use)
                when_words = when_to_use_norm.split()
                matched_when_words = sum(1 for q_word in query_words if any(q_word in w_word or w_word in q_word for w_word in when_words))
                if matched_when_words > 0:
                    score = max(score, 2.0 + matched_when_words * 0.2)
            
            if score > 0:
                advice_name = self.catalog[advice_id].name if advice_id in self.catalog else advice_id
                results.append((advice_id, advice_name, score))
        
        # Sort by score descending
        results.sort(key=lambda x: x[2], reverse=True)
        return results
    
    def get_advice(self, advice_id_or_query: str) -> Optional[str]:
        """
        Get full advice content by ID or search query.
        
        Args:
            advice_id_or_query: Advice ID (e.g., "ADV-G01") or search query
            
        Returns:
            Full advice content as string, or None if not found
        """
        if not advice_id_or_query or not advice_id_or_query.strip():
            return None
        
        advice_id_or_query = advice_id_or_query.strip()
        
        # Try direct ID lookup first (fast path)
        aid_upper = advice_id_or_query.upper()
        if aid_upper in self.catalog:
            # Found by exact ID
            return self._get_advice_by_id(aid_upper)
        
        # Try normalized name lookup
        normalized_query = self._normalize_text(advice_id_or_query)
        if normalized_query in self.name_to_id:
            # Found by exact normalized name
            actual_id = self.name_to_id[normalized_query]
            return self._get_advice_by_id(actual_id)
        
        # Try search
        search_results = self.find_advice(advice_id_or_query)
        if search_results:
            # Use the best match
            best_match_id = search_results[0][0]
            return self._get_advice_by_id(best_match_id)
        
        logger.warning(f"Advice not found for query: {advice_id_or_query}")
        return None
    
    def _get_advice_by_id(self, advice_id: str) -> Optional[str]:
        """Internal method to get advice content by ID"""
        # Check cache first
        if advice_id in self.content_cache:
            return self.content_cache[advice_id]
        
        # Load content file if needed
        self._load_content()
        
        # Get from content data
        if self._content_data is None:
            return None
            
        advice_data = self._content_data.get(advice_id)
        if advice_data is None:
            logger.warning(f"Advice {advice_id} not found in content file")
            return None
        
        # Extract content
        content = advice_data.get('content', '')
        
        # Cache and return
        self.content_cache[advice_id] = content
        return content
    
    def get_multiple_advices(self, advice_queries: List[str]) -> Dict[str, str]:
        """
        Get multiple advices at once using IDs or search queries.
        
        Args:
            advice_queries: List of advice IDs or search queries
            
        Returns:
            Dict mapping ID to content (only found advices)
        """
        result = {}
        for query in advice_queries:
            content = self.get_advice(query)
            if content:
                # Find the actual ID that was used
                if query.upper() in self.catalog:
                    result[query.upper()] = content
                else:
                    # Find which ID was actually matched
                    search_results = self.find_advice(query)
                    if search_results:
                        result[search_results[0][0]] = content
        return result
    
    def is_available(self) -> bool:
        """Check if advice system has loaded advices"""
        return self._initialized and len(self.catalog) > 0


# ============================================================================
# GLOBAL INSTANCE MANAGEMENT
# ============================================================================

_ADVICE_SYSTEM: Optional[AdviceSystem] = None


def init_advice_system(base_path: Optional[str] = None) -> AdviceSystem:
    """
    Initialize the global advice system.
    """
    global _ADVICE_SYSTEM
    if _ADVICE_SYSTEM is None:
        _ADVICE_SYSTEM = AdviceSystem(base_path)
    return _ADVICE_SYSTEM


def get_advice_system() -> AdviceSystem:
    """
    Get the global advice system instance.
    """
    global _ADVICE_SYSTEM
    if _ADVICE_SYSTEM is None:
        _ADVICE_SYSTEM = AdviceSystem()
    return _ADVICE_SYSTEM


def get_catalog_for_prompt(mode: str = "ask") -> str:
    """
    Generate catalog text for inclusion in Orchestrator's system prompt.
    
    Args:
        mode: Either "ask" (existing project) or "new_project"
        
    Returns:
        Formatted catalog string
        
    NOTE: Methodology is in the main prompt. This only provides the catalog listing.
    """
    try:
        system = get_advice_system()
    except Exception as e:
        logger.error(f"Failed to get advice system: {e}")
        return ""
    
    if not system.catalog:
        return ""  # Return empty if no advices available
    
    lines = []
    
    # === SIMPLE CATALOG HEADER ===
    lines.append("─────────────────────────────────────────────────────────────")
    lines.append("ADVICE CATALOG (use get_advice tool to load)")
    lines.append("─────────────────────────────────────────────────────────────")
    lines.append("")
    lines.append("📢 СОВЕТ: Теперь вы можете искать советы не только по ID, но и по названию!")
    lines.append("Примеры: get_advice(\"ADV-G01\") или get_advice(\"Bug Hunting\") или get_advice(\"security audit\")")
    lines.append("")
    
    # Group by category, filter by mode
    by_category: Dict[str, List[AdviceSummary]] = {}
    for advice in system.catalog.values():
        cat = advice.category
        if cat in system.categories:
            if mode not in system.categories[cat].applies_to:
                continue
        
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(advice)
    
    # Define category order
    category_order = ["general", "existing_project", "new_project"]
    
    # Output by category
    for cat_id in category_order:
        if cat_id not in by_category:
            continue
        
        advices = by_category[cat_id]
        cat_info = system.categories.get(cat_id)
        cat_name = cat_info.name if cat_info else cat_id.replace("_", " ").title()
        
        lines.append(f"### {cat_name}")
        
        for adv in sorted(advices, key=lambda x: x.id):
            lines.append(f"• **{adv.id}** — {adv.name}")
            lines.append(f"  {adv.description}")
            if adv.when_to_use:
                lines.append(f"  📌 Когда использовать: {adv.when_to_use}")
        lines.append("")
    
    return "\n".join(lines)


# ============================================================================
# TOOL EXECUTION
# ============================================================================

def execute_get_advice(advice_queries: str) -> str:
    """
    Execute the get_advice tool with enhanced search capabilities.
    
    This is called by ToolExecutor when Orchestrator requests advice.
    
    Args:
        advice_queries: Single query or comma-separated queries (e.g., "ADV-G01", 
                       "Bug Hunting", "security,performance", "ADV-G01,ADV-E01")
        
    Returns:
        XML-formatted response with advice content
    """
    try:
        system = get_advice_system()
    except Exception as e:
        return f"""<advice_error>
<message>Advice system initialization failed: {e}</message>
</advice_error>"""
    
    if not system.is_available():
        return """<advice_error>
<message>Advice system not available (no advices loaded)</message>
</advice_error>"""
    
    # Parse queries
    queries = [q.strip() for q in advice_queries.split(",") if q.strip()]
    
    if not queries:
        return """<advice_error>
<message>No advice queries provided</message>
<hint>Use format: get_advice("ADV-G01") or get_advice("Bug Hunting") or get_advice("ADV-G01,ADV-E01")</hint>
<available_ids>""" + ", ".join(sorted(system.catalog.keys())) + """</available_ids>
</advice_error>"""
    
    results = []
    not_found = []
    search_results_info = []
    
    for query in queries:
        # Try to get advice
        content = system.get_advice(query)
        
        if content:
            # Find which advice was actually matched
            search_results = system.find_advice(query)
            if search_results:
                matched_id, matched_name, score = search_results[0]
                
                # Get metadata from catalog
                summary = system.catalog.get(matched_id)
                actual_name = summary.name if summary else matched_id
                
                results.append(f"""<advice id="{matched_id}" name="{actual_name}" matched_query="{query}">
{content}
</advice>""")
                
                # Add to search results info
                search_results_info.append(f"• Query: '{query}' → Matched: {matched_id} ({actual_name}) with score: {score:.1f}")
            else:
                # Should not happen if content was found
                not_found.append(query)
        else:
            not_found.append(query)
    
    # Build response
    output_parts = []
    
    # Add search summary if we have info
    if search_results_info:
        output_parts.append("<search_summary>")
        output_parts.append("<note>Advice search results:</note>")
        output_parts.extend(search_results_info)
        output_parts.append("</search_summary>")
    
    if results:
        output_parts.append("<loaded_advices>")
        output_parts.append(f"<count>{len(results)}</count>")
        output_parts.append("<note>Adapt these frameworks to your specific context. Skip inapplicable sections.</note>")
        output_parts.extend(results)
        output_parts.append("</loaded_advices>")
    
    if not_found:
        # Try to provide suggestions for not found queries
        suggestions = []
        for nf in not_found:
            possible_matches = system.find_advice(nf)
            if possible_matches:
                suggestions.append(f"Query '{nf}' not found. Did you mean: " + 
                                 ", ".join([f"{id} ({name})" for id, name, _ in possible_matches[:3]]))
            else:
                suggestions.append(f"Query '{nf}' not found. No similar advices found.")
        
        available = ", ".join(sorted(system.catalog.keys()))
        output_parts.append(f"""<advice_warning>
<not_found>{", ".join(not_found)}</not_found>
<suggestions>{" ".join(suggestions)}</suggestions>
<available_ids>{available}</available_ids>
</advice_warning>""")
    
    if not results and not_found:
        # Show top advices as suggestions
        all_advices = [(id, system.catalog[id].name) for id in sorted(system.catalog.keys())]
        top_suggestions = all_advices[:5]  # Show top 5
        
        return f"""<advice_error>
<message>No advices found for queries: {", ".join(not_found)}</message>
<top_suggestions>{", ".join([f"{id} ({name})" for id, name in top_suggestions])}</top_suggestions>
<available_ids>{", ".join(sorted(system.catalog.keys()))}</available_ids>
<hint>Check the ADVICE CATALOG in your system prompt for available advices</hint>
<hint>You can search by: ID (e.g., "ADV-G01"), name (e.g., "Bug Hunting"), or keywords (e.g., "security audit")</hint>
</advice_error>"""
    
    return "\n".join(output_parts)