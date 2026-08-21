"""Tests for the prose-density ratchet (basicly-wxr3).

The gate exists to fail, so most of these assert a failure and name the number reported.
Three fail-open shapes are pinned: a docstring that is not counted (which would let
narration relocate out of a `#` comment into the docstring `D` already mandates), a
pragma that is counted (which would price a `# noqa` like an essay), and a frozen entry
that never expires (which licenses regrowth to its go-live share).

Logic tests drive :func:`collect` with synthetic modules; a `git ls-files` fixture per
case would test git. One subprocess run covers the real tree. The `basicly.d` delta route
(basicly-05g0) is the sibling :mod:`test_check_comment_density_fragments`, which the size
cap forced out of here: the boundary is measurement and ratchet decisions against the
fragment route.

Every waiver marker below is indented inside a string literal, so this file does not waive
itself — ``test_neither_the_gate_nor_this_test_carries_a_waiver`` proves it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_comment_density.py"
RATCHET = REPO_ROOT / ".scripts" / "ratchet.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_comment_density")
CAP = gate.CAP

# The marker the gate reads, without its colon; `ratchet.waiver_reason` takes it as data.
WAIVER_MARKER = "comment-density-waiver"


def _waiver(subject: str, reason: str) -> object:
    """A granted waiver of the ordinary kind: permanent, so nothing retires it."""
    return gate.Waiver(subject=subject, kind=gate.COHESION, retires=None, reason=reason)


def _module(path: str, share: float, waiver: str | None = None) -> object:
    return gate.Module(
        path=path,
        share=share,
        tokens=1000,
        waiver=None if waiver is None else _waiver(path, waiver),
    )


def _ratchet(frozen: dict[str, float] | None = None, waivers: int = 0) -> object:
    return gate.Ratchet(frozen=frozen or {}, count=waivers)


def test_a_docstring_counts_as_prose() -> None:
    """Otherwise narration relocates into the docstring ruff `D` already requires."""
    bare = "def f(a):\n    return a\n"
    documented = (
        'def f(a):\n    """Return a, at length, describing the return of it."""\n    return a\n'
    )
    assert gate.prose_tokens(bare) == 0
    assert gate.prose_tokens(documented) > 0
    assert gate.measure(documented)[0] > gate.measure(bare)[0]


@pytest.mark.parametrize(
    "pragma",
    ["# noqa: E501", "# nosec B603", "# type: ignore[arg-type]", "# module-size-waiver: big"],
)
def test_a_pragma_is_not_prose(pragma: str) -> None:
    """Charging for a marker a tool reads would price a suppression like an essay."""
    assert gate.prose_tokens(f"x = 1  {pragma}\n") == 0


def test_an_ordinary_comment_is_prose() -> None:
    """The narration the gate exists to refuse has to register as something."""
    assert gate.prose_tokens("# this restates the assignment below it\nx = 1\n") > 0


def test_unparseable_source_is_refused_rather_than_scored_zero() -> None:
    """0 said the opposite of the truth: a raw slice read as pure code (basicly-e7rtjn)."""
    with pytest.raises(gate.RatchetError, match="does not parse"):
        gate.prose_tokens("def (:\n")


def test_a_tracked_module_that_does_not_parse_still_lets_the_gate_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruff owns a syntax error; the refusal above is for a lane measuring a fragment."""
    broken = "def (:\n" + "# pad past the token floor.\n" * 40
    monkeypatch.setattr(gate, "tracked_sources", lambda _repo: [("src/broken.py", broken)])
    (module,) = gate.tracked_modules(Path())
    assert module.share == 0.0


def test_a_module_over_the_cap_and_not_frozen_fails() -> None:
    """The closed frozen list is what makes the debt fall; nothing may join it silently."""
    (finding,) = gate.collect([_module("a.py", CAP + 0.1)], _ratchet())
    assert f"over the {CAP}% cap" in finding.detail
    assert "cut narration" in finding.remedy


def test_a_module_at_the_cap_and_not_frozen_passes() -> None:
    """The cap is inclusive, so a module sitting exactly on it is not a finding."""
    assert gate.collect([_module("a.py", CAP)], _ratchet()) == []


def test_a_frozen_module_that_grew_fails_and_names_both_shares() -> None:
    """A failure without both numbers leaves the author guessing how far to come back."""
    (finding,) = gate.collect([_module("a.py", 67.6)], _ratchet({"a.py": 67.5}))
    assert "67.6% prose, up from the frozen 67.5%" in finding.detail


def test_a_frozen_module_that_fell_passes() -> None:
    """Falling is the only direction a frozen entry may move without editing the table."""
    assert gate.collect([_module("a.py", 60.0)], _ratchet({"a.py": 67.5})) == []


