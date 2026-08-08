"""The kit-boundary gate must discriminate, not merely report (basicly-vkh0.16).

`docs/requirements/work-tracker.md` §4 claimed the one-way kit boundary was already
enforced by `lint-imports`. It was not: import-linter analyses the `basicly`
package, and the kit is flat modules outside it. `test_import_linter_cannot_see_a_kit_violation`
runs the tool on a seeded violation and records that it reports clean — so this
file's other tests are the control pair for a gate that replaces an unenforceable
claim, and every violation class is seeded rather than argued about.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sysconfig
import tomllib
from pathlib import Path

import pytest

from basicly.hooks import load_hook_specs

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "kit-boundary.py"
KIT_ROOT = REPO_ROOT / ".basicly" / "core" / "kit"


def _load_hook():
    spec = importlib.util.spec_from_file_location("kit_boundary", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_hook()


def _seed(root: Path, name: str, source: str) -> Path:
    """Write one module into a temporary kit tree."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(source, encoding="utf-8")
    return path


def _rules(root: Path) -> list[str]:
    """The rule names the gate trips on a kit tree, in report order."""
    return [finding.rule for finding in gate.scan(root)]


# --- the positive control ----------------------------------------------------


def test_the_kit_tree_this_gate_guards_actually_exists() -> None:
    """Everything below is vacuous if the gate is scanning nothing."""
    modules = gate.kit_modules(KIT_ROOT)
    assert len(modules) >= 3, f"expected the shipped kit modules under {KIT_ROOT}"
    assert {module.name for module in modules} >= {"tier_resolver.py", "claude_tier_hook.py"}


def test_the_unchanged_kit_tree_passes() -> None:
    """The shipped kit honours its own boundary."""
    assert gate.scan(KIT_ROOT, REPO_ROOT) == []


# --- one seeded violation per class ------------------------------------------


@pytest.mark.parametrize(
    ("case", "source"),
    [
        ("static-import", "from basicly.config import load_harness_config\n"),
        ("plain-import", "import basicly\n"),
        ("submodule-import", "import basicly.policy as policy\n"),
    ],
)
def test_a_kit_module_importing_basicly_fails(tmp_path: Path, case: str, source: str) -> None:
    """AC: the gate fails on a kit module importing basicly."""
    _seed(tmp_path / "kit", f"{case.replace('-', '_')}.py", source)
    assert _rules(tmp_path / "kit") == ["imports-basicly"]


@pytest.mark.parametrize(
    "call",
    [
        'importlib.import_module("basicly.policy")',
        'importlib.util.find_spec("basicly.config")',
        '__import__("basicly")',
    ],
)
def test_a_dynamically_imported_engine_module_fails(tmp_path: Path, call: str) -> None:
    """The kit already loads a sibling through importlib, so this route is live."""
    _seed(tmp_path / "kit", "sneaky.py", f"import importlib.util\nmod = {call}\n")
    assert _rules(tmp_path / "kit") == ["dynamic-import-basicly"]


@pytest.mark.parametrize("module", ["config.py", "ui.py", "session.py", "policy.py"])
def test_reading_an_engine_module_file_fails(tmp_path: Path, module: str) -> None:
    """AC: the four surfaces §4 names — config loader, logging, session state, policy."""
    source = f'from pathlib import Path\nTEXT = Path("src/basicly/{module}").read_text()\n'
    _seed(tmp_path / "kit", "reader.py", source)
    assert _rules(tmp_path / "kit") == ["reads-engine-source"]


@pytest.mark.parametrize(
    "expression",
    [
        'Path("basicly.toml").read_text()',
        'Path("basicly.local.toml").read_text()',
        'Path(".basicly") / "usage" / "run.json"',
        'Path(".basicly/ledger/tracker-usage.jsonl")',
        'Path(".basicly").joinpath("state", "install.json")',
        'Path(os.path.join(".basicly", "state"))',
    ],
)
def test_reading_engine_config_or_state_fails(tmp_path: Path, expression: str) -> None:
    """The config loader's input and the engine's own ledgers are off limits."""
    _seed(tmp_path / "kit", "reader.py", f"import os\nfrom pathlib import Path\nX = {expression}\n")
    assert _rules(tmp_path / "kit") == ["reads-engine-state"]


def test_an_unparseable_module_is_a_finding_not_a_skip(tmp_path: Path) -> None:
    """A gate that silently passes what it could not read is the shape this replaces."""
    _seed(tmp_path / "kit", "broken.py", "def f(:\n")
    assert _rules(tmp_path / "kit") == ["unparseable"]


# --- and the same shapes that are legitimate --------------------------------


def test_prose_about_the_boundary_is_not_a_finding(tmp_path: Path) -> None:
    """Every kit module documents the rule; a docstring naming it is not a breach."""
    source = (
        '"""No basicly: no import basicly, never src/basicly/config.py, '
        'never .basicly/usage."""\n'
        "VALUE = 1\n"
    )
    _seed(tmp_path / "kit", "documented.py", source)
    assert _rules(tmp_path / "kit") == []


