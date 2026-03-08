from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LanguageAdapter(ABC):
    """Abstract base class for language-specific adapters."""
    
    @abstractmethod
    def lint(self, code: str, file_path: str, fix: bool = False) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Lint code and optionally fix issues.
        
        Args:
            code: Source code to lint
            file_path: Path to the file (for context)
            fix: Whether to apply auto-fixes
            
        Returns:
            Tuple of (fixed_content, lint_issues)
            - fixed_content: The content after fixes (or original if no fixes)
            - lint_issues: List of remaining lint issues as dicts with keys:
              'line', 'column', 'message', 'severity' (error/warning)
        """
        pass
    
    @abstractmethod
    def compile_check(self, code: str, file_path: str, project_root: Optional[Path] = None) -> Dict[str, Any]:
        """
        Check if code compiles/is valid.
        
        Args:
            code: Source code to check
            file_path: Path to the file
            project_root: Optional project root for dependency resolution
            
        Returns:
            Dict with keys:
            - 'success': bool
            - 'stderr': str (error output if any)
            - 'stdout': str (standard output if any)
            - 'exit_code': int
        """
        pass
    
    @abstractmethod
    def get_language_name(self) -> str:
        """Returns the language name (e.g., 'javascript', 'go', 'java')."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the adapter tools are available."""
        pass
    
    @abstractmethod
    def format_code(self, code: str, file_path: str) -> str:
        """Format code using language-specific formatter."""
        pass

    @abstractmethod
    def compile_check_with_deps(self, files: List[Tuple[str, str]], project_root: Optional[Path] = None) -> Dict[str, Any]:
        """
        Check compilation of multiple related files together.
        Default implementation compiles each file separately.
        Subclasses can override for languages that support multi-file compilation.
        
        Args:
            files: List of (code, file_path) tuples
            project_root: Optional project root path
            
        Returns:
            Dict with keys:
            - 'success': bool (True if all files compiled successfully)
            - 'results': List of individual file results
            - 'stderr': str (combined error output)
            - 'stdout': str (combined standard output)
            - 'exit_code': int (0 if all success, 1 otherwise)
        """
        all_results = []
        all_success = True
        all_stderr = []
        all_stdout = []
        
        for code, file_path in files:
            try:
                result = self.compile_check(code, file_path, project_root)
                all_results.append({
                    'file_path': file_path,
                    **result
                })
                
                if not result.get('success', False):
                    all_success = False
                
                if result.get('stderr'):
                    all_stderr.append(f"[{file_path}] {result['stderr']}")
                if result.get('stdout'):
                    all_stdout.append(f"[{file_path}] {result['stdout']}")
            
            except Exception as e:
                all_success = False
                all_stderr.append(f"[{file_path}] Error: {str(e)}")
                all_results.append({
                    'file_path': file_path,
                    'success': False,
                    'stderr': str(e),
                    'exit_code': -1,
                })
        
        return {
            'success': all_success,
            'results': all_results,
            'stderr': '\n'.join(all_stderr),
            'stdout': '\n'.join(all_stdout),
            'exit_code': 0 if all_success else 1,
        }


