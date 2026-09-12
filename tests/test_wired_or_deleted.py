from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "wired_or_deleted.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wired = _load(SCRIPT, "wired_or_deleted")

FIELD = "holds"
FINDING = f"record-field:basicly.sample.Sample.{FIELD}"


def _repo_with_field(root: Path) -> None:
    module = root / "src" / "basicly" / "sample.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "from dataclasses import dataclass\n\n\n"
        f"@dataclass(frozen=True)\nclass Sample:\n    {FIELD}: int\n",
        encoding="utf-8",
    )


def _schema(root: Path, body: dict[str, Any]) -> None:
    path = root / ".basicly" / "core" / "schemas" / "verdict.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def _template(root: Path, text: str) -> None:
    path = root / ".basicly" / "core" / "templates" / "claude" / "rule_md.j2"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _field_keys(root: Path) -> list[str]:
    return [finding.key for finding in wired.field_findings(root, wired.build_index(root))]


def test_a_field_named_in_schema_prose_only_is_still_a_finding(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _schema(
        tmp_path,
        {
            "title": "removal verdict",
            "description": "Whatever the removal holds while a lane is still live.",
            "type": "object",
        },
    )

    assert _field_keys(tmp_path) == [FINDING]


def test_a_title_is_prose_too_and_does_not_wire_a_field(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _schema(tmp_path, {"title": f"what a verdict {FIELD}", "type": "object"})

    assert _field_keys(tmp_path) == [FINDING]


def test_a_schema_key_named_for_a_field_is_a_reference(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _schema(
        tmp_path,
        {
            "type": "object",
            "properties": {FIELD: {"type": "array", "description": "Reasons to keep it."}},
        },
    )

    assert _field_keys(tmp_path) == []


def test_a_required_entry_names_a_field_as_a_key_does(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _schema(tmp_path, {"type": "object", "required": [FIELD]})

    assert _field_keys(tmp_path) == []


def test_an_enum_value_is_a_reference_as_a_key_is(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _schema(tmp_path, {"type": "object", "properties": {"verdict": {"enum": [FIELD, "drop"]}}})

    assert _field_keys(tmp_path) == []


def test_a_const_value_is_a_reference_as_an_enum_member_is(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _schema(tmp_path, {"type": "object", "properties": {"verdict": {"const": FIELD}}})

    assert _field_keys(tmp_path) == []


def test_a_schema_that_is_not_json_fails_the_gate_rather_than_reverting_to_prose(
    tmp_path: Path,
) -> None:

    _repo_with_field(tmp_path)
    path = tmp_path / ".basicly" / "core" / "schemas" / "notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"The verdict {FIELD} until the lane ends.\n", encoding="utf-8")

    with pytest.raises(wired.WiringError, match=r"schemas/notes\.md"):
        wired.build_index(tmp_path)


def test_a_template_literal_is_prose_and_does_not_wire_a_field(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _template(tmp_path, f"# What the rule {FIELD}\n\n{{{{ fragment.body }}}}\n")

    assert _field_keys(tmp_path) == [FINDING]


def test_a_template_expression_naming_a_field_is_a_reference(tmp_path: Path) -> None:
    _repo_with_field(tmp_path)
    _template(
        tmp_path, f"{{% for reason in fragment.{FIELD} %}}- {{{{ reason }}}}\n{{% endfor %}}\n"
    )

    assert _field_keys(tmp_path) == []


def test_this_repos_schemas_donate_their_keys_and_not_their_descriptions() -> None:

    names = wired.schema_names((REPO_ROOT / ".basicly/core/schemas/skill.schema.json").read_text())

    assert "schema_version" in names
    assert "packages" not in names
