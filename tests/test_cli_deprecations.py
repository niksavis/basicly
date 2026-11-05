from __future__ import annotations

import pytest

from basicly import cli

ARGV = {
    ("catalog", "review"): ["catalog", "review"],
    ("health",): ["health"],
    ("rubric", "eval"): ["rubric", "eval", "x"],
    ("runner", "run"): ["runner", "run", "--prompt", "p"],
    ("usage", "forecast"): ["usage", "forecast"],
}


def test_every_deprecated_command_has_an_invocation_here() -> None:
    assert set(ARGV) == set(cli.DEPRECATED_COMMANDS)


@pytest.mark.parametrize("path", sorted(cli.DEPRECATED_COMMANDS))
def test_a_deprecated_command_warns_and_names_a_replacement_that_parses(
    path: tuple[str, ...],
) -> None:
    parser = cli._build_parser()
    warning = cli.deprecation_warning(parser.parse_args(ARGV[path]))
    assert warning is not None
    assert warning.startswith(f"basicly {' '.join(path)}: deprecated")
    replacement = cli.DEPRECATED_COMMANDS[path]
    if not replacement:
        assert "no replacement is planned" in warning
        return
    assert f"`{replacement}`" in warning
    words = replacement.replace("<id>", "x").split()[1:]
    assert cli.deprecation_warning(parser.parse_args(words)) is None


def test_a_wired_command_carries_no_warning() -> None:
    args = cli._build_parser().parse_args(["catalog", "list", "skill"])
    assert cli.deprecation_warning(args) is None


def test_the_warning_goes_to_stderr_before_the_command_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_handlers", lambda: {"usage": lambda _args: 0})
    assert cli.main(["usage", "forecast"]) == 0
    captured = capsys.readouterr()
    assert captured.err.startswith("basicly usage forecast: deprecated")
    assert "basicly usage forecast" not in captured.out
