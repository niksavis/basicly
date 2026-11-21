from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.markdownlint_runtime_fixture import REPO_ROOT, provision_markdownlint_double
from tests.test_install_baseline import _install

PROJECTED = (
    ".claude/agents/orchestrator.md",
    ".github/agents/orchestrator.agent.md",
    ".agents/roles/orchestrator.md",
)
LINTER = REPO_ROOT / "node_modules" / ".bin" / "markdownlint-cli2"


def test_every_projected_orchestrator_agent_passes_the_repository_markdownlint_rules(
    tmp_path: Path,
) -> None:
    if not LINTER.is_file() or shutil.which("node") is None:
        pytest.fail("markdownlint-cli2 is not installed; run npm ci in the repository")
    provision_markdownlint_double(tmp_path)
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=clean)  # nosec B603 B607
    config = tmp_path / "basicly.toml"
    assert _install(tmp_path).returncode == 0
    config.write_text(
        config.read_text(encoding="utf-8") + "\n[orchestration]\nenabled = true\n", encoding="utf-8"
    )
    assert _install(tmp_path).returncode == 0
    rules = yaml.safe_load((REPO_ROOT / ".markdownlint-cli2.yaml").read_text(encoding="utf-8"))
    lint_dir = tmp_path / "lint"
    lint_dir.mkdir()
    (lint_dir / ".markdownlint-cli2.jsonc").write_text(
        json.dumps({"config": rules["config"]}), encoding="utf-8"
    )
    for index, relative in enumerate(PROJECTED):
        source = tmp_path / relative
        assert source.is_file(), relative
        shutil.copyfile(source, lint_dir / f"agent-{index}.md")

    linted = subprocess.run(  # nosec B603
        [str(LINTER), "agent-*.md"],
        cwd=lint_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert linted.returncode == 0, linted.stdout + linted.stderr
