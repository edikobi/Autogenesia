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


class JsTsAdapter(LanguageAdapter):
    """Adapter for JavaScript and TypeScript files."""
    
    def __init__(self, project_root: Optional[Path] = None):
        """Initialize JS/TS adapter and check tool availability."""
        self.project_root = project_root
        self._node_available = shutil.which('node') is not None
        self._npm_available = shutil.which('npm') is not None
        self._eslint_available = shutil.which('eslint') is not None
        self._prettier_available = shutil.which('prettier') is not None
        self._ts_node_available = shutil.which('ts-node') is not None
        self._tsc_available = shutil.which('tsc') is not None
        
        logger.debug(
            f"JsTsAdapter initialized: "
            f"node={self._node_available}, npm={self._npm_available}, "
            f"eslint={self._eslint_available}, prettier={self._prettier_available}, "
            f"ts-node={self._ts_node_available}, tsc={self._tsc_available}"
        )
    
    def get_language_name(self) -> str:
        """Return the language name."""
        return "javascript"
    
    def is_available(self) -> bool:
        """Check if the adapter is available."""
        return self._node_available
    
    def lint(self, code: str, file_path: str, fix: bool = False) -> Tuple[str, List[Dict]]:
        """
        Lint code using eslint.
        
        Args:
            code: Source code to lint
            file_path: Path to the file (for context)
            fix: Whether to apply auto-fixes
            
        Returns:
            Tuple of (fixed_code, issues_list)
        """
        fixed_code = code
        issues = []
        
        if not self._eslint_available:
            logger.warning("eslint not available, skipping lint")
            return code, []
        
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp()
            
            # Write code to temp file using just the filename
            temp_file = Path(temp_dir) / Path(file_path).name
            temp_file.write_text(code, encoding='utf-8')
            
            # Apply fixes if requested
            if fix:
                try:
                    result = subprocess.run(
                        ['eslint', '--fix', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    # Read back fixed code
                    fixed_code = temp_file.read_text(encoding='utf-8')
                except Exception as e:
                    logger.warning(f"eslint --fix failed: {e}")
            
            # Run eslint to get issues
            try:
                result = subprocess.run(
                    ['eslint', '--format', 'json', str(temp_file)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                
                # Parse JSON output
                if result.stdout.strip():
                    try:
                        eslint_output = json.loads(result.stdout)
                        if eslint_output and len(eslint_output) > 0:
                            file_results = eslint_output[0]
                            for msg in file_results.get('messages', []):
                                issues.append({
                                    'line': msg.get('line'),
                                    'column': msg.get('column'),
                                    'message': msg.get('message', ''),
                                    'severity': 'error' if msg.get('severity', 2) == 2 else 'warning',
                                })
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse eslint JSON output")
            except Exception as e:
                logger.warning(f"eslint check failed: {e}")
        
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return fixed_code, issues
    
    def format_code(self, code: str, file_path: str) -> str:
        """
        Format code using prettier.
        
        Args:
            code: Source code to format
            file_path: Path to the file (for context)
            
        Returns:
            Formatted code
        """
        if not self._prettier_available:
            logger.warning("prettier not available, skipping format")
            return code
        
        try:
            result = subprocess.run(
                ['prettier', '--stdin-filepath', file_path],
                input=code,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"prettier failed: {result.stderr}")
                return code
        except Exception as e:
            logger.warning(f"prettier format failed: {e}")
            return code
    
    def compile_check(self, code: str, file_path: str, project_root: Optional[Path] = None) -> Dict:
        """Check JavaScript/TypeScript code compilation."""
        effective_root = project_root or self.project_root
        
        # Determine if we should use project root
        use_project_root = False
        cwd = None
        
        if effective_root:
            effective_root_path = Path(effective_root)
            package_json = effective_root_path / 'package.json'
            node_modules = effective_root_path / 'node_modules'
            
            if package_json.exists() or node_modules.exists():
                use_project_root = True
                cwd = str(effective_root_path)
        
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix='js_check_')
            
            # Determine file extension
            if file_path.endswith('.ts') or file_path.endswith('.tsx'):
                ext = '.ts'
                is_typescript = True
            else:
                ext = '.js'
                is_typescript = False
            
            temp_file = Path(temp_dir) / f"temp_check{ext}"
            temp_file.write_text(code, encoding='utf-8')
            
            # Use TypeScript compiler if available and file is TypeScript
            if is_typescript and self._tsc_available:
                result = subprocess.run(
                    ['tsc', '--noEmit', '--skipLibCheck', str(temp_file)],
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
            
            # Use Node.js for basic syntax check
            if self._node_available:
                result = subprocess.run(
                    ['node', '--check', str(temp_file)],
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
            
            return {
                'success': False,
                'stderr': 'No JavaScript/TypeScript compiler available',
                'exit_code': -1,
            }
        
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'stderr': 'Compilation timed out',
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
            Check JavaScript/TypeScript code compilation for multiple files together.
            TypeScript files are compiled together with tsc, JavaScript files are checked individually.
            
            Args:
                files: List of (code, file_path) tuples
                project_root: Optional project root path
                
            Returns:
                Dict with 'success', 'results', 'stderr', 'stdout', 'exit_code'
            """
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
                temp_dir = tempfile.mkdtemp(prefix='js_ts_check_deps_')
                
                # Separate TypeScript and JavaScript files
                ts_files = []  # List of (code, file_path, temp_file_path)
                js_files = []  # List of (code, file_path, temp_file_path)
                
                # Write all files to temp directory preserving relative paths
                for code, file_path in files:
                    # Normalize path
                    rel_path = Path(file_path)
                    if rel_path.is_absolute():
                        rel_path = Path(rel_path.name)
                    
                    temp_file = Path(temp_dir) / rel_path
                    temp_file.parent.mkdir(parents=True, exist_ok=True)
                    temp_file.write_text(code, encoding='utf-8')
                    
                    # Categorize by file type
                    if file_path.endswith('.ts') or file_path.endswith('.tsx'):
                        ts_files.append((code, file_path, str(temp_file)))
                    else:
                        js_files.append((code, file_path, str(temp_file)))
                
                # Copy tsconfig.json if exists in project root
                if effective_root:
                    tsconfig_path = Path(effective_root) / 'tsconfig.json'
                    if tsconfig_path.exists():
                        shutil.copy(str(tsconfig_path), str(Path(temp_dir) / 'tsconfig.json'))
                
                all_stderr = []
                all_stdout = []
                individual_results = []
                overall_success = True
                
                # Compile TypeScript files together if any exist
                if ts_files and self._tsc_available:
                    try:
                        # Get list of temp file paths for tsc
                        ts_temp_paths = [tf for _, _, tf in ts_files]
                        
                        result = subprocess.run(
                            ['tsc', '--noEmit', '--skipLibCheck'] + ts_temp_paths,
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='replace',
                            timeout=60,
                            cwd=temp_dir,
                        )
                        
                        ts_success = result.returncode == 0
                        if not ts_success:
                            overall_success = False
                        
                        if result.stderr:
                            all_stderr.append(f"[TypeScript] {result.stderr}")
                        if result.stdout:
                            all_stdout.append(f"[TypeScript] {result.stdout}")
                        
                        # Add individual results for each TypeScript file
                        for code, file_path, temp_path in ts_files:
                            individual_results.append({
                                'file_path': file_path,
                                'success': ts_success,
                                'stderr': result.stderr if not ts_success else '',
                                'stdout': result.stdout,
                                'exit_code': result.returncode,
                            })
                    
                    except subprocess.TimeoutExpired:
                        overall_success = False
                        all_stderr.append('[TypeScript] tsc compilation timed out')
                        for code, file_path, temp_path in ts_files:
                            individual_results.append({
                                'file_path': file_path,
                                'success': False,
                                'stderr': 'tsc compilation timed out',
                                'exit_code': -1,
                            })
                    except Exception as e:
                        overall_success = False
                        all_stderr.append(f'[TypeScript] Error: {str(e)}')
                        for code, file_path, temp_path in ts_files:
                            individual_results.append({
                                'file_path': file_path,
                                'success': False,
                                'stderr': str(e),
                                'exit_code': -1,
                            })
                elif ts_files and not self._tsc_available:
                    # TypeScript compiler not available
                    all_stderr.append('[TypeScript] tsc not available, skipping TypeScript files')
                    for code, file_path, temp_path in ts_files:
                        individual_results.append({
                            'file_path': file_path,
                            'success': True,  # Skip gracefully
                            'stderr': 'tsc not available, skipped',
                            'exit_code': 0,
                        })
                
                # Check JavaScript files individually
                if js_files:
                    if self._node_available:
                        for code, file_path, temp_path in js_files:
                            try:
                                result = subprocess.run(
                                    ['node', '--check', temp_path],
                                    capture_output=True,
                                    text=True,
                                    encoding='utf-8',
                                    errors='replace',
                                    timeout=30,
                                    cwd=temp_dir,
                                )
                                
                                js_success = result.returncode == 0
                                if not js_success:
                                    overall_success = False
                                
                                if result.stderr:
                                    all_stderr.append(f"[{file_path}] {result.stderr}")
                                if result.stdout:
                                    all_stdout.append(f"[{file_path}] {result.stdout}")
                                
                                individual_results.append({
                                    'file_path': file_path,
                                    'success': js_success,
                                    'stderr': result.stderr if not js_success else '',
                                    'stdout': result.stdout,
                                    'exit_code': result.returncode,
                                })
                            
                            except subprocess.TimeoutExpired:
                                overall_success = False
                                all_stderr.append(f'[{file_path}] node --check timed out')
                                individual_results.append({
                                    'file_path': file_path,
                                    'success': False,
                                    'stderr': 'node --check timed out',
                                    'exit_code': -1,
                                })
                            except Exception as e:
                                overall_success = False
                                all_stderr.append(f'[{file_path}] Error: {str(e)}')
                                individual_results.append({
                                    'file_path': file_path,
                                    'success': False,
                                    'stderr': str(e),
                                    'exit_code': -1,
                                })
                    else:
                        # Node.js not available
                        all_stderr.append('[JavaScript] node not available, skipping JavaScript files')
                        for code, file_path, temp_path in js_files:
                            individual_results.append({
                                'file_path': file_path,
                                'success': True,  # Skip gracefully
                                'stderr': 'node not available, skipped',
                                'exit_code': 0,
                            })
                
                return {
                    'success': overall_success,
                    'results': individual_results,
                    'stderr': '\n'.join(all_stderr),
                    'stdout': '\n'.join(all_stdout),
                    'exit_code': 0 if overall_success else 1,
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
                
