"""Tests for the tree-growth signal (basicly-5p49).

Three things have to hold or the signal is not worth printing, and each has a way of
failing quietly:

* **It moves on the action the per-file gates reward.** Adding a compliant module is the
  cheapest way to satisfy a `module-size` ratchet, so a tree signal that stayed flat on it
  would be the third gate agreeing with the two it was built to sit above.
* **It does not call a split growth.** Module count cannot tell the two apart and the mean
  falls under both; net tokens is the one candidate that separates them, which is why the
  synthetic pair below adds exactly one module each way and asserts the counts are equal.
* **It never blocks.** D23 makes an unfired sizing control observability, so every path out
  of :func:`main` — including the ones that reach no number — exits 0.

The discrimination is asserted twice: once on synthetic trees, where the shapes are exact,
and once on this repository's own history, where the claims in the gate's docstring came
from. The history pair skips on a checkout that does not carry the commits (CI clones the
matrix job at depth 1), so it never silently passes by measuring nothing.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly.config import load_verify_config

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_tree_growth.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_tree_growth")


def _tree(when: str, **tokens: int) -> Any:
    """A measured tree; keyword names stand in for module paths."""
    return gate.Tree(ref=when, when=when, tokens=dict(tokens))


def _growth(base: Any, now: Any) -> Any:
    return gate.Growth(base=base, now=now, days=7)


# --- what the signal discriminates ----------------------------------------------------


def test_compliant_new_modules_move_the_signal() -> None:
    """The whole point: the per-file gates are silent here and this is not."""
    base = _tree("start", a=3000, b=3000)
    now = _tree("end", a=3000, b=3000, c=1200, d=1100, e=700)

    growth = _growth(base, now)

    assert growth.net == 3000
    assert growth.in_new == 3000
    assert growth.in_existing == 0
    assert "+3000 tokens over 7d" in gate.report_lines(growth)[0]


def test_a_split_with_no_new_code_is_not_reported_as_growth() -> None:
    """The origin loses exactly what the extracted module gains, so the net is flat."""
    base = _tree("start", origin=6000)
    now = _tree("end", origin=3500, extracted=2500)

    growth = _growth(base, now)

    assert growth.net == 0
    assert growth.in_new == 2500
    assert growth.in_existing == -2500
    assert "net is 0% of the new tokens" in gate.report_lines(growth)[1]


def test_module_count_alone_cannot_tell_an_addition_from_a_split() -> None:
    """Why the signal is tokens: both changes add one module and only one is growth."""
    base = _tree("start", origin=6000)
    added = _growth(base, _tree("end", origin=6000, new=2500))
    split = _growth(base, _tree("end", origin=3500, extracted=2500))

    counted = len(added.now.tokens) - len(added.base.tokens)
    assert counted == len(split.now.tokens) - len(split.base.tokens)
    assert added.net == 2500
    assert split.net == 0


def test_a_deleted_module_is_reported_apart_from_shrinkage() -> None:
    """A deletion and an equal shrink are both -1000 net; only one is a module going."""
    deleted = _growth(_tree("start", a=3000, b=1000), _tree("end", a=3000))
    shrunk = _growth(_tree("start", a=3000, b=1000), _tree("end", a=2000, b=1000))

    assert deleted.net == shrunk.net == -1000
    assert (deleted.in_deleted, deleted.in_existing) == (-1000, 0)
    assert (shrunk.in_deleted, shrunk.in_existing) == (0, -1000)


def test_the_three_components_sum_to_the_net() -> None:
    """The decomposition is exhaustive, or the second line contradicts the first."""
    growth = _growth(_tree("start", a=3000, b=1000, c=500), _tree("end", a=3500, c=500, d=900))

    assert growth.in_new + growth.in_existing + growth.in_deleted == growth.net


def test_the_ratio_is_omitted_rather_than_divided_by_zero() -> None:
    """A window that added no module still has to print a line."""
    growth = _growth(_tree("start", a=3000), _tree("end", a=4000))

    assert "net is -" in gate.report_lines(growth)[1]


# --- the window -----------------------------------------------------------------------


def _dated_repo(root: Path, dates: tuple[str, ...]) -> Path:
    """A git repo with one commit per entry in *dates*, committed at that instant."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@e"], check=True)
    module = root / "src" / "pkg" / "a.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    for index, when in enumerate(dates):
        module.write_text("x = 1\n" * (index + 1), encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", when], check=True, env=env)
    return root


def test_the_baseline_is_the_last_commit_before_the_window_opened(tmp_path: Path) -> None:
    """Anchored on HEAD's own date, so one checkout always answers the same thing.

    Every commit here is from 2020. A wall-clock anchor would put the cutoff in the present
    and return HEAD, reporting a window in which nothing happened; the assertion is that it
    returns the 2020-01-01 commit instead.
    """
    repo = _dated_repo(
        tmp_path / "repo",
        ("2020-01-01T12:00:00+00:00", "2020-01-10T12:00:00+00:00", "2020-01-12T12:00:00+00:00"),
    )
    oldest = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert gate.baseline_ref(repo, days=7) == oldest


def test_a_checkout_that_does_not_reach_back_that_far_has_no_baseline(tmp_path: Path) -> None:
    """A shallow clone must report an uncovered window, never a rescaled one."""
    repo = _dated_repo(tmp_path / "repo", ("2020-01-12T12:00:00+00:00",))

    assert gate.baseline_ref(repo, days=7) is None


def test_a_measured_commit_carries_its_short_ref_and_date(tmp_path: Path) -> None:
    """The window has to be legible from the line alone, or it cannot be checked."""
    repo = _dated_repo(tmp_path / "repo", ("2020-01-01T12:00:00+00:00",))

    tree = gate.measure_commit(repo, "HEAD")

    assert tree.when == "2020-01-01"
    assert tree.tokens and set(tree.tokens) == {"src/pkg/a.py"}


# --- it reports, and never blocks -----------------------------------------------------


def test_an_uncovered_window_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No baseline is a thing to say, not a thing to fail."""
    repo = _dated_repo(tmp_path / "repo", ("2020-01-12T12:00:00+00:00",))
    monkeypatch.setattr(gate, "REPO_ROOT", repo)

    assert gate.main() == 0
    assert "window unmeasured" in capsys.readouterr().out


def test_a_checkout_git_cannot_answer_for_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not a repository at all. An observability signal that fails is a gate."""
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)

    assert gate.main() == 0
    assert "unmeasured" in capsys.readouterr().out


