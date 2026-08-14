"""Tests for the prose-density ratchet (basicly-wxr3).

The gate exists to fail, so most of these assert a failure and name the number reported.
Three fail-open shapes are pinned: a docstring that is not counted (which would let
narration relocate out of a `#` comment into the docstring `D` already mandates), a
pragma that is counted (which would price a `# noqa` like an essay), and a frozen entry
that never expires (which licenses regrowth to its go-live share).

Logic tests drive :func:`collect` with synthetic modules; a `git ls-files` fixture per
case would test git. One subprocess run covers the real tree, and the `basicly.d` delta
route (basicly-05g0) is exercised in a scratch repository, because a fragment only means
anything once `git ls-files`, the TOML read and the composer run together.

Every waiver marker below is indented inside a string literal, so this file does not waive
itself — ``test_neither_the_gate_nor_this_test_carries_a_waiver`` proves it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from basicly import dropin

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


def _module(path: str, share: float, waiver: str | None = None) -> object:
    return gate.Module(path=path, share=share, tokens=1000, waiver=waiver)


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


def test_unparseable_source_scores_zero_rather_than_crashing() -> None:
    """A syntax error is ruff's finding to report, not a reason this gate cannot run."""
    assert gate.prose_tokens("def (:\n") == 0


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
    assert gate.waiver_reason("    # comment-density-waiver: indented", WAIVER_MARKER) is None
    assert gate.waiver_reason("# comment-density-waiver:", WAIVER_MARKER) is None
    reason = gate.waiver_reason("# comment-density-waiver: vendor data", WAIVER_MARKER)
    assert reason == "vendor data"


def test_a_missing_ratchet_table_raises_rather_than_defaulting_to_empty(tmp_path: Path) -> None:
    """An empty baseline would fail all 75 frozen modules, so the gate must not invent one."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")
    with pytest.raises(gate.RatchetError, match=r"no \[tool\.comment_density\]"):
        gate.load_ratchet(tmp_path)


def _prose_module(marker: str = "") -> str:
    """A module over the cap and over the token floor, whose payload is narration.

    Written rather than borrowed from the tree so the share it measures is a property of
    this test, not of whatever the module it borrowed from looks like this week.
    """
    functions = "\n\n".join(
        f'def f{index}() -> int:\n    """Say nothing the signature does not, at some'
        f' length, for the {word} time."""\n    # And restate the return below.\n'
        f"    return {index}\n"
        for index, word in enumerate(("first", "second", "third", "fourth", "fifth"))
    )
    return f'"""A module whose whole payload is narration."""\n{marker}\n\n{functions}'


def _scratch_repo(tmp_path: Path, module: str, *, frozen: str = "", waiver_count: int = 0) -> Path:
    """A tiny git repo holding the gate, the two modules it reads through, and *module*.

    The gate resolves its root from its own location, so a copy of it in a tmp tree
    exercises `git ls-files`, the tokenizer, the TOML read and the fragments together
    without putting a deliberate defect in this working tree.

    Only the measured modules are added to the index. `src` and `.scripts` are both in the
    gate's scope roots, so tracking the modules the copy has to import would measure them
    too — `read_cost.py` is 82% prose against a record that freezes nothing, and every case
    here would report it. Importing does not need git; measuring does.
    """
    scripts = tmp_path / ".scripts"
    scripts.mkdir()
    for script in (SCRIPT, RATCHET):
        shutil.copy(script, scripts / script.name)
    package = tmp_path / "src" / "basicly"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("dropin.py", "read_cost.py"):
        shutil.copy(REPO_ROOT / "src" / "basicly" / name, package / name)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.comment_density]\nwaiver_count = {waiver_count}\n\n"
        f"[tool.comment_density.frozen]\n{frozen}",
        encoding="utf-8",
    )
    (tmp_path / "src" / "mod.py").write_text(module, encoding="utf-8")
    (tmp_path / dropin.FRAGMENT_DIR).mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "src/mod.py"], check=True)
    return tmp_path


def _fragment(repo: Path, name: str, body: str) -> None:
    """Drop one lane's fragment into the scratch repo's ``basicly.d``."""
    (repo / dropin.FRAGMENT_DIR / f"{name}.toml").write_text(body, encoding="utf-8")


