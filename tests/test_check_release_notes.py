from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

from basicly import config, release, tracker
from tests import flipped_tracker

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_release_notes.py"

SHIPPED_SCOPE = "## Scope\n\n- `src/basicly/thing.py`\n"
FRAGMENT = "changelog.d/fix-1.added.md"
CHANGELOG = "CHANGELOG.md"
MACHINERY_SCOPE = "## Scope\n\n- `tests/test_thing.py`\n- `.scripts/gate.py`\n"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_release_notes")

standing = sys.modules["release_note_standing"]


def _repo(
    tmp_path: Path,
    records: list[dict[str, str]],
    *,
    ratchet: dict[str, object] | None = None,
    notes: dict[str, str] | None = None,
) -> Path:

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
    found = gate.standings(repo)
    ratchet = gate.load_ratchet(repo)
    declared = gate.declarations(repo)
    return [
        f"{finding.subject}: {finding.detail}" for finding in gate.collect(found, ratchet, declared)
    ]


def _closed(issue_id: str, description: str = SHIPPED_SCOPE) -> dict[str, str]:
    return {"id": issue_id, "status": "closed", "description": description}


def test_the_gate_is_wired_as_a_verify_check() -> None:
    configured = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    wired = [
        check
        for check in configured["verify"]["checks"]
        if SCRIPT.name in " ".join(check["command"])
    ]
    assert [check["name"] for check in wired] == ["release-notes"]
    assert wired[0]["modes"] == ["fast", "full"]


def test_every_frozen_entry_names_a_record_this_tracker_holds() -> None:
    config.load_tracker_mode(REPO_ROOT)
    held = {str(record.get("id")) for record in tracker.all_records(REPO_ROOT)}
    frozen = set(gate.load_ratchet(REPO_ROOT).frozen)
    assert frozen, "an empty baseline would pass every record at once"
    assert frozen <= held


def test_a_closed_record_that_reached_a_shipped_surface_with_no_note_is_refused(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, [_closed("fix-1")])
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-1"]
    assert "no release note" in _findings(repo)[0]


def test_a_closed_record_declaring_no_scope_is_not_judged(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("old-1", "## Acceptance Criteria\n\n- it works\n")])
    assert _findings(repo) == []
    assert gate.standings(repo)["old-1"].reason == standing.UNSCOPED


def test_a_closed_record_scoped_to_machinery_alone_owes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("gate-1", MACHINERY_SCOPE)])
    assert _findings(repo) == []


def test_an_open_record_owes_nothing_yet(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [{"id": "wip-1", "status": "open", "description": SHIPPED_SCOPE}])
    assert _findings(repo) == []


def test_a_fragment_named_for_the_record_accounts_for_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("fix-1")], notes={FRAGMENT: "- did a thing\n"})
    assert _findings(repo) == []


def test_a_citation_in_another_fragment_accounts_for_a_record(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        [_closed("fix-1"), _closed("fix-2")],
        notes={FRAGMENT: "- did two things (fix-1, fix-2)\n"},
    )
    assert _findings(repo) == []


def test_a_citation_in_the_changelog_accounts_for_a_released_record(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("fix-1")], notes={CHANGELOG: "- shipped (fix-1)\n"})
    assert _findings(repo) == []


def test_prose_naming_a_record_outside_parentheses_does_not_account_for_it(
    tmp_path: Path,
) -> None:
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        notes={CHANGELOG: "- supersedes fix-1 (see the pre-commit hook)\n"},
    )
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-1"]


def test_a_frozen_entry_whose_record_gained_a_note_is_reported(tmp_path: Path) -> None:
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
    repo = _repo(
        tmp_path,
        [{"id": "fix-1", "status": "open", "description": SHIPPED_SCOPE}],
        ratchet={"frozen": {"fix-1": 1}},
    )
    assert "is not closed" in _findings(repo)[0]


def test_a_frozen_entry_naming_no_record_at_all_is_reported(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("fix-1")], ratchet={"frozen": {"fix-1": 1, "ghost-9": 1}})
    assert "ghost-9: " in "\n".join(_findings(repo))


def test_a_declaration_accepts_a_record_no_consumer_can_see(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("fix-1")], ratchet={"invisible": {"fix-1": "internal"}})
    assert _findings(repo) == []


def test_a_declaration_that_exempts_nothing_is_reported(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        [_closed("fix-1", MACHINERY_SCOPE)],
        ratchet={"invisible": {"fix-1": "internal"}},
    )
    assert "exempts nothing" in _findings(repo)[0]


def test_a_declaration_with_no_reason_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_closed("fix-1")], ratchet={"invisible": {"fix-1": "  "}})
    assert "with no reason" in _findings(repo)[0]


def test_a_declaration_that_is_also_frozen_is_refused(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        ratchet={"frozen": {"fix-1": 1}, "invisible": {"fix-1": "internal"}},
    )
    assert "and frozen in" in "\n".join(_findings(repo))


def test_a_declaration_added_without_moving_the_count_is_refused(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        [_closed("fix-1")],
        ratchet={"invisible": {"fix-1": "internal"}, "declared_count": 0},
    )
    assert "declared_count is 0" in _findings(repo)[0]


def test_a_malformed_declaration_table_refuses_rather_than_reading_as_empty(
    tmp_path: Path,
) -> None:
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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _lane(tmp_path: Path, *, lands: bool = True, ratchet: dict[str, object] | None = None) -> Path:

    repo = _repo(tmp_path, [_closed("fix-1")], ratchet=ratchet)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester@example.invalid")
    _git(repo, "config", "user.name", "tester")
    (repo / "changelog.d" / ".keep").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the branch point")
    _git(repo, "branch", "-q", "harness/lane")
    if lands:
        (repo / FRAGMENT).write_text("- did a thing\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "the note lands on main")
    _git(repo, "checkout", "-q", "harness/lane")
    return repo


def test_a_note_that_landed_on_base_after_the_lane_branched_is_behind_not_owed(
    tmp_path: Path,
) -> None:
    repo = _lane(tmp_path)
    assert release.fragments_on_base(repo) == {"fix-1": FRAGMENT}
    assert _findings(repo) == []


def test_a_lane_that_is_behind_is_told_to_rebase_and_the_pass_line_counts_it(
    tmp_path: Path,
) -> None:
    repo = _lane(tmp_path)
    found = gate.standings(repo)
    warnings = gate.behind_warnings(found)
    assert len(warnings) == 1
    assert FRAGMENT in warnings[0]
    assert "Rebase" in warnings[0]
    assert "1 behind the base" in gate.summary(found, gate.load_ratchet(repo), {})


def test_a_note_on_neither_tree_is_refused_exactly_as_before(tmp_path: Path) -> None:
    repo = _lane(tmp_path, lands=False)
    assert release.fragments_on_base(repo) == {}
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-1"]


def test_a_note_deleted_on_the_base_branch_itself_is_debt_rather_than_lag(
    tmp_path: Path,
) -> None:
    repo = _lane(tmp_path)
    _git(repo, "checkout", "-q", "main")
    (repo / FRAGMENT).unlink()
    assert release.fragments_on_base(repo) == {}
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-1"]


def test_a_behind_record_still_counts_against_its_frozen_entry(tmp_path: Path) -> None:
    repo = _lane(tmp_path, ratchet={"frozen": {"fix-1": 1}})
    assert gate.standings(repo)["fix-1"].count == standing.OWED
    assert _findings(repo) == []
