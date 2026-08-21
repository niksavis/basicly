"""Cross-producer parity: the contract checked against a producer that is not basicly.

The claim under test is the one `basicly-rn0o.13` says nothing detects. A conformance kit run
only against `board_snapshot` - the reference producer - cannot tell a contract from a wire
format, because the single implementation is always self-consistent. So this suite runs the
same check against **two** producers, and the second one
(`tests/fixtures/board/foreign/produce.py`) imports no basicly module.

Three directions, and the middle one is why this is not just a second fixture:

- **Both producers conform.** Neither is privileged by the check.
- **A section the reference emits and the foreign producer does not is a declared asymmetry
  or a failure.** :data:`DECLARED_ASYMMETRY` is where "on purpose" is written down with a
  reason; :func:`parity_gap` names anything else. The negative control asserts it fires,
  because a check written only against the passing side passes when it always passes.
- **A foreign document reports every section it does not emit.** That is what a board draws
  as "not emitted by this producer" rather than as a zero.

Scope boundary, stated rather than implied: the *page*-side of `basicly-rn0o.13`'s third
criterion belongs to unit C's AC 10 (`basicly-rn0o.3`), which owns `board_render` and asserts
this same corpus renders. `board_render` does not exist yet and is not in this lane's scope, so
what is asserted here is the consumer surface that does exist - the verdict's absent inventory
and the text `basicly board validate` prints from it.
"""

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

# Injected so the reference document is a function of its fixture rather than of the clock.
_NOW = datetime(2026, 8, 21, 9, 15, tzinfo=UTC)

# Every section the reference producer emits and the foreign one does not, each with the
# reason it is absent rather than broken. A gap not named here fails `parity_gap`: that is the
# rot this module exists to catch, and the only way to clear it is to write down why.
DECLARED_ASYMMETRY = {
    "session": "needs a live supervisor lock; a file export has no running supervisor",
    "lanes": "in-flight loop lanes, and a foreign tracker drives no loop",
    "asks": "basicly checkpoint wait markers; the export has no wait vocabulary",
    "gates": "read from `.basicly/usage/`, which a foreign harness does not write",
    "spend": "needs a per-run usage format; the declared limit is to omit, never estimate",
    "health": "same source as `spend`, and omitted for the same reason",
    "graph": "the export carries no dependency edges, so any edge would be invented",
    "events": "basicly marker rows folded out of its own event log",
}


def optional_sections() -> tuple[str, ...]:
    """The contract's optional top-level sections, from the shipped schema itself."""
    schema = json.loads(
        (REPO_ROOT / ".basicly" / "core" / "schemas" / board_schema.SCHEMA_FILE).read_text(
            encoding="utf-8"
        )
    )
    required = set(schema["required"])
    return tuple(name for name in schema["properties"] if name not in required)


def foreign_document() -> dict[str, object]:
    """The checked-in snapshot the foreign producer emits."""
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def reference_document(repo_root: Path) -> dict[str, object]:
    """The reference producer at its fullest, so every asymmetry has to be declared.

    Both caller-supplied fact records are passed: with them absent the `session` and `lanes`
    sections would be omitted, and the gap this suite measures would be smaller than the
    contract actually allows.
    """
    facts = board_snapshot.Facts(
        session=board_snapshot.SessionFacts(root_issue="basicly-rn0o"),
        lanes=[board_sections.LaneFacts(id="basicly-rn0o.13", phase="build")],
    )
    return board_snapshot.build_document(repo_root, facts=facts, now=_NOW)


def emitted(document: dict[str, object], sections: tuple[str, ...]) -> set[str]:
    """Which of *sections* *document* carries."""
    return {name for name in sections if name in document}


def parity_gap(reference: set[str], foreign: set[str], declared: set[str]) -> tuple[str, ...]:
    """The sections *reference* emits, *foreign* does not, and nobody declared.

    Sorted so a failure names the same sections in the same order every run.
    """
    return tuple(sorted(reference - foreign - declared))


def test_the_conformance_check_runs_against_both_producers(work_repo: Path) -> None:
    """Both producers' documents conform, and the check privileges neither."""
    for label, document in (
        ("reference", reference_document(work_repo)),
        ("foreign", foreign_document()),
    ):
        verdict = board_schema.verdict(work_repo, document)
        assert verdict.outcome == board_schema.OK, f"{label}: {verdict.summary}"
        assert verdict.exit_code == 0, f"{label}: {verdict.summary}"


def test_the_second_producer_imports_no_basicly_module() -> None:
    """Every import in the foreign producer is standard library, and none is basicly."""
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
    """The foreign producer emits its snapshot where `import basicly` raises.

    The static scan above reads the file; this runs it. `basicly` is installed in the test
    interpreter's site-packages, so absence cannot be arranged by unsetting a variable - a
    module that raises on import is placed *ahead* of site-packages on `PYTHONPATH` instead.
    The first assertion is the positive control: without it, a broken shadow would make the
    second assertion pass for the wrong reason.
    """
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
    """`snapshot.json` cannot drift from `produce.py`, because the stamp is not a clock read."""
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
    """The real gap between the two producers is fully declared."""
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
    """THE NEGATIVE CONTROL: the check fires, and the failure carries the section name."""
    gap = parity_gap({"units", "graph", "events"}, {"units"}, {"graph"})
    assert gap == ("events",)


def test_declaring_an_asymmetry_silences_it() -> None:
    """The same pair passes once the asymmetry is written down."""
    assert parity_gap({"units", "events"}, {"units"}, {"events"}) == ()


@pytest.mark.parametrize("section", sorted(DECLARED_ASYMMETRY))
def test_every_declared_asymmetry_is_real(section: str) -> None:
    """A declaration names a schema section the foreign producer genuinely omits.

    Without this, the table would be a place to park a name and forget it: a stale entry
    covering a section the foreign producer *does* emit would silence a future real gap.
    """
    assert section in optional_sections()
    assert section not in foreign_document()
    assert DECLARED_ASYMMETRY[section].strip(), "a declaration without a reason is not one"


def test_the_foreign_fixture_names_every_section_it_does_not_emit() -> None:
    """Every absent section is reported, so a board draws it as unemitted rather than zero.

    The foreign export is a non-basicly tracker shape - `key`, `summary`, `state`, `severity`,
    `kind` - so most of the contract is genuinely not derivable from it. The document is
    readable, and the sections it lacks are named rather than silently missing.
    """
    document = foreign_document()
    sections = optional_sections()
    verdict = board_schema.verdict(REPO_ROOT, document)

    assert verdict.readable, verdict.summary
    assert set(verdict.absent) == set(sections) - emitted(document, sections)
    assert set(verdict.absent) == set(DECLARED_ASYMMETRY)
    for name in verdict.absent:
        assert name in verdict.summary