def test_a_frozen_module_that_reached_the_cap_must_leave_the_list() -> None:
    """Leaving the entry would license regrowth back to the go-live share."""
    (finding,) = gate.collect([_module("a.py", CAP)], _ratchet({"a.py": 67.5}))
    assert "licenses it to grow back" in finding.detail
    assert 'delete `"a.py"`' in finding.remedy


def test_a_frozen_entry_for_a_vanished_module_is_stale() -> None:
    """A baseline describing nothing is a line a future reader would trust wrongly."""
    (finding,) = gate.collect([], _ratchet({"gone.py": 67.5}))
    assert "no readable tracked module is at this path" in finding.detail


def test_a_waiver_replaces_the_frozen_entry() -> None:
    """Holding both would leave a baseline no longer measured against anything."""
    waived = [_module("a.py", 90.0, waiver="pinned vendor data")]
    (finding,) = gate.collect(waived, _ratchet({"a.py": 67.5}, waivers=1))
    assert "it carries a waiver, which replaces the frozen entry" in finding.detail


def test_a_waived_module_over_the_cap_passes() -> None:
    """A module whose payload is provenance is the case the ratchet is not a purge for."""
    waived = [_module("a.py", 90.0, waiver="pinned vendor data")]
    assert gate.collect(waived, _ratchet(waivers=1)) == []


@pytest.mark.parametrize(("waivers", "declared", "direction"), [(1, 0, "added"), (0, 1, "removed")])
def test_the_waiver_count_is_ratcheted_in_both_directions(
    waivers: int, declared: int, direction: str
) -> None:
    """A count that only rises decays into a blanket exemption nobody reads."""
    modules = [_module(f"w{i}.py", 90.0, waiver="reason") for i in range(waivers)]
    findings = gate.collect(modules, _ratchet(waivers=declared))
    counted = next(finding for finding in findings if finding.subject == "pyproject.toml")
    assert direction in counted.detail


def test_a_waiver_must_start_the_line_and_carry_a_reason() -> None:
    """Otherwise a file that merely mentions the marker waives itself."""
    assert gate.read_waiver("a.py", "    # comment-density-waiver: indented", WAIVER_MARKER) is None
    assert gate.read_waiver("a.py", "# comment-density-waiver:", WAIVER_MARKER) is None
    reason = gate.read_waiver("a.py", "# comment-density-waiver: vendor data", WAIVER_MARKER).reason
    assert reason == "vendor data"


def test_a_missing_ratchet_table_raises_rather_than_defaulting_to_empty(tmp_path: Path) -> None:
    """An empty baseline would fail all 75 frozen modules, so the gate must not invent one."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")
    with pytest.raises(gate.RatchetError, match=r"no \[tool\.comment_density\]"):
        gate.load_ratchet(tmp_path)


def test_a_composed_share_is_reported_on_the_grid_it_is_measured_on() -> None:
    """A sum of one-decimal floats is not one: 51.3 - 0.1 is 51.199999999999996.

    Unrounded, the module the lane just cut would measure 51.2 and be reported as having
    grown past its own new baseline.
    """
    assert 51.3 - 0.1 != 51.2
    assert round(51.3 - 0.1, 1) == 51.2


def test_a_small_module_is_out_of_scope() -> None:
    """One mandatory docstring dominates a stub, so its share says nothing about density."""
    modules = gate.tracked_modules(REPO_ROOT)
    assert all(module.tokens >= gate.MIN_TOKENS for module in modules)


def test_the_frozen_list_matches_the_tree() -> None:
    """A baseline at or under the cap is a graduated entry the gate would never clear."""
    ratchet = gate.load_ratchet(REPO_ROOT)
    measured = {module.path: module.share for module in gate.tracked_modules(REPO_ROOT)}
    for path, baseline in ratchet.frozen.items():
        assert path in measured, f"{path} is frozen but not in the tree"
        assert baseline > CAP, f"{path} is frozen at {baseline}%, within the {CAP}% cap"


def test_the_gate_passes_on_the_real_tree() -> None:
    """A gate that cannot go green on its own baseline gets turned off instead of paid."""
    completed = subprocess.run(  # nosec B603 B607
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    assert completed.returncode == 0, completed.stderr
    assert "within the" in completed.stdout


def test_the_gate_is_wired_to_something_that_runs_it() -> None:
    """An instrument built and never connected is this repo's named defect class."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = config.get("verify", {}).get("checks", [])
    (entry,) = [check for check in checks if check["name"] == "comment-density"]
    assert entry["command"][-1].endswith("check_comment_density.py")


def test_neither_the_gate_nor_this_test_carries_a_waiver() -> None:
    """Both files name the marker repeatedly; a column-0 one would exempt them."""
    for path in (SCRIPT, Path(__file__)):
        assert gate.read_waiver(str(path), path.read_text(encoding="utf-8"), WAIVER_MARKER) is None
