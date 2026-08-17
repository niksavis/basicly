"""Tests for the §9.4 test-file naming gate (basicly-u2hl.14).

The gate's whole value is that it fails, so most of these assert a *failure* and name the
unit it has to report. Three ways it could be fail-open, and each is pinned here:

* **A scan that found nothing must not pass.** No source units, or no test files, raises
  rather than reporting a clean tree — a gate whose scope silently emptied is
  indistinguishable from one whose tree is clean, which is this repo's named defect class.
* **A derived name that is another unit's own test file does not count.** Otherwise
  splitting `catalog` into `catalog_lint` and deleting `test_catalog.py` reads as covered,
  which is exactly the "the tests live under the other module's name" drift the gate was
  filed for.
* **The gate is wired to something that runs it.** An instrument built and never connected
  is the shape Phase S exists to remove.

The logic tests drive :func:`collect` and its two readers with synthetic path lists rather
than building repos: the observable behaviour is which findings a given tree produces, and
a `git ls-files` fixture per case would test git. One subprocess run covers the real tree
end to end.
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
SCRIPT = REPO_ROOT / ".scripts" / "check_test_naming.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_test_naming")


def _src(*names: str) -> list[str]:
    return [f"src/basicly/{name}" for name in names]


def _tests(*names: str) -> list[str]:
    return [f"tests/{name}" for name in names]


# --- what counts as a unit ------------------------------------------------------


def test_a_top_level_module_is_one_unit() -> None:
    """The default shape: one `<name>.py`, one unit named `<name>`."""
    assert gate.source_units(_src("cli.py", "loop.py")) == ["cli", "loop"]


def test_the_packages_own_init_is_not_a_unit() -> None:
    """It is the package, and a package with no other module has nothing to test."""
    assert gate.source_units(_src("__init__.py", "cli.py")) == ["cli"]


def test_a_subpackage_is_one_unit_however_many_modules_it_holds() -> None:
    """The unit is what `basicly` exposes, so `test_renderers.py` covers the package.

    The limit is deliberate and stated: a *new* subpackage with no test file fails, a new
    module inside an already-covered one does not. Widening that is a change to §9.4.
    """
    paths = _src("renderers/__init__.py", "renderers/claude.py", "renderers/codex.py")

    assert gate.source_units(paths) == ["renderers"]


def test_a_non_python_file_under_the_package_is_not_a_unit() -> None:
    """A marker file or shipped data has nothing a test file could be named after."""
    assert gate.source_units(_src("py.typed", "data.json", "cli.py")) == ["cli"]


def test_a_path_outside_the_package_is_ignored() -> None:
    """The gate's scope is `src/basicly`; `.scripts` and the kit have their own tests."""
    assert gate.source_units([".scripts/check_test_naming.py", "src/other/x.py"]) == []


# --- what counts as coverage ----------------------------------------------------


def test_a_test_files_name_is_its_claim_whatever_directory_it_is_in() -> None:
    """`tests/test_git_hooks/test_x.py` claims `x` exactly as a top-level file would."""
    stems = gate.test_stems(_tests("test_cli.py", "test_git_hooks/test_hook.py", "conftest.py"))

    assert stems == {"cli", "hook"}


def test_an_exact_name_covers_a_unit() -> None:
    """`test_<module>.py`, the default form §9.4 states first."""
    assert gate.collect(["cli"], {"cli"}) == []


def test_a_derived_name_covers_a_unit_when_no_exact_file_exists() -> None:
    """`test_<module>_<aspect>.py` is §9.4's second form, for a module worth splitting."""
    assert gate.collect(["br"], {"br_adapter", "br_seam"}) == []


def test_a_derived_candidate_that_is_another_units_test_file_does_not_count() -> None:
    """Otherwise deleting `test_catalog.py` after a split reads as covered.

    The positive control is the same call with `catalog_lint` absent from the unit list:
    the identical stem then *does* cover `catalog`, so the rule is discriminating between
    the two cases rather than rejecting every derived name.
    """
    findings = gate.collect(["catalog", "catalog_lint"], {"catalog_lint"})

    assert [finding.unit for finding in findings] == ["catalog"]
    assert gate.collect(["catalog"], {"catalog_lint"}) == []


def test_a_prefix_that_is_not_followed_by_an_underscore_is_not_coverage() -> None:
    """`test_loopstate.py` is not `test_loop_...`, and `merge` is not covered by `merger`."""
    findings = gate.collect(["loop", "merge"], {"loopstate", "merger"})

    assert [finding.unit for finding in findings] == ["loop", "merge"]


def test_an_uncovered_unit_is_reported_with_both_accepted_names() -> None:
    """The repair is naming a file, so the message spells both forms it will accept."""
    (finding,) = gate.collect(["mirror"], {"br_seam"})

    assert finding.unit == "mirror"
    assert "tests/test_mirror.py" in finding.remedy
    assert "tests/test_mirror_<aspect>.py" in finding.remedy


def test_findings_come_back_in_unit_order() -> None:
    """Stable output, so a diff of two runs shows what changed rather than a reshuffle."""
    findings = gate.collect(["zulu", "alpha", "mike"], set())

    assert [finding.unit for finding in findings] == ["alpha", "mike", "zulu"]


# --- a scan that found nothing must not pass ------------------------------------


@pytest.mark.parametrize(
    ("tracked", "expected"),
    [
        ([], "no source units found"),
        (["src/basicly/cli.py"], "no test_*.py files found"),
    ],
)
def test_a_scan_that_found_nothing_is_an_error_rather_than_a_clean_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tracked: list[str],
    expected: str,
) -> None:
    """A gate whose scope silently emptied is indistinguishable from a clean tree.

    Driven through `main` against a real empty git repo, because that is where the two
    guards live; the gate anchors on its own location rather than the working directory,
    so running it elsewhere would still scan this tree.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in tracked:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)

    assert gate.main() == 1
    assert expected in capsys.readouterr().err


def test_git_refusing_the_question_is_an_error(tmp_path: Path) -> None:
    """Not a repository, so the listing fails and the gate must say so rather than pass."""
    with pytest.raises(gate.ScanError, match="could not list tracked files"):
        gate._tracked(tmp_path, "src/basicly")


# --- the real tree, and the wiring ----------------------------------------------


def test_the_gate_passes_on_this_repository() -> None:
    """Run as a consumer runs it — every source unit has a test file named after it."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert "source units each have a test file named after them" in completed.stdout


def test_the_ten_modules_this_gate_was_filed_for_are_covered_by_an_exact_name() -> None:
    """The bead's acceptance criterion, held after the fact.

    Exact rather than derived, because each of these had its tests sitting under the
    origin module's name and a derived match would have been satisfied by that.

    ``surface_report`` left the list with the module: it enumerated the external tracker's
    CLI surface, and there is none (basicly-vkh0.42.7).
    """
    stems = gate.test_stems(gate._tracked(REPO_ROOT, gate.TEST_ROOT))

    assert {
        "capability_proof",
        "catalog_source",
        "dispatch_phase",
        "mirror",
        "owned_store",
        "repair_brief",
        "skill_source",
        "spend_calibration",
        "ui",
    } <= stems


def test_the_gate_is_declared_as_a_verify_check() -> None:
    """Wired to the fast set, so it runs at commit time and not only on request."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "test-naming" in checks
    entry = checks["test-naming"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    # A bare `python` on windows-latest is a system interpreter, not the project's.
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}
