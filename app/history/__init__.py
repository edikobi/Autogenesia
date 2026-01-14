# app/history/__init__.py
"""
History management module.

Provides:
- HistoryManager: Main entry point for conversation history
- IntraSessionCompressor: Context compression within agent sessions (NEW!)
"""

from app.history.manager import HistoryManager
from app.history.storage import Message, HistoryStorage
from app.history.compressor import compress_history_if_needed
from app.history.context_manager import (
    IntraSessionCompressor,
    maybe_compress_context,
    get_compressor,
    reset_compressor,
    is_context_overflow_error,
    PROACTIVE_COMPRESSION_MODELS,
)

__all__ = [
    "HistoryManager",
    "Message",
    "HistoryStorage",
    "compress_history_if_needed",
    # New exports for intra-session compression
    "IntraSessionCompressor",
    "maybe_compress_context",
    "get_compressor",
    "reset_compressor",
    "is_context_overflow_error",
    "PROACTIVE_COMPRESSION_MODELS",
]