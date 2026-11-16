"""Отдельное read-only окно просмотра кода (DiffViewerScreen).

Экран только для просмотра: никаких действий с файлами и мутаций.
Закрытие — Escape или кнопка; после закрытия фокус возвращается в
``#query-input`` (через callback ``push_screen``).
"""

from __future__ import annotations

import logging
from os.path import splitext
from typing import Any, Dict, List, Optional

from rich.syntax import Syntax
from rich.text import Text

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RichLog, TabPane, TabbedContent

logger = logging.getLogger(__name__)

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".java": "java", ".go": "go",
    ".json": "json", ".md": "markdown", ".css": "css", ".html": "html",
    ".yml": "yaml", ".yaml": "yaml", ".sh": "bash", ".sql": "sql", ".txt": "text",
}


def guess_lexer(path: str) -> str:
    """Определить язык подсветки по расширению файла."""
    return _LANG_BY_EXT.get(splitext(path)[1].lower(), "text")


def full_content_of_change(change: Any) -> str:
    """Полное новое содержимое из VFS PendingChange или pipeline PendingChange."""
    content = getattr(change, "new_content", None)
    if isinstance(content, str) and content:
        return content
    block = getattr(change, "code_block", None)
    if block is not None:
        code = getattr(block, "code", None)
        if isinstance(code, str):
            return code
    return ""


class DiffViewerScreen(ModalScreen):
    """Read-only просмотр изменений: Diff ↔ Full по клавише F."""

    BINDINGS = [
        Binding("escape", "close", "Закрыть"),
        Binding("f", "toggle_mode", "Diff ↔ Full"),
    ]

    def __init__(
        self,
        diffs: Dict[str, str],
        pending_changes: Optional[List[Any]] = None,
        deletions: Optional[List[Any]] = None,
    ) -> None:
        super().__init__()
        self.diffs: Dict[str, str] = dict(diffs or {})
        self.pending_changes: List[Any] = list(pending_changes or [])
        self.deletions: List[Any] = list(deletions) if isinstance(deletions, list) else []
        self._full_mode = False
        self._log_by_path: Dict[str, RichLog] = {}
        self._pane_by_path: Dict[str, TabPane] = {}
        self._change_by_path: Dict[str, Any] = {}
        for change in self.pending_changes:
            path = str(getattr(change, "file_path", "") or "")
            if path:
                self._change_by_path.setdefault(path, change)
        self._deletions_log: Optional[RichLog] = None

    def compose(self) -> ComposeResult:
        """Вкладка на файл + отдельная вкладка «Удалённые файлы».

        Textual 8.x контракт: TabbedContent принимает строковые заголовки
        позиционными аргументами (``TabbedContent(*titles, id=...)``), а
        TabPane-панели отдаются дочерними элементами внутри ``with``-блока.
        Передача TabPane позиционными аргументами приводит к
        ``'TabPane' object has no attribute 'translate'``.
        """
        panes = []
        titles = []
        for path in self.diffs.keys():
            log = RichLog(wrap=True, markup=False, auto_scroll=False)
            self._log_by_path[path] = log
            label = self._pane_label(path)
            pane = TabPane(label, log)
            self._pane_by_path[path] = pane
            panes.append(pane)
            titles.append(label)
        if self.deletions:
            self._deletions_log = RichLog(wrap=True, markup=False)
            panes.append(TabPane("🗑 Удалённые файлы", self._deletions_log))
            titles.append("🗑 Удалённые файлы")
        if not panes:
            panes.append(TabPane("(нет изменений)", Label("Нет изменений для отображения")))
            titles.append("(нет изменений)")
        with Vertical(id="diff-viewer"):
            with Horizontal(id="diff-header"):
                yield Label(
                    "👁 Просмотр изменений — F: Diff↔Full, Esc: закрыть", id="diff-title"
                )
                yield Button("[ ✖ Закрыть ]", id="diff-close-btn")
            with TabbedContent(*titles, id="diff-tabs"):
                for pane in panes:
                    yield pane

    def on_mount(self) -> None:
        """Отрисовать содержимое вкладок и вернуть фокус на просмотрщик."""
        self._render_all()
        try:
            self.query_one("#diff-tabs", TabbedContent).focus()
        except Exception:
            pass

    def _pane_label(self, path: str) -> str:
        """Заголовок вкладки с индикатором режима Diff/Full."""
        mode = "Full" if self._full_mode else "Diff"
        return f"{path} [[{mode}]]"

    def _content_for(self, path: str) -> str:
        """Diff или полный файл в зависимости от режима."""
        if self._full_mode:
            change = self._change_by_path.get(path)
            return full_content_of_change(change) if change is not None else ""
        return self.diffs.get(path, "")

    @staticmethod
    def _write_code(log: RichLog, path: str, content: str) -> None:
        """Подсветка Syntax (github-light); при SyntaxError/ValueError — plain."""
        if not content:
            log.write(Text("(пусто)", style="dim"))
            return
        try:
            log.write(Syntax(content, guess_lexer(path), theme="github-light", line_numbers=True))
        except Exception:
            log.write(content)

    def _render_all(self) -> None:
        """Перерисовать все вкладки текущим режимом."""
        for path, log in self._log_by_path.items():
            log.clear()
            self._write_code(log, path, self._content_for(path))
        self._refresh_labels()
        if self._deletions_log is not None:
            self._deletions_log.clear()
            for d in self.deletions:
                if isinstance(d, dict):
                    status = "✓" if d.get("success") else "✗"
                    self._deletions_log.write(
                        f"{status} {d.get('target', '')} ({d.get('type', '')}) в {d.get('file', '')}"
                    )
                    if d.get("reason"):
                        self._deletions_log.write(f"    причина: {d['reason']}")
                else:
                    self._deletions_log.write(str(d))

    def _refresh_labels(self) -> None:
        """Обновить подписи вкладок (безопасно, best-effort)."""
        try:
            tabs = self.query_one("#diff-tabs", TabbedContent)
        except Exception:
            logger.debug("Could not update pane label", exc_info=True)
            return
        for path, pane in self._pane_by_path.items():
            try:
                tab = tabs.get_tab(pane)
                tab.label = self._pane_label(path)
            except Exception:
                logger.debug("Could not update pane label", exc_info=True)

    def action_toggle_mode(self) -> None:
        """Переключить Diff ↔ Full."""
        self._full_mode = not self._full_mode
        self._render_all()

    def action_close(self) -> None:
        """Закрыть экран (Escape)."""
        self.dismiss(None)

    @on(Button.Pressed, "#diff-close-btn")
    def _on_close_btn(self, event: Button.Pressed) -> None:
        """Закрыть экран кнопкой."""
        event.stop()
        self.dismiss(None)


def show_diff_viewer(app, diffs: Dict[str, str], pending_changes: Optional[List[Any]] = None) -> None:
    """Открыть просмотрщик поверх приложения; после закрытия — фокус в #query-input."""
    source = dict(diffs or {})
    deletions = source.pop("__deletions__", None)

    def _refocus(_result=None) -> None:
        """Вернуть фокус в поле ввода активного воркспейса."""
        try:
            app.query_one("#query-input").focus()
        except Exception:
            pass

    screen = DiffViewerScreen(source, pending_changes=pending_changes, deletions=deletions)
    app._push_user_screen(screen, _refocus)
