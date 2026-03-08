from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import json
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from app.services.language_adapter import LanguageAdapter

logger = logging.getLogger(__name__)


class GoAdapter(LanguageAdapter):
    """Adapter for Go files."""
    
    def __init__(self, project_root: Optional[Path] = None):
        """Initialize Go adapter and check tool availability."""
        self.project_root = project_root
        self._go_available = shutil.which('go') is not None
        self._golangci_lint_available = shutil.which('golangci-lint') is not None
        self._goimports_available = shutil.which('goimports') is not None
        self._gofmt_available = shutil.which('gofmt') is not None
        
        logger.debug(
            f"GoAdapter initialized: "
            f"go={self._go_available}, golangci-lint={self._golangci_lint_available}, "
            f"goimports={self._goimports_available}, gofmt={self._gofmt_available}"
        )
    
    def get_language_name(self) -> str:
        """Return the language name."""
        return "go"
    
    def is_available(self) -> bool:
        """Check if the adapter is available."""
        return self._go_available
    
    def lint(self, code: str, file_path: str, fix: bool = False) -> Tuple[str, List[Dict]]:
        """
        Lint code using go vet or golangci-lint.
        
        Args:
            code: Source code to lint
            file_path: Path to the file (for context)
            fix: Whether to apply auto-fixes
            
        Returns:
            Tuple of (fixed_code, issues_list)
        """
        fixed_code = code
        issues = []
        
        if not self._go_available:
            logger.warning("go not available, skipping lint")
            return code, []
        
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp()
            
            # Write code to temp file using just the filename
            temp_file = Path(temp_dir) / Path(file_path).name
            temp_file.write_text(code, encoding='utf-8')
            
            # Apply fixes if requested
            if fix and self._goimports_available:
                try:
                    result = subprocess.run(
                        ['goimports', '-w', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=temp_dir,
                    )
                    # Read back fixed code
                    fixed_code = temp_file.read_text(encoding='utf-8')
                except Exception as e:
                    logger.warning(f"goimports failed: {e}")
            
            # Run linter to get issues
            if self._golangci_lint_available:
                try:
                    result = subprocess.run(
                        ['golangci-lint', 'run', '--out-format=json', '--no-config', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=temp_dir,
                    )
                    
                    if result.stdout.strip():
                        try:
                            lint_output = json.loads(result.stdout)
                            for issue in lint_output.get('Issues', []):
                                issues.append({
                                    'line': issue.get('Line'),
                                    'column': issue.get('Column'),
                                    'message': issue.get('Text', ''),
                                    'severity': 'error',
                                })
                        except json.JSONDecodeError:
                            logger.warning("Failed to parse golangci-lint JSON output")
                except Exception as e:
                    logger.warning(f"golangci-lint check failed: {e}")
            else:
                # Fall back to go vet
                try:
                    result = subprocess.run(
                        ['go', 'vet', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=temp_dir,
                    )
                    
                    # Parse go vet output
                    if result.stderr:
                        for line in result.stderr.split('\n'):
                            if line.strip():
                                issues.append({
                                    'line': None,
                                    'column': None,
                                    'message': line,
                                    'severity': 'error',
                                })
                except Exception as e:
                    logger.warning(f"go vet check failed: {e}")
        
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return fixed_code, issues
    
    def format_code(self, code: str, file_path: str) -> str:
        """
        Format code using goimports or gofmt.
        
        Args:
            code: Source code to format
            file_path: Path to the file (for context)
            
        Returns:
            Formatted code
        """
        if not self._go_available:
            logger.warning("go not available, skipping format")
            return code
        
        try:
            # Try goimports first (includes gofmt + import management)
            if self._goimports_available:
                result = subprocess.run(
                    ['goimports'],
                    input=code,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                # Fall back to gofmt
                result = subprocess.run(
                    ['gofmt'],
                    input=code,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"format failed: {result.stderr}")
                return code
        except Exception as e:
            logger.warning(f"format failed: {e}")
            return code
    
    def compile_check(self, code: str, file_path: str, project_root: Optional[Path] = None) -> Dict:
        """Check Go code compilation."""
        if not self._go_available:
            return {
                'success': False,
                'stderr': 'go compiler not available',
                'exit_code': -1,
            }
        
        effective_root = project_root or self.project_root
        
        # Check if we should use project root for go build
        use_project_root = False
        if effective_root:
            go_mod_path = Path(effective_root) / 'go.mod'
            if go_mod_path.exists():
                use_project_root = True
        
        temp_dir = None
        try:
            if use_project_root:
                # Use project root as working directory for go build
                cwd = str(effective_root)
                
                # Create temp file in project root
                temp_file = Path(effective_root) / f"_temp_{Path(file_path).stem}_{int(time.time())}.go"
                temp_file.write_text(code, encoding='utf-8')
                temp_dir = None  # Don't clean up project root
                
                try:
                    result = subprocess.run(
                        ['go', 'build', '-o', '/dev/null', str(temp_file)],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=30,
                        cwd=cwd,
                    )
                    
                    return {
                        'success': result.returncode == 0,
                        'stderr': result.stderr,
                        'stdout': result.stdout,
                        'exit_code': result.returncode,
                    }
                finally:
                    # Clean up temp file
                    if temp_file.exists():
                        temp_file.unlink()
            else:
                # Use temp directory as before
                temp_dir = tempfile.mkdtemp(prefix='go_check_')
                temp_file = Path(temp_dir) / f"{Path(file_path).stem}.go"
                temp_file.write_text(code, encoding='utf-8')
                
                result = subprocess.run(
                    ['go', 'build', '-o', '/dev/null', str(temp_file)],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=30,
                    cwd=temp_dir,
                )
                
                return {
                    'success': result.returncode == 0,
                    'stderr': result.stderr,
                    'stdout': result.stdout,
                    'exit_code': result.returncode,
                }
        
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'stderr': 'go build timed out',
                'exit_code': -1,
            }
        except Exception as e:
            return {
                'success': False,
                'stderr': str(e),
                'exit_code': -1,
            }
        finally:
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
                
                
    def compile_check_with_deps(self, files: List[Tuple[str, str]], project_root: Optional[Path] = None) -> Dict:
        """
        Check Go code compilation for multiple files together.
        Go supports multi-file compilation in the same package.
        
        Args:
            files: List of (code, file_path) tuples
            project_root: Optional project root path
            
        Returns:
            Dict with 'success', 'results', 'stderr', 'stdout', 'exit_code'
        """
        if not self._go_available:
            return {
                'success': False,
                'results': [],
                'stderr': 'go compiler not available',
                'stdout': '',
                'exit_code': -1,
            }
        
        if not files:
            return {
                'success': True,
                'results': [],
                'stderr': '',
                'stdout': '',
                'exit_code': 0,
            }
        
        effective_root = project_root or self.project_root
        temp_dir = None
        
        try:
            temp_dir = tempfile.mkdtemp(prefix='go_check_deps_')
            written_files = []
            
            # Write all files to temp directory preserving relative paths
            for code, file_path in files:
                # Normalize path and create subdirectories if needed
                rel_path = Path(file_path)
                if rel_path.is_absolute():
                    rel_path = Path(rel_path.name)
                
                temp_file = Path(temp_dir) / rel_path
                temp_file.parent.mkdir(parents=True, exist_ok=True)
                temp_file.write_text(code, encoding='utf-8')
                written_files.append(str(temp_file))
            
            # Check if go.mod exists in effective_root, if so copy it
            if effective_root:
                go_mod_path = Path(effective_root) / 'go.mod'
                if go_mod_path.exists():
                    shutil.copy(str(go_mod_path), str(Path(temp_dir) / 'go.mod'))
                
                go_sum_path = Path(effective_root) / 'go.sum'
                if go_sum_path.exists():
                    shutil.copy(str(go_sum_path), str(Path(temp_dir) / 'go.sum'))
            
            # Run go build ./... to compile all Go files together
            result = subprocess.run(
                ['go', 'build', './...'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
                cwd=temp_dir,
            )
            
            # Build individual results
            individual_results = []
            for code, file_path in files:
                individual_results.append({
                    'file_path': file_path,
                    'success': result.returncode == 0,
                    'stderr': result.stderr if result.returncode != 0 else '',
                    'stdout': result.stdout,
                    'exit_code': result.returncode,
                })
            
            return {
                'success': result.returncode == 0,
                'results': individual_results,
                'stderr': result.stderr,
                'stdout': result.stdout,
                'exit_code': result.returncode,
            }
        
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'results': [{'file_path': fp, 'success': False, 'stderr': 'go build timed out', 'exit_code': -1} for _, fp in files],
                'stderr': 'go build timed out',
                'stdout': '',
                'exit_code': -1,
            }
        except Exception as e:
            return {
                'success': False,
                'results': [{'file_path': fp, 'success': False, 'stderr': str(e), 'exit_code': -1} for _, fp in files],
                'stderr': str(e),
                'stdout': '',
                'exit_code': -1,
            }
        finally:
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
                
