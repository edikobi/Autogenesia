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


class GoAdapter(LanguageAdapter):
    """Adapter for Go files."""
    
    def __init__(self, project_root: Optional[Path] = None, vfs: Optional['VirtualFileSystem'] = None):
        """Initialize Go adapter and check tool availability."""
        self.project_root = project_root
        self.vfs = vfs  # VFS reference for reading staged files
        self._go_available = shutil.which('go') is not None
        self._golangci_lint_available = shutil.which('golangci-lint') is not None
        self._goimports_available = shutil.which('goimports') is not None
        self._gofmt_available = shutil.which('gofmt') is not None
        
        logger.debug(
            f"GoAdapter initialized: "
            f"go={self._go_available}, golangci-lint={self._golangci_lint_available}, "
            f"goimports={self._goimports_available}, gofmt={self._gofmt_available}, "
            f"vfs={'yes' if self.vfs else 'no'}"
        )
    
    def _extract_package_from_code(self, code: str) -> Optional[str]:
        """
        Extract package declaration from Go source code.
        
        Args:
            code: Go source code
            
        Returns:
            Package name (e.g., 'main', 'handlers') or None if not found
        """
        # Match: package main or package handlers
        match = re.search(r'^\s*package\s+(\w+)', code, re.MULTILINE)
        if match:
            return match.group(1)
        return None
    
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
        Normalize file path for temporary directory, preserving Go package structure.
        
        Uses semantic analysis and common ancestor detection as primary methods,
        falls back to directory name heuristics only as last resort.
        
        Args:
            code: Go source code (used to extract package declaration)
            file_path: Original file path
            effective_root: Project root path (if available)
            all_paths: List of all file paths being processed (for common ancestor detection)
            
        Returns:
            Normalized relative path preserving package structure
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
        # This preserves the relative structure between files
        if len(all_paths) > 1:
            all_path_objects = [Path(p) for p in all_paths if Path(p).is_absolute()]
            if all_path_objects:
                common_ancestor = self._find_common_ancestor(all_path_objects)
                if common_ancestor:
                    try:
                        return rel_path.relative_to(common_ancestor)
                    except ValueError:
                        pass
        
        # Priority 4: For single file, try to find go.mod location indicator in path
        # Go modules are rooted at go.mod, so find the module root
        parts = rel_path.parts
        for i in range(len(parts) - 1, -1, -1):
            # Look for typical Go project indicators
            if parts[i] in ('cmd', 'pkg', 'internal', 'api'):
                # Return from this directory onwards
                return Path(*parts[i:])
        
        # Priority 5: Preserve directory containing the file + file name
        # This maintains at least the immediate package context
        if len(parts) >= 2:
            return Path(*parts[-2:])
        
        # Priority 6: Last resort - just the file name
        return Path(rel_path.name)
    
    def _read_file_content(self, file_path: Path, rel_path: str) -> Optional[str]:
        """
        Read file content, preferring VFS staged content over disk content.
        
        This ensures that when compiling Go files together, we use the latest
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
            logger.warning("go not available, returning error")
            issues.append({
                'line': 1,
                'column': 1,
                'message': 'Go compiler not available. Please install Go to validate this file.',
                'severity': 'error',
            })
            return code, issues
        
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
                except subprocess.TimeoutExpired:
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': 'golangci-lint timed out',
                        'severity': 'error',
                    })
                except Exception as e:
                    logger.warning(f"golangci-lint check failed: {e}")
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': f'golangci-lint error: {e}',
                        'severity': 'warning',
                    })
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
                                    'message': line.strip(),
                                    'severity': 'error',
                                })
                                
                    # Also check return code
                    if result.returncode != 0 and not issues:
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': f'go vet failed with exit code {result.returncode}',
                            'severity': 'error',
                        })
                except subprocess.TimeoutExpired:
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': 'go vet timed out',
                        'severity': 'error',
                    })
                except Exception as e:
                    logger.warning(f"go vet check failed: {e}")
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': f'go vet error: {e}',
                        'severity': 'error',
                    })
        
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

            CRITICAL: For Go projects, we must compile ALL project Go files together,
            not just the changed files. This is because Go requires all package files
            to be present during compilation.

            Args:
                files: List of (code, file_path) tuples - these are the STAGED/CHANGED files
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
        
                # Build a map of staged files with MULTIPLE path formats for robust lookup
                # This ensures we can match paths regardless of separator style
                staged_files_map: Dict[str, str] = {}
                staged_paths_normalized: set = set()
        
                for code, file_path in files:
                    # Store with multiple path formats for robust matching
                    # Format 1: Original path
                    staged_files_map[file_path] = code
            
                    # Format 2: Forward slashes (Unix-style)
                    forward_slash_path = file_path.replace('\\', '/')
                    staged_files_map[forward_slash_path] = code
            
                    # Format 3: Backslashes (Windows-style)
                    back_slash_path = file_path.replace('/', '\\')
                    staged_files_map[back_slash_path] = code
            
                    # Format 4: Normalized via Path
                    normalized_path = str(Path(file_path)).replace('\\', '/')
                    staged_files_map[normalized_path] = code
            
                    # Track all normalized paths for quick lookup
                    staged_paths_normalized.add(forward_slash_path)
                    staged_paths_normalized.add(normalized_path)
        
                # ================================================================
                # STEP 1: Copy ALL Go files from project to temp directory
                # This ensures all dependencies are available for compilation
                # ================================================================
                all_go_files_in_temp = []
                processed_rel_paths: set = set()  # Track which relative paths we've processed
        
                if effective_root and Path(effective_root).exists():
                    for go_file in Path(effective_root).rglob('*.go'):
                        try:
                            rel_path = go_file.relative_to(effective_root)
                            rel_path_str = str(rel_path).replace('\\', '/')
                    
                            # Skip vendor, hidden folders, and test cache
                            parts = rel_path.parts
                            if any(p.startswith('.') or p in ('vendor', 'testdata') for p in parts):
                                continue
                    
                            # Mark this path as processed
                            processed_rel_paths.add(rel_path_str)
                    
                            # Determine content: use staged version if available, otherwise read via VFS/disk
                            content = None
                    
                            # Check if this file has staged content (explicitly passed to this method)
                            # Use normalized path for comparison
                            if rel_path_str in staged_files_map:
                                content = staged_files_map[rel_path_str]
                                logger.debug(f"Using explicitly staged content for {rel_path_str}")
                            elif str(rel_path) in staged_files_map:
                                content = staged_files_map[str(rel_path)]
                                logger.debug(f"Using explicitly staged content for {rel_path}")
                            elif rel_path_str in staged_paths_normalized:
                                # Double-check with normalized set
                                content = staged_files_map.get(rel_path_str)
                                logger.debug(f"Using staged content (normalized match) for {rel_path_str}")
                            else:
                                # Read file content via VFS (if available) or disk
                                # CRITICAL: Only use VFS/disk if file is NOT in our staged list
                                # This prevents reading stale VFS content when we have fresh staged content
                                content = self._read_file_content(go_file, rel_path_str)
                    
                            if content is None:
                                continue
                    
                            # Write to temp directory preserving structure
                            temp_file = Path(temp_dir) / rel_path
                            temp_file.parent.mkdir(parents=True, exist_ok=True)
                            temp_file.write_text(content, encoding='utf-8')
                            all_go_files_in_temp.append(str(temp_file))
                    
                        except ValueError:
                            # File is not under effective_root
                            continue
                        except Exception as e:
                            logger.warning(f"Error processing {go_file}: {e}")
                            continue
            
                    # ================================================================
                    # STEP 1.1: Copy go.mod/go.sum and download Go module dependencies
                    # This ensures external libraries are available for compilation
                    # ================================================================
                    go_mod_copied = False
                    go_mod_path = Path(effective_root) / 'go.mod'
                    go_sum_path = Path(effective_root) / 'go.sum'
            
                    # Check VFS for go.mod first (might be staged)
                    go_mod_content = None
                    if self.vfs is not None:
                        go_mod_content = self.vfs.read_file('go.mod')
            
                    if go_mod_content is not None:
                        # Write go.mod from VFS to temp dir
                        temp_go_mod = Path(temp_dir) / 'go.mod'
                        temp_go_mod.write_text(go_mod_content, encoding='utf-8')
                        go_mod_copied = True
                        logger.debug(f"Copied go.mod from VFS to {temp_dir}")
                    elif go_mod_path.exists():
                        # Copy go.mod from disk to temp dir
                        shutil.copy(str(go_mod_path), str(Path(temp_dir) / 'go.mod'))
                        go_mod_copied = True
                        logger.debug(f"Copied go.mod from disk to {temp_dir}")
            
                    # Check VFS for go.sum first (might be staged)
                    go_sum_content = None
                    if self.vfs is not None:
                        go_sum_content = self.vfs.read_file('go.sum')
            
                    if go_sum_content is not None:
                        # Write go.sum from VFS to temp dir
                        temp_go_sum = Path(temp_dir) / 'go.sum'
                        temp_go_sum.write_text(go_sum_content, encoding='utf-8')
                        logger.debug(f"Copied go.sum from VFS to {temp_dir}")
                    elif go_sum_path.exists():
                        # Copy go.sum from disk to temp dir
                        shutil.copy(str(go_sum_path), str(Path(temp_dir) / 'go.sum'))
                        logger.debug(f"Copied go.sum from disk to {temp_dir}")
            
                    # Run go mod download if go.mod was copied
                    if go_mod_copied:
                        try:
                            logger.info(f"Downloading Go module dependencies in {temp_dir}")
                    
                            download_result = subprocess.run(
                                [shutil.which('go') or 'go', 'mod', 'download'],
                                capture_output=True,
                                text=True,
                                encoding='utf-8',
                                errors='replace',
                                timeout=120,
                                cwd=temp_dir,
                            )
                    
                            if download_result.returncode == 0:
                                logger.info("Go module dependencies downloaded successfully")
                            else:
                                logger.warning(f"go mod download failed: {download_result.stderr[:300]}")
                        
                        except subprocess.TimeoutExpired:
                            logger.warning("go mod download timed out after 120 seconds")
                        except Exception as e:
                            logger.warning(f"Error running go mod download: {e}")
        
                # ================================================================
                # STEP 1.5: Include VFS-staged Go files that don't exist on disk
                # This handles newly created files that only exist in VFS
                # ================================================================
                if self.vfs is not None:
                    try:
                        affected_files = self.vfs.get_staged_files()
                        for vfs_rel_path in affected_files:
                            # Only process Go files
                            if not vfs_rel_path.endswith('.go'):
                                continue
                    
                            # Normalize path for comparison
                            vfs_path_normalized = vfs_rel_path.replace('\\', '/')
                    
                            # Skip if already processed from disk scan
                            if vfs_path_normalized in processed_rel_paths:
                                continue
                    
                            # Skip vendor, testdata, and hidden directories
                            parts = Path(vfs_rel_path).parts
                            if any(p.startswith('.') or p in ('vendor', 'testdata') for p in parts):
                                continue
                    
                            # ============================================================
                            # CRITICAL: Check staged_files_map FIRST (authoritative source)
                            # Only fall back to VFS if file is NOT in staged_files_map
                            # ============================================================
                            content = None
                    
                            if vfs_path_normalized in staged_files_map:
                                content = staged_files_map[vfs_path_normalized]
                                logger.debug(f"Using AUTHORITATIVE staged content for VFS file {vfs_path_normalized}")
                            else:
                                # File is not in authoritative list - read from VFS
                                content = self.vfs.read_file(vfs_rel_path)
                                if content is not None:
                                    logger.debug(f"Read VFS-only file from VFS: {vfs_rel_path}")
                    
                            if content is None:
                                continue
                    
                            # Write to temp directory preserving structure
                            temp_file = Path(temp_dir) / vfs_rel_path
                            temp_file.parent.mkdir(parents=True, exist_ok=True)
                            temp_file.write_text(content, encoding='utf-8')
                            all_go_files_in_temp.append(str(temp_file))
                            processed_rel_paths.add(vfs_path_normalized)
                    
                    except Exception as e:
                        logger.warning(f"Error scanning VFS for additional Go files: {e}")
        
                # ================================================================
                # STEP 2: Write any staged files that don't exist in project yet
                # (new files being created)
                # ================================================================
                all_paths = [file_path for _, file_path in files]
        
                for code, file_path in files:
                    # Check if we already wrote this file
                    rel_path = self._normalize_path_for_temp(code, file_path, effective_root, all_paths)
                    rel_path_str = str(rel_path).replace('\\', '/')
            
                    # Skip if already processed
                    if rel_path_str in processed_rel_paths:
                        continue
            
                    temp_file = Path(temp_dir) / rel_path
            
                    if not temp_file.exists():
                        # This is a new file, write it
                        temp_file.parent.mkdir(parents=True, exist_ok=True)
                        temp_file.write_text(code, encoding='utf-8')
                        all_go_files_in_temp.append(str(temp_file))
                        processed_rel_paths.add(rel_path_str)
                        logger.debug(f"Wrote new staged file: {rel_path}")
        
                if not all_go_files_in_temp:
                    return {
                        'success': True,
                        'results': [],
                        'stderr': '',
                        'stdout': 'No Go files to compile',
                        'exit_code': 0,
                    }
        
                # ================================================================
                # STEP 3: Run go build ./... to compile ALL Go files together
                # ================================================================
                logger.debug(f"Compiling {len(all_go_files_in_temp)} Go files together")
        
                result = subprocess.run(
                    [shutil.which('go') or 'go', 'build', './...'],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=60,
                    cwd=temp_dir,
                )
        
                # Build individual results for the STAGED files only
                # (these are the files the caller cares about)
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
                logger.error(f"Go compilation error: {e}", exc_info=True)
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
                
