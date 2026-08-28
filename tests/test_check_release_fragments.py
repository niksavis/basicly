"""Tests for the release-fragment gate (basicly-x8hwwv's sibling: size and bullet shape).

The baseline is git itself, so every case here drives a real scratch repository: a
fragment in HEAD carries its committed size as its own ceiling, a new one must fit the
cap, and a bulletless first line is refused unconditionally.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_release_fragments.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_release_fragments")

OLD_BODY = "- an entry committed long ago, well over any cap\n" + ("x" * 5000) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch repository whose HEAD holds one oversized fragment."""
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()
    (fragments / "basicly-old1.fixed.md").write_text(OLD_BODY, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run(
        [*git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"], check=True
    )
    return tmp_path


def test_a_committed_fragment_keeps_its_size_as_its_own_ceiling(repo: Path) -> None:
    """An unchanged oversized fragment is inherited debt, not a finding."""
    refused, new, inherited = gate.findings(repo)
    assert refused == []
    assert (new, inherited) == (0, 1)


def test_a_committed_fragment_that_grows_is_refused_and_one_that_shrinks_passes(
    repo: Path,
) -> None:
    """Both directions of the shrink-only rule on the same committed file."""
    path = repo / "changelog.d" / "basicly-old1.fixed.md"
    path.write_text(OLD_BODY + "grown\n", encoding="utf-8")
    refused, _new, _inherited = gate.findings(repo)
    assert len(refused) == 1
    assert "may only shrink" in refused[0]

    path.write_text("- an entry committed long ago, shrunk\n", encoding="utf-8")
    assert gate.findings(repo)[0] == []


def test_a_new_fragment_must_fit_the_cap(repo: Path) -> None:
    """Absent from HEAD means new, and new means the cap binds, both directions."""
    path = repo / "changelog.d" / "basicly-new1.added.md"
    path.write_text("- " + "y" * (gate.CAP_CHARS + 10) + "\n", encoding="utf-8")
    refused, new, _inherited = gate.findings(repo)
    assert new == 1
    assert len(refused) == 1
    assert f"{gate.CAP_CHARS}-char cap" in refused[0]

    path.write_text("- a short entry\n", encoding="utf-8")
    assert gate.findings(repo)[0] == []


def test_a_bulletless_first_line_is_refused_whatever_its_age(repo: Path) -> None:
    """The v0.9.0 orphaning defect: loose prose loses its entry at assembly."""
    path = repo / "changelog.d" / "basicly-new2.added.md"
    path.write_text("loose prose with no bullet\n", encoding="utf-8")
    refused, _new, _inherited = gate.findings(repo)
    assert len(refused) == 1
    assert "must start with `- `" in refused[0]


def test_a_misnamed_file_is_not_this_gates_population(repo: Path) -> None:
    """`basicly release` refuses misnamed files; counting them here would double-report."""
    (repo / "changelog.d" / "README.md").write_text("not a fragment\n", encoding="utf-8")
    (repo / "changelog.d" / "basicly-x.typo.md").write_text("bad category\n", encoding="utf-8")
    assert gate.findings(repo)[0] == []


def test_the_gate_fails_end_to_end_and_names_the_file(repo: Path) -> None:
    """The script alone travels: stdlib-only, so the copy needs no package beside it."""
    scripts = repo / ".scripts"
    scripts.mkdir()
    copied = shutil.copy(SCRIPT, scripts / SCRIPT.name)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, copied], capture_output=True, text=True, check=False, cwd=repo
        )

    baseline = run()
    assert baseline.returncode == 0
    assert "1 fragment(s)" in baseline.stdout

    (repo / "changelog.d" / "basicly-new3.security.md").write_text(
        "no bullet either\n", encoding="utf-8"
    )
    completed = run()
    assert completed.returncode == 1
    assert "basicly-new3.security.md" in completed.stderr


def test_the_live_tree_passes_its_own_gate() -> None:
    """The positive control for the wiring: the gate sees the whole live population, green.

    No floor on the count: a release deletes every fragment it assembles, so a release
    commit legitimately holds zero and the directory's README is the marker that the
    population is the real one (basicly-ssv5qq).
    """
    refused, new, inherited = gate.findings(REPO_ROOT)
    assert refused == []
    assert (REPO_ROOT / gate.FRAGMENT_DIR / "README.md").is_file()
    assert inherited + new == len(gate.fragment_paths(REPO_ROOT))