def _second_waived_module(repo: Path) -> None:
    """A second module carrying a waiver, tracked, so two waivers are outstanding."""
    (repo / "src" / "other.py").write_text(
        _prose_module("# comment-density-waiver: also provenance"), encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-f", "src/other.py"], check=True)


def _run_gate(repo: Path) -> subprocess.CompletedProcess[str]:
    """Run the copied gate the way a commit runs it."""
    return subprocess.run(  # nosec B603
        [sys.executable, str(repo / ".scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
    )


def test_a_fragment_delta_reaches_the_gate_end_to_end(tmp_path: Path) -> None:
    """The delta route the other two ratchets already have (basicly-05g0).

    The module is recorded at exactly the share it measures, so it passes; the fragment
    then lowers that record by 1.4 and the same unchanged module is over its baseline. A
    gate reading `pyproject.toml` raw reports nothing at all here.
    """
    module = _prose_module()
    share, _ = gate.measure(module)
    repo = _scratch_repo(tmp_path, module, frozen=f'"src/mod.py" = {share}\n')
    assert _run_gate(repo).returncode == 0, "the recorded share must pass before the fragment"

    _fragment(repo, "basicly-one", '[ratchet.comment_density.frozen]\n"src/mod.py" = -1.4\n')
    completed = _run_gate(repo)

    assert completed.returncode == 1
    assert f"{share}% prose, up from the frozen {round(share - 1.4, 1)}%" in completed.stderr


def test_two_fragments_each_carrying_a_waiver_compose_to_two_new_waivers(tmp_path: Path) -> None:
    """The shape `basicly-kr7t` could not record: a waiver taken without editing the anchor."""
    repo = _scratch_repo(tmp_path, _prose_module("# comment-density-waiver: provenance"))
    _second_waived_module(repo)
    for name in ("basicly-one", "basicly-two"):
        _fragment(repo, name, "[ratchet.comment_density]\ncount_delta = 1\n")

    completed = _run_gate(repo)

    assert completed.returncode == 0, completed.stderr
    assert "2 waived" in completed.stdout


def test_the_waiver_ratchet_still_binds_when_a_fragment_is_missing(tmp_path: Path) -> None:
    """The mutation: one fragment for two waivers is still a waiver nothing declared."""
    repo = _scratch_repo(tmp_path, _prose_module("# comment-density-waiver: provenance"))
    _second_waived_module(repo)
    _fragment(repo, "basicly-one", "[ratchet.comment_density]\ncount_delta = 1\n")

    completed = _run_gate(repo)

    assert completed.returncode == 1
    assert "waiver was added without saying so" in completed.stderr
    assert "under [ratchet.comment_density] in basicly.d/<bead-id>.toml" in completed.stderr


def test_a_fragment_that_is_not_a_delta_stops_the_gate(tmp_path: Path) -> None:
    """Fails closed: a fragment it cannot read must not compose to the recorded baseline."""
    repo = _scratch_repo(tmp_path, _prose_module())
    _fragment(repo, "basicly-one", '[ratchet.comment_density.frozen]\n"src/mod.py" = "58.6"\n')

    completed = _run_gate(repo)

    assert completed.returncode == 1
    assert "must be a numeric delta" in completed.stderr


def test_a_composed_share_is_reported_on_the_grid_it_is_measured_on() -> None:
    """A sum of one-decimal floats is not one: 51.3 - 0.1 is 51.199999999999996.

    Unrounded, the module the lane just cut would measure 51.2 and be reported as having
    grown past its own new baseline.
    """
    assert 51.3 - 0.1 != 51.2
    assert round(51.3 - 0.1, 1) == 51.2


def test_the_gate_composes_the_fragments_the_other_ratchets_do(tmp_path: Path) -> None:
    """One composer for three gates, so a fragment means the same thing at each of them."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.comment_density]\nwaiver_count = 1\n\n"
        '[tool.comment_density.frozen]\n"a.py" = 60.0\n',
        encoding="utf-8",
    )
    (tmp_path / dropin.FRAGMENT_DIR).mkdir()
    (tmp_path / dropin.FRAGMENT_DIR / "basicly-one.toml").write_text(
        '[ratchet.comment_density]\ncount_delta = 1\nfrozen = {"a.py" = -1.4}\n', encoding="utf-8"
    )

    ratchet = gate.load_ratchet(tmp_path)

    assert ratchet.frozen == {"a.py": 58.6}
    assert ratchet.count == 2


def test_a_small_module_is_out_of_scope() -> None:
    """One mandatory docstring dominates a stub, so its share says nothing about density."""
    modules = gate.tracked_modules(REPO_ROOT)
    assert all(module.tokens >= gate._MIN_TOKENS for module in modules)


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
        assert gate.waiver_reason(path.read_text(encoding="utf-8"), WAIVER_MARKER) is None
