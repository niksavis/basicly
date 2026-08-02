"""The `.importlinter` gate must discriminate, not merely report.

`.importlinter` used to declare one contract forbidding `basicly.fragments` and
`basicly.targets`. Neither module existed and neither structurally could — fragments
and targets are YAML under `.basicly/core/`, never Python under `src/basicly/` — so
`lint-imports` reported `1 kept, 0 broken` over the whole tree forever while running
on every commit in this repo and in every consumer repo (`basicly-tcmy.2`).

These tests are the control pair the old contract could never have passed: the same
staged copy of the package is checked unchanged (kept) and again with one upward
import injected (broken, naming both modules). A contract that cannot fail fails
these, so the gate's own gate is a gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sysconfig
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _lint_imports_binary() -> str:
    """Resolve import-linter's console script for the interpreter running the tests."""
    found = shutil.which("lint-imports", path=sysconfig.get_path("scripts"))
    if found is None:  # pragma: no cover - a dev-group install always provides it
        pytest.skip("lint-imports is not installed in this environment")
    return found


def _stage_package(tmp_path: Path) -> Path:
    """Copy the engine source and the real `.importlinter` into an isolated root.

    The checks run against this copy rather than the working tree so a test can
    inject a violation without touching `src/`. `PYTHONPATH` puts the copy ahead of
    the editable install's `.pth` entry, so grimp resolves `basicly` to the copy.
    """
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
    """`verify` sits well below `loop`; importing it is the archetypal violation."""
    _append_import(root / "basicly" / "verify.py", "from basicly import loop")


def _import_sideways_between_renderers(root: Path) -> None:
    """Per-target renderers are declared independent of one another."""
    _append_import(
        root / "basicly" / "renderers" / "copilot.py",
        "from basicly.renderers import claude",
    )


def _add_undeclared_module(root: Path) -> None:
    """`exhaustive = True` is what stops a new module from escaping the tiers."""
    (root / "basicly" / "ghost.py").write_text('"""Undeclared."""\n', encoding="utf-8")


def test_contracts_pass_on_the_unchanged_package(tmp_path: Path) -> None:
    """The positive control: the staged copy is exactly what the repo ships."""
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
    ],
)
def test_contracts_break_on_a_real_violation(
    tmp_path: Path, mutate: Callable[[Path], None], expected: tuple[str, ...]
) -> None:
    """The negative control: each violation is reported, naming the modules involved."""
    root = _stage_package(tmp_path)
    mutate(root)

    result = _run_lint_imports(root)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    for fragment in expected:
        assert fragment in output, output
