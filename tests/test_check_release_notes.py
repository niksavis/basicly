"""Tests for the release-note ratchet (basicly-7phc).

Driven against fixture ledgers in ``tmp_path``, never against this repo's own tracker: a
gate asserted on live record text becomes a report on whatever the tracker holds today,
and any lane closing a record turns the suite red. The one real-tree assertion is that
every frozen entry names a record the tracker holds — a positive control on the table
being measured rather than composed, which an all-fixture suite cannot give.

The stale-entry tests are the ones that matter. A frozen set visited only where the tree
produced a subject is a set whose obsolete entries are satisfied by never being looked at,
and this gate's whole reason for existing is that absence is invisible to a presence check.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

from basicly import config, tracker
from tests import flipped_tracker

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_release_notes.py"

SHIPPED_SCOPE = "## Scope\n\n- `src/basicly/thing.py`\n"
FRAGMENT = "changelog.d/fix-1.added.md"
CHANGELOG = "CHANGELOG.md"
MACHINERY_SCOPE = "## Scope\n\n- `tests/test_thing.py`\n- `.scripts/gate.py`\n"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_release_notes")


def _repo(
    tmp_path: Path,
    records: list[dict[str, str]],
    *,
    ratchet: dict[str, object] | None = None,
    notes: dict[str, str] | None = None,
) -> Path:
    """A checkout holding *records*, the recorded ratchet, and the notes on disk.

    *ratchet* is the ``[tool.release_notes]`` table as its three keys; *notes* maps a
    repo-relative path to its body, so one argument covers both a `changelog.d` fragment
    and `CHANGELOG.md` — the two places a release note lives are the same input here.
    """
    flipped_tracker.seed_records(tmp_path, records)
    table = ratchet or {}
    invisible: dict[str, str] = table.get("invisible", {})  # type: ignore[assignment]
    frozen: dict[str, int] = table.get("frozen", {})  # type: ignore[assignment]
    lines = [
        "[tool.release_notes]",
        f"declared_count = {table.get('declared_count', len(invisible))}",
        "[tool.release_notes.frozen]",
        *(f'"{key}" = {value}' for key, value in frozen.items()),
        "[tool.release_notes.invisible]",
        *(f'"{key}" = {value!r}' for key, value in invisible.items()),
    ]
    (tmp_path / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "changelog.d").mkdir(exist_ok=True)
    for name, body in (notes or {}).items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def _findings(repo: Path) -> list[str]:
    """Every finding the gate reports for *repo*, as ``subject: detail`` lines."""
    found = gate.standings(repo)
    ratchet = gate.load_ratchet(repo)
    declared = gate.declarations(repo)
    return [
        f"{finding.subject}: {finding.detail}" for finding in gate.collect(found, ratchet, declared)
    ]


def _closed(issue_id: str, description: str = SHIPPED_SCOPE) -> dict[str, str]:
    """One closed record declaring *description* as its body."""
    return {"id": issue_id, "status": "closed", "description": description}


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """An instrument nothing runs is the defect class this repo keeps paying for."""
    configured = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    wired = [
        check
        for check in configured["verify"]["checks"]
        if SCRIPT.name in " ".join(check["command"])
    ]
    assert [check["name"] for check in wired] == ["release-notes"]
    assert wired[0]["modes"] == ["fast", "full"]


def test_every_frozen_entry_names_a_record_this_tracker_holds() -> None:
    """The positive control: the table was measured over the population, not composed."""
    config.load_tracker_mode(REPO_ROOT)
    held = {str(record.get("id")) for record in tracker.all_records(REPO_ROOT)}
    frozen = set(gate.load_ratchet(REPO_ROOT).frozen)
    assert frozen, "an empty baseline would pass every record at once"
    assert frozen <= held


def test_a_closed_record_that_reached_a_shipped_surface_with_no_note_is_refused(
    tmp_path: Path,
) -> None:
    """The omission the gate exists for, and it names the record."""
    repo = _repo(tmp_path, [_closed("fix-1")])
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-1"]
    assert "no release note" in _findings(repo)[0]


def test_a_closed_record_declaring_no_scope_is_not_judged(tmp_path: Path) -> None:
    """Absence alone cannot tell a forgotten note from a record that predates the rule."""
    repo = _repo(tmp_path, [_closed("old-1", "## Acceptance Criteria\n\n- it works\n")])
    assert _findings(repo) == []
    assert gate.standings(repo)["old-1"].reason == gate._UNSCOPED


def test_a_closed_record_scoped_to_machinery_alone_owes_nothing(tmp_path: Path) -> None:
    """`tests/` and `.scripts/` are not in the wheel and are not what a consumer reads."""
    repo = _repo(tmp_path, [_closed("gate-1", MACHINERY_SCOPE)])
    assert _findings(repo) == []


def test_an_open_record_owes_nothing_yet(tmp_path: Path) -> None:
    """The note is owed at closure, not while the work is still running."""
    repo = _repo(tmp_path, [{"id": "wip-1", "status": "open", "description": SHIPPED_SCOPE}])
    assert _findings(repo) == []


def test_a_fragment_named_for_the_record_accounts_for_it(tmp_path: Path) -> None:
    """The ordinary case: the lane wrote its note where the release will assemble it."""
    repo = _repo(tmp_path, [_closed("fix-1")], notes={FRAGMENT: "- did a thing\n"})
    assert _findings(repo) == []


def test_a_citation_in_another_fragment_accounts_for_a_record(tmp_path: Path) -> None:
    """One note may speak for several records, and says so by citing them."""
    repo = _repo(
        tmp_path,
        [_closed("fix-1"), _closed("fix-2")],
        notes={FRAGMENT: "- did two things (fix-1, fix-2)\n"},
    )
    assert _findings(repo) == []


def test_a_citation_in_the_changelog_accounts_for_a_released_record(tmp_path: Path) -> None:
    """Assembly deletes the fragment, so the assembled note has to keep the credit."""
    repo = _repo(tmp_path, [_closed("fix-1")], notes={CHANGELOG: "- shipped (fix-1)\n"})
    assert _findings(repo) == []


def test_prose_naming_a_record_outside_parentheses_does_not_account_for_it(
    tmp_path: Path,
) -> None:
    """A mention is not a citation: crediting free prose is how a dead field read as wired."""
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        notes={CHANGELOG: "- supersedes fix-1 (see the pre-commit hook)\n"},
    )
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-1"]


def test_a_frozen_entry_whose_record_gained_a_note_is_reported(tmp_path: Path) -> None:
    """A graduated entry left in place licenses the omission coming back for free."""
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        ratchet={"frozen": {"fix-1": 1}},
        notes={FRAGMENT: "- did a thing\n"},
    )
    findings = _findings(repo)
    assert len(findings) == 1
    assert "already has a release note" in findings[0]


def test_a_frozen_entry_whose_record_reopened_is_reported(tmp_path: Path) -> None:
    """What closes the reopen hole: the exemption cannot survive rework."""
    repo = _repo(
        tmp_path,
        [{"id": "fix-1", "status": "open", "description": SHIPPED_SCOPE}],
        ratchet={"frozen": {"fix-1": 1}},
    )
    assert "is not closed" in _findings(repo)[0]


def test_a_frozen_entry_naming_no_record_at_all_is_reported(tmp_path: Path) -> None:
    """A subject the tree never produces is exactly what an unvisited frozen set hides."""
    repo = _repo(tmp_path, [_closed("fix-1")], ratchet={"frozen": {"fix-1": 1, "ghost-9": 1}})
    assert "ghost-9: " in "\n".join(_findings(repo))


def test_a_declaration_accepts_a_record_no_consumer_can_see(tmp_path: Path) -> None:
    """The declaration half: an invisible change is declarable, not defaulted."""
    repo = _repo(tmp_path, [_closed("fix-1")], ratchet={"invisible": {"fix-1": "internal"}})
    assert _findings(repo) == []


def test_a_declaration_that_exempts_nothing_is_reported(tmp_path: Path) -> None:
    """Validated against the population it exempts from, like every other list here."""
    repo = _repo(
        tmp_path,
        [_closed("fix-1", MACHINERY_SCOPE)],
        ratchet={"invisible": {"fix-1": "internal"}},
    )
    assert "exempts nothing" in _findings(repo)[0]


def test_a_declaration_with_no_reason_is_refused(tmp_path: Path) -> None:
    """An unargued exemption is the empty fragment this gate must not train people to write."""
    repo = _repo(tmp_path, [_closed("fix-1")], ratchet={"invisible": {"fix-1": "  "}})
    assert "with no reason" in _findings(repo)[0]


def test_a_declaration_that_is_also_frozen_is_refused(tmp_path: Path) -> None:
    """Two exemptions for one record means deleting either one leaves it still exempt."""
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        ratchet={"frozen": {"fix-1": 1}, "invisible": {"fix-1": "internal"}},
    )
    assert "and frozen in" in "\n".join(_findings(repo))


def test_a_declaration_added_without_moving_the_count_is_refused(tmp_path: Path) -> None:
    """The one way this gate's exemption surface can grow may not grow quietly."""
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        ratchet={"invisible": {"fix-1": "internal"}, "declared_count": 0},
    )
    assert "declared_count is 0" in _findings(repo)[0]


def test_a_malformed_declaration_table_refuses_rather_than_reading_as_empty(
    tmp_path: Path,
) -> None:
    """A typo must not silently withdraw every declaration."""
    repo = _repo(tmp_path, [_closed("fix-1")])
    (repo / "pyproject.toml").write_text(
        "[tool.release_notes]\ndeclared_count = 0\n[tool.release_notes.invisible]\nx = 1\n",
        encoding="utf-8",
    )
    try:
        gate.declarations(repo)
    except gate.RatchetError as exc:
        assert "must map each record id to its reason" in str(exc)
    else:
        raise AssertionError("a malformed table read as empty")
