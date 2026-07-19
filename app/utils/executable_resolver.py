"""Centralized cross-platform executable path resolution.

On Windows, subprocess.run(['npm', ...]) with shell=False fails with
[WinError 2] because npm is actually npm.cmd, which is not recognized
by CreateProcess. Using shutil.which() correctly finds .cmd/.bat
files via PATHEXT, solving this issue.

This module provides resolve_executable() to resolve tool names to
their full path, ensuring subprocess.run works cross-platform.
"""

import shutil
from typing import Optional


def resolve_executable(name: str) -> str:
    """Resolve an executable name to its full path using shutil.which().
    
    On Windows, this correctly finds .cmd/.bat wrapper files via PATHEXT,
    solving the issue where subprocess.run(['npm', ...]) with shell=False
    fails with [WinError 2].
    
    If the executable is not found on the system PATH, the original name
    is returned unchanged, preserving existing error handling.
    
    Args:
        name: Executable name (e.g., 'npm', 'node', 'tsc')
        
    Returns:
        Full path to the executable if found, otherwise the original name.
    """
    resolved = shutil.which(name)
    return resolved if resolved is not None else name