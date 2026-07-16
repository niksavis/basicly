"""Shared terminal UI: one rich Console, styled only on a real terminal.

Every CLI command routes user-facing lines through this module. On a TTY the
output gains color and tables; piped/redirected output (tests, CI, log greps)
stays plain text with the exact same wording — ``soft_wrap`` disables rich's
default 80-column wrapping and ``markup=False`` keeps literal brackets safe, so
the byte-compatibility contract with existing consumers holds.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

_THEME = Theme({
    "ok": "green",
    "warn": "yellow",
    "err": "red",
    "accent": "bold cyan",
    "muted": "dim",
})

console = Console(highlight=False, soft_wrap=True, theme=_THEME)
err_console = Console(stderr=True, highlight=False, soft_wrap=True, theme=_THEME)


def say(text: str, style: str | None = None) -> None:
    """Print one plain-worded line, styled only when stdout is a terminal."""
    console.print(text, style=style, markup=False)


def warn(text: str) -> None:
    """Print one warning line to stderr."""
    err_console.print(text, style="warn", markup=False)


def fail(text: str) -> None:
    """Print one error line to stderr."""
    err_console.print(text, style="err", markup=False)


def heading(text: str) -> None:
    """Print a section heading (install steps and similar)."""
    console.print(text, style="accent", markup=False)


def table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    """Render a titled table (rich box on a TTY, ASCII when piped)."""
    grid = Table(title=title, title_justify="left", header_style="accent")
    for column in columns:
        grid.add_column(column, overflow="fold")
    for row in rows:
        grid.add_row(*row)
    console.print(grid)
