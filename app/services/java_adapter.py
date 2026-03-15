from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Tuple


from app.services.language_adapter import LanguageAdapter

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.virtual_fs import VirtualFileSystem


logger = logging.getLogger(__name__)


class JavaAdapter(LanguageAdapter):
    """Adapter for Java files."""
    
    def __init__(self, project_root: Optional[Path] = None, vfs: Optional['VirtualFileSystem'] = None):
        """Initialize Java adapter and check tool availability."""
        self.project_root = project_root
        self.vfs = vfs  # NEW: Store VFS reference for reading staged files
        self._java_available = shutil.which('java') is not None
        self._javac_available = shutil.which('javac') is not None
        
        # Find JAR files in project
        self._jar_files = []
        if self.project_root:
            project_path = Path(self.project_root)
            self._jar_files = list(project_path.rglob('*.jar'))
        
        # Find JAR files
        self._checkstyle_jar = self._find_jar('checkstyle')
        self._google_java_format_jar = self._find_jar('google-java-format')
        
        logger.debug(
            f"JavaAdapter initialized: "
            f"java={self._java_available}, javac={self._javac_available}, "
            f"checkstyle_jar={self._checkstyle_jar is not None}, "
            f"google_java_format_jar={self._google_java_format_jar is not None}, "
            f"vfs={'yes' if self.vfs else 'no'}"
        )
    
    def _extract_package_from_code(self, code: str) -> Optional[str]:
        """
        Extract package declaration from Java source code.
        
        Args:
            code: Java source code
            
        Returns:
            Package name (e.g., 'com.example.app') or None if not found
        """
        # Match: package com.example.app;
        match = re.search(r'^\s*package\s+([\w.]+)\s*;', code, re.MULTILINE)
        if match:
            return match.group(1)
        return None

    def _build_path_from_package(self, package_name: str, file_name: str) -> Path:
        """
        Build file path from Java package name and file name.
        
        Args:
            package_name: Java package (e.g., 'com.example.app')
            file_name: File name (e.g., 'MyClass.java')
            
        Returns:
            Path object representing the correct directory structure
        """
        # Split package by dots: com.example.app -> ['com', 'example', 'app']
        package_parts = package_name.split('.')
        # Build path: com/example/app/MyClass.java
        return Path(*package_parts) / file_name

    def _normalize_path_for_temp(self, code: str, file_path: str, effective_root: Optional[Path]) -> Path:
        """
        Normalize file path for temporary directory, preserving Java package structure.
        
        Uses semantic analysis (package declaration) as primary method,
        falls back to directory name heuristics only as last resort.
        
        Args:
            code: Java source code (used to extract package declaration)
            file_path: Original file path
            effective_root: Project root path (if available)
            
        Returns:
            Normalized relative path preserving package structure
        """
        rel_path = Path(file_path)
        file_name = rel_path.name
        
        # Priority 1: If path is already relative, use as-is
        if not rel_path.is_absolute():
            return rel_path
        
        # Priority 2: If we have project root, try to make relative
        if effective_root:
            try:
                return rel_path.relative_to(effective_root)
            except ValueError:
                pass  # Path is not under project root, continue to other methods
        
        # Priority 3: Extract package from code and build path from it
        # This is the most reliable method for Java
        package_name = self._extract_package_from_code(code)
        if package_name:
            return self._build_path_from_package(package_name, file_name)
        
        # Priority 4: Find common Java source directories in path
        # Only as fallback when package declaration is not found
        parts = rel_path.parts
        known_dirs = ('src', 'source', 'java', 'main', 'test')
        for i, part in enumerate(parts):
            if part in known_dirs:
                # Return path from this directory onwards
                return Path(*parts[i:])
        
        # Priority 5: Last resort - preserve last N parts to maintain some structure
        # Use 6 parts to cover typical: src/main/java/com/example/Class.java
        return Path(*parts[-min(len(parts), 6):])
    
    def _read_file_content(self, file_path: Path, rel_path: str) -> Optional[str]:
        """
        Read file content, preferring VFS staged content over disk content.
        
        This ensures that when compiling Java files together, we use the latest
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
    
    
    def _find_jar(self, jar_name: str) -> Optional[Path]:
        """Find a JAR file in common locations."""
        common_paths = [
            Path.home() / '.m2' / 'repository',
            Path('/usr/share/java'),
            Path('/opt/java'),
        ]
        
        for base_path in common_paths:
            if base_path.exists():
                for jar_file in base_path.rglob(f'{jar_name}*.jar'):
                    return jar_file
        
        return None
    
    def get_language_name(self) -> str:
        """Return the language name."""
        return "java"
    
    def is_available(self) -> bool:
        """Check if the adapter is available."""
        return self._java_available
    
    def lint(self, code: str, file_path: str, fix: bool = False) -> Tuple[str, List[Dict]]:
        """
        Lint code using checkstyle and/or javac for syntax check.
        
        Args:
            code: Source code to lint
            file_path: Path to the file (for context)
            fix: Whether to apply auto-fixes
            
        Returns:
            Tuple of (fixed_code, issues_list)
        """
        fixed_code = code
        issues = []
        
        if not self._java_available:
            logger.warning("java not available, returning error")
            issues.append({
                'line': 1,
                'column': 1,
                'message': 'Java runtime not available. Please install Java to validate this file.',
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
            if fix and self._google_java_format_jar:
                try:
                    result = subprocess.run(
                        ['java', '-jar', str(self._google_java_format_jar), '-i', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    # Read back fixed code
                    fixed_code = temp_file.read_text(encoding='utf-8')
                except Exception as e:
                    logger.warning(f"google-java-format failed: {e}")
            
            # Run checkstyle to get issues
            if self._checkstyle_jar:
                try:
                    result = subprocess.run(
                        ['java', '-jar', str(self._checkstyle_jar), '-f', 'xml', str(temp_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    
                    if result.stdout.strip():
                        try:
                            import xml.etree.ElementTree as ET
                            root = ET.fromstring(result.stdout)
                            for file_elem in root.findall('file'):
                                for error_elem in file_elem.findall('error'):
                                    issues.append({
                                        'line': int(error_elem.get('line', 0)),
                                        'column': int(error_elem.get('column', 0)),
                                        'message': error_elem.get('message', ''),
                                        'severity': 'error' if error_elem.get('severity') == 'error' else 'warning',
                                    })
                        except Exception as e:
                            logger.warning(f"Failed to parse checkstyle XML output: {e}")
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
                        'message': 'checkstyle timed out',
                        'severity': 'error',
                    })
                except Exception as e:
                    logger.warning(f"checkstyle check failed: {e}")
                    issues.append({
                        'line': None,
                        'column': None,
                        'message': f'checkstyle error: {e}',
                        'severity': 'warning',
                    })
            else:
                # Fall back to javac for syntax check
                if self._javac_available:
                    try:
                        result = subprocess.run(
                            ['javac', str(temp_file)],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            cwd=temp_dir,
                        )
                        
                        # Parse javac output
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
                                'message': f'javac failed with exit code {result.returncode}',
                                'severity': 'error',
                            })
                    except subprocess.TimeoutExpired:
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': 'javac timed out',
                            'severity': 'error',
                        })
                    except Exception as e:
                        logger.warning(f"javac check failed: {e}")
                        issues.append({
                            'line': None,
                            'column': None,
                            'message': f'javac error: {e}',
                            'severity': 'error',
                        })
        
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return fixed_code, issues
    
    def format_code(self, code: str, file_path: str) -> str:
        """
        Format code using google-java-format.
        
        Args:
            code: Source code to format
            file_path: Path to the file (for context)
            
        Returns:
            Formatted code
        """
        if not self._java_available or not self._google_java_format_jar:
            logger.warning("google-java-format not available, skipping format")
            return code
        
        try:
            result = subprocess.run(
                ['java', '-jar', str(self._google_java_format_jar)],
                input=code,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"google-java-format failed: {result.stderr}")
                return code
        except Exception as e:
            logger.warning(f"format failed: {e}")
            return code
    
    def compile_check(self, code: str, file_path: str, project_root: Optional[Path] = None) -> Dict:
        """Check Java code compilation."""
        if not self._javac_available:
            return {
                'success': False,
                'stderr': 'javac compiler not available',
                'exit_code': -1,
            }
        
        effective_root = project_root or self.project_root
        
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix='java_check_')
            
            # Create temp Java file
            temp_file = Path(temp_dir) / f"Temp_{int(time.time())}.java"
            temp_file.write_text(code, encoding='utf-8')
            
            # Build javac command
            cmd = ['javac']
            
            # Add classpath if project root has pom.xml (Maven project)
            if effective_root:
                effective_root_path = Path(effective_root)
                pom_xml = effective_root_path / 'pom.xml'
                
                if pom_xml.exists():
                    # Add Maven target/classes to classpath
                    target_classes = effective_root_path / 'target' / 'classes'
                    if target_classes.exists():
                        cmd.extend(['-cp', str(target_classes)])
            
            # Add temp file
            cmd.append(str(temp_file))
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
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
                'stderr': 'javac timed out',
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
        Check Java code compilation for multiple files together.
        Java supports multi-file compilation with javac.
        
        CRITICAL: For Java projects, we must compile ALL project Java files together,
        not just the changed files. This is because Java requires all dependencies
        to be present during compilation.
        
        Args:
            files: List of (code, file_path) tuples - these are the STAGED/CHANGED files
            project_root: Optional project root path
            
        Returns:
            Dict with 'success', 'results', 'stderr', 'stdout', 'exit_code'
        """
        if not self._javac_available:
            return {
                'success': False,
                'results': [],
                'stderr': 'javac compiler not available',
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
            temp_dir = tempfile.mkdtemp(prefix='java_check_deps_')
            written_files = []
            
            # Build a map of staged files: file_path -> code
            # These are the files with NEW content that should override project files
            staged_files_map: Dict[str, str] = {}
            for code, file_path in files:
                # Normalize path for comparison
                normalized_path = str(Path(file_path)).replace('\\', '/')
                staged_files_map[normalized_path] = code
                # Also store with original path
                staged_files_map[file_path] = code
            
            # ================================================================
            # STEP 1: Copy ALL Java files from project to temp directory
            # This ensures all dependencies are available for compilation
            # ================================================================
            all_java_files_in_temp = []
            
            if effective_root and Path(effective_root).exists():
                for java_file in Path(effective_root).rglob('*.java'):
                    try:
                        rel_path = java_file.relative_to(effective_root)
                        rel_path_str = str(rel_path).replace('\\', '/')
                        
                        # Skip build directories and hidden folders
                        parts = rel_path.parts
                        if any(p.startswith('.') or p in ('target', 'build', 'out', 'bin') for p in parts):
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
                            content = self._read_file_content(java_file, rel_path_str)
                        
                        if content is None:
                            continue
                        
                        # Write to temp directory preserving structure
                        temp_file = Path(temp_dir) / rel_path
                        temp_file.parent.mkdir(parents=True, exist_ok=True)
                        temp_file.write_text(content, encoding='utf-8')
                        all_java_files_in_temp.append(str(temp_file))
                        
                    except ValueError:
                        # File is not under effective_root
                        continue
                    except Exception as e:
                        logger.warning(f"Error processing {java_file}: {e}")
                        continue
            
            # ================================================================
            # STEP 2: Write any staged files that don't exist in project yet
            # (new files being created)
            # ================================================================
            for code, file_path in files:
                # Check if we already wrote this file
                rel_path = self._normalize_path_for_temp(code, file_path, effective_root)
                temp_file = Path(temp_dir) / rel_path
                
                if not temp_file.exists():
                    # This is a new file, write it
                    temp_file.parent.mkdir(parents=True, exist_ok=True)
                    temp_file.write_text(code, encoding='utf-8')
                    all_java_files_in_temp.append(str(temp_file))
                    logger.debug(f"Wrote new staged file: {rel_path}")
            
            if not all_java_files_in_temp:
                return {
                    'success': True,
                    'results': [],
                    'stderr': '',
                    'stdout': 'No Java files to compile',
                    'exit_code': 0,
                }
            
            # ================================================================
            # STEP 3: Build classpath and compile ALL files together
            # ================================================================
            classpath_parts = []
            
            # Add project JAR files
            if self._jar_files:
                classpath_parts.extend([str(jar) for jar in self._jar_files])
            
            # Add Maven target/classes if exists
            if effective_root:
                maven_classes = Path(effective_root) / 'target' / 'classes'
                if maven_classes.exists():
                    classpath_parts.append(str(maven_classes))
                
                # Also add target/test-classes for test dependencies
                test_classes = Path(effective_root) / 'target' / 'test-classes'
                if test_classes.exists():
                    classpath_parts.append(str(test_classes))
            
            # Build javac command
            cmd = ['javac', '-d', temp_dir]
            
            if classpath_parts:
                classpath = os.pathsep.join(classpath_parts)
                cmd.extend(['-cp', classpath])
            
            # Add ALL Java files for compilation
            cmd.extend(all_java_files_in_temp)
            
            logger.debug(f"Compiling {len(all_java_files_in_temp)} Java files together")
            
            # Run javac to compile all Java files together
            result = subprocess.run(
                cmd,
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
                'results': [{'file_path': fp, 'success': False, 'stderr': 'javac compilation timed out', 'exit_code': -1} for _, fp in files],
                'stderr': 'javac compilation timed out',
                'stdout': '',
                'exit_code': -1,
            }
        except Exception as e:
            logger.error(f"Java compilation error: {e}", exc_info=True)
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
