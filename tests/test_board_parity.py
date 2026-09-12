from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from basicly import board_schema, board_sections, board_snapshot

REPO_ROOT = Path(__file__).parent.parent
FOREIGN = REPO_ROOT / "tests" / "fixtures" / "board" / "foreign"
PRODUCER = FOREIGN / "produce.py"
EXPORT = FOREIGN / "export.json"
SNAPSHOT = FOREIGN / "snapshot.json"

_NOW = datetime(2026, 8, 21, 9, 15, tzinfo=UTC)

DECLARED_ASYMMETRY = {
    "session": "needs a live supervisor lock; a file export has no running supervisor",
    "lanes": "in-flight loop lanes, and a foreign tracker drives no loop",
    "asks": "basicly checkpoint wait markers; the export has no wait vocabulary",
    "gates": "read from `.basicly/usage/`, which a foreign harness does not write",
    "spend": "needs a per-run usage format; the declared limit is to omit, never estimate",
    "health": "same source as `spend`, and omitted for the same reason",
    "graph": "the export carries no dependency edges, so any edge would be invented",
    "events": "basicly marker rows folded out of its own event log",
    "detail": (
        "loop state per record - the worktree binding, the checkpoints, the rework tally and "
        "the command that moves it - none of which a tracker that drives no loop holds"
    ),
}


def optional_sections() -> tuple[str, ...]:
    schema = json.loads(
        (REPO_ROOT / ".basicly" / "core" / "schemas" / board_schema.SCHEMA_FILE).read_text(
            encoding="utf-8"
        )
    )
    required = set(schema["required"])
    return tuple(name for name in schema["properties"] if name not in required)


def foreign_document() -> dict[str, object]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def reference_document(repo_root: Path) -> dict[str, object]:

    facts = board_snapshot.Facts(
        session=board_snapshot.SessionFacts(root_issue="basicly-rn0o"),
        lanes=[board_sections.LaneFacts(id="basicly-rn0o.13", phase="build")],
    )
    return board_snapshot.build_document(repo_root, facts=facts, now=_NOW)


def emitted(document: dict[str, object], sections: tuple[str, ...]) -> set[str]:
    return {name for name in sections if name in document}


def parity_gap(reference: set[str], foreign: set[str], declared: set[str]) -> tuple[str, ...]:

    return tuple(sorted(reference - foreign - declared))


def test_the_conformance_check_runs_against_both_producers(work_repo: Path) -> None:
    for label, document in (
        ("reference", reference_document(work_repo)),
        ("foreign", foreign_document()),
    ):
        verdict = board_schema.verdict(work_repo, document)
        assert verdict.outcome == board_schema.OK, f"{label}: {verdict.summary}"
        assert verdict.exit_code == 0, f"{label}: {verdict.summary}"


def test_the_second_producer_imports_no_basicly_module() -> None:
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import would give it a package to sit in"
            roots.add((node.module or "").split(".")[0])
    assert roots, "no imports found at all, so this probe proves nothing"
    assert "basicly" not in roots
    outside = roots - sys.stdlib_module_names
    assert not outside, f"not standard library: {outside}"


def test_the_second_producer_runs_with_basicly_unimportable(tmp_path: Path) -> None:

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "basicly.py").write_text('raise ImportError("poisoned")\n', encoding="utf-8")
    env = {"PYTHONPATH": str(shadow), "PYTHONDONTWRITEBYTECODE": "1"}

    control = subprocess.run(
        [sys.executable, "-c", "import basicly"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert control.returncode != 0, "the shadow module did not shadow anything"
    assert "poisoned" in control.stderr

    run = subprocess.run(
        [sys.executable, str(PRODUCER), str(EXPORT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == SNAPSHOT.read_text(encoding="utf-8")


def test_the_checked_in_foreign_snapshot_is_what_the_producer_emits() -> None:
    run = subprocess.run(
        [sys.executable, str(PRODUCER), str(EXPORT)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert run.stdout == SNAPSHOT.read_text(encoding="utf-8")


def test_no_undeclared_section_is_emitted_by_the_reference_producer_alone(
    work_repo: Path,
) -> None:
    sections = optional_sections()
    gap = parity_gap(
        emitted(reference_document(work_repo), sections),
        emitted(foreign_document(), sections),
        set(DECLARED_ASYMMETRY),
    )
    assert gap == (), (
        f"the reference producer emits {', '.join(gap)} and the foreign producer does not, "
        "with no entry in DECLARED_ASYMMETRY saying why"
    )


def test_an_undeclared_asymmetry_fails_and_names_the_section() -> None:
    gap = parity_gap({"units", "graph", "events"}, {"units"}, {"graph"})
    assert gap == ("events",)


def test_declaring_an_asymmetry_silences_it() -> None:
    assert parity_gap({"units", "events"}, {"units"}, {"events"}) == ()


@pytest.mark.parametrize("section", sorted(DECLARED_ASYMMETRY))
def test_every_declared_asymmetry_is_real(section: str) -> None:

    assert section in optional_sections()
    assert section not in foreign_document()
    assert DECLARED_ASYMMETRY[section].strip(), "a declaration without a reason is not one"


def test_the_foreign_fixture_names_every_section_it_does_not_emit() -> None:

    document = foreign_document()
    sections = optional_sections()
    verdict = board_schema.verdict(REPO_ROOT, document)

    assert verdict.readable, verdict.summary
    assert set(verdict.absent) == set(sections) - emitted(document, sections)
    assert set(verdict.absent) == set(DECLARED_ASYMMETRY)
    for name in verdict.absent:
        assert name in verdict.summary
