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


_EVALS = """\
schema_version: 1
trigger:
  positive:
    - prompt: "{own} one"
    - prompt: "{own} two"
    - prompt: "{own} three"
  negative:
    - prompt: "{other} please"
      owner: {owner}
    - prompt: "{other} now"
      owner: {owner}
tasks:
  - query: q1
    expected_behavior: [b1]
  - query: q2
    expected_behavior: [b2]
  - query: q3
    expected_behavior: [b3]
"""


def _standard_catalog(tmp_path: Path) -> Path:
    words = {"parse": "Parse json documents in pipelines.", "draw": "Draw directory trees."}
    root = _catalog(
        tmp_path,
        {
            slug: _VALID.replace("name: s", f"name: {slug}").replace(
                "description: d", f"description: {text}"
            )
            for slug, text in words.items()
        },
    )
    (root / ".basicly/core/schemas/evals.schema.json").write_text(
        (REPO / ".basicly/core/schemas/evals.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for slug, other, owner in (
        ("parse", "draw directory trees", "draw"),
        ("draw", "parse json documents", "parse"),
    ):
        own = words[slug].lower().rstrip(".")
        (root / ".basicly/core/skills" / slug / "evals.yaml").write_text(
            _EVALS.format(own=own, other=other, owner=owner), encoding="utf-8"
        )
    return root


def test_a_skill_with_no_evals_fails_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(_catalog(tmp_path, {"s": _VALID}))

    assert cmd_catalog_lint(argparse.Namespace()) == 1
    assert "s/skill.yaml: has no evals.yaml" in capsys.readouterr().err


def test_a_clean_catalog_passes_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(_standard_catalog(tmp_path))

    exit_code = cmd_catalog_lint(argparse.Namespace())

    assert exit_code == 0
    assert "catalog lint: OK" in capsys.readouterr().out


def test_a_pre_axis_source_still_gets_its_migration_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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

    skills = {f"legacy{index}": _PRE_AXIS for index in range(3)}
    monkeypatch.chdir(_catalog(tmp_path, {"s": _VALID, **skills}))

    cmd_catalog_lint(argparse.Namespace())

    err = capsys.readouterr().err
    for slug in skills:
        assert slug in err, f"{slug} was never named"


def test_the_shipped_schema_agrees_with_the_validator_on_what_is_required() -> None:

    schema = json.loads(
        (REPO / ".basicly/core/schemas/skill.schema.json").read_text(encoding="utf-8")
    )

    assert "invocation" in schema["required"], "the validator requires what the schema omits"
