from __future__ import annotations

from pathlib import Path

import pytest

from basicly import skill_standard

REPO = Path(__file__).resolve().parents[1]

TASKS = """\
schema_version: 1
tasks:
  - query: one
    expected_behavior: [a]
  - query: two
    expected_behavior: [b]
  - query: three
    expected_behavior: [c]
"""


def _skill(root: Path, slug: str, body: str = "# Title\n", evals: str | None = TASKS) -> Path:
    skill_dir = root / ".basicly/core/skills" / slug
    skill_dir.mkdir(parents=True)
    indented = "".join(f"  {line}\n" for line in body.splitlines())
    (skill_dir / "skill.yaml").write_text(
        f"schema_version: 1\nname: {slug}\ninvocation: model\ninstructions: |\n{indented}",
        encoding="utf-8",
    )
    if evals is not None:
        (skill_dir / "evals.yaml").write_text(evals, encoding="utf-8")
    return skill_dir


def _found(root: Path, debt: frozenset[str] = frozenset()) -> list[str]:
    return skill_standard.violations(root, debt)


def test_a_complete_skill_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "good")
    assert _found(tmp_path) == []


def test_a_skill_with_no_evals_is_refused_by_name(tmp_path: Path) -> None:
    _skill(tmp_path, "bare", evals=None)
    assert _found(tmp_path) == [
        ".basicly/core/skills/bare/skill.yaml: has no evals.yaml; add trigger cases and "
        "at least 3 task cases (see skill-creator)"
    ]


def test_two_task_cases_are_one_too_few(tmp_path: Path) -> None:
    two = TASKS.rsplit("  - query: three", 1)[0]
    _skill(tmp_path, "short", evals=two)
    assert _found(tmp_path) == [
        ".basicly/core/skills/short/skill.yaml: evals.yaml has 2 task cases; add at least 3, "
        "each with a query and its expected_behavior"
    ]


def test_a_task_naming_a_missing_input_file_is_refused(tmp_path: Path) -> None:
    evals = TASKS.replace(
        "expected_behavior: [a]", "files: [assets/in.txt]\n    expected_behavior: [a]"
    )
    _skill(tmp_path, "inputs", evals=evals)
    assert _found(tmp_path) == [
        ".basicly/core/skills/inputs/skill.yaml: evals.yaml task 1 names file "
        "'assets/in.txt', which does not exist"
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Read [x](references/gone.md).", ["links 'references/gone.md', which is not a file"]),
        ("Run `python3 scripts/gone.py`.", ["links 'scripts/gone.py', which is not a file"]),
        (
            "Read references/deep/x.md.",
            [
                "links 'references/deep/x.md', which is not a file",
                "links 'references/deep/x.md' more than one level deep",
            ],
        ),
    ],
)
def test_a_broken_or_deep_link_is_refused(tmp_path: Path, body: str, expected: list[str]) -> None:
    _skill(tmp_path, "links", body=body)
    found = _found(tmp_path)
    assert len(found) == len(expected)
    for line, fragment in zip(found, expected, strict=True):
        assert fragment in line


def test_a_link_to_an_existing_resource_passes(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, "linked", body="Read [x](references/x.md) and run scripts/x.py.")
    (skill_dir / "references").mkdir()
    (skill_dir / "references/x.md").write_text("x\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts/x.py").write_text("x = 1\n", encoding="utf-8")
    assert _found(tmp_path) == []


def test_a_long_reference_needs_a_contents_heading(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, "long")
    (skill_dir / "references").mkdir()
    long = "line\n" * (skill_standard.TOC_LINE_THRESHOLD + 1)
    (skill_dir / "references/long.md").write_text(long, encoding="utf-8")
    assert len(_found(tmp_path)) == 1
    (skill_dir / "references/long.md").write_text("## Contents\n" + long, encoding="utf-8")
    assert _found(tmp_path) == []


def test_a_body_over_the_cap_is_refused(tmp_path: Path) -> None:
    _skill(tmp_path, "huge", body="line\n" * (skill_standard.MAX_BODY_LINES + 1))
    assert "SKILL.md body is 501 lines" in _found(tmp_path)[0]


def test_a_skill_in_debt_is_exempt(tmp_path: Path) -> None:
    _skill(tmp_path, "old", evals=None)
    assert _found(tmp_path, frozenset({"old"})) == []


def test_a_debt_entry_that_now_passes_must_be_removed(tmp_path: Path) -> None:
    _skill(tmp_path, "fixed")
    assert _found(tmp_path, frozenset({"fixed"})) == [
        ".basicly/core/skills/fixed/skill.yaml: 'fixed' now meets the skill standard; "
        "remove it from DEBT in src/basicly/skill_standard.py"
    ]


def test_the_shipped_catalog_meets_the_standard_or_is_in_debt() -> None:
    assert skill_standard.violations(REPO) == []
