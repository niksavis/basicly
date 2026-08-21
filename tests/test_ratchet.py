"""Tests for the ratchet the three `[[verify.checks]]` gates share (basicly-2j5a).

`module-size`, `comment-density` and `noqa-debt` each defined `Ratchet`, `Finding`,
`RatchetError`, the loader, the git walk and `SCOPE_ROOTS` for themselves. The scope roots
agreed by luck, and the acceptance criterion is that they can no longer disagree — so the
first two tests here are the extraction itself: one definition in the tree, and one added
root reaching all three walks.

The rest pin the two properties `basicly-05g0` established that the extraction had to carry
across, because both are invisible in a green run:

* **A waiver count stays whole.** `compose` takes `fractional` per gate, and `count_delta`
  is passed `fractional=False` whatever the entries are counted in, so a waiver can never be
  half taken.
* **A composed share is rounded to the grid it is measured on.** `51.3 - 0.1` is
  `51.199999999999996`, and unrounded the module a lane just cut reads as having grown.

The gates are loaded here before their own test modules may have loaded them, so `ratchet`
is loaded first: each gate's ``import ratchet`` then binds this instance, which is what makes
patching :data:`ratchet.SCOPE_ROOTS` reach all three.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / ".scripts"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ratchet = _load(SCRIPTS / "ratchet.py", "ratchet")
GATE_FILES = ("check_module_size.py", "check_comment_density.py", "check_noqa_debt.py")
GATES = tuple(_load(SCRIPTS / name, name.removesuffix(".py")) for name in GATE_FILES)

# A module large enough to clear `comment-density`'s 200-token floor, in a root no gate
# scopes today, carrying one countable directive.
_OUTSIDE = (
    '"""A module in a root no gate scopes."""\n\nX = [\n'
    + "".join(f'    "value {index}",\n' for index in range(120))
    + "]\nY = X  # noqa: E402 - a suppression the walk has to reach\n"
)


def _scratch_repo(tmp_path: Path) -> Path:
    """A git repo whose only tracked module sits outside :data:`ratchet.SCOPE_ROOTS`."""
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra" / "mod.py").write_text(_OUTSIDE, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def _walked(repo: Path) -> tuple[list[str], list[str], list[str]]:
    """What each of the three gates finds in *repo*, in the order :data:`GATES` names them."""
    module_size, comment_density, noqa_debt = GATES
    return (
        [module.path for module in module_size.tracked_modules(repo)],
        [module.path for module in comment_density.tracked_modules(repo)],
        [item.path for item in noqa_debt.tracked_suppressions(repo)],
    )


# --- the extraction ------------------------------------------------------------------


@pytest.mark.parametrize(
    "definition",
    ["class Ratchet[", "class Finding", "class RatchetError", "SCOPE_ROOTS = "],
)
def test_the_framework_is_defined_once_for_the_three_ratchet_gates(definition: str) -> None:
    """Three copies is how the scope roots came to agree by luck rather than by rule.

    Scoped to the four files rather than to `.scripts/`: `kit_deployment.py`,
    `check_test_naming.py` and `wired_or_deleted.py` each hold a `Finding` of their own with
    different fields, and `check_corpus_drift.py` a `RatchetError(RuntimeError)` for a gate
    with no frozen baseline. Those are separate gates, and folding them in would be a claim
    this change did not check.
    """
    pattern = re.compile(rf"^{re.escape(definition)}", re.MULTILINE)
    holders = [
        name
        for name in ("ratchet.py", *GATE_FILES)
        if pattern.search((SCRIPTS / name).read_text(encoding="utf-8"))
    ]

    assert holders == ["ratchet.py"]


def test_a_scope_root_added_once_is_seen_by_all_three_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion, in both directions: unscoped, then scoped by one edit."""
    repo = _scratch_repo(tmp_path)

    assert _walked(repo) == ([], [], [])

    monkeypatch.setattr(ratchet, "SCOPE_ROOTS", (*ratchet.SCOPE_ROOTS, "extra"))

    assert _walked(repo) == (["extra/mod.py"], ["extra/mod.py"], ["extra/mod.py"])


