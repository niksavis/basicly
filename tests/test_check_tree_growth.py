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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_tree_growth")


def _tree(when: str, **tokens: int) -> Any:
    return gate.Tree(ref=when, when=when, tokens=dict(tokens))


def _growth(base: Any, now: Any) -> Any:
    return gate.Growth(base=base, now=now, days=7)


def test_compliant_new_modules_move_the_signal() -> None:
    base = _tree("start", a=3000, b=3000)
    now = _tree("end", a=3000, b=3000, c=1200, d=1100, e=700)

    growth = _growth(base, now)

    assert growth.net == 3000
    assert growth.in_new == 3000
    assert growth.in_existing == 0
    assert "+3000 tokens over 7d" in gate.report_lines(growth)[0]


def test_a_split_with_no_new_code_is_not_reported_as_growth() -> None:
    base = _tree("start", origin=6000)
    now = _tree("end", origin=3500, extracted=2500)

    growth = _growth(base, now)

    assert growth.net == 0
    assert growth.in_new == 2500
    assert growth.in_existing == -2500
    assert "net is 0% of the new tokens" in gate.report_lines(growth)[1]


def test_module_count_alone_cannot_tell_an_addition_from_a_split() -> None:
    base = _tree("start", origin=6000)
    added = _growth(base, _tree("end", origin=6000, new=2500))
    split = _growth(base, _tree("end", origin=3500, extracted=2500))

    counted = len(added.now.tokens) - len(added.base.tokens)
    assert counted == len(split.now.tokens) - len(split.base.tokens)
    assert added.net == 2500
    assert split.net == 0


def test_a_deleted_module_is_reported_apart_from_shrinkage() -> None:
    deleted = _growth(_tree("start", a=3000, b=1000), _tree("end", a=3000))
    shrunk = _growth(_tree("start", a=3000, b=1000), _tree("end", a=2000, b=1000))

    assert deleted.net == shrunk.net == -1000
    assert (deleted.in_deleted, deleted.in_existing) == (-1000, 0)
    assert (shrunk.in_deleted, shrunk.in_existing) == (0, -1000)


def test_the_three_components_sum_to_the_net() -> None:
    growth = _growth(_tree("start", a=3000, b=1000, c=500), _tree("end", a=3500, c=500, d=900))

    assert growth.in_new + growth.in_existing + growth.in_deleted == growth.net


def test_the_ratio_is_omitted_rather_than_divided_by_zero() -> None:
    growth = _growth(_tree("start", a=3000), _tree("end", a=4000))

    assert "net is -" in gate.report_lines(growth)[1]


def _dated_repo(root: Path, dates: tuple[str, ...]) -> Path:
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
    repo = _dated_repo(tmp_path / "repo", ("2020-01-12T12:00:00+00:00",))

    assert gate.baseline_ref(repo, days=7) is None


def test_a_measured_commit_carries_its_short_ref_and_date(tmp_path: Path) -> None:
    repo = _dated_repo(tmp_path / "repo", ("2020-01-01T12:00:00+00:00",))

    tree = gate.measure_commit(repo, "HEAD")

    assert tree.when == "2020-01-01"
    assert tree.tokens and set(tree.tokens) == {"src/pkg/a.py"}


def test_an_uncovered_window_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _dated_repo(tmp_path / "repo", ("2020-01-12T12:00:00+00:00",))
    monkeypatch.setattr(gate, "REPO_ROOT", repo)

    assert gate.main() == 0
    assert "window unmeasured" in capsys.readouterr().out


def test_a_checkout_git_cannot_answer_for_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)

    assert gate.main() == 0
    assert "unmeasured" in capsys.readouterr().out


def test_the_signal_reports_a_value_and_a_window_on_this_repository() -> None:

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


def _history_growth(sha: str) -> Any:
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

    split = _history_growth("ca7c68e")
    addition = _history_growth("53ed12c")

    assert (split.in_new, split.net) == (30974, 10172)
    assert (addition.in_new, addition.net) == (4958, 4958)
    assert split.net / split.in_new < addition.net / addition.in_new


def test_the_signal_is_wired_to_something_that_runs_it() -> None:
    checks = {check.name: check for check in load_verify_config(REPO_ROOT).checks}

    entry = checks["tree-growth"]
    assert list(entry.command)[-1].endswith("check_tree_growth.py")
    assert list(entry.command)[:3] == ["uv", "run", "python"]
    assert set(entry.modes) == {"fast", "full"}
    assert not entry.fix_command
