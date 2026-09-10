"""Tests for the `basicly catalog lint` command, as opposed to the lint function.

The gate's own unit tests call ``lint_catalog`` and always passed. The command wraps it
in an advisory pass that loads every skill, and a source the schema refuses raised there
— so the command failed with a diagnostic the function never produced, and the
per-file migration the function *did* produce was never printed. Reported live from a
repo vendored at 0.5.1, whose catalog predates the invocation axis (basicly-m4zv.9).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly.cli import cmd_catalog_lint

if TYPE_CHECKING:
    import pytest

REPO = Path(__file__).parent.parent
_INSTRUCTIONS = "token_cost:\n  listing: 6\ninstructions: |\n  # x\n\n  text\n"
_VALID = f"schema_version: 1\nname: s\ninvocation: model\ndescription: d\n{_INSTRUCTIONS}"
_PRE_AXIS = f"schema_version: 1\nname: legacy\ndescription: d\n{_INSTRUCTIONS}"


def _catalog(tmp_path: Path, skills: dict[str, str]) -> Path:
    """A minimal catalog carrying the real schemas and the given skill sources."""
    schemas = tmp_path / ".basicly/core/schemas"
    schemas.mkdir(parents=True)
    for name in ("skill.schema.json", "fragment.schema.json", "agent.schema.json"):
        (schemas / name).write_text(
            (REPO / ".basicly/core/schemas" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    for slug, body in skills.items():
        source = tmp_path / ".basicly/core/skills" / slug / "skill.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(body, encoding="utf-8")
    return tmp_path


def test_a_clean_catalog_passes_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The positive control: without it, a command that always failed reads as a pass."""
    monkeypatch.chdir(_catalog(tmp_path, {"s": _VALID}))

    exit_code = cmd_catalog_lint(argparse.Namespace())

    assert exit_code == 0
    assert "catalog lint: OK" in capsys.readouterr().out


def test_a_pre_axis_source_still_gets_its_migration_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The advisory raise must not take the violation list down with it."""
    monkeypatch.chdir(_catalog(tmp_path, {"s": _VALID, "legacy": _PRE_AXIS}))

    exit_code = cmd_catalog_lint(argparse.Namespace())

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "no 'invocation' declared" in err
    assert "invocation: model" in err
    assert "advisories unavailable" in err


def test_the_command_names_every_pre_axis_source_not_just_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A whole-catalog break read as one bad file.

    The raise stopped at the alphabetically first source, so the consumer had to count
    all 31 by hand to learn the scope.
    """
    skills = {f"legacy{index}": _PRE_AXIS for index in range(3)}
    monkeypatch.chdir(_catalog(tmp_path, {"s": _VALID, **skills}))

    cmd_catalog_lint(argparse.Namespace())

    err = capsys.readouterr().err
    for slug in skills:
        assert slug in err, f"{slug} was never named"


def test_the_shipped_schema_agrees_with_the_validator_on_what_is_required() -> None:
    """A field required by code but absent from the schema is invisible to a consumer.

    The vendored schema is the only statement of the contract a pinned repo can read,
    so the two must not disagree about `invocation`.
    """
    schema = json.loads(
        (REPO / ".basicly/core/schemas/skill.schema.json").read_text(encoding="utf-8")
    )

    assert "invocation" in schema["required"], "the validator requires what the schema omits"
