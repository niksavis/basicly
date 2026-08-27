"""Tests for pass contention: the collisions a pass can see before any lane starts.

The reported pass (basicly-o8p0): three lanes, ``VERDICT: ready``, and a
``CHANGELOG.md`` in nobody's scope that bounced the third lane twice. The lanes were
hand-filed siblings, so ``decompose`` never grouped them — preflight is the only
surface that sees the set, and the whole cost of missing it is a rework budget.

Moved out of ``test_supervise`` with the module it exercises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import contention, loop, needs_input
from tests import fake_tracker

if TYPE_CHECKING:
    import pytest


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _scope_reader(monkeypatch: pytest.MonkeyPatch, scopes: dict[str, tuple[str, ...]]) -> None:
    """Serve each lane's ``## Scope`` through the real parse chain, not a stubbed dict.

    The report reads scopes via ``merge.declared_scopes`` -> ``bead_class_and_scope``
    -> ``tracker.read_record``, and the recorded body is where a declaration is easy to get
    wrong, so the fake stops at ``br`` and everything above it stays live. It is
    installed on ``tracker.try_run_br`` because the record read is the one seam every
    consumer shares (basicly-tcmy.14); stubbing anything above it would leave the seam
    spawning a real tracker, and every lane would then read as declaring no scope — which is
    the *warn* branch, so the test would fail by warning about all three.
    """

    def show(_repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        assert args[:1] == ["show"], f"unexpected br call: {args}"
        body = "## Scope\n" + "\n".join(f"- `{glob}`" for glob in scopes.get(args[1], ()))
        return _Proc(json.dumps([{"id": args[1], "issue_type": "task", "description": body}]))

    fake_tracker.install(monkeypatch, show)


def test_the_report_warns_when_every_lane_appends_to_a_path_none_declares(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bead's own reproduction, at the surface that could have predicted it."""
    lanes = ("basicly-ky5z", "basicly-1piy", "basicly-3ymj")
    _scope_reader(
        monkeypatch,
        {
            "basicly-ky5z": ("src/basicly/schema.py",),
            "basicly-1piy": ("src/basicly/config.py",),
            "basicly-3ymj": ("src/basicly/usage.py",),
        },
    )

    lines = contention.append_only_report(tmp_path, lanes, ("CHANGELOG.md",))

    assert "`CHANGELOG.md`" in lines[0]
    assert "3 lane(s) will each append to `CHANGELOG.md`" in lines[1]
    for lane in lanes:
        assert lane in lines[1]
    assert "build them in " in lines[2]


