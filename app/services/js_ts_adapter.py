from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import json
import os
import time
import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple


from app.services.language_adapter import LanguageAdapter

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.virtual_fs import VirtualFileSystem


logger = logging.getLogger(__name__)


class JsTsAdapter(LanguageAdapter):
    """Adapter for JavaScript and TypeScript files."""
    
    def __init__(self, project_root: Optional[Path] = None, vfs: Optional['VirtualFileSystem'] = None):
        """Initialize JS/TS adapter and check tool availability."""
        self.project_root = project_root
        self.vfs = vfs  # VFS reference for reading staged files
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
            f"ts-node={self._ts_node_available}, tsc={self._tsc_available}, "
            f"vfs={'yes' if self.vfs else 'no'}"
        )
    
    def _extract_imports_from_code(self, code: str) -> List[str]:
        """
        Extract relative import paths from JavaScript/TypeScript source code.
        
        Args:
            code: JS/TS source code
            
        Returns:
            List of relative import paths (e.g., ['../utils/helper', './config'])
        """
        imports = []
        
        # Match ES6 imports: import { foo } from './path' or import foo from "../path"
        es6_pattern = r'''import\s+(?:[\w{}\s,*]+\s+from\s+)?['"]([./][^'"]+)['"]'''
        imports.extend(re.findall(es6_pattern, code))
        
        # Match require statements: require('./path') or require("../path")
        require_pattern = r'''require\s*\(\s*['"]([./][^'"]+)['"]\s*\)'''
        imports.extend(re.findall(require_pattern, code))
        
        # Match dynamic imports: import('./path')
        dynamic_pattern = r'''import\s*\(\s*['"]([./][^'"]+)['"]\s*\)'''
        imports.extend(re.findall(dynamic_pattern, code))
        
        # Filter to only relative imports (starting with ./ or ../)
        return [imp for imp in imports if imp.startswith('./') or imp.startswith('../')]
    
    @staticmethod
    def _find_common_ancestor(paths: List[Path]) -> Optional[Path]:
        """
        Find the common ancestor directory of multiple paths.
        
        Args:
            paths: List of Path objects
            
        Returns:
            Common ancestor Path or None if no common ancestor
        """
        if not paths:
            return None
        
        if len(paths) == 1:
            return paths[0].parent
        
        # Convert all paths to absolute and get their parts
        abs_paths = [p.resolve() if not p.is_absolute() else p for p in paths]
        
        # Get parts of each path
        all_parts = [p.parts for p in abs_paths]
        
        # Find common prefix
        common_parts = []
        for parts_tuple in zip(*all_parts):
            if len(set(parts_tuple)) == 1:
                common_parts.append(parts_tuple[0])
            else:
                break
        
        if not common_parts:
            return None
        
        return Path(*common_parts)
    
    def _normalize_path_for_temp(self, code: str, file_path: str, effective_root: Optional[Path], all_paths: List[str]) -> Path:
        """
        Normalize file path for temporary directory, preserving JS/TS module structure.
        
        Uses semantic analysis (relative imports) and common ancestor detection as primary methods,
        falls back to directory name heuristics only as last resort.
        
        Args:
            code: JS/TS source code (used to analyze import structure)
            file_path: Original file path
            effective_root: Project root path (if available)
            all_paths: List of all file paths being processed (for common ancestor detection)
            
        Returns:
            Normalized relative path preserving module structure
        """
        rel_path = Path(file_path)
        
        # Priority 1: If path is already relative, use as-is
        if not rel_path.is_absolute():
            return rel_path
        
        # Priority 2: If we have project root, try to make relative
        if effective_root:
            try:
                return rel_path.relative_to(effective_root)
            except ValueError:
                pass  # Path is not under project root, continue to other methods
        
        # Priority 3: Find common ancestor of all files and make paths relative to it
        # This is the most reliable method for JS/TS as it preserves relative imports
        if len(all_paths) > 1:
            all_path_objects = [Path(p) for p in all_paths if Path(p).is_absolute()]
            if all_path_objects:
                common_ancestor = self._find_common_ancestor(all_path_objects)
                if common_ancestor:
                    try:
                        return rel_path.relative_to(common_ancestor)
                    except ValueError:
                        pass
        
        # Priority 4: Analyze import depth to determine how much path to preserve
        # If file has imports like '../../utils', we need at least 3 levels of path
        imports = self._extract_imports_from_code(code)
        max_depth = 1  # At minimum, preserve the file name
        for imp in imports:
            # Count how many levels up the import goes
            depth = imp.count('../') + 1
            max_depth = max(max_depth, depth + 1)  # +1 for the current directory
        
        parts = rel_path.parts
        if len(parts) > max_depth:
            return Path(*parts[-max_depth:])
        
        # Priority 5: Look for common JS/TS source directories
        # Only as fallback when other methods don't work
        known_dirs = ('src', 'lib', 'app', 'components', 'pages', 'modules', 'dist', 'build')
        for i, part in enumerate(parts):
            if part in known_dirs:
                return Path(*parts[i:])
        
        # Priority 6: Preserve last 4 parts to maintain reasonable structure
        return Path(*parts[-min(len(parts), 4):])
    
    def _read_file_content(self, file_path: Path, rel_path: str) -> Optional[str]:
        """
        Read file content, preferring VFS staged content over disk content.
        
        This ensures that when compiling JS/TS files together, we use the latest
        staged versions of ALL files, not just the ones explicitly passed to
        compile_check_with_deps.
        
        Args:
            file_path: Absolute path to the file on disk
            rel_path: Relative path from project root (for VFS lookup)
            
        Returns:
            File content as string, or None if file cannot be read
        """
        # Priority 1: Try VFS (includes staged changes)
        if self.vfs is not None:
            # Normalize path for VFS lookup (use forward slashes)
            vfs_path = rel_path.replace('\\', '/')
            content = self.vfs.read_file(vfs_path)
            if content is not None:
                logger.debug(f"Read {rel_path} from VFS (staged or original)")
                return content
        
        # Priority 2: Fall back to disk
        try:
            content = file_path.read_text(encoding='utf-8')
            logger.debug(f"Read {rel_path} from disk")
            return content
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")
            return None
    
    
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
        
        # Check if Node.js is available - this is required for JS/TS
        if not self._node_available:
            logger.warning("node not available, returning error")
            issues.append({
                'line': 1,
                'column': 1,
                'message': 'Node.js runtime not available. Please install Node.js to validate JavaScript/TypeScript files.',
                'severity': 'error',
            })
            return code, issues
        
        if not self._eslint_available:
            logger.warning("eslint not available, adding warning")
            issues.append({
                'line': None,
                'column': None,
                'message': 'ESLint not available. Install eslint for detailed linting. Basic syntax check will be performed with node --check.',
                'severity': 'warning',
            })
            # Continue without eslint - we can still do basic syntax check
        
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp()
            
            # Write code to temp file using just the filename
            temp_file = Path(temp_dir) / Path(file_path).name
            temp_file.write_text(code, encoding='utf-8')
            
            # Apply fixes if requested and eslint is available
            if fix and self._eslint_available:
                try:
                    result = subprocess.run(
                        ['eslint', '--fix', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    # Read back fixed code
                    fixed_code = temp_file.read_text(encoding='utf-8')
                except subprocess.TimeoutExpired:
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': 'eslint --fix timed out',
                        'severity': 'warning',
                    })
                except Exception as e:
                    logger.warning(f"eslint --fix failed: {e}")
            
            # Run eslint to get issues if available
            if self._eslint_available:
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
                            # Parse stderr for errors
                            if result.stderr:
                                for line in result.stderr.split('\n'):
                                    if line.strip():
                                        issues.append({
                                            'line': None,
                                            'column': None,
                                            'message': line.strip(),
                                            'severity': 'error',
                                        })
                    
                    # Check return code
                    if result.returncode != 0 and not any(i.get('severity') == 'error' for i in issues):
                        # eslint found issues but we didn't parse them
                        if result.stderr:
                            issues.append({
                                'line': None,
                                'column': None,
                                'message': f'eslint error: {result.stderr[:200]}',
                                'severity': 'error',
                            })
                            
                except subprocess.TimeoutExpired:
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': 'eslint timed out',
                        'severity': 'error',
                    })
                except Exception as e:
                    logger.warning(f"eslint check failed: {e}")
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': f'eslint error: {e}',
                        'severity': 'warning',
                    })
            else:
                # Fallback to node --check for basic syntax validation (JS only)
                if not file_path.endswith('.ts') and not file_path.endswith('.tsx'):
                    try:
                        result = subprocess.run(
                            ['node', '--check', str(temp_file)],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        
                        if result.returncode != 0:
                            # Parse node --check output for errors
                            error_output = result.stderr or result.stdout
                            if error_output:
                                for line in error_output.split('\n'):
                                    if line.strip():
                                        issues.append({
                                            'line': None,
                                            'column': None,
                                            'message': line.strip(),
                                            'severity': 'error',
                                        })
                            else:
                                issues.append({
                                    'line': None,
                                    'column': None,
                                    'message': f'node --check failed with exit code {result.returncode}',
                                    'severity': 'error',
                                })
                    except subprocess.TimeoutExpired:
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': 'node --check timed out',
                            'severity': 'error',
                        })
                    except Exception as e:
                        logger.warning(f"node --check failed: {e}")
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': f'node --check error: {e}',
                            'severity': 'error',
                        })
                elif self._tsc_available:
                    # For TypeScript, use tsc for syntax check
                    try:
                        result = subprocess.run(
                            ['tsc', '--noEmit', '--skipLibCheck', str(temp_file)],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        
                        if result.returncode != 0:
                            error_output = result.stderr or result.stdout
                            if error_output:
                                for line in error_output.split('\n'):
                                    if line.strip():
                                        issues.append({
                                            'line': None,
                                            'column': None,
                                            'message': line.strip(),
                                            'severity': 'error',
                                        })
                            else:
                                issues.append({
                                    'line': None,
                                    'column': None,
                                    'message': f'tsc failed with exit code {result.returncode}',
                                    'severity': 'error',
                                })
                    except subprocess.TimeoutExpired:
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': 'tsc timed out',
                            'severity': 'error',
                        })
                    except Exception as e:
                        logger.warning(f"tsc check failed: {e}")
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': f'tsc error: {e}',
                            'severity': 'error',
                        })
                else:
                    # TypeScript file but no tsc available
                    issues.append({
                        'line': 1,
                        'column': 1,
                        'message': 'TypeScript compiler (tsc) not available. Please install TypeScript to validate .ts/.tsx files.',
                        'severity': 'error',
                    })
        
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
        
        CRITICAL: For JS/TS projects, we must include ALL project source files,
        not just the changed files. This is because TypeScript/JavaScript modules
        may import other project files that need to be present during compilation.
        
        Args:
            files: List of (code, file_path) tuples - these are the STAGED/CHANGED files
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
            
            # Build a map of staged files: file_path -> code
            # These are the files with NEW content that should override project files
            staged_files_map: Dict[str, str] = {}
            for code, file_path in files:
                # Normalize path for comparison
                normalized_path = str(Path(file_path)).replace('\\', '/')
                staged_files_map[normalized_path] = code
                # Also store with original path
                staged_files_map[file_path] = code
            
            # Separate TypeScript and JavaScript files from staged files
            ts_staged_files = []  # List of (code, file_path)
            js_staged_files = []  # List of (code, file_path)
            
            for code, file_path in files:
                if file_path.endswith('.ts') or file_path.endswith('.tsx'):
                    ts_staged_files.append((code, file_path))
                else:
                    js_staged_files.append((code, file_path))
            
            # ================================================================
            # STEP 1: Copy ALL JS/TS files from project to temp directory
            # This ensures all dependencies are available for compilation
            # ================================================================
            all_ts_files_in_temp = []
            all_js_files_in_temp = []
            
            if effective_root and Path(effective_root).exists():
                # Define extensions to copy
                extensions = ('*.ts', '*.tsx', '*.js', '*.jsx', '*.mjs', '*.cjs')
                
                for ext in extensions:
                    for source_file in Path(effective_root).rglob(ext):
                        try:
                            rel_path = source_file.relative_to(effective_root)
                            rel_path_str = str(rel_path).replace('\\', '/')
                            
                            # Skip node_modules, hidden folders, dist, build
                            parts = rel_path.parts
                            if any(p.startswith('.') or p in ('node_modules', 'dist', 'build', 'coverage', '.next', '__pycache__') for p in parts):
                                continue
                            
                            # Determine content: use staged version if available, otherwise read via VFS/disk
                            content = None
                            
                            # Check if this file has staged content (explicitly passed to this method)
                            if rel_path_str in staged_files_map:
                                content = staged_files_map[rel_path_str]
                                logger.debug(f"Using explicitly staged content for {rel_path_str}")
                            elif str(rel_path) in staged_files_map:
                                content = staged_files_map[str(rel_path)]
                                logger.debug(f"Using explicitly staged content for {rel_path}")
                            else:
                                # Read file content via VFS (if available) or disk
                                # This ensures we get VFS-staged content for files not in our explicit list
                                content = self._read_file_content(source_file, rel_path_str)
                            
                            if content is None:
                                continue
                            
                            # Write to temp directory preserving structure
                            temp_file = Path(temp_dir) / rel_path
                            temp_file.parent.mkdir(parents=True, exist_ok=True)
                            temp_file.write_text(content, encoding='utf-8')
                            
                            # Track by file type
                            if ext in ('*.ts', '*.tsx'):
                                all_ts_files_in_temp.append(str(temp_file))
                            else:
                                all_js_files_in_temp.append(str(temp_file))
                            
                        except ValueError:
                            # File is not under effective_root
                            continue
                        except Exception as e:
                            logger.warning(f"Error processing {source_file}: {e}")
                            continue
                
                # Copy tsconfig.json if it exists
                tsconfig_path = Path(effective_root) / 'tsconfig.json'
                if tsconfig_path.exists():
                    shutil.copy(str(tsconfig_path), str(Path(temp_dir) / 'tsconfig.json'))
                
                # Copy package.json if it exists
                package_json_path = Path(effective_root) / 'package.json'
                if package_json_path.exists():
                    shutil.copy(str(package_json_path), str(Path(temp_dir) / 'package.json'))
            
            # ================================================================
            # STEP 2: Write any staged files that don't exist in project yet
            # (new files being created)
            # ================================================================
            for code, file_path in files:
                rel_path = Path(file_path)
                if rel_path.is_absolute() and effective_root:
                    try:
                        rel_path = rel_path.relative_to(effective_root)
                    except ValueError:
                        pass
                
                temp_file = Path(temp_dir) / rel_path
                
                if not temp_file.exists():
                    # This is a new file, write it
                    temp_file.parent.mkdir(parents=True, exist_ok=True)
                    temp_file.write_text(code, encoding='utf-8')
                    
                    if file_path.endswith('.ts') or file_path.endswith('.tsx'):
                        all_ts_files_in_temp.append(str(temp_file))
                    else:
                        all_js_files_in_temp.append(str(temp_file))
                    
                    logger.debug(f"Wrote new staged file: {rel_path}")
            
            # ================================================================
            # STEP 3: Compile TypeScript files if any exist
            # ================================================================
            individual_results = []
            ts_compilation_success = True
            ts_stderr = ''
            ts_stdout = ''
            
            if all_ts_files_in_temp and self._tsc_available:
                logger.debug(f"Compiling {len(all_ts_files_in_temp)} TypeScript files together")
                
                result = subprocess.run(
                    ['tsc', '--noEmit', '--skipLibCheck'],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=60,
                    cwd=temp_dir,
                )
                
                ts_compilation_success = result.returncode == 0
                ts_stderr = result.stderr
                ts_stdout = result.stdout
                
                # Add results for TypeScript staged files
                for code, file_path in ts_staged_files:
                    individual_results.append({
                        'file_path': file_path,
                        'success': ts_compilation_success,
                        'stderr': ts_stderr if not ts_compilation_success else '',
                        'stdout': ts_stdout,
                        'exit_code': result.returncode,
                    })
            else:
                # Add results for TypeScript staged files (no tsc available)
                for code, file_path in ts_staged_files:
                    individual_results.append({
                        'file_path': file_path,
                        'success': True,
                        'stderr': '',
                        'stdout': 'TypeScript compiler not available',
                        'exit_code': 0,
                    })
            
            # ================================================================
            # STEP 4: Check JavaScript files individually
            # ================================================================
            for code, file_path in js_staged_files:
                js_success = True
                js_stderr = ''
                js_stdout = ''
                
                if self._eslint_available:
                    # Find the temp file for this JS file
                    rel_path = Path(file_path)
                    if rel_path.is_absolute() and effective_root:
                        try:
                            rel_path = rel_path.relative_to(effective_root)
                        except ValueError:
                            pass
                    
                    temp_js_file = Path(temp_dir) / rel_path
                    
                    if temp_js_file.exists():
                        try:
                            result = subprocess.run(
                                ['eslint', str(temp_js_file)],
                                capture_output=True,
                                text=True,
                                encoding='utf-8',
                                errors='replace',
                                timeout=30,
                                cwd=temp_dir,
                            )
                            
                            js_success = result.returncode == 0
                            js_stderr = result.stderr
                            js_stdout = result.stdout
                        except subprocess.TimeoutExpired:
                            js_success = False
                            js_stderr = 'eslint timed out'
                        except Exception as e:
                            logger.warning(f"eslint check failed for {file_path}: {e}")
                            js_success = False
                            js_stderr = str(e)
                
                individual_results.append({
                    'file_path': file_path,
                    'success': js_success,
                    'stderr': js_stderr,
                    'stdout': js_stdout,
                    'exit_code': 0 if js_success else 1,
                })
            
            # Overall success is true only if all checks passed
            overall_success = all(r['success'] for r in individual_results) if individual_results else True
            
            return {
                'success': overall_success,
                'results': individual_results,
                'stderr': ts_stderr,
                'stdout': ts_stdout,
                'exit_code': 0 if overall_success else 1,
            }
        
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'results': [{'file_path': fp, 'success': False, 'stderr': 'compilation timed out', 'exit_code': -1} for _, fp in files],
                'stderr': 'compilation timed out',
                'stdout': '',
                'exit_code': -1,
            }
        except Exception as e:
            logger.error(f"JS/TS compilation error: {e}", exc_info=True)
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
                
