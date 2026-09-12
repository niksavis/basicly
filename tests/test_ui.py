from __future__ import annotations

import pytest
from rich.errors import MissingStyle

from basicly import ui

THEME_STYLES = ("ok", "warn", "err", "accent", "muted")


def test_a_line_longer_than_eighty_columns_is_not_wrapped(
    capsys: pytest.CaptureFixture[str],
) -> None:

    line = "x" * 200

    ui.say(line)

    assert capsys.readouterr().out == line + "\n"


def test_literal_brackets_survive_verbatim(capsys: pytest.CaptureFixture[str]) -> None:

    ui.say("verify fast failed: [pytest] and [/ruff]")

    assert capsys.readouterr().out == "verify fast failed: [pytest] and [/ruff]\n"


def test_a_style_changes_no_bytes_when_the_output_is_piped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui.say("done", style="ok")
    ui.say("done")

    out = capsys.readouterr().out
    assert out.splitlines() == ["done", "done"]


@pytest.mark.parametrize("style", THEME_STYLES)
def test_every_style_the_cli_uses_resolves(style: str, capsys: pytest.CaptureFixture[str]) -> None:

    ui.say("text", style=style)

    assert capsys.readouterr().out == "text\n"


def test_a_style_outside_the_theme_is_an_error() -> None:
    with pytest.raises(MissingStyle):
        ui.say("text", style="nosuchstyle")


def test_warn_and_fail_go_to_stderr_and_say_does_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    ui.table("Ready (2)", ["record", "score"], [["b-1", "4001"], ["b-2", "3002"]])

    out = capsys.readouterr().out
    assert out.startswith("Ready (2)\n")
    assert "record" in out
    for cell in ("b-1", "4001", "b-2", "3002"):
        assert cell in out


def test_a_table_cell_does_not_keep_its_brackets_the_way_a_line_does(
    capsys: pytest.CaptureFixture[str],
) -> None:

    ui.table("T", ["a"], [["[red]v[/red]"]])

    out = capsys.readouterr().out
    assert "[red]" not in out
    assert "│ v" in out


def test_the_two_consoles_are_configured_for_piped_output(
    capsys: pytest.CaptureFixture[str],
) -> None:

    ui.say("wrote 42 rows to /tmp/x.json")
    ui.warn("wrote 42 rows to /tmp/x.json")

    captured = capsys.readouterr()
    assert captured.out == "wrote 42 rows to /tmp/x.json\n"
    assert captured.err == "wrote 42 rows to /tmp/x.json\n"
