from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("basicly-tracker", "basicly-board", "basicly-comments", "basicly-tier")


@pytest.mark.parametrize("tool", ["basicly", *TOOLS])
def test_distributed_entrypoints_name_uv_before_loading_on_an_old_runtime(tool: str) -> None:
    source = (
        ROOT / "src/basicly/__init__.py"
        if tool == "basicly"
        else ROOT / "packages" / tool / tool.replace("-", "_") / "__init__.py"
    )
    boot = (
        "import importlib.util,sys; sys.version_info=(3,13,0); "
        "s=importlib.util.spec_from_file_location('runtime_probe',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", boot, str(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert done.returncode != 0
    assert "Python 3.14" in done.stderr
    assert "uvx --python" in done.stderr
    assert tool in done.stderr
    assert "Traceback" not in done.stderr


def test_every_distribution_declares_the_same_supported_runtime_floor() -> None:
    projects = [
        ROOT / "pyproject.toml",
        *(ROOT / "packages" / tool / "pyproject.toml" for tool in TOOLS),
    ]

    assert len(projects) == 5
    for project in projects:
        assert tomllib.loads(project.read_text())["project"]["requires-python"] == ">=3.14"
