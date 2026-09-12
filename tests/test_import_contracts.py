from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sysconfig
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _lint_imports_binary() -> str:
    found = shutil.which("lint-imports", path=sysconfig.get_path("scripts"))
    if found is None:  # pragma: no cover - a dev-group install always provides it
        pytest.skip("lint-imports is not installed in this environment")
    return found


def _stage_package(tmp_path: Path) -> Path:

    root = tmp_path / "staged"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "src" / "basicly", root / "basicly")
    shutil.copy2(REPO_ROOT / ".importlinter", root / ".importlinter")
    return root


def _run_lint_imports(root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(root)}
    return subprocess.run(
        [_lint_imports_binary(), "--no-cache"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _append_import(module: Path, statement: str) -> None:
    module.write_text(f"{module.read_text(encoding='utf-8')}\n{statement}\n", encoding="utf-8")


def _import_upward_across_engine_tiers(root: Path) -> None:
    _append_import(root / "basicly" / "verify.py", "from basicly import loop")


def _import_sideways_between_renderers(root: Path) -> None:
    _append_import(
        root / "basicly" / "renderers" / "copilot.py",
        "from basicly.renderers import claude",
    )


def _add_undeclared_module(root: Path) -> None:
    (root / "basicly" / "ghost.py").write_text('"""Undeclared."""\n', encoding="utf-8")


def _action_surface_reads_engine_state(root: Path) -> None:

    _append_import(root / "basicly" / "board_actions.py", "from basicly import policy")


def test_contracts_pass_on_the_unchanged_package(tmp_path: Path) -> None:
    result = _run_lint_imports(_stage_package(tmp_path))

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "0 broken" in output, output


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            _import_upward_across_engine_tiers,
            ("basicly.verify is not allowed to import basicly.loop",),
            id="upward-across-tiers",
        ),
        pytest.param(
            _import_sideways_between_renderers,
            ("basicly.renderers.copilot is not allowed to import basicly.renderers.claude",),
            id="sideways-between-renderers",
        ),
        pytest.param(
            _add_undeclared_module,
            ("not listed as layers", "basicly.ghost"),
            id="module-outside-every-tier",
        ),
        pytest.param(
            _action_surface_reads_engine_state,
            (
                "basicly.board_actions is not allowed to import basicly.tracker",
                "basicly.board_actions -> basicly.policy",
            ),
            id="action-surface-reaching-engine-state",
        ),
    ],
)
def test_contracts_break_on_a_real_violation(
    tmp_path: Path, mutate: Callable[[Path], None], expected: tuple[str, ...]
) -> None:
    root = _stage_package(tmp_path)
    mutate(root)

    result = _run_lint_imports(root)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    for fragment in expected:
        assert fragment in output, output


@pytest.mark.parametrize("module", ["board_snapshot", "board_fields"])
def test_the_board_producer_does_not_import_supervise(module: str) -> None:

    source = REPO_ROOT / "src" / "basicly" / f"{module}.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            imported.add(node.module or "")

    assert imported, "no imports were read, so this probe proves nothing"
    assert "supervise" not in imported
    assert not any(name.endswith(".supervise") for name in imported)
