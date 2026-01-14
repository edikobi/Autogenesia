# app/services/project_map_builder.py
"""
Project Map Builder - creates and maintains project map with AI descriptions.

Creates a flat map of ALL project files (code and non-code) with:
- File path, type, hash, tokens
- AI-generated descriptions for non-code files (README, configs, docs)
- Incremental updates (add, modify, delete, move detection)

The map is used by Pre-filter and Orchestrator to understand project structure.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from datetime import datetime

logger = logging.getLogger(__name__)


# ============== ВАЛИДАЦИЯ НАСТРОЕК ==============

def _validate_settings():
    """
    Проверяет наличие необходимых настроек в settings.py.
    Вызывается при импорте модуля.
    
    Raises:
        ImportError: Если отсутствуют необходимые настройки
    """
    from config.settings import cfg
    
    required_attrs = [
        'PROJECT_MAP_MAX_FILE_TOKENS',
        'PROJECT_MAP_DESCRIBE_MODEL',
    ]
    
    missing = []
    for attr in required_attrs:
        if not hasattr(cfg, attr):
            missing.append(attr)
    
    if missing:
        raise ImportError(
            f"Missing required settings in config/settings.py: {', '.join(missing)}\n"
            f"Please add:\n"
            f"  PROJECT_MAP_MAX_FILE_TOKENS = 30000\n"
            f"  PROJECT_MAP_DESCRIBE_MODEL = MODEL_NORMAL"
        )


# Валидация при импорте модуля
_validate_settings()

# Импорт cfg после валидации
from config.settings import cfg
from app.utils.token_counter import TokenCounter
from app.utils.file_types import FileTypeDetector


# ============== CONSTANTS ==============

PROJECT_MAP_FILENAME = "project_map.json"
PROJECT_MAP_MD_FILENAME = "project_map.md"

# Directories to ignore
IGNORE_DIRS: Set[str] = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ai-agent", "egg-info", ".eggs", ".tox", ".nox",
}

# Files to ignore
IGNORE_FILES: Set[str] = {
    ".env", ".DS_Store", "Thumbs.db", "NTUSER.DAT",
    "project_map.json", "project_map.md",
    "semantic_index.json", "compact_index.json", "compact_index.md",
}

# Binary extensions (skip entirely)
BINARY_EXTENSIONS: Set[str] = {
    ".dat", ".exe", ".dll", ".so", ".pyc", ".pyo", ".pyd",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tiff",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac", ".ogg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".o", ".a", ".lib", ".obj",
    ".class", ".jar", ".war",
    ".lock",
}

# Code file types (will get descriptions from semantic_index, not AI)
CODE_FILE_TYPES: Set[str] = {
    "code/python", "code/go", "code/javascript", "code/typescript",
    "code/java", "code/c", "code/cpp", "code/csharp", "code/rust",
    "code/ruby", "code/php", "code/swift", "code/kotlin", "code/scala",
}

# Non-code files that need AI descriptions
DESCRIBABLE_EXTENSIONS: Set[str] = {
    # Documentation
    ".md", ".rst", ".txt", ".adoc",
    # Configuration
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env.example", ".env.sample",
    # Build/CI
    ".dockerfile", ".dockerignore", ".gitignore",
    # Other text
    ".csv", ".xml", ".html", ".css", ".scss",
}

DESCRIBABLE_FILENAMES: Set[str] = {
    "readme", "license", "changelog", "contributing", "authors",
    "makefile", "dockerfile", "vagrantfile", "gemfile", "rakefile",
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "tsconfig.json", "webpack.config.js",
    ".gitignore", ".dockerignore", ".editorconfig",
}


# ============== DATA CLASSES ==============

@dataclass
class ProjectFileEntry:
    """Single file entry in project map."""
    path: str
    type: str  # File type from FileTypeDetector
    hash: str  # MD5 hash for change detection
    tokens: int  # Token count
    description: str = ""  # AI-generated or from semantic index
    is_code: bool = False  # True if it's a code file
    last_updated: str = ""  # ISO timestamp


@dataclass
class ProjectMap:
    """Complete project map."""
    root: str
    created_at: str
    updated_at: str
    total_files: int
    total_tokens: int
    files: List[ProjectFileEntry] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)  # AI description errors
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_files": self.total_files,
            "total_tokens": self.total_tokens,
            "files": [asdict(f) for f in self.files],
            "errors": self.errors,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectMap":
        # Фильтруем неизвестные поля при создании ProjectFileEntry
        known_fields = {
            'path', 'type', 'hash', 'tokens', 
            'description', 'is_code', 'last_updated'
        }
        
        files = []
        for f in data.get("files", []):
            # Оставляем только известные поля
            filtered = {k: v for k, v in f.items() if k in known_fields}
            
            # Обработка legacy: tokens_total → tokens
            if 'tokens_total' in f and 'tokens' not in filtered:
                filtered['tokens'] = f['tokens_total']
            
            files.append(ProjectFileEntry(**filtered))
        
        return cls(
            root=data.get("root", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            total_files=data.get("total_files", 0),
            total_tokens=data.get("total_tokens", 0),
            files=files,
            errors=data.get("errors", []),
        )


# ============== PROMPTS FOR AI DESCRIPTIONS ==============

DESCRIBE_FILE_SYSTEM_PROMPT = """You are a code analyst. Your task is to describe what a file does in a project.

