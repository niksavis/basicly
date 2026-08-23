"""Tests for the retired-vocabulary gate (basicly-e90rue).

The baseline is git itself, so every case drives a real scratch repository: a module's
allowance is its own committed prose count and may only shrink, a new module starts at
zero, and a mention outside a comment or docstring never counts. The retired name is
assembled here by concatenation, never spelled as a bare word, so this test never
appears in its own gate's findings.
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
SCRIPT = REPO_ROOT / ".scripts" / "check_retired_vocabulary.py"

# The retired term, assembled so this module never spells it as a bare prose word.
TERM = "b" + "r"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_retired_vocabulary")


def _source_with_mentions(count: int) -> str:
    """A syntactically valid module whose comments each name the retired term once."""
    lines = [f"# note: this line mentions {TERM} once" for _ in range(count)]
    lines.append("VALUE = 1")
    return "\n".join(lines) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch repository whose HEAD holds one module with three prose mentions."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.py").write_text(_source_with_mentions(3), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run(
        [*git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"], check=True
    )
    return tmp_path


def test_a_committed_module_keeps_its_count_as_its_own_ceiling(repo: Path) -> None:
    """An unchanged module at its committed count is not a finding."""
    refused, carrying, total = gate.findings(repo)
    assert refused == []
    assert (carrying, total) == (1, 3)


def test_a_committed_module_that_grows_is_refused_and_one_that_shrinks_passes(
    repo: Path,
) -> None:
    """Both directions of the shrink-only rule on the same committed module."""
    path = repo / "src" / "sample.py"
    path.write_text(_source_with_mentions(4), encoding="utf-8")
    refused, _carrying, _total = gate.findings(repo)
    assert len(refused) == 1
    assert "4" in refused[0]
    assert "3" in refused[0]

    path.write_text(_source_with_mentions(1), encoding="utf-8")
    assert gate.findings(repo)[0] == []


def _track(repo: Path) -> None:
    """Stage every file so a brand-new module joins the tracked population `git ls-files` reads."""
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)


def test_a_new_module_must_start_at_zero_allowance(repo: Path) -> None:
    """Absent from HEAD means new, and new means zero allowance, both directions."""
    path = repo / "src" / "newmod.py"
    path.write_text(_source_with_mentions(1), encoding="utf-8")
    _track(repo)
    refused, _carrying, _total = gate.findings(repo)
    assert len(refused) == 1
    assert "allowance of 0" in refused[0]

    path.write_text(_source_with_mentions(0), encoding="utf-8")
    assert gate.findings(repo)[0] == []


def test_a_mention_inside_a_string_literal_or_identifier_does_not_count(repo: Path) -> None:
    """Only a comment or a docstring counts; code identifiers and string bodies never do."""
    source = f'def {TERM}():\n    return "a {TERM} value"\n'
    pattern = gate.RETIRED[TERM]
    assert gate.prose_count(source, pattern) == 0

    path = repo / "src" / "identifiers.py"
    path.write_text(source, encoding="utf-8")
    _track(repo)
    assert gate.findings(repo)[0] == []


def test_the_gate_fails_end_to_end_and_names_the_module(repo: Path) -> None:
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
    assert f"carrying '{TERM}'" in baseline.stdout

    (repo / "src" / "sample.py").write_text(_source_with_mentions(4), encoding="utf-8")
    completed = run()
    assert completed.returncode == 1
    assert "src/sample.py" in completed.stderr


def test_the_live_tree_currently_passes_with_a_real_population() -> None:
    """The positive control for the wiring: the population is real and currently green."""
    refused, carrying, _total = gate.findings(REPO_ROOT)
    assert refused == []
    assert carrying > 50
