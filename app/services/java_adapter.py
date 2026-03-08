from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from app.services.language_adapter import LanguageAdapter

logger = logging.getLogger(__name__)


class JavaAdapter(LanguageAdapter):
    """Adapter for Java files."""
    
    def __init__(self, project_root: Optional[Path] = None):
        """Initialize Java adapter and check tool availability."""
        self.project_root = project_root
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
            f"google_java_format_jar={self._google_java_format_jar is not None}"
        )
    
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
        Lint code using checkstyle.
        
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
            logger.warning("java not available, skipping lint")
            return code, []
        
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
                    
                    # Parse XML output
                    if result.stdout.strip():
                        try:
                            root = ET.fromstring(result.stdout)
                            for file_elem in root.findall('file'):
                                for error_elem in file_elem.findall('error'):
                                    issues.append({
                                        'line': int(error_elem.get('line', 0)),
                                        'column': int(error_elem.get('column', 0)),
                                        'message': error_elem.get('message', ''),
                                        'severity': error_elem.get('severity', 'error'),
                                    })
                        except ET.ParseError:
                            logger.warning("Failed to parse checkstyle XML output")
                except Exception as e:
                    logger.warning(f"checkstyle check failed: {e}")
        
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
        
        Args:
            files: List of (code, file_path) tuples
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
            
            # Write all files to temp directory preserving package structure
            for code, file_path in files:
                # Normalize path
                rel_path = Path(file_path)
                if rel_path.is_absolute():
                    rel_path = Path(rel_path.name)
                
                temp_file = Path(temp_dir) / rel_path
                temp_file.parent.mkdir(parents=True, exist_ok=True)
                temp_file.write_text(code, encoding='utf-8')
                written_files.append(str(temp_file))
            
            # Build classpath from jar files and Maven target/classes if exists
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
                # Use os.pathsep for platform-independent classpath separator
                classpath = os.pathsep.join(classpath_parts)
                cmd.extend(['-cp', classpath])
            
            # Add all Java files
            cmd.extend(written_files)
            
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
                'results': [{'file_path': fp, 'success': False, 'stderr': 'javac compilation timed out', 'exit_code': -1} for _, fp in files],
                'stderr': 'javac compilation timed out',
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
