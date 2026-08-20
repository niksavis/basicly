"""The landing scope gate and the two files every lane writes without declaring them.

`loop._scope_block` holds a lane's committed diff against its declared `## Scope`, and the
repo's own landing conventions have every lane write `basicly.d/<id>.toml` and
`changelog.d/<id>.<category>.md` — neither of which appears in any bead's declaration.
Observed 2026-08-19 on `basicly-gvlpxm`: both were reported beside one genuine collision,
in a message that offers `[policy] scope_collision = "warn"` as the way to land. Two
obviously wrong entries in a list of five teach an operator to take that offer, which is
why a false positive here is worse than a missed one (basicly-kjc5.64).

The discrimination these tests exist to hold is that the remedy is *derived* and not a
directory whitelist: `basicly.d/<other-id>.toml` is a real collision, and this gate is the
only one that sees it — `[worktree] shared_paths` is empty on purpose and serializing every
lane was the cost the repo already backed out of.
"""

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

# The lane's own two derived files, spelled the way a lane really writes them.
OWN_DROPIN = f"basicly.d/{RECORD}.toml"
OWN_FRAGMENT = f"changelog.d/{RECORD}.fixed.md"


def _session(record: str = RECORD) -> Session:
    """A session record for *record*'s lane, named the way `loop._worktree_name` does."""
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
    """Pin the gate's four reads; return the violations it records on the bead."""
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


# --- the derived scope itself ------------------------------------------------


def test_a_lanes_derived_scope_is_two_globs_keyed_on_its_own_record() -> None:
    """Both drop-in directories, each narrowed to the filename the id produces."""
    assert config.lane_scope(RECORD) == (
        f"basicly.d/{RECORD}.toml",
        f"changelog.d/{RECORD}.*.md",
    )


def test_the_two_drop_in_directory_names_are_the_producers_own() -> None:
    """A respelled directory name is a drift risk unless something pins it.

    `config` cannot import `release` (the engine tiers run one way), so the equality the
    import would have guaranteed is asserted here instead.
    """
    assert release.FRAGMENT_DIR.as_posix() == config.CHANGELOG_FRAGMENT_DIR
    assert config.lane_scope(RECORD)[0].startswith(f"{dropin.FRAGMENT_DIR}/")


# --- what the refused-path list admits, and what it still refuses ------------


def test_a_lanes_own_drop_in_and_fragment_are_in_scope_without_being_declared() -> None:
    """The false positive: a lane that followed both conventions was faulted for it."""
    scope = ("src/basicly/config.py", *config.lane_scope(RECORD))
    changed = ("src/basicly/config.py", OWN_DROPIN, OWN_FRAGMENT)
    assert merge.out_of_scope_paths(changed, scope) == ()


def test_another_lanes_drop_in_is_still_out_of_scope() -> None:
    """The blanket-whitelist guard: the id in the filename is the discriminator.

    Widening this to `basicly.d/**` would land two lanes writing one lane's baseline with
    nothing left to notice, and this gate is the only place that can.
    """
    scope = ("src/basicly/config.py", *config.lane_scope(RECORD))
    assert merge.out_of_scope_paths((f"basicly.d/{OTHER}.toml",), scope) == (
        f"basicly.d/{OTHER}.toml",
    )
    assert merge.out_of_scope_paths((f"changelog.d/{OTHER}.fixed.md",), scope) == (
        f"changelog.d/{OTHER}.fixed.md",
    )


def test_the_directories_own_documentation_is_not_a_lanes_to_write() -> None:
    """`README.md` in either directory names no record, so no lane owns it."""
    scope = ("src/basicly/config.py", *config.lane_scope(RECORD))
    changed = ("basicly.d/README.md", "changelog.d/README.md")
    assert merge.out_of_scope_paths(changed, scope) == changed


# --- the gate, through the landing precondition ------------------------------


def test_a_lane_that_wrote_only_its_own_derived_files_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate is silent, so no evidence marker and no refusal reach the bead."""
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
    """The demonstration: two derived files plus one real collision reports one path."""
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