class AdapterManager:
    """Centralized manager for language-specific adapters. Auto-discovers and caches adapter instances.
    Provides unified interface for linting, formatting, and compiling code in any supported language.
    
    Implements check→fix→recheck→rollback pattern:
    1. Check original code for errors
    2. If errors found and fix=True, attempt auto-fix
    3. Re-check fixed code
    4. If still broken, rollback to original and report original errors
    """
    
    _instance: Optional['AdapterManager'] = None
    
    EXTENSION_TO_LANGUAGE: Dict[str, str] = {
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.mjs': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.go': 'go',
        '.java': 'java',
    }
    
    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root
        self._adapters: Dict[str, LanguageAdapter] = {}
        self._register_default_adapters()
    
    @classmethod
    def get_instance(cls, project_root: Optional[Path] = None) -> 'AdapterManager':
        if cls._instance is None:
            cls._instance = cls(project_root)
        elif project_root is not None and cls._instance.project_root != project_root:
            cls._instance.project_root = project_root
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None
    
    def _register_default_adapters(self) -> None:
        """Register default language adapters."""
        # JavaScript/TypeScript
        try:
            from app.services.js_ts_adapter import JsTsAdapter
            adapter = JsTsAdapter(project_root=self.project_root)
            if adapter.is_available():
                self._adapters['javascript'] = adapter
                self._adapters['typescript'] = adapter
                logger.info("JsTsAdapter registered for javascript/typescript")
        except ImportError as e:
            logger.debug(f"JsTsAdapter not available: {e}")
        except Exception as e:
            logger.warning(f"Failed to register JsTsAdapter: {e}")
        
        # Go
        try:
            from app.services.go_adapter import GoAdapter
            adapter = GoAdapter(project_root=self.project_root)
            if adapter.is_available():
                self._adapters['go'] = adapter
                logger.info("GoAdapter registered for go")
        except ImportError as e:
            logger.debug(f"GoAdapter not available: {e}")
        except Exception as e:
            logger.warning(f"Failed to register GoAdapter: {e}")
        
        # Java
        try:
            from app.services.java_adapter import JavaAdapter
            adapter = JavaAdapter(project_root=self.project_root)
            if adapter.is_available():
                self._adapters['java'] = adapter
                logger.info("JavaAdapter registered for java")
        except ImportError as e:
            logger.debug(f"JavaAdapter not available: {e}")
        except Exception as e:
            logger.warning(f"Failed to register JavaAdapter: {e}")
    
    
    def register_adapter(self, language: str, adapter: LanguageAdapter) -> None:
        """Register a language adapter."""
        self._adapters[language] = adapter
    
    def get_adapter(self, language: str) -> Optional[LanguageAdapter]:
        """Get adapter for a specific language."""
        return self._adapters.get(language)
    
    def get_adapter_for_file(self, file_path: str) -> Optional[LanguageAdapter]:
        """Get adapter for a file based on its extension."""
        extension = Path(file_path).suffix.lower()
        language = self.EXTENSION_TO_LANGUAGE.get(extension)
        if language:
            return self.get_adapter(language)
        return None
    
    def get_language_for_file(self, file_path: str) -> Optional[str]:
        """Get language name for a file based on its extension."""
        extension = Path(file_path).suffix.lower()
        return self.EXTENSION_TO_LANGUAGE.get(extension)
    
    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions that have registered adapters."""
        supported = []
        for ext, language in self.EXTENSION_TO_LANGUAGE.items():
            if self.get_adapter(language) is not None:
                supported.append(ext)
        return supported
    
    def is_supported(self, file_path: str) -> bool:
        """Check if file type is supported by a registered adapter."""
        return self.get_adapter_for_file(file_path) is not None
    
    def lint_file(self, code: str, file_path: str, fix: bool = False) -> Tuple[str, List[Dict]]:
        """
        Lint a file using the appropriate adapter.
        
        Args:
            code: Source code to lint
            file_path: Path to the file
            fix: Whether to apply auto-fixes
            
        Returns:
            Tuple of (fixed_code, issues_list)
        """
        original_code = code
        
        # Get adapter for this file
        adapter = self.get_adapter_for_file(file_path)
        if not adapter:
            language = self.get_language_for_file(file_path)
            logger.warning(f"No adapter available for {file_path} (language: {language})")
            return code, []
        
        try:
            # Call the adapter's lint method
            fixed_code, issues = adapter.lint(code, file_path, fix=fix)
            
            # Validate fixed code
            if not fixed_code or not fixed_code.strip():
                logger.warning(f"Adapter returned empty code for {file_path}, reverting to original")
                return original_code, issues
            
            # Rollback if code quality worsened
            if fix and fixed_code != code:
                # Re-check the fixed code without fixing
                _, new_issues = adapter.lint(fixed_code, file_path, fix=False)
                _, original_issues = adapter.lint(original_code, file_path, fix=False)
                
                if len(new_issues) > len(original_issues):
                    logger.warning(
                        f"Code quality worsened after fix ({len(new_issues)} vs {len(original_issues)} issues), "
                        f"rolling back for {file_path}"
                    )
                    return original_code, original_issues
            
            return fixed_code, issues
        
        except Exception as e:
            logger.error(f"Linting failed for {file_path}: {e}", exc_info=True)
            return original_code, []
    
    
    def format_file(self, code: str, file_path: str) -> str:
        """Format a file using the appropriate adapter."""
        adapter = self.get_adapter_for_file(file_path)
        if adapter is None:
            return code
        return adapter.format_code(code, file_path)
    
    def compile_check(self, code: str, file_path: str) -> Dict:
        """
        Check if code compiles/parses without errors.
        
        Returns:
            Dict with keys: success, stdout, stderr, exit_code, language
        """
        adapter = self.get_adapter_for_file(file_path)
        if adapter is None:
            return {
                'success': True,
                'stdout': '',
                'stderr': '',
                'exit_code': 0,
                'language': 'unknown'
            }
        
        try:
            # Pass project_root to adapter
            result = adapter.compile_check(code, file_path, project_root=self.project_root)
            result['language'] = adapter.get_language_name()
            return result
        except Exception as e:
            logger.error(f"Compile check failed for {file_path}: {e}")
            return {
                'success': False,
                'stdout': '',
                'stderr': str(e),
                'exit_code': -1,
                'language': adapter.get_language_name() if adapter else 'unknown'
            }
            
    def compile_check_with_deps(self, files: List[Tuple[str, str, Optional[str]]]) -> Dict[str, Any]:
        """
        Check compilation of multiple files, grouping by language.
        Each tuple is (code, file_path, language_hint).
        
        Args:
            files: List of (code, file_path, language_hint) tuples.
                language_hint is optional and can be None.
            
        Returns:
            Dict with keys:
            - 'success': bool (True if all files compiled successfully)
            - 'by_language': Dict mapping language to compilation result
            - 'stderr': str (combined error output from all languages)
            - 'failed_files': List of file paths that failed compilation
        """
        # Group files by language
        files_by_language: Dict[str, List[Tuple[str, str]]] = {}
        
        for item in files:
            code, file_path = item[0], item[1]
            language_hint = item[2] if len(item) > 2 else None
            
            try:
                # Determine language from hint or file extension
                language = language_hint
                if not language:
                    language = self.get_language_for_file(file_path)
                
                if not language:
                    logger.warning(f"Cannot determine language for {file_path}, skipping")
                    continue
                
                if language not in files_by_language:
                    files_by_language[language] = []
                
                files_by_language[language].append((code, file_path))
            
            except Exception as e:
                logger.warning(f"Error grouping file {file_path}: {e}")
        
        # Compile each language group
        all_results: Dict[str, Dict[str, Any]] = {}
        all_stderr = []
        failed_files = []
        overall_success = True
        
        for language, language_files in files_by_language.items():
            try:
                adapter = self.get_adapter(language)
                
                if adapter:
                    # Use multi-file compilation if available
                    result = adapter.compile_check_with_deps(language_files, self.project_root)
                    all_results[language] = result
                    
                    if not result.get('success', False):
                        overall_success = False
                        # Collect failed files
                        for file_result in result.get('results', []):
                            if not file_result.get('success', False):
                                failed_files.append(file_result.get('file_path', 'unknown'))
                    
                    if result.get('stderr'):
                        all_stderr.append(f"[{language}]\n{result['stderr']}")
                else:
                    logger.warning(f"No adapter available for language: {language}")
                    # Mark all files of this language as failed
                    for code, file_path in language_files:
                        failed_files.append(file_path)
                    overall_success = False
                    all_stderr.append(f"[{language}] No adapter available")
            
            except Exception as e:
                logger.error(f"Error compiling {language} files: {e}")
                overall_success = False
                all_stderr.append(f"[{language}] Error: {str(e)}")
                for code, file_path in language_files:
                    failed_files.append(file_path)
        
        return {
            'success': overall_success,
            'by_language': all_results,
            'stderr': '\n'.join(all_stderr),
            'failed_files': failed_files,
        }