def test_the_signal_reports_a_value_and_a_window_on_this_repository() -> None:
    """Run as a consumer runs it — the shape the running checkout can honestly print.

    Which shape is correct is a property of the checkout, not of the machine, so the
    branch is taken on ``baseline_ref`` — the gate's own answer to whether this clone
    reaches back the window — rather than on the output being read. The matrix job
    clones at depth 1, where one ``unmeasured`` line is the honest report; demanding
    the measured shape there turned every push red on output the gate was right to
    print. Asserted rather than skipped, because the unreachable-window path is the
    one a consumer meets on CI.
    """
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    lines = completed.stdout.splitlines()

    assert completed.returncode == 0, completed.stderr
    if gate.baseline_ref(REPO_ROOT) is None:
        assert len(lines) == 1
        assert "unmeasured" in lines[0]
        return
    first, second, *_ = lines
    assert "tokens over 7d" in first
    assert "->" in first
    assert "tracked modules" in first
    assert "new" in second


# --- the claims the gate's docstring makes about this repository's history ------------


def _history_growth(sha: str) -> Any:
    """This repo's own tree either side of *sha*; skips where the commit is unreachable."""
    known = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}~1^{{commit}}"], check=False
    )
    if known.returncode != 0:
        pytest.skip(f"{sha} is not in this checkout")
    return gate.Growth(
        base=gate.measure_commit(REPO_ROOT, f"{sha}~1"),
        now=gate.measure_commit(REPO_ROOT, sha),
        days=1,
    )


def test_it_separates_a_real_split_from_a_real_addition_in_this_history() -> None:
    """The evidence the metric was chosen on, kept falsifiable.

    ``ca7c68e`` is "split what it grew": 14 new modules holding 30,974 tokens, of which
    10,172 was new code. ``53ed12c`` added the comment-density gate: every token in its two
    new modules was new. Module count ranks them the same way round and the mean ranks them
    backwards; only the net separates them.
    """
    split = _history_growth("ca7c68e")
    addition = _history_growth("53ed12c")

    assert (split.in_new, split.net) == (30974, 10172)
    assert (addition.in_new, addition.net) == (4958, 4958)
    assert split.net / split.in_new < addition.net / addition.in_new


# --- the wiring -----------------------------------------------------------------------


def test_the_signal_is_wired_to_something_that_runs_it() -> None:
    """Read through the composed config: the entry lives in `basicly.d`, not the anchor."""
    checks = {check.name: check for check in load_verify_config(REPO_ROOT).checks}

    entry = checks["tree-growth"]
    assert list(entry.command)[-1].endswith("check_tree_growth.py")
    # A bare `python` on windows-latest is a system interpreter, not the project's.
    assert list(entry.command)[:3] == ["uv", "run", "python"]
    assert set(entry.modes) == {"fast", "full"}
    assert not entry.fix_command
