from __future__ import annotations

import subprocess
from pathlib import Path

from tests.markdownlint_runtime_fixture import provision_markdownlint_double
from tests.test_install_baseline import _install

FRAGMENT_TITLE = "Orchestrated Session"


def test_the_orchestration_catalog_reaches_a_consumer_only_when_enabled(tmp_path: Path) -> None:
    provision_markdownlint_double(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # nosec B603 B607

    off = _install(tmp_path)

    assert off.returncode == 0, off.stdout + off.stderr
    assert FRAGMENT_TITLE not in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert not (tmp_path / ".claude/agents/orchestrator.md").exists()

    config = tmp_path / "basicly.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\n[orchestration]\nenabled = true\n", encoding="utf-8"
    )
    on = _install(tmp_path)

    assert on.returncode == 0, on.stdout + on.stderr
    for always_on in ("AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md"):
        assert FRAGMENT_TITLE in (tmp_path / always_on).read_text(encoding="utf-8"), always_on
    assert (tmp_path / ".claude/agents/orchestrator.md").is_file()
