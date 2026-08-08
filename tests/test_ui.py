"""The shared terminal UI: one console, and the plain-text contract piped output keeps.

Written when the §9.4 naming gate was made binding (basicly-u2hl.14). `ui` is the oldest
of the ten modules the gate found uncovered and the only one not created by the 2026-08-08
splits: every CLI command routes its user-facing lines through it, and nothing asserted
the one thing it promises — that piped output stays byte-compatible plain text with the
exact same wording a terminal would show.

Every assertion here is about the *piped* branch, which is the one CI, log greps and this
suite see. That is not a gap: the styled branch differs only in the escape codes rich
adds, and the two settings that make the promise true — `soft_wrap` against rich's
80-column default and `markup=False` against literal brackets — change the bytes on both.
"""

from __future__ import annotations

import pytest
from rich.errors import MissingStyle

from basicly import ui

# Every style name the CLI passes to `say`. Five, and they are the theme's own keys —
# an unknown one raises (asserted below), so a sixth is a crash rather than a plain line.
THEME_STYLES = ("ok", "warn", "err", "accent", "muted")


def test_a_line_longer_than_eighty_columns_is_not_wrapped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`soft_wrap` against rich's 80-column default for a non-terminal.

    Without it a message a consumer greps for is broken across lines at whatever width
    rich assumed, and the grep fails on output the code got right.
    """
    line = "x" * 200

    ui.say(line)

    assert capsys.readouterr().out == line + "\n"


def test_literal_brackets_survive_verbatim(capsys: pytest.CaptureFixture[str]) -> None:
    """`markup=False`, and the text this repo prints is full of brackets.

    A bead title, a gate finding or a rendered argv can carry `[...]`; with markup on,
    rich reads it as a tag and prints the message with a piece missing — or raises on a
    tag it cannot parse.
    """
    ui.say("verify fast failed: [pytest] and [/ruff]")

    assert capsys.readouterr().out == "verify fast failed: [pytest] and [/ruff]\n"


def test_a_style_changes_no_bytes_when_the_output_is_piped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same wording styled and unstyled, which is what "exact same wording" means."""
    ui.say("done", style="ok")
    ui.say("done")

    out = capsys.readouterr().out
    assert out.splitlines() == ["done", "done"]


@pytest.mark.parametrize("style", THEME_STYLES)
def test_every_style_the_cli_uses_resolves(style: str, capsys: pytest.CaptureFixture[str]) -> None:
    """A style absent from the theme raises rather than degrading to plain output.

    So an unknown name is a crash on a piped run too, not a colour that only fails to
    appear on somebody's terminal — which is why this is checkable here at all.
    """
    ui.say("text", style=style)

    assert capsys.readouterr().out == "text\n"


def test_a_style_outside_the_theme_is_an_error() -> None:
    """The control for the test above: without it, a theme of nothing would pass."""
    with pytest.raises(MissingStyle):
        ui.say("text", style="nosuchstyle")


def test_warn_and_fail_go_to_stderr_and_say_does_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller piping stdout into a parser must not receive diagnostics in it."""
    ui.say("out")
    ui.heading("head")
    ui.warn("careful")
    ui.fail("broken")

    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["out", "head"]
    assert captured.err.splitlines() == ["careful", "broken"]


def test_a_table_renders_its_title_columns_and_every_row(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Left-justified title, then the header, then one line per row."""
    ui.table("Measured surface (2)", ["binary", "calls"], [["br", "42"], ["bv", "7"]])

    out = capsys.readouterr().out
    assert out.startswith("Measured surface (2)\n")
    assert "binary" in out
    for cell in ("br", "42", "bv", "7"):
        assert cell in out


def test_a_table_cell_does_not_keep_its_brackets_the_way_a_line_does(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Measured, and it is an asymmetry with :func:`say` rather than a stated rule.

    `markup=False` is set on the console's `print`, and a `Table`'s cells are rendered
    with markup regardless — so `[red]v[/red]` in a cell prints as `v`. Anything routing
    text that may contain brackets into a table loses it silently; pinned here so a
    change to either half has to be deliberate.
    """
    ui.table("T", ["a"], [["[red]v[/red]"]])

    out = capsys.readouterr().out
    assert "[red]" not in out
    assert "│ v" in out


def test_the_two_consoles_are_configured_for_piped_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Highlighting off, so a number or a path in a message is not recoloured mid-line.

    Asserted by output rather than by reading the flag: a digit and a quoted path both
    come back exactly as handed over.
    """
    ui.say("wrote 42 rows to /tmp/x.json")
    ui.warn("wrote 42 rows to /tmp/x.json")

    captured = capsys.readouterr()
    assert captured.out == "wrote 42 rows to /tmp/x.json\n"
    assert captured.err == "wrote 42 rows to /tmp/x.json\n"
