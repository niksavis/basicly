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
    console.print(text, style=style, markup=False)


def warn(text: str) -> None:
    err_console.print(text, style="warn", markup=False)


def fail(text: str) -> None:
    err_console.print(text, style="err", markup=False)


def heading(text: str) -> None:
    console.print(text, style="accent", markup=False)


def table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    grid = Table(title=title, title_justify="left", header_style="accent")
    for column in columns:
        grid.add_column(column, overflow="fold")
    for row in rows:
        grid.add_row(*row)
    console.print(grid)
