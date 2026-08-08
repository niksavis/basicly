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

import pytest

from basicly import br, contention, decompose


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _scope_reader(monkeypatch: pytest.MonkeyPatch, scopes: dict[str, tuple[str, ...]]) -> None:
    """Serve each lane's ``## Scope`` through the real parse chain, not a stubbed dict.

    The report reads scopes via ``merge.declared_scopes`` -> ``bead_class_and_scope``
    -> ``br.read_record``, and the recorded body is where a declaration is easy to get
    wrong, so the fake stops at ``br`` and everything above it stays live. It is
    installed on ``br.try_run_br`` because the record read is the one seam every
    consumer shares (basicly-tcmy.14); stubbing ``decompose``'s alias would leave the
    seam spawning a real br, and every lane would then read as declaring no scope —
    which is the *warn* branch, so the test would fail by warning about all three.
    """

    def show(_repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        assert args[:1] == ["show"], f"unexpected br call: {args}"
        body = "## Scope\n" + "\n".join(f"- `{glob}`" for glob in scopes.get(args[1], ()))
        return _Proc(json.dumps([{"id": args[1], "issue_type": "task", "description": body}]))

    monkeypatch.setattr(br, "try_run_br", show)


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

    monkeypatch.setattr(decompose, "_run_br", refuse)

    (line,) = contention.append_only_report(tmp_path, ("only",), ("CHANGELOG.md",))

    assert "1 lane(s) in this pass, so nothing contends" in line