def test_the_report_leaves_out_a_lane_that_declared_the_path_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane that says out loud it writes the file is not part of the blind population.

    Its declaration is already visible to the band and to the landing scope check, so
    counting it here would report a contention two existing gates can see. With only
    one undeclared lane left there is nothing to warn about at all.
    """
    _scope_reader(
        monkeypatch,
        {
            "a": ("src/a.py", "CHANGELOG.md"),
            "b": ("src/b.py", "CHANGELOG.md"),
            "c": ("src/c.py",),
        },
    )

    lines = contention.append_only_report(tmp_path, ("a", "b", "c"), ("CHANGELOG.md",))

    assert len(lines) == 1
    assert "`CHANGELOG.md`" in lines[0]


def test_the_report_says_when_nothing_is_declared_append_only(tmp_path: Path) -> None:
    """Inert, and it says so: a check that prints nothing reads as a check that passed.

    The same reason :func:`supervise.band_coverage` exists. This one is inert in every
    repo that has not listed a path, which is every repo by default.
    """
    (line,) = contention.append_only_report(tmp_path, ("a", "b"), ())

    assert "[worktree] append_only_paths" in line
    assert "invisible to the grouping" in line


def test_a_single_lane_pass_contends_with_nobody(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One lane cannot collide with itself, and the check must not read the tracker for it."""

    def refuse(_repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        raise AssertionError(f"a one-lane pass must not read scopes: {args}")

    fake_tracker.install(monkeypatch, refuse)

    (line,) = contention.append_only_report(tmp_path, ("only",), ("CHANGELOG.md",))

    assert "1 lane(s) in this pass, so nothing contends" in line


# --- The fence a lane reads, rather than the report an operator reads --------

_ROOT = "basicly-root"
_LANE = "basicly-mine"


def _graph(
    monkeypatch: pytest.MonkeyPatch,
    children: dict[str, tuple[str, tuple[str, ...]]],
    *,
    parent: str | None = _ROOT,
) -> None:
    """Serve a root and its children, each with a status and a declared ``## Scope``.

    Stops at the tracker stand-in like :func:`_scope_reader`, so the sibling walk, the
    dispatchable filter and the ``## Scope`` parse all stay live - the three places a
    wrong fence would come from.
    """

    def show(_repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        assert args[:1] == ["show"], f"unexpected tracker call: {args}"
        issue = args[1]
        if issue == _ROOT:
            payload = {
                "id": _ROOT,
                "dependents": [
                    {"id": child, "dependency_type": "parent-child", "status": status}
                    for child, (status, _) in children.items()
                ],
            }
        else:
            status, scope = children.get(issue, ("open", ()))
            payload = {
                "id": issue,
                "issue_type": "task",
                "status": status,
                "description": "## Scope\n" + "\n".join(f"- `{glob}`" for glob in scope),
                "dependencies": (
                    [{"id": parent, "dependency_type": "parent-child"}] if parent else []
                ),
            }
        return _Proc(json.dumps([payload]))

    fake_tracker.install(monkeypatch, show)


def test_the_fence_names_every_open_sibling_and_the_ground_it_declares(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bead's reproduction: the lane must learn the collision before the merge queue."""
    _graph(
        monkeypatch,
        {
            _LANE: ("open", ("src/basicly/mine.py",)),
            "basicly-sib1": ("open", ("src/basicly/board_render.py",)),
            "basicly-sib2": ("in_progress", ("src/basicly/board_regions.py",)),
        },
    )

    fence = contention.with_scope_fence(tmp_path, _LANE, "BASE")

    assert fence.startswith("BASE\n\n")
    assert "- basicly-sib1 owns `src/basicly/board_render.py`" in fence
    assert "- basicly-sib2 owns `src/basicly/board_regions.py`" in fence
    assert "holds this record until a human rules on the scope" in fence


def test_the_fence_restates_what_the_landing_admits_from_this_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Declared scope plus the derived paths, which is the ``held`` tuple the gate uses.

    The derived half is asserted because ``## Scope`` alone would tell the lane its own
    drop-in and release fragment are out of bounds - the false positive basicly-kjc5.64
    removed from the gate, which a fence could quietly reintroduce as prose.
    """
    _graph(
        monkeypatch,
        {
            _LANE: ("open", ("src/basicly/mine.py", "tests/test_mine.py")),
            "basicly-sib1": ("open", ("src/basicly/theirs.py",)),
        },
    )

    fence = contention.with_scope_fence(tmp_path, _LANE, "BASE")

    assert "admits from you: `src/basicly/mine.py`, `tests/test_mine.py`" in fence
    assert f"`basicly.d/{_LANE}.toml`" in fence
    assert f"`changelog.d/{_LANE}.*.md`" in fence
    assert "outrank any sibling glob" in fence


def test_the_fence_routes_a_needed_sibling_path_at_the_sentinel_not_an_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The route has to be the fact a human is actually holding, spelled once."""
    _graph(
        monkeypatch,
        {_LANE: ("open", ("src/basicly/mine.py",)), "basicly-sib1": ("open", ("src/x.py",))},
    )

    fence = contention.with_scope_fence(tmp_path, _LANE, "BASE")

    assert needs_input.SENTINEL_FILE.as_posix() in fence
    assert f'"fact": "{contention.SCOPE_FACT}"' in fence
    assert "do not edit it and do not widen your own declaration" in fence


def test_the_scope_fact_is_the_one_the_landing_blocks_under() -> None:
    """A second spelling would file the sentinel against a question nobody holds.

    Read off ``loop``'s source rather than asserted twice: ``loop._scope_block`` passes
    the fact as a literal, and this is the only cheap check that fails when either side
    drifts.
    """
    source = Path(loop.__file__).read_text(encoding="utf-8")

    assert f'needs_input="{contention.SCOPE_FACT}"' in source


def test_the_fence_is_silent_when_no_open_sibling_declares_ground(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Byte-identical, because every brief pays for a section that says "nobody"."""
    _graph(
        monkeypatch,
        {
            _LANE: ("open", ("src/basicly/mine.py",)),
            "basicly-done": ("closed", ("src/basicly/theirs.py",)),
            "basicly-parked": ("deferred", ("src/basicly/parked.py",)),
            "basicly-unscoped": ("open", ()),
        },
    )

    assert contention.with_scope_fence(tmp_path, _LANE, "BASE") == "BASE"


def test_the_fence_is_silent_for_a_lane_with_no_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hand-filed leaf and a root have no sibling set to be fenced from."""
    _graph(
        monkeypatch,
        {_LANE: ("open", ("src/basicly/mine.py",)), "basicly-sib1": ("open", ("src/x.py",))},
        parent=None,
    )

    assert contention.with_scope_fence(tmp_path, _LANE, "BASE") == "BASE"


def test_the_fence_says_an_undeclared_lane_is_not_a_narrowly_scoped_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``loop._scope_block`` returns early on an empty declaration, so nothing is refused.

    Naming the two derived paths as "the paths admitted" would invent a fence the gate
    does not hold, and a lane that believed it would file a sentinel it never needed.
    """
    _graph(
        monkeypatch,
        {_LANE: ("open", ()), "basicly-sib1": ("open", ("src/basicly/theirs.py",))},
    )

    fence = contention.with_scope_fence(tmp_path, _LANE, "BASE")

    assert "scope check is inert on your diff and admits it whole" in fence
    assert "- basicly-sib1 owns `src/basicly/theirs.py`" in fence
