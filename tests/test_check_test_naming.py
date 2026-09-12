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


def test_a_top_level_module_is_one_unit() -> None:
    assert gate.source_units(_src("cli.py", "loop.py")) == ["cli", "loop"]


def test_the_packages_own_init_is_not_a_unit() -> None:
    assert gate.source_units(_src("__init__.py", "cli.py")) == ["cli"]


def test_a_subpackage_is_one_unit_however_many_modules_it_holds() -> None:

    paths = _src("renderers/__init__.py", "renderers/claude.py", "renderers/codex.py")

    assert gate.source_units(paths) == ["renderers"]


def test_a_non_python_file_under_the_package_is_not_a_unit() -> None:
    assert gate.source_units(_src("py.typed", "data.json", "cli.py")) == ["cli"]


def test_a_path_outside_the_package_is_ignored() -> None:
    assert gate.source_units([".scripts/check_test_naming.py", "src/other/x.py"]) == []


def test_a_test_files_name_is_its_claim_whatever_directory_it_is_in() -> None:
    stems = gate.test_stems(_tests("test_cli.py", "test_git_hooks/test_hook.py", "conftest.py"))

    assert stems == {"cli", "hook"}


def test_an_exact_name_covers_a_unit() -> None:
    assert gate.collect(["cli"], {"cli"}) == []


def test_a_derived_name_covers_a_unit_when_no_exact_file_exists() -> None:
    assert gate.collect(["br"], {"br_adapter", "br_seam"}) == []


def test_a_derived_candidate_that_is_another_units_test_file_does_not_count() -> None:

    findings = gate.collect(["catalog", "catalog_lint"], {"catalog_lint"})

    assert [finding.unit for finding in findings] == ["catalog"]
    assert gate.collect(["catalog"], {"catalog_lint"}) == []


def test_a_prefix_that_is_not_followed_by_an_underscore_is_not_coverage() -> None:
    findings = gate.collect(["loop", "merge"], {"loopstate", "merger"})

    assert [finding.unit for finding in findings] == ["loop", "merge"]


def test_an_uncovered_unit_is_reported_with_both_accepted_names() -> None:
    (finding,) = gate.collect(["mirror"], {"br_seam"})

    assert finding.unit == "mirror"
    assert "tests/test_mirror.py" in finding.remedy
    assert "tests/test_mirror_<aspect>.py" in finding.remedy


def test_findings_come_back_in_unit_order() -> None:
    findings = gate.collect(["zulu", "alpha", "mike"], set())

    assert [finding.unit for finding in findings] == ["alpha", "mike", "zulu"]


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
    with pytest.raises(gate.ScanError, match="could not list tracked files"):
        gate._tracked(tmp_path, "src/basicly")


def test_the_gate_passes_on_this_repository() -> None:
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
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "test-naming" in checks
    entry = checks["test-naming"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}