You are given:
1. The file content
2. A compact map of code files in the project (for context)

Write a CONCISE but COMPLETE description (2-4 sentences, 50-150 words) explaining:
- What this file is for (primary purpose)
- Key functionality or components it contains
- How it relates to the project (if clear from context)

Rules:
- Be SPECIFIC and INFORMATIVE - avoid vague descriptions
- Use English
- Focus on PURPOSE and KEY FEATURES, not just listing contents
- If it's a config file, explain what it configures and key settings
- If it's documentation, summarize the main topics covered
- Include important details that would help someone understand this file's role

Output ONLY the description, no formatting, no quotes."""

DESCRIBE_FILE_USER_PROMPT = """Project code structure (for context):
<code_map>
{code_map}
</code_map>

File to describe: {file_path}
File type: {file_type}

<file_content>
{file_content}
</file_content>

Describe what this file does in the project:"""


# ============== MAIN CLASS ==============

class ProjectMapBuilder:
    """
    Builds and maintains project map with AI descriptions.
    
    Features:
    - Scans all files (code and non-code)
    - Gets descriptions for code files from semantic index
    - Generates AI descriptions for non-code files (README, configs, etc.)
    - Incremental updates (detects add/modify/delete/move)
    - Respects token limits (skips AI for files > 30k tokens)
    - Tracks errors from AI description generation
    """
    
    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.token_counter = TokenCounter()
        self.type_detector = FileTypeDetector()
        self.map_path = self.project_path / ".ai-agent" / PROJECT_MAP_FILENAME
        self.map_md_path = self.project_path / ".ai-agent" / PROJECT_MAP_MD_FILENAME
        
        # Ensure .ai-agent directory exists
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
    
    # ============== PUBLIC METHODS ==============
    
    async def build_map(self, code_map_context: str = "") -> ProjectMap:
        """
        Build complete project map from scratch.
        
        Args:
            code_map_context: Compact code map (from semantic index) for AI context
            
        Returns:
            ProjectMap with all files and descriptions
        """
        logger.info(f"Building project map for: {self.project_path}")
        
        # Step 1: Scan all files
        files = self._scan_all_files()
        logger.info(f"Found {len(files)} files")
        
        # Step 2: Load semantic index for code file descriptions
        code_descriptions = self._load_code_descriptions()
        
        # Step 3: Identify files needing AI descriptions
        files_to_describe: List[ProjectFileEntry] = []
        for file_entry in files:
            if file_entry.is_code:
                # Use description from semantic index
                file_entry.description = code_descriptions.get(
                    file_entry.path, 
                    "Code file (no description available)"
                )
            elif self._should_describe_with_ai(file_entry):
                if file_entry.tokens > cfg.PROJECT_MAP_MAX_FILE_TOKENS:
                    file_entry.description = f"File exceeds {cfg.PROJECT_MAP_MAX_FILE_TOKENS} tokens"
                else:
                    files_to_describe.append(file_entry)
        
        # Step 4: Generate AI descriptions asynchronously
        errors: List[Dict[str, str]] = []
        if files_to_describe:
            logger.info(f"Generating AI descriptions for {len(files_to_describe)} files...")
            errors = await self._generate_descriptions_batch(files_to_describe, code_map_context)
            
            if errors:
                logger.warning(f"AI description errors: {len(errors)} files failed")
        
        # Step 5: Create project map
        now = datetime.utcnow().isoformat() + "Z"
        project_map = ProjectMap(
            root=str(self.project_path),
            created_at=now,
            updated_at=now,
            total_files=len(files),
            total_tokens=sum(f.tokens for f in files),
            files=files,
            errors=errors,
        )
        
        # Step 6: Save
        self._save_map(project_map)
        
        logger.info(f"Project map built: {len(files)} files, {project_map.total_tokens} tokens")
        return project_map
    
    async def sync_map(self, code_map_context: str = "") -> ProjectMap:
        """
        Incrementally update project map.
        
        Detects:
        - New files (added)
        - Modified files (hash changed)
        - Deleted files (no longer exist)
        - Moved files (same hash, different path)
        
        Args:
            code_map_context: Compact code map for AI context
            
        Returns:
            Updated ProjectMap
        """
        existing_map = self._load_existing_map()
        
        if not existing_map:
            logger.info("No existing map found, building from scratch")
            return await self.build_map(code_map_context)
        
        logger.info(f"Syncing project map: {len(existing_map.files)} existing files")
        
        # Build lookup by path and hash
        existing_by_path: Dict[str, ProjectFileEntry] = {f.path: f for f in existing_map.files}
        existing_by_hash: Dict[str, ProjectFileEntry] = {f.hash: f for f in existing_map.files}
        
        # Scan current files
        current_files = self._scan_all_files()
        current_paths = {f.path for f in current_files}
        
        # Load code descriptions
        code_descriptions = self._load_code_descriptions()
        
        # Categorize changes
        new_files: List[ProjectFileEntry] = []
        modified_files: List[ProjectFileEntry] = []
        unchanged_files: List[ProjectFileEntry] = []
        
        for file_entry in current_files:
            if file_entry.path in existing_by_path:
                existing = existing_by_path[file_entry.path]
                if existing.hash == file_entry.hash:
                    # Unchanged - keep existing description
                    file_entry.description = existing.description
                    file_entry.last_updated = existing.last_updated
                    unchanged_files.append(file_entry)
                else:
                    # Modified - need new description
                    modified_files.append(file_entry)
            elif file_entry.hash in existing_by_hash:
                # Moved file - keep description from old location
                old_entry = existing_by_hash[file_entry.hash]
                file_entry.description = old_entry.description
                file_entry.last_updated = old_entry.last_updated
                unchanged_files.append(file_entry)
                logger.info(f"Detected move: {old_entry.path} -> {file_entry.path}")
            else:
                # New file
                new_files.append(file_entry)
        
        # Deleted files are simply not in current_files (automatic cleanup)
        deleted_count = len(existing_by_path) - len(current_paths & set(existing_by_path.keys()))
        if deleted_count > 0:
            logger.info(f"Detected {deleted_count} deleted files")
        
        # Process files needing descriptions
        files_to_describe: List[ProjectFileEntry] = []
        
        for file_entry in new_files + modified_files:
            if file_entry.is_code:
                file_entry.description = code_descriptions.get(
                    file_entry.path,
                    "Code file (no description available)"
                )
            elif self._should_describe_with_ai(file_entry):
                if file_entry.tokens > cfg.PROJECT_MAP_MAX_FILE_TOKENS:
                    file_entry.description = f"File exceeds {cfg.PROJECT_MAP_MAX_FILE_TOKENS} tokens"
                else:
                    files_to_describe.append(file_entry)
        
        # Generate AI descriptions
        errors: List[Dict[str, str]] = []
        if files_to_describe:
            logger.info(f"Generating AI descriptions for {len(files_to_describe)} new/modified files...")
            errors = await self._generate_descriptions_batch(files_to_describe, code_map_context)
            
            if errors:
                logger.warning(f"AI description errors: {len(errors)} files failed")
        
        # Combine all files
        all_files = unchanged_files + new_files + modified_files
        all_files.sort(key=lambda f: f.path)
        
        # Update timestamps
        now = datetime.utcnow().isoformat() + "Z"
        for f in new_files + modified_files:
            f.last_updated = now
        
        # Create updated map
        project_map = ProjectMap(
            root=str(self.project_path),
            created_at=existing_map.created_at,
            updated_at=now,
            total_files=len(all_files),
            total_tokens=sum(f.tokens for f in all_files),
            files=all_files,
            errors=errors,
        )
        
        # Save
        self._save_map(project_map)
        
        logger.info(
            f"Project map synced: {len(new_files)} new, {len(modified_files)} modified, "
            f"{deleted_count} deleted, {len(unchanged_files)} unchanged"
        )
        
        return project_map
    
    def get_compact_map(self) -> str:
        """
        Get compact project map as formatted string for prompts.
        
        Format:
        ```
        PROJECT MAP (42 files, 15000 tokens)
        
        app/main.py [python, 450 tok] - Entry point, FastAPI application setup
        app/services/auth.py [python, 1200 tok] - Authentication service with JWT
        README.md [markdown, 500 tok] - Project documentation and setup guide
        config/settings.yaml [yaml, 150 tok] - Application configuration
        ...
        ```
        """
        project_map = self._load_existing_map()
        
        if not project_map:
            return "PROJECT MAP: Not available (run indexing first)"
        
        lines = [
            f"PROJECT MAP ({project_map.total_files} files, {project_map.total_tokens} tokens)",
            "",
        ]
        
        for f in project_map.files:
            # Format type nicely
            type_short = f.type.split("/")[-1] if "/" in f.type else f.type
            
            # Truncate long descriptions
            desc = f.description or "No description"
            if len(desc) > 80:
                desc = desc[:77] + "..."
            
            lines.append(f"{f.path} [{type_short}, {f.tokens} tok] - {desc}")
        
        return "\n".join(lines)
    
    def load_map(self) -> Optional[ProjectMap]:
        """Load existing project map from disk."""
        return self._load_existing_map()
    
    # ============== PRIVATE METHODS ==============
    
    def _scan_all_files(self) -> List[ProjectFileEntry]:
        """Scan all files in project directory."""
        files: List[ProjectFileEntry] = []
        
        for dirpath, dirnames, filenames in os.walk(self.project_path):
            # Filter directories in place
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
            
            for filename in filenames:
                # Skip ignored files
                if filename in IGNORE_FILES or filename.startswith("."):
                    continue
                
                full_path = Path(dirpath) / filename
                
                # Skip binary files
                if full_path.suffix.lower() in BINARY_EXTENSIONS:
                    continue
                
                # Get relative path
                rel_path = str(full_path.relative_to(self.project_path))
                
                # Detect file type
                file_type = self.type_detector.detect(str(full_path))
                
                # Check if code file
                is_code = file_type in CODE_FILE_TYPES
                
                # Calculate hash
                file_hash = self._hash_file(full_path)
                if file_hash == "access_denied":
                    continue
                
                # Count tokens
                tokens = self._count_tokens(full_path)
                
                files.append(ProjectFileEntry(
                    path=rel_path,
                    type=file_type,
                    hash=file_hash,
                    tokens=tokens,
                    is_code=is_code,
                ))
        
        # Sort by path
        files.sort(key=lambda f: f.path)
        return files
    
    def _hash_file(self, path: Path) -> str:
        """Calculate MD5 hash of file."""
        hasher = hashlib.md5()
        try:
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (PermissionError, OSError):
            return "access_denied"
    
    def _count_tokens(self, path: Path) -> int:
        """Count tokens in file."""
        encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1251"]
        
        for encoding in encodings:
            try:
                text = path.read_text(encoding=encoding)
                return self.token_counter.count(text)
            except UnicodeDecodeError:
                continue
            except (PermissionError, OSError, MemoryError):
                return 0
        
        return 0
    
    def _should_describe_with_ai(self, file_entry: ProjectFileEntry) -> bool:
        """Check if file should get AI-generated description."""
        if file_entry.is_code:
            return False
        
        path = Path(file_entry.path)
        
        # Check extension
        if path.suffix.lower() in DESCRIBABLE_EXTENSIONS:
            return True
        
        # Check filename
        if path.name.lower() in DESCRIBABLE_FILENAMES:
            return True
        
        # Check if text file type
        if file_entry.type.startswith("text/") or file_entry.type.startswith("data/"):
            return True
        
        return False
    
    def _load_code_descriptions(self) -> Dict[str, str]:
        """Load code file descriptions from semantic index."""
        descriptions = {}
        
        # Try semantic_index.json first (dict format)
        semantic_path = self.project_path / ".ai-agent" / "semantic_index.json"
        if semantic_path.exists():
            try:
                with semantic_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                
                files_data = data.get("files", {})
                if isinstance(files_data, dict):
                    for file_path, file_info in files_data.items():
                        if isinstance(file_info, dict):
                            desc = file_info.get("description", "")
                            if desc:
                                descriptions[file_path] = desc
                    return descriptions
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not load semantic index: {e}")
        
        # Fallback to compact_index.json (list format)
        compact_path = self.project_path / ".ai-agent" / "compact_index.json"
        if compact_path.exists():
            try:
                with compact_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                
                files_data = data.get("files", [])
                
                if isinstance(files_data, list):
                    for file_info in files_data:
                        if isinstance(file_info, dict):
                            file_path = file_info.get("path", "")
                            desc = file_info.get("description", "")
                            if file_path and desc:
                                descriptions[file_path] = desc
                elif isinstance(files_data, dict):
                    for file_path, file_info in files_data.items():
                        if isinstance(file_info, dict):
                            desc = file_info.get("description", "")
                            if desc:
                                descriptions[file_path] = desc
                                
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not load compact index: {e}")
    
        return descriptions    
    
    async def _generate_descriptions_batch(
        self,
        files: List[ProjectFileEntry],
        code_map_context: str
    ) -> List[Dict[str, str]]:
        """
        Generate AI descriptions for multiple files asynchronously.
        
        Args:
            files: List of files to describe
            code_map_context: Compact code map for context
            
        Returns:
            List of errors: [{"file": "path", "error": "message"}, ...]
        """
        errors: List[Dict[str, str]] = []
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests
        
        async def describe_with_limit(file_entry: ProjectFileEntry) -> Optional[Dict[str, str]]:
            """Describe single file with concurrency limit."""
            async with semaphore:
                try:
                    description = await self._generate_single_description(file_entry, code_map_context)
                    file_entry.description = description
                    
                    # Check if description indicates failure
                    if description == "Description unavailable":
                        return {"file": file_entry.path, "error": "AI returned no description"}
                    
                    return None  # Success
                    
                except Exception as e:
                    file_entry.description = "Description unavailable"
                    error_msg = str(e)[:100]  # Truncate long errors
                    logger.warning(f"AI description failed for {file_entry.path}: {error_msg}")
                    return {"file": file_entry.path, "error": error_msg}
        
        # Run all tasks concurrently
        tasks = [describe_with_limit(f) for f in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect errors
        for i, result in enumerate(results):
            if isinstance(result, dict):
                # Error returned from describe_with_limit
                errors.append(result)
            elif isinstance(result, Exception):
                # Unexpected exception
                file_path = files[i].path if i < len(files) else "unknown"
                errors.append({"file": file_path, "error": str(result)[:100]})
        
        return errors
    
    async def _generate_single_description(
        self,
        file_entry: ProjectFileEntry,
        code_map_context: str
    ) -> str:
        """Generate AI description for a single file."""
        from app.llm.api_client import call_llm
        
        # Read file content
        full_path = self.project_path / file_entry.path
        
        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = full_path.read_text(encoding="latin-1")
            except Exception:
                return "Could not read file content"
        except Exception as e:
            return f"Read error: {str(e)[:50]}"
        
        # Truncate if very long (but under token limit)
        if len(content) > 50000:
            content = content[:50000] + "\n... [truncated]"
        
        # Build prompt
        messages = [
            {"role": "system", "content": DESCRIBE_FILE_SYSTEM_PROMPT},
            {"role": "user", "content": DESCRIBE_FILE_USER_PROMPT.format(
                code_map=code_map_context or "Not available",
                file_path=file_entry.path,
                file_type=file_entry.type,
                file_content=content,
            )},
        ]
        
        try:
            response = await call_llm(
                model=cfg.PROJECT_MAP_DESCRIBE_MODEL,
                messages=messages,
                temperature=0,
                max_tokens=300,
            )
            
            description = response.strip()
            description = description.strip('"\'')  # Убираем кавычки если есть
                        
            return description
            
        except Exception as e:
            logger.warning(f"AI description failed for {file_entry.path}: {e}")
            return "Description unavailable"
    
    def _load_existing_map(self) -> Optional[ProjectMap]:
        """Load existing project map from disk."""
        if not self.map_path.exists():
            return None
        
        try:
            with self.map_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return ProjectMap.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load existing map: {e}")
            return None
    
    def _save_map(self, project_map: ProjectMap) -> None:
        """Save project map to disk (JSON and MD formats)."""
        # Save JSON
        with self.map_path.open("w", encoding="utf-8") as f:
            json.dump(project_map.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Save MD (human-readable)
        md_content = self._format_map_as_markdown(project_map)
        with self.map_md_path.open("w", encoding="utf-8") as f:
            f.write(md_content)
        
        logger.info(f"Saved project map: {self.map_path}")
    
    def _format_map_as_markdown(self, project_map: ProjectMap) -> str:
        """Format project map as Markdown."""
        lines = [
            "# Project Map",
            "",
            f"**Root:** `{project_map.root}`",
            f"**Files:** {project_map.total_files}",
            f"**Total tokens:** {project_map.total_tokens}",
            f"**Updated:** {project_map.updated_at}",
            "",
        ]
        
        # Add errors section if any
        if project_map.errors:
            lines.extend([
                "## Errors",
                "",
                f"**{len(project_map.errors)} file(s) failed AI description:**",
                "",
            ])
            for err in project_map.errors[:10]:  # Show first 10
                lines.append(f"- `{err.get('file', 'unknown')}`: {err.get('error', 'Unknown error')}")
            if len(project_map.errors) > 10:
                lines.append(f"- ... and {len(project_map.errors) - 10} more")
            lines.append("")
        
        lines.extend([
            "## Files",
            "",
            "| Path | Type | Tokens | Description |",
            "|------|------|--------|-------------|",
        ])
        
        for f in project_map.files:
            type_short = f.type.split("/")[-1] if "/" in f.type else f.type
            desc = f.description or "-"
            # Escape pipe characters in description
            desc = desc.replace("|", "\\|")
            lines.append(f"| `{f.path}` | {type_short} | {f.tokens} | {desc} |")
        
        return "\n".join(lines)


# ============== CONVENIENCE FUNCTIONS ==============

async def build_project_map(project_path: str, code_map_context: str = "") -> ProjectMap:
    """Build project map from scratch."""
    builder = ProjectMapBuilder(project_path)
    return await builder.build_map(code_map_context)


async def sync_project_map(project_path: str, code_map_context: str = "") -> ProjectMap:
    """Incrementally sync project map."""
    builder = ProjectMapBuilder(project_path)
    return await builder.sync_map(code_map_context)


def get_project_map_for_prompt(project_path: str) -> str:
    """Get compact project map string for prompts."""
    builder = ProjectMapBuilder(project_path)
    return builder.get_compact_map()