def test_the_kits_own_data_root_is_allowed(tmp_path: Path) -> None:
    """`tier_resolver.CORE_DIR` is written exactly this way and must stay legal."""
    source = (
        "from pathlib import Path\n"
        'CORE_DIR = Path(".basicly") / "core"\n'
        'MAP = CORE_DIR / "models" / "model-map.json"\n'
    )
    _seed(tmp_path / "kit", "resolver.py", source)
    assert _rules(tmp_path / "kit") == []


def test_a_sibling_kit_module_load_is_allowed(tmp_path: Path) -> None:
    """`tracker/events.py` loads `ids.py` through importlib; only basicly is barred."""
    source = (
        "import importlib.util\n"
        'spec = importlib.util.spec_from_file_location("kit_ids", "ids.py")\n'
        'mod = importlib.import_module("json")\n'
    )
    _seed(tmp_path / "kit", "events.py", source)
    assert _rules(tmp_path / "kit") == []


def test_the_hooks_directory_is_not_mistaken_for_the_package(tmp_path: Path) -> None:
    """`.basicly/core/hooks/x.py` is a path under the kit's own root, not `basicly/`."""
    source = 'MIRROR = ".basicly/core/hooks/tracker-path-scan.py"\n'
    _seed(tmp_path / "kit", "mirror.py", source)
    assert _rules(tmp_path / "kit") == []


# --- the whole tree, and the report ------------------------------------------


def test_every_violating_module_in_a_tree_is_reported(tmp_path: Path) -> None:
    """Findings are per module, ordered, and a clean sibling is not implicated."""
    kit = tmp_path / "kit"
    _seed(kit, "a_clean.py", '"""Fine."""\nX = 1\n')
    _seed(kit, "b_import.py", "import basicly\n")
    _seed(kit / "tracker", "c_state.py", 'from pathlib import Path\nP = Path("basicly.toml")\n')
    findings = gate.scan(kit, kit)
    assert [(f.path, f.rule) for f in findings] == [
        ("b_import.py", "imports-basicly"),
        ("tracker/c_state.py", "reads-engine-state"),
    ]


def test_main_fails_and_names_the_module_and_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator gets the file, the line and the rule, not just an exit code."""
    _seed(tmp_path / "kit", "leaky.py", "\nfrom basicly import config\n")
    assert gate.main(["--kit-root", str(tmp_path / "kit")]) == 1
    err = capsys.readouterr().err
    assert "leaky.py:2: imports-basicly" in err
    assert "one-way" in err


def test_main_passes_on_the_repos_own_kit(capsys: pytest.CaptureFixture[str]) -> None:
    """The gate exercised the way the hook invokes it, against the shipped tree."""
    assert gate.main(["--kit-root", str(KIT_ROOT)]) == 0
    assert capsys.readouterr().err == ""


def test_main_passes_when_a_consumer_has_no_kit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hook ships to repos that installed no kit; it says so rather than failing."""
    assert gate.main(["--kit-root", str(tmp_path / "absent")]) == 0
    assert "nothing to gate" in capsys.readouterr().out


# --- the wiring: CI and commit time, not advisory ----------------------------


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """`full` is what CI runs (`pre-push.py`), so the failure is a CI failure."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    check = next(c for c in config["verify"]["checks"] if c["name"] == "kit-boundary")
    assert "full" in check["modes"]
    assert ".basicly/core/hooks/kit-boundary.py" in check["command"]


def test_the_gate_is_wired_as_a_pre_commit_hook() -> None:
    """And it ships to consumers with the kit, so the boundary travels with it."""
    spec = next(spec for spec in load_hook_specs() if spec.id == "kit-boundary")
    assert spec.script == "kit-boundary.py"
    assert spec.stage == "pre-commit"
    assert spec.always_run is True


# --- why the claim it replaces was unenforceable -----------------------------


def test_no_kit_module_is_part_of_the_basicly_package() -> None:
    """The structural reason import-linter cannot reach the kit: it is not in it."""
    assert not (KIT_ROOT / "__init__.py").exists()
    assert KIT_ROOT.relative_to(REPO_ROOT).parts[0] != "src"
    contracts = (REPO_ROOT / ".importlinter").read_text(encoding="utf-8")
    assert "root_package = basicly" in contracts


def test_import_linter_cannot_see_a_kit_violation(tmp_path: Path) -> None:
    """Measured, not argued: the tool reports clean on the violation this gate fails.

    Same seeded module, two gates. `lint-imports` runs over a staged copy of the
    engine package with the real contracts and a kit beside it — the layout the
    design assumed was covered — and keeps every contract.
    """
    binary = shutil.which("lint-imports", path=sysconfig.get_path("scripts"))
    if binary is None:  # pragma: no cover - a dev-group install always provides it
        pytest.skip("lint-imports is not installed in this environment")

    root = tmp_path / "staged"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "src" / "basicly", root / "basicly")
    shutil.copy2(REPO_ROOT / ".importlinter", root / ".importlinter")
    kit = root / ".basicly" / "core" / "kit"
    _seed(kit, "leaky.py", "import basicly.config\n")

    env = {**os.environ, "PYTHONPATH": str(root)}
    proc = subprocess.run([binary], cwd=root, env=env, capture_output=True, text=True, check=False)

    assert proc.returncode == 0, f"contracts unexpectedly broke:\n{proc.stdout}{proc.stderr}"
    assert "Contracts: 2 kept, 0 broken." in proc.stdout
    assert _rules(kit) == ["imports-basicly"]
