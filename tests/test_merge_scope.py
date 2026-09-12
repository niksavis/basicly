from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import config, decompose, dropin, loop, merge, policy, release, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
from basicly.worktree import Session

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

RECORD = "basicly-kjc5.64"
OTHER = "basicly-gvlpxm"

OWN_DROPIN = f"basicly.d/{RECORD}.toml"
OWN_FRAGMENT = f"changelog.d/{RECORD}.fixed.md"


def _session(record: str = RECORD) -> Session:
    name = record.replace(".", "-")
    return Session(
        name=name,
        branch=f"harness/{name}",
        base="main",
        base_head="abc",
        worktree_path=f"/tmp/{name}",
        created_at="2026-08-19T00:00:00Z",
    )


def _ctx(tmp_path: Path, collision: str = "block") -> loop._Ctx:
    state = NodeState(
        issue_id=RECORD,
        status="in_progress",
        issue_type="bug",
        phase="build",
        worktree=WorktreeBinding(RECORD, f"harness/{RECORD}"),
        gates=GateStatus(False, (), (), (), ()),
        checkpoints=(),
        rework={},
        has_children=False,
    )
    return loop._Ctx(
        repo_root=tmp_path,
        issue_id=RECORD,
        state=state,
        config=PolicyConfig(required_gates=("verify",), max_rework=2, scope_collision=collision),
        inputs=loop.Inputs(),
    )


def _pin(
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed: tuple[str, ...],
    scopes: dict[str, tuple[str, ...]],
    live: tuple[str, ...] = (RECORD,),
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session())
    monkeypatch.setattr(worktree, "list_sessions", lambda _r: [_session(n) for n in live])
    monkeypatch.setattr(decompose, "bead_class_and_scope", lambda _r, b: ("task", scopes[b]))
    monkeypatch.setattr(merge, "branch_changed_paths", lambda *_a: changed)
    monkeypatch.setattr(merge, "known_bead_ids", lambda _r: set(scopes))
    recorded: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def _record(_repo_root, _issue, paths, colliding=()):
        recorded.append((tuple(paths), tuple(colliding)))
        return True

    monkeypatch.setattr(policy, "record_scope_violation", _record)
    return recorded


def test_a_lanes_derived_scope_is_two_globs_keyed_on_its_own_record() -> None:
    assert config.lane_scope(RECORD) == (
        f"basicly.d/{RECORD}.toml",
        f"changelog.d/{RECORD}.*.md",
    )


def test_the_two_drop_in_directory_names_are_the_producers_own() -> None:

    assert release.FRAGMENT_DIR.as_posix() == config.CHANGELOG_FRAGMENT_DIR
    assert config.lane_scope(RECORD)[0].startswith(f"{dropin.FRAGMENT_DIR}/")


def test_a_lanes_own_drop_in_and_fragment_are_in_scope_without_being_declared() -> None:
    scope = ("src/basicly/config.py", *config.lane_scope(RECORD))
    changed = ("src/basicly/config.py", OWN_DROPIN, OWN_FRAGMENT)
    assert merge.out_of_scope_paths(changed, scope) == ()


def test_another_lanes_drop_in_is_still_out_of_scope() -> None:

    scope = ("src/basicly/config.py", *config.lane_scope(RECORD))
    assert merge.out_of_scope_paths((f"basicly.d/{OTHER}.toml",), scope) == (
        f"basicly.d/{OTHER}.toml",
    )
    assert merge.out_of_scope_paths((f"changelog.d/{OTHER}.fixed.md",), scope) == (
        f"changelog.d/{OTHER}.fixed.md",
    )


def test_the_directories_own_documentation_is_not_a_lanes_to_write() -> None:
    scope = ("src/basicly/config.py", *config.lane_scope(RECORD))
    changed = ("basicly.d/README.md", "changelog.d/README.md")
    assert merge.out_of_scope_paths(changed, scope) == changed


def test_a_lane_that_wrote_only_its_own_derived_files_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorded = _pin(
        monkeypatch,
        changed=("src/basicly/config.py", OWN_DROPIN, OWN_FRAGMENT),
        scopes={RECORD: ("src/basicly/config.py",)},
    )
    assert loop._scope_block(_ctx(tmp_path), _session().name) is None
    assert recorded == []


def test_the_report_names_the_genuine_collision_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorded = _pin(
        monkeypatch,
        changed=("src/basicly/config.py", OWN_DROPIN, OWN_FRAGMENT, "src/basicly/release.py"),
        scopes={RECORD: ("src/basicly/config.py",), OTHER: ("src/basicly/release.py",)},
        live=(RECORD, OTHER),
    )
    result = loop._scope_block(_ctx(tmp_path), _session().name)
    assert result is not None and result.needs_input == "scope"
    assert "src/basicly/release.py" in result.detail
    assert OWN_DROPIN not in result.detail and OWN_FRAGMENT not in result.detail
    assert recorded == [(("src/basicly/release.py",), (OTHER,))]
