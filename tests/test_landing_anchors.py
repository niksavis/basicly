"""Three lanes that each wire a verify check and move a ratchet must all land (basicly-ef7t).

The acceptance criterion for the fragment split, and it is a merge-queue test rather than a
unit test because the failure it pins was never in the composition: three of five lanes on
the 2026-08-08 pass wrote *correct* config and bounced on the rebase, because they wrote it
into the same two files. So the assertion has to be over real git — real branches, a real
replay onto a moving base, a real ``git merge-tree`` probe — with the lanes landing one after
another exactly as ``merge_queue`` orders them.

The second test is the positive control, and this file is worth little without it: the same
three lanes appending to ``basicly.toml`` and ``pyproject.toml`` directly still collide, so a
regression that quietly stopped the fragments from being read would not pass both.

Substituted: ``load_session`` (the queue's worktrees are provisioned by ``git worktree`` here,
not by the harness), the tracker reconcile, and ``policy.record_rework`` — which is spied
rather than stubbed away, because "zero rework recorded" is one of the things being asserted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from basicly import config, dropin, merge, policy
from basicly.worktree import Session

LANES = ("one", "three", "two")

# The interpreter running the tests, as a check command a fixture repo can really execute.
# `as_posix` because a Windows path in TOML would eat its own backslashes.
_PYTHON = Path(sys.executable).as_posix()

_CONFIG = f"""\
[worktree]
base_branch = "main"

[[verify.checks]]
name = "base"
command = [{_PYTHON!r}, "-c", ""]
modes = ["fast", "full"]

[policy]
required_gates = ["verify"]
max_rework = 2
"""

_PYPROJECT = """\
[tool.noqa_debt]
unreasoned_count = 0

[tool.noqa_debt.frozen]
S603 = 1
"""


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _check(name: str) -> str:
    return (
        f'[[verify.checks]]\nname = "{name}"\ncommand = [{_PYTHON!r}, "-c", ""]\nmodes = ["fast"]\n'
    )


def _fragment(lane: str) -> str:
    """One lane's whole contribution: the check it wired, and the debt its change added."""
    return (
        f"{_check(f'gate-{lane}')}\n"
        "[ratchet.noqa_debt]\ncount_delta = 1\n\n"
        "[ratchet.noqa_debt.frozen]\nS603 = 1\n"
    )


def _commit(worktree: Path, message: str) -> None:
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", message)


@pytest.fixture
def three_lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Session]]:
    """A base checkout on ``main`` and three provisioned lanes, none of them committed yet."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "user.name", "Harness Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "basicly.toml").write_text(_CONFIG, encoding="utf-8")
    (repo / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (repo / dropin.FRAGMENT_DIR).mkdir()
    (repo / dropin.FRAGMENT_DIR / "README.md").write_text("# fragments\n", encoding="utf-8")
    _commit(repo, "chore: seed the fixture repo")
    base_head = _git(repo, "rev-parse", "HEAD")

    sessions: dict[str, Session] = {}
    for lane in LANES:
        path = tmp_path / f"worktree-{lane}"
        _git(repo, "worktree", "add", "-q", "-b", f"harness/{lane}", str(path), "main")
        sessions[lane] = Session(
            name=lane,
            branch=f"harness/{lane}",
            base="main",
            base_head=base_head,
            worktree_path=str(path),
            created_at="2026-08-13T00:00:00Z",
        )
    monkeypatch.setattr(merge, "load_session", lambda name, _root: sessions[name])
    monkeypatch.setattr(merge, "current_branch", lambda _root: "main")
    monkeypatch.setattr(merge, "reconcile_beads", lambda _root: None)
    return repo, sessions


def _land(repo: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[merge.QueueResult], list[str]]:
    """Run the queue over all three lanes, recording every rework attempt it charges."""
    charged: list[str] = []
    monkeypatch.setattr(
        policy, "record_rework", lambda _root, bead, _gate: charged.append(bead) or 1
    )
    queue = [(lane, f"fx-{lane}") for lane in LANES]
    return merge.merge_queue(repo, queue, verify_mode="fast"), charged


def test_three_lanes_each_adding_a_check_and_a_ratchet_entry_all_land(
    three_lanes: tuple[Path, dict[str, Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The criterion: no bounce, no rework, and a composed state that matches the tree."""
    repo, sessions = three_lanes
    for lane, session in sessions.items():
        fragment = session.path / dropin.FRAGMENT_DIR / f"basicly-{lane}.toml"
        fragment.write_text(_fragment(lane), encoding="utf-8")
        _commit(session.path, f"feat: lane {lane} wires a gate")

    results, charged = _land(repo, monkeypatch)

    assert [result.result.status for result in results] == ["merged"] * 3, [
        result.result.detail for result in results
    ]
    assert [result.result.conflicts for result in results] == [()] * 3
    assert charged == []

    # The assembled config on the merged base carries every lane's check, in filename order
    # rather than landing order, so the set does not depend on who landed first.
    assert [check.name for check in config.load_verify_config(repo).checks] == [
        "base",
        "gate-one",
        "gate-three",
        "gate-two",
    ]
    # And the ratchet the three lanes each moved agrees with the tree they made together:
    # 1 + 1 + 1 + 1 suppressions, three unargued ones, from three deltas of +1.
    assert dropin.compose(repo, "noqa_debt", frozen={"S603": 1}, count=0) == dropin.Baseline(
        {"S603": 4}, 3
    )


def test_the_unsplit_form_bounces_the_lanes_that_land_second(
    three_lanes: tuple[Path, dict[str, Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the same three lanes on the shared anchors still collide.

    Without this, deleting the fragment scan would leave the test above passing on three
    lanes that wrote nothing anyone reads — which is the shape of failure this whole change
    is about. Two of the three bounce, each on both anchors, and each is charged.
    """
    repo, sessions = three_lanes
    for lane, session in sessions.items():
        for name, addition in (
            ("basicly.toml", f"\n{_check(f'gate-{lane}')}"),
            ("pyproject.toml", f'"{lane}" = 1\n'),
        ):
            path = session.path / name
            path.write_text(path.read_text(encoding="utf-8") + addition, encoding="utf-8")
        _commit(session.path, f"feat: lane {lane} edits the anchors")

    results, charged = _land(repo, monkeypatch)

    statuses = [result.result.status for result in results]
    assert statuses.count("merged") == 1, [result.result.detail for result in results]
    bounced = [result for result in results if result.result.conflicted]
    assert len(bounced) == 2
    assert charged == [f"fx-{result.result.name}" for result in bounced]
    for result in bounced:
        assert set(result.result.conflicts) == {"basicly.toml", "pyproject.toml"}