def test_a_tree_git_will_not_list_is_refused_rather_than_measured_as_empty(
    tmp_path: Path,
) -> None:
    """An empty walk would report every frozen entry as stale at once."""
    with pytest.raises(ratchet.RatchetError, match="could not list tracked files"):
        list(ratchet.tracked_sources(tmp_path))


# --- the recorded state --------------------------------------------------------------


def _pyproject(repo: Path, body: str) -> Path:
    (repo / "pyproject.toml").write_text(body, encoding="utf-8")
    (repo / "basicly.d").mkdir(exist_ok=True)
    return repo


def _fragment(repo: Path, body: str) -> None:
    (repo / "basicly.d" / "basicly-one.toml").write_text(body, encoding="utf-8")


def _load_shares(repo: Path) -> Any:
    """A fractional gate's baseline, which is the shape both preserved properties live on."""
    return ratchet.compose_ratchet(repo, "gate", count_key="waiver_count", entry_type=float)


def test_a_composed_share_is_rounded_to_the_grid_it_is_measured_on(tmp_path: Path) -> None:
    """Unrounded, 51.3 - 0.1 is 51.199999999999996 and the module just cut reads as grown."""
    repo = _pyproject(
        tmp_path,
        '[tool.gate]\nwaiver_count = 0\n\n[tool.gate.frozen]\n"src/fsck.py" = 51.3\n',
    )
    _fragment(repo, '[ratchet.gate.frozen]\n"src/fsck.py" = -0.1\n')

    assert _load_shares(repo).frozen == {"src/fsck.py": 51.2}


def test_a_waiver_count_stays_whole_when_the_entries_are_fractional(tmp_path: Path) -> None:
    """`count_delta` counts modules, so a fractional gate may not take half a waiver."""
    repo = _pyproject(tmp_path, "[tool.gate]\nwaiver_count = 1\n")
    _fragment(repo, "[ratchet.gate]\ncount_delta = 1.5\n")

    with pytest.raises(ratchet.RatchetError, match="count_delta must be an integer delta"):
        _load_shares(repo)


def test_a_whole_delta_still_moves_a_fractional_gate_s_waiver_count(tmp_path: Path) -> None:
    """The other half: refusing 1.5 must not refuse the 1 a lane really records."""
    repo = _pyproject(tmp_path, "[tool.gate]\nwaiver_count = 1\n")
    _fragment(repo, "[ratchet.gate]\ncount_delta = 1\n")

    assert _load_shares(repo).count == 2


def test_a_missing_table_is_refused_rather_than_defaulted_to_empty(tmp_path: Path) -> None:
    """A gate that invented an empty baseline would pass everything it exists to refuse."""
    repo = _pyproject(tmp_path, "[tool.other]\n")

    with pytest.raises(ratchet.RatchetError, match=r"no \[tool\.gate\]"):
        _load_shares(repo)


def test_a_count_recorded_under_another_key_is_refused(tmp_path: Path) -> None:
    """The two gates spell it `waiver_count` and the third `unreasoned_count`."""
    repo = _pyproject(tmp_path, "[tool.gate]\nunreasoned_count = 1\n")

    with pytest.raises(ratchet.RatchetError, match="must declare waiver_count"):
        _load_shares(repo)


def test_a_counting_gate_refuses_a_fractional_entry(tmp_path: Path) -> None:
    """`entry_type` is the validation as well as the composition mode."""
    repo = _pyproject(
        tmp_path, '[tool.gate]\nwaiver_count = 0\n\n[tool.gate.frozen]\n"a.py" = 4.5\n'
    )

    with pytest.raises(ratchet.RatchetError, match="go-live number"):
        ratchet.compose_ratchet(repo, "gate", count_key="waiver_count", entry_type=int)


# --- what a finding says -------------------------------------------------------------


def test_a_finding_prints_its_subject_then_its_remedy(capsys: pytest.CaptureFixture) -> None:
    """`noqa-debt`'s subject is a rule code, which is why the field is not called `path`."""
    ratchet.report("noqa-debt", [ratchet.Finding("E731", "2 up from 1", "record it")])

    assert capsys.readouterr().err == "noqa-debt: E731: 2 up from 1\nnoqa-debt:   record it\n"
